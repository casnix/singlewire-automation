"""Standalone diagnostic mode: fetch one specific resource (e.g. `users`) and
report exactly what happened, without running the rest of the crawl or
rendering a report. This is what `--test` on the CLI drives.

The point is to answer, quickly and concretely: "did we actually get
everything?" — advertised total vs. items collected, how many pages it took,
and how long it took — rather than having to read through a full report or a
wall of --debug output from an entire crawl.
"""
from __future__ import annotations

import time
from typing import Optional

from .api_client import ApiError, FusionApiClient
from .crawler import list_domains
from .resources import ResourceSpec, get_resource


def test_resource(client: FusionApiClient, key: str, domain_id_override: Optional[str] = None) -> bool:
    """Run a diagnostic fetch of one resource. Prints a human-readable
    breakdown and returns True if everything checked out, False if a
    mismatch or error was detected (used as the process exit code).
    """
    try:
        spec = get_resource(key)
    except KeyError as exc:
        print(f"✗ {exc}")
        return False

    print(f"\n{'=' * 70}")
    print(f"Testing resource: {spec.key}  (label: {spec.label!r}, group: {spec.group})")
    print(f"Path: {spec.path}   domain_scoped: {spec.domain_scoped}")
    if spec.notes:
        print(f"Note: {spec.notes}")
    print("=" * 70)

    ok = True

    if domain_id_override:
        domain_targets = [{"id": domain_id_override, "name": "(specified)"}]
    elif spec.domain_scoped:
        domain_targets = list_domains(client)
        if not domain_targets:
            domain_targets = [None]
    else:
        domain_targets = [None]

    for domain in domain_targets:
        domain_id = domain["id"] if domain else None
        domain_label = domain["name"] if domain else "(no domain / instance-level)"
        print(f"\n-- Domain: {domain_label} --")

        stats: dict = {}
        start = time.monotonic()
        try:
            items = list(client.paged_get(spec.path, domain_id=domain_id, stats=stats))
        except ApiError as exc:
            print(f"  ✗ ERROR: {exc}")
            ok = False
            continue
        elapsed = time.monotonic() - start

        pages = stats.get("pages", 0)
        advertised = stats.get("advertised_total")
        envelope = stats.get("envelope", "unknown")
        truncated = stats.get("truncated", False)
        duplicates = stats.get("duplicates", 0)
        raw_items = stats.get("raw_items")

        print(f"  Envelope shape:       {envelope}")
        print(f"  Pages fetched:        {pages}")
        print(f"  Unique items:         {len(items)}")
        if raw_items is not None and raw_items != len(items):
            print(f"  Raw items received:   {raw_items} ({duplicates} duplicate(s) filtered out)")
        print(
            "  API-advertised total: "
            + (str(advertised) if advertised is not None else "n/a (endpoint has no total field)")
        )
        print(f"  Time taken:           {elapsed:.2f}s")

        if duplicates:
            print(
                f"  ⚠ NOTE: {duplicates} duplicate item(s) were seen across pages and "
                "filtered out. This endpoint may not be honoring the offset parameter "
                "reliably — re-run with --debug to see exactly which pages overlapped. "
                "If a page ever returns the *exact same* items as the one before it, "
                "this will raise an error instead (pagination is stuck, not just "
                "overlapping)."
            )

        if truncated:
            print(
                "  ⚠ NOTE: a page had to be truncated to match the advertised total — "
                "this endpoint's partial/next flags claimed more data was available "
                "past its own declared total. Re-run with --debug to see exactly which "
                "page and how much was cut. Data collected is still correct (capped at "
                "the real total), just flagging that this endpoint's flags aren't reliable."
            )

        if advertised is not None and len(items) != advertised:
            print(
                f"  ⚠ MISMATCH: collected {len(items)} item(s) but the API reported "
                f"total={advertised}. Pagination likely stopped early or duplicated data — "
                f"rerun with --debug for a full per-page trace of {spec.path}."
            )
            ok = False
        elif advertised is not None:
            print("  ✓ Item count matches the API's advertised total.")
        else:
            print("  (endpoint doesn't report a total, so this is just item/page counts.)")

        if items:
            print(f"  Sample item fields:   {list(items[0].keys())}")
            print(f"  First item:           {_short(items[0])}")
            if len(items) > 1:
                print(f"  Last item:            {_short(items[-1])}")
        else:
            print("  (no items returned — either genuinely empty, or not visible to this token.)")

    print()
    return ok


def _short(item: dict, max_len: int = 160) -> str:
    s = str(item)
    return s if len(s) <= max_len else s[: max_len - 3] + "..."
