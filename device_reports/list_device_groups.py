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

    all_groups = []
    params = {"limit": 100, "offset": 0}

    while True:
        try:
            resp = _request_with_retry("get_device_groups", url, headers, params)
        except RequestException as e:
            print(f"Error fetching device groups: {e}", file=sys.stderr)
            if getattr(e, "response", None) is not None:
                print(e.response.text, file=sys.stderr)
            sys.exit(1)

        payload = resp.json()
        page = payload.get("data", [])
        all_groups.extend(page)

        # The API's pagination wrapper exposes a "next" cursor/offset;
        # stop once there isn't one or the page came back empty.
        next_cursor = payload.get("next")
        if not next_cursor or not page:
            break
        params["offset"] = next_cursor

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