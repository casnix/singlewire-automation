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
        pagination_style: str = "offset",
    ) -> Iterator[dict]:
        """Yield every item from a paginated list endpoint.

        Handles the standard {total, partial, previous, next, data} envelope
        used throughout the Fusion API. Falls back gracefully if a response
        is a bare list instead (a few endpoints aren't paginated).

        `pagination_style` controls how the *next page* is requested — this
        turned out to genuinely vary by endpoint, not just be a guess:

          - "offset" (default): send `offset=<running count>` each request,
            incrementing by however many items the previous page returned.
            This matches the numeric-looking `next` values documented for
            most cloud/mobile-API resources (users, alarms, etc.).
          - "cursor": don't compute anything — just echo back whatever the
            previous response's `next` field contained, verbatim, as a
            `start` query param. Confirmed necessary for `/device-groups`,
            where passing a computed offset instead is silently ignored by
            the server (you just get page 1 back, forever). Likely needed
            for other endpoints too; if `--test <resource>` shows duplicate
            items or a "stuck" error under "offset" mode, try "cursor" via
            `--test <resource> --pagination-style cursor` before assuming
            the endpoint itself is broken.

        Three things are cross-checked regardless of style, because trusting
        any single one of them has already caused a real production incident:

          1. Total-as-ceiling. If the envelope reports a `total`, it is
             authoritative: once `total` unique items have been collected,
             pagination stops there — even if `partial`/`next` still claim
             there's more. If a page would push the running count past
             `total`, it's truncated to exactly the remaining count and a
             warning is logged (some endpoints report `partial: true` on
             every single page regardless of real dataset size — trusting
             that alone previously caused pagination to keep running for
             dozens of extra pages past the API's own stated total, e.g.
             still fetching at offset=9000 when total=448).

          2. Duplicate/stuck-page detection. Some endpoints silently ignore
             the page-advancement parameter (or don't honor it correctly)
             and just keep re-serving the same page of data — with the
             *same* `partial`/`next`/`total` fields each time, so nothing in
             the envelope itself flags it. This is detected directly by
             tracking every item id seen so far:
               - If a page's ids exactly match the previous page's ids,
                 pagination is clearly stuck (advancing had zero effect) —
                 this raises immediately rather than waiting for the page
                 cap, since continuing would just burn through hundreds of
                 identical requests for no new data.
               - If a page partially overlaps with ids already collected,
                 that's logged as a warning and the duplicate items are
                 filtered out of what gets yielded, so callers never see
                 duplicate records.
             Items without an `id` field can't be deduplicated this way and
             are always treated as new.

          3. No-total fallback. If an endpoint never reports `total` at
             all, stopping falls back to the documented `partial`/`next`
             fields OR a full page having come back (`len(data) == limit`)
             — a short/empty page is the only reliable "last page" signal
             when there's no total to check against.

        `stats`, if given a dict, is populated with diagnostic info (pages
        fetched, unique items yielded, raw items received, duplicates
        filtered, advertised total, envelope shape, whether truncation
        kicked in, and the pagination_style used) — used by --test.
        """
        if pagination_style not in ("offset", "cursor"):
            raise ValueError(f"Unknown pagination_style {pagination_style!r} (expected 'offset' or 'cursor')")

        offset = 0
        cursor_token: Optional[str] = None
        page_num = 0
        unique_yielded = 0
        raw_received = 0
        duplicates_filtered = 0
        advertised_total: Optional[int] = None
        truncated = False
        seen_ids: set = set()
        prev_page_id_set: frozenset = frozenset()
        start_time = time.monotonic()

        while True:
            page_num += 1
            if page_num > MAX_PAGES_PER_RESOURCE:
                # Something is wrong: either the server never stops reporting
                # more data, or the page-advancement parameter isn't having
                # any effect. Fail loudly rather than paging forever.
                raise ApiError(
                    f"Aborting {path}: exceeded {MAX_PAGES_PER_RESOURCE} pages "
                    f"({unique_yielded} unique item(s) so far, "
                    f"advertised total={advertised_total}, "
                    f"pagination_style={pagination_style!r}) without reaching a stopping "
                    "point. This looks like a pagination loop, not real data — run with "
                    "--test <resource> --debug to inspect the raw per-page responses.",
                    path=path,
                )

            params = {"limit": limit}
            if pagination_style == "offset":
                params["offset"] = offset
                page_position_desc = f"offset={offset}"
            else:  # cursor
                if cursor_token is not None:
                    params["start"] = cursor_token
                page_position_desc = f"start={cursor_token!r}"
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
                    unique_yielded += 1
                if stats is not None:
                    stats.update(pages=1, items=unique_yielded, advertised_total=None, envelope="bare-list")
                return

            data = payload.get("data", [])
            partial = payload.get("partial", False)
            next_cursor = payload.get("next")
            total = payload.get("total")
            if total is not None:
                advertised_total = total
            raw_received += len(data)

            # -- duplicate / stuck-page detection ---------------------------
            page_id_set = frozenset(
                item["id"] for item in data if item.get("id") is not None
            )

            if page_id_set and page_id_set == prev_page_id_set:
                raise ApiError(
                    f"{path}: page {page_num} ({page_position_desc}) returned the exact "
                    f"same {len(page_id_set)} item id(s) as the previous page. Pagination "
                    f"is stuck — this endpoint appears to be ignoring the page-advancement "
                    f"parameter ({pagination_style!r} style) and always returning the same "
                    "data. If this is 'offset' style, try `--pagination-style cursor` with "
                    "--test to see if this endpoint actually wants a cursor token instead — "
                    "confirmed necessary for /device-groups, plausibly true here too.",
                    path=path,
                )

            overlap = page_id_set & seen_ids
            if overlap:
                logger.warning(
                    "%s: page %d (%s) returned %d item(s) (of %d) already seen on "
                    "an earlier page. This endpoint may not be honoring '%s'-style "
                    "pagination reliably — duplicates are being filtered from the result, "
                    "but real data may be getting skipped or re-fetched. Try "
                    "`--test <resource> --pagination-style %s` to compare.",
                    path, page_num, page_position_desc, len(overlap), len(page_id_set),
                    pagination_style, "cursor" if pagination_style == "offset" else "offset",
                )

            new_items = []
            local_seen: set = set()
            for item in data:
                item_id = item.get("id")
                if item_id is not None:
                    if item_id in seen_ids or item_id in local_seen:
                        duplicates_filtered += 1
                        continue
                    local_seen.add(item_id)
                new_items.append(item)

            seen_ids |= page_id_set
            prev_page_id_set = page_id_set

            # -- total-as-ceiling: truncate/stop once the advertised total is reached --
            page_was_truncated = False
            if advertised_total is not None:
                remaining = advertised_total - unique_yielded
                if remaining <= 0:
                    logger.debug(
                        "%s: already have advertised_total=%d unique item(s) — "
                        "discarding this page's %d new item(s) and stopping.",
                        path, advertised_total, len(new_items),
                    )
                    new_items = []
                    page_was_truncated = True
                elif len(new_items) > remaining:
                    logger.warning(
                        "%s: page %d returned %d new item(s) at %s, but only %d "
                        "more were needed to reach the API-advertised total=%d. "
                        "Truncating and stopping — this endpoint's partial/next flags "
                        "(partial=%s, next=%s) claimed more data was available past its "
                        "own declared total, so they aren't being trusted here.",
                        path, page_num, len(new_items), page_position_desc, remaining,
                        advertised_total, partial, next_cursor,
                    )
                    new_items = new_items[:remaining]
                    truncated = True
                    page_was_truncated = True

            unique_yielded += len(new_items)

            logger.debug(
                "%s page %d: %s raw=%d new=%d dup_overlap=%d partial=%s next=%s "
                "total=%s (%.0fms)%s",
                path, page_num, page_position_desc, len(data), len(new_items), len(overlap),
                partial, next_cursor, total, page_elapsed * 1000,
                " [truncated to match total]" if page_was_truncated and new_items else "",
            )
            if page_num == 1 or page_num % 5 == 0:
                logger.progress(
                    "  %s: page %d, %d unique item(s) so far (%.1fs elapsed)",
                    path, page_num, unique_yielded, time.monotonic() - start_time,
                )

            for item in new_items:
                yield item

            if stats is not None:
                stats.update(
                    pages=page_num, items=unique_yielded, raw_items=raw_received,
                    duplicates=duplicates_filtered, advertised_total=advertised_total,
                    envelope="paginated", truncated=truncated, pagination_style=pagination_style,
                )

            if not data:
                logger.debug("%s: empty page, stopping", path)
                return

            if advertised_total is not None:
                # Total is authoritative once known: stop exactly when reached,
                # regardless of what partial/next claim.
                if unique_yielded >= advertised_total:
                    logger.debug(
                        "%s: reached advertised total (%d unique items) after %d page(s), %.1fs",
                        path, unique_yielded, page_num, time.monotonic() - start_time,
                    )
                    return
            else:
                # No total available on this endpoint at all — fall back to
                # the documented flags plus the full-page heuristic, since
                # that's all we have to go on.
                flag_says_more = bool(partial) or (next_cursor is not None)
                full_page = len(data) == limit
                if not (flag_says_more or full_page):
                    logger.debug(
                        "%s: complete after %d page(s), %d unique item(s), %.1fs "
                        "(no total field on this endpoint)",
                        path, page_num, unique_yielded, time.monotonic() - start_time,
                    )
                    return

            if pagination_style == "offset":
                offset += len(data)
            else:  # cursor
                cursor_token = next_cursor
