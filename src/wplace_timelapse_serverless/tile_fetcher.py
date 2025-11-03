"""HTTP tile fetching helpers."""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from requests import Response


class TileFetchError(RuntimeError):
    """Raised when a tile cannot be fetched."""


@dataclass(slots=True)
class TileFetcher:
    """Download tiles from the WPlace backend or compatible API."""

    base_url: str
    request_delay: float = 0.5
    timeout: int = 10

    def fetch_tile(self, x: int, y: int) -> bytes:
        url = f"{self.base_url.rstrip('/')}/{x}/{y}.png"
        response = self._request(url)
        return response.content

    def _request(self, url: str) -> Response:
        try:
            response = requests.get(url, timeout=self.timeout)
        except requests.RequestException as exc:  # pragma: no cover - network failure path
            raise TileFetchError(f"Failed to fetch {url}: {exc}") from exc
        if response.status_code != 200:
            raise TileFetchError(f"{url} returned HTTP {response.status_code}")
        return response

    def sleep_between_requests(self, remaining: int) -> None:
        if remaining > 0 and self.request_delay > 0:
            time.sleep(self.request_delay)


__all__ = ["TileFetchError", "TileFetcher"]
