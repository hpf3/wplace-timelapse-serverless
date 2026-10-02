"""HTTP-backed storage helpers for reading manifests and tiles via an asset base URL."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from requests import Response

from wplace_timelapse_serverless.manifest import DeltaManifest, ManifestPointer, parse_timestamp
from wplace_timelapse_serverless.storage.base import AbstractStorageBackend, Coordinate, StoredTile


class HttpAssetError(RuntimeError):
    """Raised when an HTTP asset cannot be fetched."""

    def __init__(self, message: str, *, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class HttpAssetClient:
    """Fetch JSON and binary assets from a base URL."""

    asset_base_url: str
    timeout: int = 10
    cache_dir: Optional[Path] = None

    def fetch_json(self, path: str, *, cache_bust: bool = False) -> dict[str, Any]:
        if cache_bust or not self.cache_dir:
            response = self._request(path, cache_bust=cache_bust)
            return self._parse_json_response(response)

        url = self._join_url(path, cache_bust=False)
        cached = self._read_cache(url, kind="json", extension=".json")
        if cached is not None:
            try:
                return json.loads(cached)
            except json.JSONDecodeError:
                self._remove_cache(url, kind="json", extension=".json")

        response = self._request(path, cache_bust=False)
        payload = response.content
        data = self._parse_json_payload(response.url, payload)
        self._write_cache(url, payload, kind="json", extension=".json")
        return data

    def fetch_bytes(self, path: str, *, cache_bust: bool = False) -> bytes:
        if cache_bust or not self.cache_dir:
            response = self._request(path, cache_bust=cache_bust)
            return response.content

        url = self._join_url(path, cache_bust=False)
        extension = self._infer_extension(url, default=".bin")
        cached = self._read_cache(url, kind="bytes", extension=extension)
        if cached is not None:
            return cached

        response = self._request(path, cache_bust=False)
        payload = response.content
        self._write_cache(url, payload, kind="bytes", extension=extension)
        return payload

    def _request(self, path: str, *, cache_bust: bool = False) -> Response:
        url = self._join_url(path, cache_bust=cache_bust)
        try:
            response = requests.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise HttpAssetError(f"Failed to fetch {url}: {exc}") from exc
        if response.status_code != 200:
            raise HttpAssetError(
                f"{url} returned HTTP {response.status_code}",
                status_code=response.status_code,
            )
        return response

    def _join_url(self, path: str, *, cache_bust: bool = False) -> str:
        if path.startswith(("http://", "https://")):
            url = path
        else:
            base = self.asset_base_url.rstrip("/")
            clean_path = path.lstrip("/")
            if not clean_path:
                url = base
            else:
                url = f"{base}/{clean_path}"
        if cache_bust:
            return self._append_cache_buster(url)
        return url

    def _append_cache_buster(self, url: str) -> str:
        parts = urlsplit(url)
        query_items = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "cb"]
        query_items.append(("cb", str(time.time_ns())))
        query = urlencode(query_items)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))

    def _infer_extension(self, url: str, *, default: str) -> str:
        suffix = Path(urlsplit(url).path).suffix
        if suffix and suffix.isascii():
            return suffix
        return default

    def _cache_path(self, url: str, *, kind: str, extension: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.cache_dir / kind / digest[:2] / digest[2:4] / f"{digest}{extension}"

    def _read_cache(self, url: str, *, kind: str, extension: str) -> Optional[bytes]:
        if not self.cache_dir:
            return None
        path = self._cache_path(url, kind=kind, extension=extension)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            return None
        except OSError:
            return None

    def _write_cache(self, url: str, payload: bytes, *, kind: str, extension: str) -> None:
        if not self.cache_dir:
            return
        path = self._cache_path(url, kind=kind, extension=extension)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        except OSError:
            return

    def _remove_cache(self, url: str, *, kind: str, extension: str) -> None:
        if not self.cache_dir:
            return
        path = self._cache_path(url, kind=kind, extension=extension)
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return

    def _parse_json_response(self, response: Response) -> dict[str, Any]:
        payload = response.content
        return self._parse_json_payload(response.url, payload)

    def _parse_json_payload(self, url: str, payload: bytes) -> dict[str, Any]:
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise HttpAssetError(f"{url} returned invalid JSON") from exc


class HttpManifestStorage(AbstractStorageBackend):
    """Read-only manifest storage backed by HTTP asset URLs."""

    def __init__(
        self,
        *,
        asset_base_url: str,
        manifest_prefix: str = "manifests",
        latest_suffix: str = "latest.json",
        timeout: int = 10,
        cache_dir: Optional[Path] = None,
    ) -> None:
        self.client = HttpAssetClient(
            asset_base_url=asset_base_url,
            timeout=timeout,
            cache_dir=cache_dir,
        )
        self.manifest_prefix = manifest_prefix.rstrip("/")
        self.latest_suffix = latest_suffix

    def get_latest_manifest(self, slug: str) -> Optional[ManifestPointer]:
        key = self._latest_key(slug)
        try:
            data = self.client.fetch_json(key, cache_bust=True)
        except HttpAssetError as exc:
            if exc.status_code == 404:
                return None
            raise

        manifest_key = data.get("manifest_key")
        capture_time = data.get("capture_time")
        if not manifest_key or not capture_time:
            raise HttpAssetError(f"Latest manifest pointer {key} is missing required fields")

        return ManifestPointer(
            object_key=str(manifest_key),
            capture_time=parse_timestamp(str(capture_time)),
        )

    def load_manifest(self, pointer: ManifestPointer) -> DeltaManifest:
        try:
            data = self.client.fetch_json(pointer.object_key)
        except HttpAssetError as exc:
            if exc.status_code == 404:
                raise FileNotFoundError(f"Manifest object {pointer.object_key} not found") from exc
            raise
        return DeltaManifest.from_dict(data)

    def store_tile(
        self,
        slug: str,
        capture_time: datetime,
        coord: Coordinate,
        payload: bytes,
        checksum: str,
    ) -> StoredTile:
        raise NotImplementedError("HttpManifestStorage is read-only.")

    def write_manifest(self, manifest: DeltaManifest) -> ManifestPointer:
        raise NotImplementedError("HttpManifestStorage is read-only.")

    def update_latest_manifest(self, slug: str, pointer: ManifestPointer) -> None:
        raise NotImplementedError("HttpManifestStorage is read-only.")

    def _latest_key(self, slug: str) -> str:
        return f"{self.manifest_prefix}/{slug}/{self.latest_suffix}"


__all__ = ["HttpAssetClient", "HttpAssetError", "HttpManifestStorage"]
