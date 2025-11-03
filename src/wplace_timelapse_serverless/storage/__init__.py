"""Storage adapter exports."""

from wplace_timelapse_serverless.storage.base import AbstractStorageBackend, StorageBackend, StoredTile
from wplace_timelapse_serverless.storage.s3 import S3StorageBackend

__all__ = [
    "AbstractStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "StoredTile",
]
