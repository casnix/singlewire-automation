#!/usr/bin/env python3
"""Generate a full configuration report for an InformaCast Fusion instance.

Examples:
    python main.py --format html --output report.html
    python main.py --format docx --output report.docx
    python main.py --format pdf  --output report.pdf
    python main.py --format html --groups access,messaging -v
"""
from __future__ import annotations

import argparse
import logging
import sys

from informacast_report.api_client import ApiError, FusionApiClient
from informacast_report.config import ConfigError, Settings
from informacast_report.crawler import Crawler
from informacast_report.resources import GROUPS, resources_for_groups


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
        "-v", "--verbose", action="store_true",
        help="Verbose logging (prints every API call)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    log = logging.getLogger("informacast_report")

    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        log.error(str(exc))
        return 1

    selected_groups = set(args.groups.split(",")) if args.groups else None
    specs = resources_for_groups(selected_groups)

    client = FusionApiClient(settings)
    crawler = Crawler(client, specs=specs)

    log.info("Starting crawl of %s ...", settings.base_url)
    try:
        report = crawler.run()
    except ApiError as exc:
        log.error("Fatal API error: %s", exc)
        return 1

    output_path = args.output or f"report.{args.format}"

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

    log.info("Report written to %s", output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
