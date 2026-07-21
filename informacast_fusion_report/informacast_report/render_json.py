"""JSON rendering of a crawled InstanceReport -- for piping into other
tools, jq, diffing between runs, etc., rather than reading a formatted
report. Structure mirrors the HTML/DOCX reports (domains -> resources ->
items) but stays close to the raw crawled data instead of being flattened
for display.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
from typing import Optional

from .crawler import InstanceReport


def build_json_dict(report: InstanceReport) -> dict:
    return {
        "base_url": report.base_url,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "domains": [
            {
                "domain": dr.domain,  # None if the instance doesn't use Domains
                "resources": {
                    key: {
                        "key": result.spec.key,
                        "label": result.spec.label,
                        "path": result.spec.path,
                        "group": result.spec.group,
                        "pagination_style": result.spec.pagination_style,
                        "notes": result.spec.notes,
                        "error": result.error,
                        "pagination_stats": result.pagination_stats,
                        "item_count": len(result.items),
                        "items": result.items,
                    }
                    for key, result in dr.resources.items()
                },
                "sites_tree": dr.sites_tree,
                "alarm_details": dr.alarm_details,
            }
            for dr in report.domains
        ],
    }


def render_json(report: InstanceReport, indent: Optional[int] = 2) -> str:
    """Serialize a report to a JSON string. `indent=None` gives compact
    single-line output, useful if you're piping into something else that
    doesn't care about formatting.
    """
    return json.dumps(build_json_dict(report), indent=indent, default=str)
