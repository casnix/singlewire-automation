"""Minimal, read-only client for the InformaCast Fusion REST API.

Deliberately exposes only GET. This tool reads configuration for reporting
purposes and should never be able to modify an instance, even by accident.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator, Optional

import requests

from .config import Settings

logger = logging.getLogger("informacast_report.api")

# Fusion list endpoints commonly cap page size around this; being explicit
# avoids relying on a server-side default that could change.
DEFAULT_PAGE_LIMIT = 100

# Retry behavior for transient failures (rate limiting, brief 5xx blips).
MAX_RETRIES = 5
BACKOFF_BASE_SECONDS = 1.5


class ApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None, path: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.path = path


class FusionApiClient:
    """Read-only wrapper around the Fusion API.

    Usage:
        client = FusionApiClient(settings)
        for domain in client.list_domains():
            for user in client.paged_get("/users", domain_id=domain["id"]):
                ...
    """

    def __init__(self, settings: Settings, session: Optional[requests.Session] = None):
        self.base_url = settings.base_url
        self.timeout = settings.timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {settings.token}",
                "Accept": "application/json",
            }
        )

    # -- low level -----------------------------------------------------

    def _get(
        self,
        path: str,
        params: Optional[dict] = None,
        domain_id: Optional[str] = None,
    ) -> requests.Response:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        headers = {}
        if domain_id:
            headers["x-singlewire-domain"] = domain_id

        attempt = 0
        while True:
            attempt += 1
            logger.debug("GET %s params=%s domain=%s", url, params, domain_id)
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                if attempt >= MAX_RETRIES:
                    raise ApiError(f"Network error calling {url}: {exc}", path=path) from exc
                self._sleep_backoff(attempt)
                continue

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= MAX_RETRIES:
                    raise ApiError(
                        f"Repeated {resp.status_code} from {url}", resp.status_code, path
                    )
                retry_after = resp.headers.get("Retry-After")
                self._sleep_backoff(attempt, retry_after)
                continue

            if resp.status_code == 401:
                raise ApiError(
                    "401 Unauthorized — the bearer token is missing, invalid, or expired. "
                    "Generate a fresh token from Admin > Users > User Tokens.",
                    401,
                    path,
                )
            if resp.status_code == 403:
                # Not fatal for the whole run — the calling code treats this as
                # "this resource isn't visible to this account" and moves on.
                raise ApiError(
                    f"403 Forbidden for {path} — the token's account lacks permission "
                    "to read this resource.",
                    403,
                    path,
                )
            if resp.status_code == 404:
                raise ApiError(f"404 Not Found for {path} — endpoint may not exist on this instance/version.", 404, path)

            if not resp.ok:
                raise ApiError(f"Unexpected {resp.status_code} from {url}: {resp.text[:300]}", resp.status_code, path)

            return resp

    @staticmethod
    def _sleep_backoff(attempt: int, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                delay = BACKOFF_BASE_SECONDS * attempt
        else:
            delay = BACKOFF_BASE_SECONDS * attempt
        logger.debug("Backing off %.1fs (attempt %d)", delay, attempt)
        time.sleep(delay)

    # -- higher level ----------------------------------------------------

    def get_one(self, path: str, domain_id: Optional[str] = None) -> dict:
        """GET a single (non-list) resource."""
        return self._get(path, domain_id=domain_id).json()

    def paged_get(
        self,
        path: str,
        domain_id: Optional[str] = None,
        extra_params: Optional[dict] = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> Iterator[dict]:
        """Yield every item from a paginated list endpoint.

        Handles the standard {total, partial, previous, next, data} envelope
        used throughout the Fusion API. Falls back gracefully if a response
        is a bare list instead (a few endpoints aren't paginated).
        """
        offset = 0
        while True:
            params = {"limit": limit, "offset": offset}
            if extra_params:
                params.update(extra_params)

            payload = self._get(path, params=params, domain_id=domain_id).json()

            if isinstance(payload, list):
                # Non-paginated endpoint — yield and stop.
                for item in payload:
                    yield item
                return

            data = payload.get("data", [])
            for item in data:
                yield item

            if not data:
                return
            if not payload.get("partial", False) and payload.get("next") is None:
                return
            offset += limit
