#!/usr/bin/env python3
"""
List Device Group names from Singlewire InformaCast Fusion.

Auth: Requires a permanent API token (create one in InformaCast:
Users > Manage Users > <user> > User Tokens > Add).

Usage:
    export INFORMACAST_TOKEN="your-token-here"
    python3 list_device_groups.py

    # Optional: if your account uses Domains and you want a specific
    # acting domain, set INFORMACAST_DOMAIN to that domain's id.
"""

import argparse
import os
import sys
import time

import requests
from requests.exceptions import RequestException

BASE_URL = "https://api.icmobile.singlewire.com/api/v1"

MAX_RETRIES = 6
BASE_BACKOFF_SECONDS = 2  # doubles each retry: 2, 4, 8, 16, 32, 64

DEBUG = False


def _log(func_name: str, url: str, params: dict, status) -> None:
    if not DEBUG:
        return
    # Build the URI with query string, no headers.
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        uri = f"{url}?{query}"
    else:
        uri = url
    status_str = "OK" if status == 200 else str(status)
    print(f"[DEBUG] {func_name} {uri} -> {status_str}", file=sys.stderr)


def _request_with_retry(func_name: str, url: str, headers: dict, params: dict) -> requests.Response:
    """GET with retry/backoff on 429 (and transient 5xx)."""
    for attempt in range(MAX_RETRIES + 1):
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        _log(func_name, url, params, resp.status_code)

        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == MAX_RETRIES:
                resp.raise_for_status()

            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = float(retry_after)
                except ValueError:
                    wait = BASE_BACKOFF_SECONDS * (2 ** attempt)
            else:
                wait = BASE_BACKOFF_SECONDS * (2 ** attempt)

            print(
                f"Got {resp.status_code}, retrying in {wait:.1f}s "
                f"(attempt {attempt + 1}/{MAX_RETRIES})...",
                file=sys.stderr,
            )
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp

    resp.raise_for_status()
    return resp


def get_device_groups(token: str, domain_id: str | None = None) -> list[dict]:
    """Fetch all device groups, following pagination until exhausted."""
    url = f"{BASE_URL}/device-groups"
    headers = {"Authorization": f"Bearer {token}"}
    if domain_id:
        headers["x-singlewire-domain"] = domain_id

    seen_ids = set()
    all_groups = []
    limit = 100
    page_num = 0  # NOTE: this endpoint appears to treat "offset" as a page
    # index (0, 1, 2, ...) rather than a record count to skip -- offset=100
    # returned an identical page to offset=0 with limit=100, which rules out
    # item-count semantics.
    total = None

    while True:
        params = {"limit": limit, "offset": page_num, "include-total": "true"}
        try:
            resp = _request_with_retry("get_device_groups", url, headers, params)
        except RequestException as e:
            print(f"Error fetching device groups: {e}", file=sys.stderr)
            if getattr(e, "response", None) is not None:
                print(e.response.text, file=sys.stderr)
            sys.exit(1)

        payload = resp.json()
        page = payload.get("data", [])
        total = payload.get("total", total)

        if DEBUG and page:
            print(
                f"[DEBUG] get_device_groups page {page_num}: "
                f"{len(page)} records, first id={page[0].get('id')}",
                file=sys.stderr,
            )

        if not page:
            break

        new_records = [g for g in page if g.get("id") not in seen_ids]
        if not new_records:
            if DEBUG:
                print(
                    f"[DEBUG] get_device_groups page {page_num} contained "
                    "no new records; stopping.",
                    file=sys.stderr,
                )
            break

        for g in new_records:
            seen_ids.add(g.get("id"))
        all_groups.extend(new_records)

        if total is not None and len(all_groups) >= total:
            break

        if len(page) < limit:
            break

        page_num += 1

        # Small pause between pages to stay under rate limits.
        time.sleep(0.5)

    return all_groups


def main():
    global DEBUG

    parser = argparse.ArgumentParser(description="List InformaCast Fusion device group names.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log each API call (function, URI, status code) to stderr.",
    )
    args = parser.parse_args()
    DEBUG = args.debug

    token = os.environ.get("INFORMACAST_TOKEN")
    if not token:
        print("Set INFORMACAST_TOKEN in your environment first.", file=sys.stderr)
        sys.exit(1)

    domain_id = os.environ.get("INFORMACAST_DOMAIN")

    groups = get_device_groups(token, domain_id)

    if not groups:
        print("No device groups found.")
        return

    for group in groups:
        print(group.get("name", "<unnamed>"))


if __name__ == "__main__":
    main()