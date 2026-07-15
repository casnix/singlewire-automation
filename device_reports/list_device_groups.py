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
import json
import os
import re
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
    """Fetch all device groups, following pagination until exhausted.

    This endpoint uses cursor-token pagination: the "start" query param
    takes the literal "next" (or "previous") token string returned by the
    prior response. It is NOT an item offset or a page index -- passing
    anything else silently returns page 1 every time.
    """
    url = f"{BASE_URL}/device-groups"
    headers = {"Authorization": f"Bearer {token}"}
    if domain_id:
        headers["x-singlewire-domain"] = domain_id

    all_groups = []
    limit = 100
    start_token = None
    total = None
    page_num = 0

    while True:
        params = {"limit": limit}
        if start_token is not None:
            params["start"] = start_token

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
        next_token = payload.get("next")

        if DEBUG:
            print(
                f"[DEBUG] get_device_groups page {page_num}: "
                f"{len(page)} records, total={total}, next={next_token!r}, "
                f"first id={page[0].get('id') if page else None}",
                file=sys.stderr,
            )

        if not page:
            break

        all_groups.extend(page)

        if total is not None and len(all_groups) >= total:
            break

        if not next_token:
            break

        start_token = next_token
        page_num += 1

        # Small pause between pages to stay under rate limits.
        time.sleep(0.5)

    return all_groups


def _describe_filter(f: dict) -> str:
    """Render a single filter rule as a readable string, e.g. Name CONTAINS 'Lobby'."""
    attr = f.get("attribute")
    comparison = f.get("comparison")
    value = f.get("value")
    text = f"{attr} {comparison} '{value}'"
    if f.get("complement"):
        text = f"NOT ({text})"
    if f.get("caseSensitive"):
        text += " [case-sensitive]"
    return text


def _build_filter_logic_string(group: dict) -> str | None:
    """Return a readable filter_logic string if this group has filter rules, else None."""
    filters = group.get("filters")
    if not filters:
        return None

    descriptions = [_describe_filter(f) for f in filters]
    filter_type = group.get("filterType")

    if filter_type == "LOGICAL_EXPRESSION":
        expr = group.get("logicalExpression") or ""

        def _sub(match: re.Match) -> str:
            idx = int(match.group(0))
            if 0 <= idx < len(descriptions):
                return f"({descriptions[idx]})"
            return match.group(0)

        return re.sub(r"\d+", _sub, expr)

    if filter_type in ("AND", "OR"):
        return f" {filter_type} ".join(descriptions)

    # ACCEPT / REJECT or any other/unknown type: just list the rules with the type as a label
    return f"{filter_type}: " + "; ".join(descriptions)


def _summarize_device(device: dict) -> dict:
    """Pull out the fields most useful for identifying a device in output."""
    attributes = device.get("attributes") or {}
    return {
        "id": device.get("id"),
        "name": attributes.get("Name") or device.get("description"),
        "type": device.get("type"),
        "deviceIdentifier": device.get("deviceIdentifier"),
    }


def build_dive_output(groups: list[dict]) -> dict:
    """Reshape raw device group records into the requested {name: {...}} schema.

    NOTE: member_devices reflects only each group's explicit "additions" --
    devices added by ID. The InformaCast API does not expose the resolved
    set of devices that a filter-based (dynamic) group currently matches,
    only the filter rules themselves and, optionally, match *counts*
    (numPhones/numIdns/numSpeakers/numPlugins via includeDeviceCounts).
    So for purely filter-driven groups, member_devices may be empty even
    though the group matches devices at notification time.
    """
    result = {}
    for group in groups:
        name = group.get("name") or group.get("id")

        entry = {
            "member_devices": [
                _summarize_device(d) for d in group.get("additions", [])
            ],
        }

        filter_logic = _build_filter_logic_string(group)
        if filter_logic is not None:
            entry["filter_logic"] = filter_logic

        result[name] = entry

    return result


def main():
    global DEBUG

    parser = argparse.ArgumentParser(description="List InformaCast Fusion device group names.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Log each API call (function, URI, status code) to stderr.",
    )
    parser.add_argument(
        "--dive",
        action="store_true",
        help=(
            "Output a JSON document with each device group's member_devices "
            "and, if defined with a filter, its filter_logic."
        ),
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
        if args.dive:
            print(json.dumps({}, indent=2))
        else:
            print("No device groups found.")
        return

    if args.dive:
        print(json.dumps(build_dive_output(groups), indent=2))
        return

    for group in groups:
        print(group.get("name", "<unnamed>"))


if __name__ == "__main__":
    main()