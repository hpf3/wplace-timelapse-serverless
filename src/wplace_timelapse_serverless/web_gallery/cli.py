"""Typer CLI for generating the dynamic gallery shell."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from wplace_timelapse_serverless.config import ProjectConfig, load_config
from wplace_timelapse_serverless.web_gallery.builder import GalleryBuildOptions, build_gallery

app = typer.Typer(add_completion=False, help="Generate dynamic galleries that stream data via the worker.")


def _load_project_config(config_path: Optional[Path]) -> ProjectConfig:
    return load_config(config_path)


@app.command()
def build(
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
    max_captures: Optional[int] = typer.Option(
        50,
        "--max-captures",
        help="Limit the number of captures fetched at runtime (use <=0 for unlimited).",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Destination HTML file (defaults to <slug>-gallery.html).",
        path_type=Path,
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Project config path. Defaults to WPLACE_CONFIG_PATH or ./config.json.",
    ),
) -> None:
    """Generate the dynamic gallery shell for a slug."""
    project_config = _load_project_config(config_path)
    timelapse = project_config.require_slug(slug)

    output_path = output or Path(f"{slug}-gallery.html")
    limit = None if max_captures is not None and max_captures <= 0 else max_captures

    artifact = build_gallery(
        timelapse=timelapse,
        global_settings=project_config.global_settings,
        options=GalleryBuildOptions(
            slug=slug,
            output_path=output_path,
            asset_base_url=asset_base_url,
            manifest_prefix=manifest_prefix,
            max_manifests=limit,
        ),
    )
    typer.echo(f"Gallery written to {artifact}")


def main() -> None:  # pragma: no cover - console entrypoint
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
