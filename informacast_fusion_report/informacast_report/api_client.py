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

# Safety valve for pagination. A well-behaved endpoint will stop advancing
# `next`/`partial` once exhausted; this exists purely to turn a server-side
# bug or a misread response envelope into a loud, early failure instead of
# a script that quietly hangs fetching the same data forever.
MAX_PAGES_PER_RESOURCE = 2000


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
            call_start = time.monotonic()
            logger.debug("GET %s params=%s domain=%s attempt=%d", url, params, domain_id, attempt)
            try:
                resp = self.session.get(
                    url, params=params, headers=headers, timeout=self.timeout
                )
            except requests.RequestException as exc:
                logger.debug("Network error on attempt %d for %s: %s", attempt, url, exc)
                if attempt >= MAX_RETRIES:
                    raise ApiError(f"Network error calling {url}: {exc}", path=path) from exc
                self._sleep_backoff(attempt)
                continue

            elapsed_ms = (time.monotonic() - call_start) * 1000
            logger.debug(
                "  -> %d in %.0fms (%d bytes)",
                resp.status_code, elapsed_ms, len(resp.content or b""),
            )

            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                if attempt >= MAX_RETRIES:
                    raise ApiError(
                        f"Repeated {resp.status_code} from {url}", resp.status_code, path
                    )
                retry_after = resp.headers.get("Retry-After")
                logger.debug(
                    "Retrying after %s status %d (Retry-After=%s)",
                    url, resp.status_code, retry_after,
                )
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
        stats: Optional[dict] = None,
    ) -> Iterator[dict]:
        """Yield every item from a paginated list endpoint.

        Handles the standard {total, partial, previous, next, data} envelope
        used throughout the Fusion API. Falls back gracefully if a response
        is a bare list instead (a few endpoints aren't paginated).

        IMPORTANT: this does NOT stop purely because `partial` is falsy or
        `next` is null. Those fields are the documented signal, but trusting
        them alone is fragile — if an endpoint ever omits them, returns them
        inconsistently, or names them differently than expected, the loop
        would silently stop after page 1 and truncate real data. Instead,
        three independent signals are OR'd together, and paging continues if
        *any* of them suggests there's more:

          1. The envelope's own `partial`/`next` fields say so (as documented).
          2. A full page came back (`len(data) == limit`) — a short/empty
             page is the only truly reliable "this was the last page" signal
             for classic offset pagination.
          3. The envelope's `total` field, if present, says we haven't
             collected that many items yet.

        `stats`, if given a dict, is populated with diagnostic info (pages
        fetched, items yielded, advertised total, envelope shape) — used by
        the --test diagnostic mode to show exactly what happened.
        """
        offset = 0
        page_num = 0
        total_yielded = 0
        advertised_total: Optional[int] = None
        start_time = time.monotonic()

        while True:
            page_num += 1
            if page_num > MAX_PAGES_PER_RESOURCE:
                # Something is wrong: either the server never stops reporting
                # more data, or offset bookkeeping isn't advancing.
                # Fail loudly rather than paging forever.
                raise ApiError(
                    f"Aborting {path}: exceeded {MAX_PAGES_PER_RESOURCE} pages "
                    f"({total_yielded} items so far) without reaching a page that's "
                    "short of the requested limit (and, if the API reports a total, "
                    "without reaching it either). This looks like a pagination loop, "
                    "not real data — run with --test <resource> --debug to inspect "
                    "the raw per-page responses.",
                    path=path,
                )

            params = {"limit": limit, "offset": offset}
            if extra_params:
                params.update(extra_params)

            page_start = time.monotonic()
            payload = self._get(path, params=params, domain_id=domain_id).json()
            page_elapsed = time.monotonic() - page_start

            if isinstance(payload, list):
                # Non-paginated endpoint — yield and stop.
                logger.debug("%s returned a bare list (%d items), not paginated", path, len(payload))
                for item in payload:
                    yield item
                    total_yielded += 1
                if stats is not None:
                    stats.update(pages=1, items=total_yielded, advertised_total=None, envelope="bare-list")
                return

            data = payload.get("data", [])
            partial = payload.get("partial", False)
            next_cursor = payload.get("next")
            total = payload.get("total")
            if total is not None:
                advertised_total = total

            total_yielded += len(data)

            logger.debug(
                "%s page %d: offset=%d got=%d partial=%s next=%s total=%s (%.0fms)",
                path, page_num, offset, len(data), partial, next_cursor, total, page_elapsed * 1000,
            )
            if page_num == 1 or page_num % 5 == 0:
                logger.progress(
                    "  %s: page %d, %d item(s) so far (%.1fs elapsed)",
                    path, page_num, total_yielded, time.monotonic() - start_time,
                )

            for item in data:
                yield item

            if stats is not None:
                stats.update(
                    pages=page_num, items=total_yielded,
                    advertised_total=advertised_total, envelope="paginated",
                )

            if not data:
                logger.debug("%s: empty page, stopping", path)
                return

            flag_says_more = bool(partial) or (next_cursor is not None)
            full_page = len(data) == limit
            total_says_more = advertised_total is not None and total_yielded < advertised_total

            if not (flag_says_more or full_page or total_says_more):
                logger.debug(
                    "%s: complete after %d page(s), %d item(s), %.1fs",
                    path, page_num, total_yielded, time.monotonic() - start_time,
                )
                if advertised_total is not None and total_yielded != advertised_total:
                    logger.warning(
                        "%s: collected %d item(s) but the API reported total=%d — "
                        "these don't match. Could be duplicate/changing data between "
                        "pages, or the total field isn't reliable for this endpoint. "
                        "Run --test on this resource for a closer look.",
                        path, total_yielded, advertised_total,
                    )
                return

            offset += len(data)
