"""Command-line interface for the serverless capture toolkit."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from dateutil import parser as date_parser

from wplace_timelapse_serverless.capture import run_capture
from wplace_timelapse_serverless.config import ProjectConfig, TimelapseConfig, load_config
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend
from wplace_timelapse_serverless.tile_fetcher import TileFetcher

app = typer.Typer(add_completion=False, help="Serverless capture helpers for WPlace timelapse.")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")


def _load_config(config_path: Optional[Path]) -> ProjectConfig:
    return load_config(config_path)


def _resolve_timelapse(config: ProjectConfig, slug: str) -> TimelapseConfig:
    try:
        return config.require_slug(slug)
    except KeyError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _parse_capture_time(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    dt = date_parser.isoparse(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@app.command()
def capture(
    *,
    slug: str = typer.Option(..., help="Timelapse slug to capture."),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config.json (defaults to WPLACE_CONFIG_PATH or ./config.json).",
    ),
    bucket: str = typer.Option(
        ...,
        "--bucket",
        envvar="WPLACE_BUCKET_NAME",
        help="S3 bucket for storing tiles and manifests.",
    ),
    region: Optional[str] = typer.Option(
        None,
        "--region",
        envvar="WPLACE_REGION",
        help="S3 region (if omitted, boto3 falls back to defaults).",
    ),
    tile_prefix: str = typer.Option(
        "tiles",
        "--tile-prefix",
        envvar="WPLACE_TILE_PREFIX",
        help="Prefix for tile objects within the bucket.",
    ),
    manifest_prefix: str = typer.Option(
        "manifests",
        "--manifest-prefix",
        envvar="WPLACE_MANIFEST_PREFIX",
        help="Prefix for manifest objects within the bucket.",
    ),
    endpoint_url: Optional[str] = typer.Option(
        None,
        "--endpoint-url",
        envvar="WPLACE_ENDPOINT_URL",
        help="Custom endpoint URL for S3-compatible providers (e.g., R2).",
    ),
    capture_time: Optional[str] = typer.Option(
        None,
        "--capture-time",
        help="Override capture timestamp (ISO8601). Defaults to now in UTC.",
    ),
) -> None:
    """Capture a single timelapse region and upload the delta manifest."""
    project_config = _load_config(config_path)
    timelapse = _resolve_timelapse(project_config, slug)
    target_time = _parse_capture_time(capture_time)

    fetcher = TileFetcher(
        base_url=project_config.global_settings.base_url,
        request_delay=project_config.global_settings.request_delay,
    )

    storage = S3StorageBackend(
        bucket=bucket,
        region=region,
        tile_prefix=tile_prefix,
        manifest_prefix=manifest_prefix,
        endpoint_url=endpoint_url,
    )

    result = run_capture(
        slug=slug,
        config=timelapse,
        storage=storage,
        fetcher=fetcher,
        capture_time=target_time,
    )

    summary = {
        "slug": slug,
        "manifest_key": result.manifest.object_key,
        "duration_seconds": round(result.duration_seconds, 3),
        "changed_tiles": len(result.changed_tiles),
        "deduplicated_tiles": len(result.deduplicated_tiles),
        "failed_tiles": len(result.failures),
    }
    typer.echo(json.dumps(summary, indent=2))


if __name__ == "__main__":  # pragma: no cover
    app()


def main() -> None:  # pragma: no cover - thin wrapper for console script
    app()
