"""Delta manifest data structures for timelapse captures."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO8601 timestamp into an aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_timestamp(value: datetime) -> str:
    """Format a datetime as ISO8601 with trailing Z."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


Coordinate = Tuple[int, int]


@dataclass(frozen=True, slots=True)
class ManifestTile:
    """Reference to a tile stored in object storage."""

    coordinate: Coordinate
    object_key: str
    checksum: str
    size: int


@dataclass(frozen=True, slots=True)
class ManifestFailure:
    """Failed tile capture metadata."""

    coordinate: Coordinate
    reason: str


@dataclass(slots=True)
class DeltaManifest:
    """Description of tiles captured during a specific run."""

    slug: str
    capture_time: datetime
    previous_manifest: Optional[str]
    tiles: List[ManifestTile] = field(default_factory=list)
    failed_tiles: List[ManifestFailure] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)

    def tile_map(self) -> Dict[Coordinate, ManifestTile]:
        return {tile.coordinate: tile for tile in self.tiles}

    def to_dict(self) -> Dict[str, object]:
        return {
            "slug": self.slug,
            "capture_time": format_timestamp(self.capture_time),
            "previous_manifest": self.previous_manifest,
            "tiles": [
                {
                    "coordinate": list(tile.coordinate),
                    "object_key": tile.object_key,
                    "checksum": tile.checksum,
                    "size": tile.size,
                }
                for tile in self.tiles
            ],
            "failed_tiles": [
                {
                    "coordinate": list(failure.coordinate),
                    "reason": failure.reason,
                }
                for failure in self.failed_tiles
            ],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "DeltaManifest":
        tiles_data = data.get("tiles", [])
        failed_data = data.get("failed_tiles", [])

        tiles: List[ManifestTile] = [
            ManifestTile(
                coordinate=tuple(entry["coordinate"]) if isinstance(entry["coordinate"], list) else entry["coordinate"],
                object_key=str(entry["object_key"]),
                checksum=str(entry["checksum"]),
                size=int(entry["size"]),
            )
            for entry in tiles_data  # type: ignore[arg-type]
        ]
        failures: List[ManifestFailure] = [
            ManifestFailure(
                coordinate=tuple(entry["coordinate"]) if isinstance(entry["coordinate"], list) else entry["coordinate"],
                reason=str(entry["reason"]),
            )
            for entry in failed_data  # type: ignore[arg-type]
        ]

        metadata = {str(k): str(v) for k, v in dict(data.get("metadata", {})).items()}  # type: ignore[arg-type]

        return cls(
            slug=str(data["slug"]),
            capture_time=parse_timestamp(str(data["capture_time"])),
            previous_manifest=data.get("previous_manifest"),  # type: ignore[arg-type]
            tiles=tiles,
            failed_tiles=failures,
            metadata=metadata,
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, payload: str) -> "DeltaManifest":
        return cls.from_dict(json.loads(payload))


@dataclass(frozen=True, slots=True)
class ManifestPointer:
    """Reference to a manifest stored in object storage."""

    object_key: str
    capture_time: datetime


__all__ = [
    "Coordinate",
    "DeltaManifest",
    "ManifestFailure",
    "ManifestPointer",
    "ManifestTile",
    "format_timestamp",
    "parse_timestamp",
]
