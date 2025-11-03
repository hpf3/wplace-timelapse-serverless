"""S3-compatible storage backend for tiles and manifests."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

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

    def tile_key(self, slug: str, checksum: str) -> str:
        return f"{self.tile_prefix.rstrip('/')}/{slug}/{checksum[:2]}/{checksum}.png"

    def manifest_key(self, slug: str, capture_time: datetime) -> str:
        stamp = capture_time.strftime("%Y/%m/%d/%H%M%S")
        return f"{self.manifest_prefix.rstrip('/')}/{slug}/{stamp}.json"

    def latest_key(self, slug: str) -> str:
        return f"{self.manifest_prefix.rstrip('/')}/{slug}/{self.latest_suffix}"


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
        endpoint_url: Optional[str] = None,
        client: Optional[BaseClient] = None,
    ) -> None:
        self.paths = S3Paths(bucket=bucket, tile_prefix=tile_prefix, manifest_prefix=manifest_prefix, latest_suffix=latest_suffix)
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
