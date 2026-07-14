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

import os
import sys

import requests
from requests.exceptions import RequestException

BASE_URL = "https://api.icmobile.singlewire.com/api/v1"


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
            resp = requests.get(url, headers=headers, params=params, timeout=15)
            resp.raise_for_status()
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

    return all_groups


def main():
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