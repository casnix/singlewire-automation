#!/usr/bin/env python3
"""Generate a full configuration report for an InformaCast Fusion instance.

Examples:
    python main.py --format html --output report.html
    python main.py --format docx --output report.docx
    python main.py --format pdf  --output report.pdf
    python main.py --format html --groups access,messaging --verbose
    python main.py --format html --debug

    # Test a single resource in isolation (no report generated) — use this
    # to check whether pagination is actually grabbing everything:
    python main.py --test users
    python main.py --test users,message_templates --debug
    python main.py --list-resources
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

from informacast_report.api_client import ApiError, FusionApiClient
from informacast_report.config import ConfigError, Settings
from informacast_report.crawler import Crawler
from informacast_report.diagnostics import test_resource
from informacast_report.logging_utils import setup_logging
from informacast_report.resources import GROUPS, RESOURCES, resources_for_groups


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--format", choices=["html", "docx", "pdf"], default="html",
        help="Output report format (default: html)",
    )
    parser.add_argument(
        "--output", default=None,
        help="Output file path (default: report.<format>)",
    )
    parser.add_argument(
        "--groups", default=None,
        help=f"Comma-separated subset of resource groups to crawl. "
             f"Available: {', '.join(GROUPS.keys())}. Default: all.",
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
        "--domain-id", default=None,
        help="Only used with --test: restrict the test to one specific Domain ID "
             "instead of testing every domain the token can act in.",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Print progress as it works: one line per resource fetched "
             "(item count, time taken), per-domain start/end, and pagination "
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
                    print(f"  {spec.key:28s} {spec.path}")
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
            ok = test_resource(client, key, domain_id_override=args.domain_id)
            all_ok = all_ok and ok
        if all_ok:
            print("All tested resources look consistent.")
        else:
            print("One or more resources reported an error or mismatch — see above.")
        return 0 if all_ok else 1

    selected_groups = set(args.groups.split(",")) if args.groups else None
    specs = resources_for_groups(selected_groups)

    crawler = Crawler(client, specs=specs)

    log.info("Starting crawl of %s ...", settings.base_url)
    try:
        report = crawler.run()
    except ApiError as exc:
        log.error("Fatal API error: %s", exc)
        return 1

    output_path = args.output or f"report.{args.format}"

    log.progress("Rendering %s report...", args.format)
    render_start = time.monotonic()

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

    log.progress("Render finished in %.2fs", time.monotonic() - render_start)
    log.info("Report written to %s", output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
