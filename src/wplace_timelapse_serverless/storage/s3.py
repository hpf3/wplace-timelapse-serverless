"""S3-compatible storage backend for tiles and manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional, Set, Tuple

import boto3
from botocore.client import BaseClient
from botocore.exceptions import ClientError

from wplace_timelapse_serverless.manifest import (
    DeltaManifest,
    ManifestPointer,
    ManifestTile,
    format_timestamp,
    parse_timestamp,
)
from wplace_timelapse_serverless.storage.base import AbstractStorageBackend, Coordinate, StoredTile


@dataclass(frozen=True, slots=True)
class S3Paths:
    bucket: str
    tile_prefix: str
    manifest_prefix: str
    latest_suffix: str = "latest.json"
    state_suffix: str = "tile-state.json"

    def tile_key(self, slug: str, checksum: str) -> str:
        return f"{self.tile_prefix.rstrip('/')}/{slug}/{checksum[:2]}/{checksum}.png"

    def manifest_key(self, slug: str, capture_time: datetime) -> str:
        stamp = capture_time.strftime("%Y/%m/%d/%H%M%S")
        return f"{self.manifest_prefix.rstrip('/')}/{slug}/{stamp}.json"

    def latest_key(self, slug: str) -> str:
        return f"{self.manifest_prefix.rstrip('/')}/{slug}/{self.latest_suffix}"

    def tile_state_key(self, slug: str) -> str:
        return f"{self.manifest_prefix.rstrip('/')}/{slug}/{self.state_suffix}"


class S3StorageBackend(AbstractStorageBackend):
    """Store tiles and manifests in any S3-compatible object store."""

    def __init__(
        self,
        *,
        bucket: str,
        region: Optional[str] = None,
        tile_prefix: str = "tiles",
        manifest_prefix: str = "manifests",
        latest_suffix: str = "latest.json",
        state_suffix: str = "tile-state.json",
        endpoint_url: Optional[str] = None,
        client: Optional[BaseClient] = None,
    ) -> None:
        self.paths = S3Paths(
            bucket=bucket,
            tile_prefix=tile_prefix,
            manifest_prefix=manifest_prefix,
            latest_suffix=latest_suffix,
            state_suffix=state_suffix,
        )
        self.client = client or boto3.client("s3", region_name=region, endpoint_url=endpoint_url)

    def get_latest_manifest(self, slug: str) -> Optional[ManifestPointer]:
        key = self.paths.latest_key(slug)
        try:
            response = self.client.get_object(Bucket=self.paths.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return None
            raise

        payload = response["Body"].read()
        data = json.loads(payload)
        return ManifestPointer(
            object_key=data["manifest_key"],
            capture_time=parse_timestamp(data["capture_time"]),
        )

    def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
        try:
            response = self.client.get_object(Bucket=self.paths.bucket, Key=pointer.object_key)
        except ClientError as exc:
            raise FileNotFoundError(f"Manifest object {pointer.object_key} not found") from exc
        payload = response["Body"].read().decode("utf-8")
        return DeltaManifest.from_json(payload)

    def store_tile(
        self,
        slug: str,
        capture_time: datetime,
        coord: Coordinate,
        payload: bytes,
        checksum: str | None = None,
    ) -> StoredTile:
        digest = checksum or hashlib.sha256(payload).hexdigest()
        object_key = self.paths.tile_key(slug, digest)

        existed = self._object_exists(object_key)
        if not existed:
            self.client.put_object(
                Bucket=self.paths.bucket,
                Key=object_key,
                Body=payload,
                ContentType="image/png",
                Metadata={
                    "slug": slug,
                    "capture_time": capture_time.isoformat(),
                    "coordinate": f"{coord[0]},{coord[1]}",
                    "checksum": digest,
                },
            )

        tile = ManifestTile(
            coordinate=coord,
            object_key=object_key,
            checksum=digest,
            size=len(payload),
        )
        return StoredTile(tile=tile, existed=existed)

    def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
        key = self.paths.manifest_key(manifest.slug, manifest.capture_time)
        self.client.put_object(
            Bucket=self.paths.bucket,
            Key=key,
            Body=manifest.to_json().encode("utf-8"),
            ContentType="application/json",
        )
        return ManifestPointer(object_key=key, capture_time=manifest.capture_time)

    def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
        payload = json.dumps(
            {
                "manifest_key": pointer.object_key,
                "capture_time": format_timestamp(pointer.capture_time),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        key = self.paths.latest_key(slug)
        self.client.put_object(
            Bucket=self.paths.bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
        )

    def load_tile_cache(self, slug: str) -> Dict[Coordinate, ManifestTile]:
        """Load cached tile metadata for a slug, returning an empty map when absent."""
        key = self.paths.tile_state_key(slug)
        try:
            response = self.client.get_object(Bucket=self.paths.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                return {}
            raise

        payload = response["Body"].read().decode("utf-8")
        data = json.loads(payload)

        tiles: Dict[Coordinate, ManifestTile] = {}
        for entry in data.get("tiles", []):
            coordinate_raw = entry.get("coordinate")
            if isinstance(coordinate_raw, (list, tuple)) and len(coordinate_raw) == 2:
                coord = (int(coordinate_raw[0]), int(coordinate_raw[1]))
            else:  # pragma: no cover - guard for unexpected legacy formats
                raise ValueError(f"Invalid coordinate entry in cached tile state: {coordinate_raw!r}")
            tiles[coord] = ManifestTile(
                coordinate=coord,
                object_key=str(entry["object_key"]),
                checksum=str(entry["checksum"]),
                size=int(entry["size"]),
            )

        return tiles

    def write_tile_cache(
        self,
        *,
        slug: str,
        capture_time: datetime,
        tiles: Dict[Coordinate, ManifestTile],
        expected_coordinates: Iterable[Coordinate],
    ) -> None:
        """Persist cached tile metadata to avoid expensive manifest history walks."""
        expected_set = set(expected_coordinates)
        payload_tiles = []
        present_coords: Set[Coordinate] = set()

        for coord, tile in tiles.items():
            if expected_set and coord not in expected_set:
                continue
            payload_tiles.append(
                {
                    "coordinate": [int(coord[0]), int(coord[1])],
                    "object_key": tile.object_key,
                    "checksum": tile.checksum,
                    "size": tile.size,
                }
            )
            present_coords.add(coord)

        payload_tiles.sort(key=lambda entry: (entry["coordinate"][0], entry["coordinate"][1]))

        missing_coords = []
        if expected_set:
            missing_coords = [
                [coord[0], coord[1]]
                for coord in sorted(expected_set - present_coords, key=lambda item: (item[0], item[1]))
            ]

        document = {
            "capture_time": format_timestamp(capture_time),
            "tile_count": len(payload_tiles),
            "expected_count": len(expected_set) if expected_set else len(present_coords),
            "missing_coordinates": missing_coords,
            "tiles": payload_tiles,
        }

        payload = json.dumps(document, separators=(",", ":")).encode("utf-8")
        key = self.paths.tile_state_key(slug)
        self.client.put_object(
            Bucket=self.paths.bucket,
            Key=key,
            Body=payload,
            ContentType="application/json",
        )

    def _object_exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.paths.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            # Some providers do not allow HEAD when GetObject is permitted; treat as missing.
            if code in {"403", "AccessDenied"}:
                return False
            raise


__all__ = ["S3StorageBackend", "S3Paths"]
