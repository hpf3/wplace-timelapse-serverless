from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

from wplace_timelapse_serverless.capture import run_capture
from wplace_timelapse_serverless.config import Coordinates, TimelapseConfig
from wplace_timelapse_serverless.manifest import DeltaManifest, ManifestPointer, ManifestTile
from wplace_timelapse_serverless.storage.base import StorageBackend, StoredTile


Coordinate = Tuple[int, int]


class DummyFetcher:
    def __init__(self) -> None:
        self.payloads: Dict[Coordinate, bytes] = {}

    def fetch_tile(self, x: int, y: int) -> bytes:
        try:
            return self.payloads[(x, y)]
        except KeyError as exc:  # pragma: no cover - guard for unexpected coordinates
            raise AssertionError(f"No payload for coordinate {(x, y)}") from exc

    def sleep_between_requests(self, remaining: int) -> None:  # pragma: no cover - no-op for tests
        return


@dataclass(slots=True)
class InMemoryStorage(StorageBackend):
    tiles: Dict[str, bytes] = field(default_factory=dict)
    manifests: Dict[str, DeltaManifest] = field(default_factory=dict)
    latest: Dict[str, ManifestPointer] = field(default_factory=dict)
    manifest_counter: int = 0

    def get_latest_manifest(self, slug: str) -> Optional[ManifestPointer]:
        return self.latest.get(slug)

    def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
        try:
            return self.manifests[pointer.object_key]
        except KeyError as exc:  # pragma: no cover - guard for unexpected lookups
            raise FileNotFoundError(pointer.object_key) from exc

    def store_tile(
        self,
        slug: str,
        capture_time: datetime,
        coord: Coordinate,
        payload: bytes,
        checksum: str,
    ) -> StoredTile:
        object_key = f"tiles/{slug}/{checksum}.png"
        existed = object_key in self.tiles
        self.tiles[object_key] = payload
        tile = ManifestTile(coordinate=coord, object_key=object_key, checksum=checksum, size=len(payload))
        return StoredTile(tile=tile, existed=existed)

    def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
        self.manifest_counter += 1
        key = f"manifests/{manifest.slug}/{self.manifest_counter}.json"
        # Store a shallow copy to mirror persisted manifest data.
        stored_manifest = DeltaManifest(
            slug=manifest.slug,
            capture_time=manifest.capture_time,
            previous_manifest=manifest.previous_manifest,
            tiles=list(manifest.tiles),
            failed_tiles=list(manifest.failed_tiles),
            metadata=dict(manifest.metadata),
        )
        self.manifests[key] = stored_manifest
        return ManifestPointer(object_key=key, capture_time=manifest.capture_time)

    def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
        self.latest[slug] = pointer


def _make_config() -> TimelapseConfig:
    return TimelapseConfig(
        slug="test-slug",
        name="Test",
        coordinates=Coordinates(xmin=0, xmax=0, ymin=0, ymax=1),
    )


def test_capture_uses_manifest_history_for_unchanged_tiles() -> None:
    storage = InMemoryStorage()
    fetcher = DummyFetcher()
    config = _make_config()

    frames = [
        {(0, 0): b"A", (0, 1): b"B"},
        {(0, 0): b"A", (0, 1): b"C"},
        {(0, 0): b"A", (0, 1): b"C"},
    ]

    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

    # First capture seeds storage with both tiles marked as changed.
    fetcher.payloads = frames[0]
    run1 = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time,
    )
    assert {tile.coordinate for tile in run1.changed_tiles} == {(0, 0), (0, 1)}
    assert not run1.deduplicated_tiles

    # Second capture only changes the second tile while deduplicating the first.
    fetcher.payloads = frames[1]
    run2 = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time + timedelta(minutes=5),
    )
    assert {tile.coordinate for tile in run2.changed_tiles} == {(0, 1)}
    assert {tile.coordinate for tile in run2.deduplicated_tiles} == {(0, 0)}

    # Third capture should re-use manifest history to deduplicate both tiles.
    fetcher.payloads = frames[2]
    run3 = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time + timedelta(minutes=10),
    )
    assert not run3.changed_tiles
    assert {tile.coordinate for tile in run3.deduplicated_tiles} == {(0, 0), (0, 1)}
