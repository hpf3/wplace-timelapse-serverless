"""Serverless capture toolkit for the WPlace timelapse system."""

from importlib.metadata import PackageNotFoundError, version


def __getattr__(name: str) -> str:
    if name == "__version__":
        try:
            return version("wplace-timelapse-serverless")
        except PackageNotFoundError:  # pragma: no cover - during editable installs
            return "0.0.0"
    raise AttributeError(name)


__all__ = ["__version__"]
