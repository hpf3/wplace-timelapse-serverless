"""Storage backend protocol for persisting tiles and manifests."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol

from wplace_timelapse_serverless.manifest import Coordinate, DeltaManifest, ManifestPointer, ManifestTile


@dataclass(frozen=True, slots=True)
class StoredTile:
    """Result of uploading tile bytes to object storage."""

    tile: ManifestTile
    existed: bool


class StorageBackend(Protocol):
    """Protocol implemented by storage adapters (S3, local FS, etc.)."""

    def get_latest_manifest(self, slug: str) -> Optional[ManifestPointer]:
        """Return the latest manifest pointer for the slug, if present."""

    def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
        """Fetch and deserialize the manifest pointed to by ``pointer``."""

    def store_tile(
        self,
        slug: str,
        capture_time: datetime,
        coord: Coordinate,
        payload: bytes,
        checksum: str,
    ) -> StoredTile:
        """Persist tile bytes, returning metadata and whether the object already existed."""

    def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
        """Persist the manifest document and return its pointer."""

    def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
        """Update the slug's latest-manifest pointer atomically."""


class AbstractStorageBackend(StorageBackend, metaclass=abc.ABCMeta):
    """Convenience base class for implementing the protocol."""

    @abc.abstractmethod
    def get_latest_manifest(self, slug: str) -> Optional[ManifestPointer]:
        raise NotImplementedError

    @abc.abstractmethod
    def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
        raise NotImplementedError

    @abc.abstractmethod
    def store_tile(
        self,
        slug: str,
        capture_time: datetime,
        coord: Coordinate,
        payload: bytes,
        checksum: str,
    ) -> StoredTile:
        raise NotImplementedError

    @abc.abstractmethod
    def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
        raise NotImplementedError

    @abc.abstractmethod
    def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
        raise NotImplementedError


__all__ = ["AbstractStorageBackend", "StorageBackend", "StoredTile"]
