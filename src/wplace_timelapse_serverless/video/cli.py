"""Typer CLI for timelapse video generation."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from wplace_timelapse_serverless.config import ProjectConfig, load_config
from wplace_timelapse_serverless.manifest import ManifestPointer
from wplace_timelapse_serverless.storage.http import HttpManifestStorage
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend
from wplace_timelapse_serverless.video.generator import TimelapseVideoGenerator, VideoGenerationOptions
from wplace_timelapse_serverless.video.http_generator import HttpTimelapseVideoGenerator

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


@app.command("render-http")
def render_http(
    *,
    slug: str = typer.Option(..., help="Timelapse slug to render."),
    asset_base_url: str = typer.Option(
        ...,
        "--asset-base-url",
        envvar="WPLACE_GALLERY_ASSET_BASE_URL",
        help="Origin that serves manifests and tiles (e.g. your Cloudflare Worker URL).",
    ),
    manifest_prefix: str = typer.Option(
        "manifests",
        "--manifest-prefix",
        help="Prefix used for manifest objects inside the bucket.",
    ),
    latest_suffix: str = typer.Option(
        "latest.json",
        "--latest-suffix",
        help="Filename for the latest manifest pointer.",
    ),
    timeout: int = typer.Option(
        10,
        "--timeout",
        help="HTTP timeout (in seconds) for manifest and tile downloads.",
    ),
    cache_dir: Optional[Path] = typer.Option(
        None,
        "--cache-dir",
        help="Directory for cached manifest/tile downloads.",
        path_type=Path,
    ),
    progress_every: int = typer.Option(
        250,
        "--progress-every",
        help="Print progress after fetching this many manifests (0 to disable).",
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
    yes: bool = typer.Option(
        False,
        "--yes",
        help="Skip the confirmation prompt and render immediately.",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to project config. Defaults to WPLACE_CONFIG_PATH or ./config.json.",
        path_type=Path,
    ),
) -> None:
    """Render frames and encode a timelapse video over HTTP asset URLs."""
    project_config = _load_project_config(config_path)
    timelapse = project_config.require_slug(slug)

    if progress_every < 0:
        progress_every = 0

    storage = HttpManifestStorage(
        asset_base_url=asset_base_url,
        manifest_prefix=manifest_prefix,
        latest_suffix=latest_suffix,
        timeout=timeout,
        cache_dir=cache_dir,
    )

    limit = max_captures if max_captures and max_captures > 0 else None
    typer.echo("Fetching latest manifest pointer...")
    pointer = storage.get_latest_manifest(slug)
    if not pointer:
        typer.echo(f"No manifests found for slug {slug}.")
        raise typer.Exit(code=1)

    typer.echo(f"Latest manifest: {pointer.object_key}")
    typer.echo("Fetching manifest history...")

    manifests = []
    unique_tiles: set[str] = set()
    visited: set[str] = set()
    count = 0

    while pointer and pointer.object_key not in visited:
        visited.add(pointer.object_key)
        manifest = storage.load_manifest(pointer)
        manifests.append((pointer, manifest))
        unique_tiles.update(tile.object_key for tile in manifest.tiles)
        count += 1

        if progress_every > 0 and (count == 1 or count % progress_every == 0):
            typer.echo(f"Fetched {count} manifests...")

        if limit is not None and count >= limit:
            break

        previous_key = manifest.previous_manifest
        if not previous_key:
            break

        pointer = ManifestPointer(object_key=previous_key, capture_time=manifest.capture_time)

    if pointer and pointer.object_key in visited:
        typer.echo("Warning: detected a manifest loop; stopping.")

    if manifests:
        manifests.reverse()

    if not manifests:
        typer.echo(f"No manifests found for slug {slug}.")
        raise typer.Exit(code=1)

    tile_count = len(unique_tiles)
    typer.echo(f"Manifest history loaded: {len(manifests)} manifests.")
    typer.echo(f"Tile files needed to render: {tile_count}")

    if tile_count > 0 and not yes:
        if not typer.confirm(f"Download {tile_count} tiles and render the video?", default=False):
            raise typer.Exit()

    generator = HttpTimelapseVideoGenerator(
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

    typer.echo("Rendering frames (this may take a while)...")
    result = generator.generate_from_manifests(manifests=manifests, options=options)

    typer.echo(f"Rendered {len(result.frames)} frames to {result.frame_dir}")
    if result.video_path and result.video_path.exists():
        typer.echo(f"Video written to {result.video_path}")
    elif not frames_only:
        typer.echo("Video encoding was skipped or failed.")


def main() -> None:  # pragma: no cover - console entrypoint
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
