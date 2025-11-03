from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Dict, Optional, Set, Tuple

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


def test_capture_records_full_snapshot_on_new_day() -> None:
    storage = InMemoryStorage()
    fetcher = DummyFetcher()
    config = _make_config()

    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

    fetcher.payloads = {(0, 0): b"A", (0, 1): b"B"}
    first = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time,
    )
    assert {tile.coordinate for tile in first.changed_tiles} == {(0, 0), (0, 1)}
    assert not first.deduplicated_tiles

    second = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time + timedelta(minutes=6),
    )
    assert not second.changed_tiles
    assert {tile.coordinate for tile in second.deduplicated_tiles} == {(0, 0), (0, 1)}

    third = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=base_time + timedelta(days=1),
    )
    assert {tile.coordinate for tile in third.changed_tiles} == {(0, 0), (0, 1)}
    assert not third.deduplicated_tiles


def test_capture_prefers_cached_tile_state_over_history() -> None:
    base_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
    capture_time = base_time + timedelta(minutes=5)

    config = _make_config()
    fetcher = DummyFetcher()

    payload_a = b"A"
    payload_b = b"B"
    fetcher.payloads = {(0, 0): payload_a, (0, 1): payload_b}

    cached_tiles: Dict[Coordinate, ManifestTile] = {
        (0, 0): ManifestTile(
            coordinate=(0, 0),
            object_key="tiles/test/aa.png",
            checksum=sha256(payload_a).hexdigest(),
            size=len(payload_a),
        ),
        (0, 1): ManifestTile(
            coordinate=(0, 1),
            object_key="tiles/test/bb.png",
            checksum=sha256(payload_b).hexdigest(),
            size=len(payload_b),
        ),
    }

    class CacheStorage(StorageBackend):
        def __init__(self) -> None:
            self.load_manifest_calls = 0
            self.last_cache_tiles: Optional[Dict[Coordinate, ManifestTile]] = None
            self.last_cache_expected: Optional[Set[Coordinate]] = None
            self.last_cache_time: Optional[datetime] = None

        def get_latest_manifest(self, slug: str) -> ManifestPointer:
            return ManifestPointer(object_key="manifests/test/prev.json", capture_time=base_time)

        def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
            self.load_manifest_calls += 1
            raise AssertionError("Manifest history should not be consulted when cache is populated")

        def store_tile(
            self,
            slug: str,
            capture_time: datetime,
            coord: Coordinate,
            payload: bytes,
            checksum: str,
        ) -> StoredTile:
            raise AssertionError("Tile uploads should be skipped when prior state is cached")

        def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
            return ManifestPointer(object_key="manifests/test/current.json", capture_time=manifest.capture_time)

        def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
            return None

        def load_tile_cache(self, slug: str) -> Dict[Coordinate, ManifestTile]:
            return cached_tiles

        def write_tile_cache(
            self,
            *,
            slug: str,
            capture_time: datetime,
            tiles: Dict[Coordinate, ManifestTile],
            expected_coordinates: Set[Coordinate],
        ) -> None:
            self.last_cache_tiles = dict(tiles)
            self.last_cache_expected = set(expected_coordinates)
            self.last_cache_time = capture_time

    storage = CacheStorage()
    outcome = run_capture(
        slug=config.slug,
        config=config,
        storage=storage,
        fetcher=fetcher,
        capture_time=capture_time,
    )

    assert storage.load_manifest_calls == 0
    assert not outcome.changed_tiles
    assert {(tile.coordinate) for tile in outcome.deduplicated_tiles} == {(0, 0), (0, 1)}

    assert storage.last_cache_tiles == cached_tiles
    assert storage.last_cache_expected == set(config.coordinates.iter_tiles())
    assert storage.last_cache_time == capture_time
