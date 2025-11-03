"""Typer CLI for timelapse video generation."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from wplace_timelapse_serverless.config import ProjectConfig, load_config
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend
from wplace_timelapse_serverless.video.generator import TimelapseVideoGenerator, VideoGenerationOptions

app = typer.Typer(add_completion=False, help="Generate timelapse videos from stored manifests.")


def _load_project_config(config_path: Optional[Path]) -> ProjectConfig:
    return load_config(config_path)


@app.command()
def render(
    *,
    slug: str = typer.Option(..., help="Timelapse slug to render."),
    bucket: str = typer.Option(..., "--bucket", envvar="WPLACE_BUCKET_NAME", help="S3 bucket with manifest and tile data."),
    region: Optional[str] = typer.Option(None, "--region", envvar="WPLACE_REGION", help="S3 region for the bucket."),
    tile_prefix: str = typer.Option("tiles", "--tile-prefix", envvar="WPLACE_TILE_PREFIX", help="Prefix for stored tiles."),
    manifest_prefix: str = typer.Option("manifests", "--manifest-prefix", envvar="WPLACE_MANIFEST_PREFIX", help="Prefix for stored manifests."),
    endpoint_url: Optional[str] = typer.Option(
        None,
        "--endpoint-url",
        envvar="WPLACE_ENDPOINT_URL",
        help="Custom endpoint for S3-compatible providers (R2, MinIO, etc.).",
    ),
    output_dir: Path = typer.Option(
        Path("timelapse-output"),
        "--output-dir",
        "-o",
        help="Directory where frames and video will be written.",
        path_type=Path,
    ),
    fps: Optional[int] = typer.Option(
        None,
        "--fps",
        help="Frames per second for the rendered video. Defaults to config's timelapse_fps.",
    ),
    ffmpeg_path: str = typer.Option("ffmpeg", "--ffmpeg-path", help="Path to the ffmpeg binary."),
    frames_only: bool = typer.Option(
        False,
        "--frames-only",
        help="Render frames but skip the video encoding step.",
    ),
    keep_frames: bool = typer.Option(
        True,
        "--keep-frames/--discard-frames",
        help="Preserve individual frame PNGs after encoding the video.",
    ),
    max_captures: Optional[int] = typer.Option(
        None,
        "--max-captures",
        help="Limit the number of manifests to render. Defaults to all available.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to project config. Defaults to WPLACE_CONFIG_PATH or ./config.json.",
        path_type=Path,
    ),
) -> None:
    """Render frames for a slug and encode a timelapse video."""
    project_config = _load_project_config(config_path)
    timelapse = project_config.require_slug(slug)

    storage = S3StorageBackend(
        bucket=bucket,
        region=region,
        tile_prefix=tile_prefix,
        manifest_prefix=manifest_prefix,
        endpoint_url=endpoint_url,
    )

    generator = TimelapseVideoGenerator(
        timelapse=timelapse,
        storage=storage,
        global_settings=project_config.global_settings,
    )

    render_fps = fps or project_config.global_settings.timelapse_fps
    options = VideoGenerationOptions(
        output_dir=output_dir,
        fps=render_fps,
        ffmpeg_path=ffmpeg_path,
        encode_video=not frames_only,
        keep_frames=keep_frames,
    )

    limit = max_captures if max_captures and max_captures > 0 else None
    result = generator.generate(options=options, limit=limit)

    typer.echo(f"Rendered {len(result.frames)} frames to {result.frame_dir}")
    if result.video_path and result.video_path.exists():
        typer.echo(f"Video written to {result.video_path}")
    elif not frames_only:
        typer.echo("Video encoding was skipped or failed.")


def main() -> None:  # pragma: no cover - console entrypoint
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
