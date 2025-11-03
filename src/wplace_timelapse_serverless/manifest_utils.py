"""Helpers for working with manifest chains."""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

from wplace_timelapse_serverless.manifest import DeltaManifest, ManifestPointer
from wplace_timelapse_serverless.storage.base import StorageBackend

ManifestWithPointer = Tuple[ManifestPointer, DeltaManifest]


def collect_manifest_chain(
    *,
    storage: StorageBackend,
    slug: str,
    limit: Optional[int] = None,
    newest_first: bool = True,
) -> List[ManifestWithPointer]:
    """Return a list of manifests, following previous-manifest pointers."""
    pointer = storage.get_latest_manifest(slug)
    results: List[ManifestWithPointer] = []

    while pointer:
        manifest = storage.load_manifest(pointer)
        results.append((pointer, manifest))

        if limit is not None and len(results) >= limit:
            break

        previous_key = manifest.previous_manifest
        if not previous_key:
            break

        pointer = ManifestPointer(object_key=previous_key, capture_time=manifest.capture_time)

    if newest_first:
        return results

    results.reverse()
    return results


__all__: Sequence[str] = ["collect_manifest_chain", "ManifestWithPointer"]
