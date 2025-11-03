"""Capture runner that orchestrates tile downloads and manifest writes."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
from time import perf_counter
from typing import Dict, Iterable, List, Optional, Set, Tuple

from wplace_timelapse_serverless.config import TimelapseConfig
from wplace_timelapse_serverless.manifest import DeltaManifest, ManifestFailure, ManifestPointer, ManifestTile
from wplace_timelapse_serverless.storage.base import StorageBackend, TileCacheSnapshot
from wplace_timelapse_serverless.tile_fetcher import TileFetchError, TileFetcher


LOGGER = logging.getLogger("wplace.capture")
_MANIFEST_HISTORY_LOOKUPS_LIMIT = 512


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
    slug: str,
    pointer: Optional[ManifestPointer],
    expected_coordinates: Iterable[Tuple[int, int]],
    *,
    max_history_lookups: int = _MANIFEST_HISTORY_LOOKUPS_LIMIT,
) -> Dict[Tuple[int, int], ManifestTile]:
    """Recover the latest metadata for each coordinate without re-fetching tile bytes."""
    tile_state: Dict[Tuple[int, int], ManifestTile] = {}
    remaining: Set[Tuple[int, int]] = set(expected_coordinates)

    cache_loader = getattr(storage, "load_tile_cache", None)
    if cache_loader:
        try:
            cached_tiles = cache_loader(slug)
        except Exception as exc:  # pragma: no cover - defensive guard around optional backend feature
            LOGGER.warning(
                "Failed to load cached tile state for %s; falling back to manifest history: %s",
                slug,
                exc,
            )
            if LOGGER.isEnabledFor(logging.DEBUG):
                LOGGER.debug("Tile cache load error for %s", slug, exc_info=True)
        else:
            cache_missing: Set[Tuple[int, int]] = set()
            cache_map: Dict[Tuple[int, int], ManifestTile]
            if isinstance(cached_tiles, TileCacheSnapshot):
                cache_map = cached_tiles.tiles
                cache_missing = set(cached_tiles.missing)
            elif isinstance(cached_tiles, tuple) and len(cached_tiles) == 2:
                cache_map = dict(cached_tiles[0])
                cache_missing = set(cached_tiles[1])
            else:
                cache_map = dict(cached_tiles)

            for coord, tile in cache_map.items():
                if coord in remaining:
                    tile_state[coord] = tile
                    remaining.discard(coord)
            for coord in cache_missing:
                if coord in remaining:
                    remaining.discard(coord)
            if not remaining:
                return tile_state

    if pointer is None:
        return tile_state

    visited_keys: Set[str] = set()
    current: Optional[ManifestPointer] = pointer
    lookups = 0

    while current and current.object_key not in visited_keys and remaining and lookups < max_history_lookups:
        manifest = storage.load_manifest(current)
        visited_keys.add(current.object_key)
        lookups += 1

        for tile in manifest.tiles:
            if tile.coordinate in remaining:
                tile_state[tile.coordinate] = tile
                remaining.discard(tile.coordinate)

        for failure in manifest.failed_tiles:
            coord = failure.coordinate
            if coord in remaining:
                remaining.discard(coord)

        previous_key = manifest.previous_manifest
        if not previous_key:
            break

        current = ManifestPointer(object_key=previous_key, capture_time=manifest.capture_time)

    if remaining:
        LOGGER.warning(
            "Tile history incomplete for %s: %d coordinates unresolved after %d manifest lookups.",
            slug,
            len(remaining),
            lookups,
        )

    return tile_state


def _as_utc_date(value: datetime) -> date:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


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
    capture_date = _as_utc_date(capture_at)

    force_full_snapshot = pointer is None or _as_utc_date(pointer.capture_time) != capture_date

    coordinates = list(config.coordinates.iter_tiles())
    coordinate_set: Set[Tuple[int, int]] = set(coordinates)
    total_tiles = len(coordinates)
    LOGGER.info("Starting capture for %s (%d tiles)", slug, total_tiles)

    previous_tiles: Dict[Tuple[int, int], ManifestTile] = (
        _build_previous_tile_map(storage, slug, pointer, coordinates) if pointer and not force_full_snapshot else {}
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
        if not force_full_snapshot and previous_tile and previous_tile.checksum == checksum:
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

    cache_writer = getattr(storage, "write_tile_cache", None)
    if cache_writer:
        tile_state: Dict[Tuple[int, int], ManifestTile] = {
            coord: tile for coord, tile in previous_tiles.items() if coord in coordinate_set
        }
        for tile in deduplicated_tiles:
            tile_state[tile.coordinate] = tile
        for tile in changed_tiles:
            tile_state[tile.coordinate] = tile
        try:
            cache_writer(
                slug=slug,
                capture_time=capture_at,
                tiles=tile_state,
                expected_coordinates=coordinate_set,
            )
        except Exception as exc:  # pragma: no cover - defensive guard around optional backend feature
            LOGGER.warning(
                "Failed to update cached tile state for %s: %s",
                slug,
                exc,
            )
            if LOGGER.isEnabledFor(logging.DEBUG):
                LOGGER.debug("Tile cache write error for %s", slug, exc_info=True)

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
