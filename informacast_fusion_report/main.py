#!/usr/bin/env python3
"""Generate a full configuration report for an InformaCast Fusion instance.

Examples:
    python main.py --format html --output report.html
    python main.py --format docx --output report.docx
    python main.py --format pdf  --output report.pdf
    python main.py --format html --groups access,messaging --verbose
    python main.py --format html --debug

    # JSON output -- prints to stdout if --output isn't given, so it's
    # pipeable straight into jq etc.:
    python main.py --format json
    python main.py --format json --output report.json

    # --unit restricts to exact resource key(s) (see --list-resources),
    # more precise than --groups. Combine with --format json to pull just
    # one resource's raw data:
    python main.py --format json --unit users
    python main.py --format json --unit users,message_templates --output subset.json

    # Instance-specific operational narrative (Word doc): explains how THIS
    # instance's configured resources actually relate to each other --
    # e.g. which DialCast pattern fires which template to which recipients --
    # rather than a raw data dump. See docs/RESOURCE_MODEL.md for the
    # conceptual version of the same relationships.
    python main.py --format narrative --output ops_narrative.docx

    # Test a single resource in isolation (no report generated) — use this
    # to check whether pagination is actually grabbing everything:
    python main.py --test users
    python main.py --test users,message_templates --debug
    python main.py --list-resources
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import sys
import time

from informacast_report.api_client import ApiError, FusionApiClient
from informacast_report.config import ConfigError, Settings
from informacast_report.crawler import Crawler
from informacast_report.diagnostics import test_resource
from informacast_report.logging_utils import setup_logging
from informacast_report.resources import GROUPS, RESOURCES, resources_for_groups, resources_for_keys


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--format", choices=["html", "docx", "pdf", "json", "narrative"], default="html",
        help="Output report format (default: html). 'json' prints to stdout "
             "if --output isn't given, instead of requiring a file. 'narrative' "
             "produces an instance-specific operational Word document explaining "
             "how the crawled resources relate to each other (see "
             "docs/RESOURCE_MODEL.md for the conceptual version), rather than a "
             "raw data dump.",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: report.<format>; for --format json "
             "with no --output, prints to stdout instead).",
    )
    parser.add_argument(
        "--groups", default=None,
        help=f"Comma-separated subset of resource GROUPS (categories) to crawl. "
             f"Available: {', '.join(GROUPS.keys())}. Default: all. For exact "
             f"resource(s) instead of a whole category, use --unit.",
    )
    parser.add_argument(
        "--unit", default=None, metavar="KEY[,KEY...]",
        help="Comma-separated exact resource key(s) to crawl, using the same keys "
             "shown by --list-resources (e.g. `--unit users` or "
             "`--unit users,message_templates`). More precise than --groups, which "
             "pulls in an entire category — --unit pulls in only what's named. "
             "Takes precedence over --groups if both are given. Most useful paired "
             "with `--format json` to pull just one resource's raw data.",
    )
    parser.add_argument(
        "--test", default=None, metavar="KEY[,KEY...]",
        help="Test one or more specific resources in isolation instead of running "
             "the full crawl/report — e.g. `--test users` or `--test users,scenarios`. "
             "Prints pages fetched, items collected, and the API's advertised total "
             "(and flags a clear MISMATCH if they disagree) so you can verify pagination "
             "is actually grabbing everything for that resource. No report file is "
             "produced in this mode. See --list-resources for valid keys.",
    )
    parser.add_argument(
        "--list-resources", action="store_true",
        help="Print every known resource key (for use with --test or --groups) and exit.",
    )
    parser.add_argument(
        "--facility-id", default=None,
        help="Only used with --test: restrict the test to one specific Facility ID "
             "instead of testing every facility the token can act in. ('Facility' is "
             "this API's real multi-tenancy concept -- see --list-resources' facilities "
             "entry, or the README, for background.)",
    )
    parser.add_argument(
        "--pagination-style", choices=["offset", "cursor"], default=None,
        help="Override the pagination style for this run: 'cursor' (the default for "
             "every resource — echoes back the previous response's `next` value as a "
             "`start` param) or 'offset' (computes offset=N each request instead). "
             "With --test, overrides just the resource(s) being tested. Without --test, "
             "overrides EVERY resource in the crawl, regardless of what's configured in "
             "resources.py — useful to force a whole run back to offset-style if a "
             "future endpoint turns out to need it.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print progress as it works: one line per resource fetched "
             "(item count, time taken), per-facility start/end, and pagination "
             "progress on large lists. Good for watching a long run and "
             "spotting a resource that's unexpectedly slow or huge.",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Everything --verbose shows, plus raw HTTP request/response "
             "details (status codes, timings, byte sizes), retry/backoff "
             "decisions, and full pagination internals (offsets, partial/"
             "next values per page). Use this to track down loops or logic "
             "errors — it's noisy by design.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(verbose=args.verbose, debug=args.debug)
    log = logging.getLogger("informacast_report")

    if args.list_resources:
        for group_key, group_label in GROUPS.items():
            print(f"\n{group_label} ({group_key}):")
            for spec in RESOURCES:
                if spec.group == group_key:
                    style_tag = "singleton" if spec.is_singleton else spec.pagination_style
                    print(f"  {spec.key:28s} {spec.path:35s} [{style_tag}]")
        return 0

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log.error(str(exc))
        return 1

    client = FusionApiClient(settings)

    if args.test:
        keys = [k.strip() for k in args.test.split(",") if k.strip()]
        all_ok = True
        for key in keys:
            ok = test_resource(
                client, key,
                facility_id_override=args.facility_id,
                pagination_style_override=args.pagination_style,
            )
            all_ok = all_ok and ok
        if all_ok:
            print("All tested resources look consistent.")
        else:
            print("One or more resources reported an error or mismatch — see above.")
        return 0 if all_ok else 1

    if args.unit:
        keys = [k.strip() for k in args.unit.split(",") if k.strip()]
        try:
            specs = resources_for_keys(keys)
        except KeyError as exc:
            log.error(str(exc))
            return 1
    else:
        selected_groups = set(args.groups.split(",")) if args.groups else None
        specs = resources_for_groups(selected_groups)

    if args.pagination_style:
        specs = [dataclasses.replace(s, pagination_style=args.pagination_style) for s in specs]
        log.info(
            "Overriding pagination_style=%r for all %d resource(s) in this run",
            args.pagination_style, len(specs),
        )

    crawler = Crawler(client, specs=specs)

    log.info("Starting crawl of %s ...", settings.base_url)
    try:
        report = crawler.run()
    except ApiError as exc:
        log.error("Fatal API error: %s", exc)
        return 1

    log.progress("Rendering %s report...", args.format)
    render_start = time.monotonic()

    if args.format == "json":
        from informacast_report.render_json import render_json
        json_str = render_json(report)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(json_str)
            log.progress("Render finished in %.2fs", time.monotonic() - render_start)
            log.info("Report written to %s", args.output)
        else:
            # No --output given: print to stdout instead of forcing a file,
            # so this is pipeable, e.g.:
            #   python main.py --format json --unit users | jq '.facilities[0].resources.users.items'
            log.progress("Render finished in %.2fs", time.monotonic() - render_start)
            print(json_str)
        return 0

    output_path = args.output or ("narrative.docx" if args.format == "narrative" else f"report.{args.format}")

    if args.format == "html":
        from informacast_report.render_html import render_html
        html = render_html(report)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)
    elif args.format == "docx":
        from informacast_report.render_docx import render_docx
        render_docx(report, output_path)
    elif args.format == "pdf":
        from informacast_report.render_html import render_pdf
        render_pdf(report, output_path)
    elif args.format == "narrative":
        from informacast_report.narrative import build_narrative
        from informacast_report.render_narrative_docx import render_narrative_docx
        narrative = build_narrative(report)
        render_narrative_docx(narrative, output_path)

    log.progress("Render finished in %.2fs", time.monotonic() - render_start)
    log.info("Report written to %s", output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
