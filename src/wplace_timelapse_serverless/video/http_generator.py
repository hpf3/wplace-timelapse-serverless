"""HTTP-backed timelapse generator that fetches tiles via asset URLs."""

from __future__ import annotations

from io import BytesIO
from typing import Dict

from PIL import Image

from wplace_timelapse_serverless.manifest import ManifestTile
from wplace_timelapse_serverless.storage.http import HttpManifestStorage
from wplace_timelapse_serverless.video.generator import TimelapseVideoGenerator


class HttpTimelapseVideoGenerator(TimelapseVideoGenerator):
    """Render timelapse videos using manifests and tiles fetched over HTTP."""

    storage: HttpManifestStorage

    def _load_tile(self, *, tile: ManifestTile, cache: Dict[str, Image.Image]) -> Image.Image:
        cached = cache.get(tile.object_key)
        if cached is not None:
            return cached

        payload = self.storage.client.fetch_bytes(tile.object_key)
        image = Image.open(BytesIO(payload)).convert("RGBA")
        cache[tile.object_key] = image
        return image


__all__ = ["HttpTimelapseVideoGenerator"]
