"""Configuration models shared between capture workers and orchestration."""

from __future__ import annotations

import json
import os
from functools import cached_property
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, ValidationInfo, field_validator


class Coordinates(BaseModel):
    """Inclusive coordinate bounds describing a tile region."""

    xmin: int
    xmax: int
    ymin: int
    ymax: int

    @field_validator("xmax")
    @classmethod
    def _validate_xmax(cls, value: int, info: ValidationInfo) -> int:
        xmin = info.data.get("xmin", value) if info.data else value
        if value < xmin:
            raise ValueError("xmax must be greater than or equal to xmin")
        return value

    @field_validator("ymax")
    @classmethod
    def _validate_ymax(cls, value: int, info: ValidationInfo) -> int:
        ymin = info.data.get("ymin", value) if info.data else value
        if value < ymin:
            raise ValueError("ymax must be greater than or equal to ymin")
        return value

    def iter_tiles(self) -> Iterator[Tuple[int, int]]:
        """Yield inclusive tile coordinates covered by this region."""
        for x in range(self.xmin, self.xmax + 1):
            for y in range(self.ymin, self.ymax + 1):
                yield x, y

    @cached_property
    def width(self) -> int:
        return (self.xmax - self.xmin) + 1

    @cached_property
    def height(self) -> int:
        return (self.ymax - self.ymin) + 1

    def tile_count(self) -> int:
        return self.width * self.height


class TimelapseMode(BaseModel):
    """Configuration for a single timelapse mode."""

    enabled: bool = True
    suffix: str = ""
    create_full_timelapse: bool = False


class TimelapseConfig(BaseModel):
    """Configuration for a single monitored region."""

    slug: str
    name: str
    coordinates: Coordinates
    description: Optional[str] = None
    enabled: bool = True
    timelapse_modes: dict[str, TimelapseMode] = Field(default_factory=dict)

    def enabled_modes(self) -> Iterable[Tuple[str, TimelapseMode]]:
        return ((name, mode) for name, mode in self.timelapse_modes.items() if mode.enabled)


class DiffSettings(BaseModel):
    threshold: int = 10
    visualization: str = "colored"
    fade_frames: int = 3
    enhancement_factor: float = 2.0


class ReportingSettings(BaseModel):
    enable_stats_file: bool = True
    seconds_per_pixel: int = 30
    coverage_gap_multiplier: Optional[float] = None


class GlobalSettings(BaseModel):
    base_url: str
    backup_interval_minutes: int = 5
    request_delay: float = 0.5
    timelapse_fps: int = 10
    timelapse_quality: int = 23
    background_color: Tuple[int, int, int] = (0, 0, 0)
    auto_crop_transparent_frames: bool = True
    diff_settings: DiffSettings = Field(default_factory=DiffSettings)
    reporting: ReportingSettings = Field(default_factory=ReportingSettings)


class ProjectConfig(BaseModel):
    timelapses: Tuple[TimelapseConfig, ...]
    global_settings: GlobalSettings

    def enabled_timelapses(self) -> Iterable[TimelapseConfig]:
        return (config for config in self.timelapses if config.enabled)

    def require_slug(self, slug: str) -> TimelapseConfig:
        for config in self.enabled_timelapses():
            if config.slug == slug:
                return config
        raise KeyError(f"Slug '{slug}' is not enabled or does not exist in config")


def load_config(path: Path | None = None) -> ProjectConfig:
    """Load a project configuration from JSON."""
    config_path = path or resolve_config_path()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return ProjectConfig.model_validate(raw)


def resolve_config_path() -> Path:
    """Resolve the config path from environment variables or defaults."""
    env_value = os.environ.get("WPLACE_CONFIG_PATH")
    if env_value:
        path = Path(env_value).expanduser()
        if not path.exists():
            raise FileNotFoundError(f"Config path {path} from WPLACE_CONFIG_PATH does not exist.")
        return path
    default = Path("config.json")
    if default.exists():
        return default
    raise FileNotFoundError(
        "Configuration path not provided. Set WPLACE_CONFIG_PATH or create config.json.",
    )


__all__ = [
    "Coordinates",
    "DiffSettings",
    "GlobalSettings",
    "ProjectConfig",
    "ReportingSettings",
    "TimelapseConfig",
    "TimelapseMode",
    "load_config",
    "resolve_config_path",
]
