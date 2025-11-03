"""Capture runner that orchestrates tile downloads and manifest writes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

from wplace_timelapse_serverless.config import TimelapseConfig
from wplace_timelapse_serverless.manifest import DeltaManifest, ManifestFailure, ManifestPointer, ManifestTile
from wplace_timelapse_serverless.storage.base import StorageBackend
from wplace_timelapse_serverless.tile_fetcher import TileFetchError, TileFetcher


LOGGER = logging.getLogger("wplace.capture")


@dataclass(frozen=True, slots=True)
class CaptureOutcome:
    """Summary of a capture run."""

    manifest: ManifestPointer
    changed_tiles: List[ManifestTile]
    deduplicated_tiles: List[ManifestTile]
    failures: List[ManifestFailure]
    duration_seconds: float


def _build_previous_tile_map(
    storage: StorageBackend,
    pointer: ManifestPointer,
    expected_coordinates: Iterable[Tuple[int, int]],
) -> Dict[Tuple[int, int], ManifestTile]:
    """Walk manifest history to recover the latest tile metadata for each coordinate."""
    tile_state: Dict[Tuple[int, int], ManifestTile] = {}
    remaining: Set[Tuple[int, int]] = set(expected_coordinates)
    visited_keys: Set[str] = set()

    current: Optional[ManifestPointer] = pointer
    while current and current.object_key not in visited_keys:
        manifest = storage.load_manifest(current)
        visited_keys.add(current.object_key)

        for tile in manifest.tiles:
            if tile.coordinate not in tile_state:
                tile_state[tile.coordinate] = tile
                remaining.discard(tile.coordinate)

        if not remaining:
            break

        previous_key = manifest.previous_manifest
        if not previous_key:
            break

        current = ManifestPointer(object_key=previous_key, capture_time=manifest.capture_time)

    return tile_state


def run_capture(
    *,
    slug: str,
    config: TimelapseConfig,
    storage: StorageBackend,
    fetcher: TileFetcher,
    capture_time: Optional[datetime] = None,
    extra_metadata: Optional[Dict[str, str]] = None,
) -> CaptureOutcome:
    """Capture tiles for a slug, emit a delta manifest, and update storage state."""
    capture_at = capture_time or datetime.now(timezone.utc)
    start = perf_counter()

    pointer = storage.get_latest_manifest(slug)

    coordinates = list(config.coordinates.iter_tiles())
    total_tiles = len(coordinates)
    LOGGER.info("Starting capture for %s (%d tiles)", slug, total_tiles)

    previous_tiles: Dict[Tuple[int, int], ManifestTile] = (
        _build_previous_tile_map(storage, pointer, coordinates) if pointer else {}
    )

    changed_tiles: List[ManifestTile] = []
    deduplicated_tiles: List[ManifestTile] = []
    failures: List[ManifestFailure] = []

    seen_checksums: Dict[str, ManifestTile] = {}

    for index, (x, y) in enumerate(coordinates):
        try:
            payload = fetcher.fetch_tile(x, y)
        except TileFetchError as exc:
            LOGGER.warning("Tile %s/%s at %d,%d failed: %s", slug, capture_at.isoformat(), x, y, exc)
            failures.append(ManifestFailure(coordinate=(x, y), reason=str(exc)))
            continue

        checksum = sha256(payload).hexdigest()
        previous_tile = previous_tiles.get((x, y))
        if previous_tile and previous_tile.checksum == checksum:
            deduplicated_tiles.append(previous_tile)
            fetcher.sleep_between_requests(total_tiles - index - 1)
            continue

        cached_tile = seen_checksums.get(checksum)
        if cached_tile:
            changed_tiles.append(
                ManifestTile(
                    coordinate=(x, y),
                    object_key=cached_tile.object_key,
                    checksum=cached_tile.checksum,
                    size=cached_tile.size,
                )
            )
            fetcher.sleep_between_requests(total_tiles - index - 1)
            continue

        stored = storage.store_tile(
            slug=slug,
            capture_time=capture_at,
            coord=(x, y),
            payload=payload,
            checksum=checksum,
        )
        seen_checksums[checksum] = stored.tile
        changed_tiles.append(stored.tile)
        fetcher.sleep_between_requests(total_tiles - index - 1)

    elapsed = perf_counter() - start
    metadata = {
        "duration_seconds": f"{elapsed:.3f}",
        "total_tiles": str(total_tiles),
        "changed_tiles": str(len(changed_tiles)),
        "deduplicated_tiles": str(len(deduplicated_tiles)),
        "failed_tiles": str(len(failures)),
    }
    if extra_metadata:
        metadata.update(extra_metadata)

    manifest = DeltaManifest(
        slug=slug,
        capture_time=capture_at,
        previous_manifest=pointer.object_key if pointer else None,
        tiles=changed_tiles,
        failed_tiles=failures,
        metadata=metadata,
    )

    manifest_pointer = storage.write_manifest(manifest)
    storage.update_latest_manifest(slug, manifest_pointer)

    LOGGER.info(
        "Capture for %s finished in %.2fs (%d changed, %d deduplicated, %d failed)",
        slug,
        elapsed,
        len(changed_tiles),
        len(deduplicated_tiles),
        len(failures),
    )

    return CaptureOutcome(
        manifest=manifest_pointer,
        changed_tiles=changed_tiles,
        deduplicated_tiles=deduplicated_tiles,
        failures=failures,
        duration_seconds=elapsed,
    )


__all__ = ["CaptureOutcome", "run_capture"]
