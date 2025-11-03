"""Render timelapse frames from manifests and encode video outputs."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image

from wplace_timelapse_serverless.config import GlobalSettings, TimelapseConfig
from wplace_timelapse_serverless.manifest import ManifestTile
from wplace_timelapse_serverless.manifest_utils import ManifestWithPointer, collect_manifest_chain
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend

LOGGER = logging.getLogger("wplace.video")
Coordinate = Tuple[int, int]


@dataclass(frozen=True, slots=True)
class FrameArtifact:
    """Metadata describing a rendered frame."""

    index: int
    capture_time: datetime
    manifest_key: str
    frame_path: Path
    tile_updates: int


@dataclass(slots=True)
class VideoGenerationOptions:
    """Options for generating a timelapse video."""

    output_dir: Path
    fps: int
    ffmpeg_path: str = "ffmpeg"
    encode_video: bool = True
    keep_frames: bool = True
    frame_prefix: str = "frame"
    start_index: int = 0


@dataclass(frozen=True, slots=True)
class VideoGenerationResult:
    """Result of rendering frames (and video when requested)."""

    slug: str
    frames: Sequence[FrameArtifact]
    frame_dir: Path
    metadata_path: Path
    video_path: Optional[Path]


class TimelapseVideoGenerator:
    """Orchestrates manifest playback into rendered frames and videos."""

    def __init__(
        self,
        *,
        timelapse: TimelapseConfig,
        storage: S3StorageBackend,
        global_settings: Optional[GlobalSettings] = None,
    ) -> None:
        self.timelapse = timelapse
        self.storage = storage
        self.global_settings = global_settings
        self._background_color = tuple(global_settings.background_color) if global_settings else (0, 0, 0)

    def generate(
        self,
        *,
        options: VideoGenerationOptions,
        limit: Optional[int] = None,
    ) -> VideoGenerationResult:
        """Render frames and optionally encode a video."""
        manifests = collect_manifest_chain(
            storage=self.storage,
            slug=self.timelapse.slug,
            limit=limit,
            newest_first=False,
        )
        if not manifests:
            raise RuntimeError(f"No manifests found for slug {self.timelapse.slug}")

        options.output_dir.mkdir(parents=True, exist_ok=True)
        frame_dir = options.output_dir / "frames"
        frame_dir.mkdir(parents=True, exist_ok=True)

        frames = self._render_frames(manifests=manifests, frame_dir=frame_dir, options=options)

        metadata_path = options.output_dir / "frames.json"
        self._write_metadata(frames=frames, path=metadata_path)

        video_path: Optional[Path] = None
        if options.encode_video and frames:
            video_path = self._encode_video(frames_dir=frame_dir, options=options)
            if video_path and not options.keep_frames:
                shutil.rmtree(frame_dir, ignore_errors=True)

        return VideoGenerationResult(
            slug=self.timelapse.slug,
            frames=frames,
            frame_dir=frame_dir,
            metadata_path=metadata_path,
            video_path=video_path,
        )

    def _render_frames(
        self,
        *,
        manifests: Sequence[ManifestWithPointer],
        frame_dir: Path,
        options: VideoGenerationOptions,
    ) -> List[FrameArtifact]:
        xmin = self.timelapse.coordinates.xmin
        ymin = self.timelapse.coordinates.ymin
        width_tiles = self.timelapse.coordinates.width
        height_tiles = self.timelapse.coordinates.height

        tile_cache: Dict[str, Image.Image] = {}
        board_state: Dict[Coordinate, str] = {}
        tile_size: Optional[Tuple[int, int]] = None

        frames: List[FrameArtifact] = []

        for offset, (pointer, manifest) in enumerate(manifests):
            frame_index = options.start_index + offset
            updates = 0

            for tile in manifest.tiles:
                image = self._load_tile(tile=tile, cache=tile_cache)
                tile_size = tile_size or image.size
                board_state[tile.coordinate] = tile.object_key
                updates += 1

            if tile_size is None:
                LOGGER.debug("Skipping frame %s because no tiles have been cached yet.", frame_index)
                continue

            frame = self._compose_frame(
                board_state=board_state,
                tile_cache=tile_cache,
                tile_size=tile_size,
                xmin=xmin,
                ymin=ymin,
                width_tiles=width_tiles,
                height_tiles=height_tiles,
            )

            frame_path = frame_dir / f"{options.frame_prefix}-{frame_index:05d}.png"
            frame.convert("RGB").save(frame_path, format="PNG", optimize=True)

            frames.append(
                FrameArtifact(
                    index=frame_index,
                    capture_time=manifest.capture_time,
                    manifest_key=pointer.object_key,
                    frame_path=frame_path,
                    tile_updates=updates,
                )
            )

            LOGGER.info(
                "Rendered frame %s from manifest %s (%d updated tiles)",
                frame_index,
                pointer.object_key,
                updates,
            )

        return frames

    def _load_tile(self, *, tile: ManifestTile, cache: Dict[str, Image.Image]) -> Image.Image:
        cached = cache.get(tile.object_key)
        if cached is not None:
            return cached

        response = self.storage.client.get_object(Bucket=self.storage.paths.bucket, Key=tile.object_key)
        payload = response["Body"].read()

        image = Image.open(BytesIO(payload)).convert("RGBA")
        cache[tile.object_key] = image
        return image

    def _compose_frame(
        self,
        *,
        board_state: Dict[Coordinate, str],
        tile_cache: Dict[str, Image.Image],
        tile_size: Tuple[int, int],
        xmin: int,
        ymin: int,
        width_tiles: int,
        height_tiles: int,
    ) -> Image.Image:
        tile_width, tile_height = tile_size
        canvas = Image.new(
            mode="RGBA",
            size=(tile_width * width_tiles, tile_height * height_tiles),
            color=(*self._background_color, 255),
        )

        for coord, object_key in board_state.items():
            tile = tile_cache.get(object_key)
            if tile is None:
                continue
            offset_x = (coord[0] - xmin) * tile_width
            offset_y = (coord[1] - ymin) * tile_height
            canvas.paste(tile, (offset_x, offset_y), tile)

        return canvas

    def _write_metadata(self, *, frames: Sequence[FrameArtifact], path: Path) -> None:
        data = [
            {
                "index": frame.index,
                "capture_time": frame.capture_time.isoformat(),
                "manifest_key": frame.manifest_key,
                "frame_path": str(frame.frame_path),
                "tile_updates": frame.tile_updates,
            }
            for frame in frames
        ]
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _encode_video(self, *, frames_dir: Path, options: VideoGenerationOptions) -> Path:
        video_path = options.output_dir / f"{self.timelapse.slug}.mp4"
        frame_pattern = frames_dir / f"{options.frame_prefix}-%05d.png"
        cmd = [
            options.ffmpeg_path,
            "-y",
            "-framerate",
            str(options.fps),
            "-i",
            str(frame_pattern),
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-pix_fmt",
            "yuv420p",
            str(video_path),
        ]
        LOGGER.info("Encoding video via ffmpeg: %s", " ".join(cmd))
        try:
            subprocess.run(cmd, check=True, capture_output=True)
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"ffmpeg failed with exit code {exc.returncode}") from exc
        return video_path


__all__ = [
    "FrameArtifact",
    "TimelapseVideoGenerator",
    "VideoGenerationOptions",
    "VideoGenerationResult",
]
