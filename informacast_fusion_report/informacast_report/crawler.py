"""Walks a Fusion instance's Domains and Resources, building a single
in-memory report model that the renderers (HTML/DOCX) consume.

Design notes:
- Resource errors (403 "you can't see this", 404 "doesn't exist on this
  instance/version") are caught per-resource so one missing/forbidden
  endpoint doesn't kill the whole report. They show up in the report as
  a visible "Not available" note instead.
- ID -> name resolution happens in a second pass, after every resource has
  been fetched, so it doesn't matter what order resources were crawled in.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .api_client import ApiError, FusionApiClient
from .resources import ResourceSpec, RESOURCES

logger = logging.getLogger("informacast_report.crawler")

# If a single resource comes back with more items than this, it's still
# rendered in full, but we log a warning — on real instances this usually
# means either a genuinely huge org, or the same page being re-fetched due
# to a bug elsewhere. Worth a second look either way.
SUSPICIOUSLY_LARGE_RESULT = 10_000


@dataclass
class ResourceResult:
    spec: ResourceSpec
    items: list[dict] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class DomainReport:
    domain: Optional[dict]  # None for the "no domains configured" case
    resources: dict[str, ResourceResult] = field(default_factory=dict)
    sites_tree: list[dict] = field(default_factory=list)  # sites -> buildings -> floors -> zones
    alarm_details: list[dict] = field(default_factory=list)  # alarms with nested actions/events


@dataclass
class InstanceReport:
    base_url: str
    domains: list[DomainReport] = field(default_factory=list)
    generated_at: str = ""


class Crawler:
    def __init__(self, client: FusionApiClient, specs: Optional[list[ResourceSpec]] = None):
        self.client = client
        self.specs = specs if specs is not None else RESOURCES

    # -- top level ---------------------------------------------------------

    def run(self) -> InstanceReport:
        run_start = time.monotonic()
        report = InstanceReport(base_url=self.client.base_url)

        domains = self._safe_list("/domains")
        if not domains:
            logger.info("No Domains in use — crawling instance with default context.")
            report.domains.append(self._crawl_domain(None))
        else:
            logger.info("Found %d domain(s): %s", len(domains), ", ".join(d.get("name", d.get("id", "?")) for d in domains))
            for i, d in enumerate(domains, start=1):
                logger.info("Crawling domain %d/%d: %s", i, len(domains), d.get("name", d.get("id")))
                report.domains.append(self._crawl_domain(d))

        logger.info(
            "Crawl complete: %d domain(s) in %.1fs", len(report.domains), time.monotonic() - run_start
        )
        return report

    def _safe_list(self, path: str) -> list[dict]:
        try:
            return list(self.client.paged_get(path))
        except ApiError as exc:
            logger.info("Could not list %s (%s) — treating as unavailable/unused.", path, exc)
            return []

    # -- per domain ----------------------------------------------------------

    def _crawl_domain(self, domain: Optional[dict]) -> DomainReport:
        domain_id = domain["id"] if domain else None
        dr = DomainReport(domain=domain)

        for spec in self.specs:
            if spec.key == "domains":
                # Already handled at the instance level; still show the count.
                continue

            result = ResourceResult(spec=spec)
            fetch_start = time.monotonic()
            try:
                fetch_domain_id = domain_id if spec.domain_scoped else None
                result.items = list(self.client.paged_get(spec.path, domain_id=fetch_domain_id))
                elapsed = time.monotonic() - fetch_start
                logger.progress(
                    "[%s] %s: %d item(s) in %.2fs", spec.group, spec.key, len(result.items), elapsed
                )
                if len(result.items) > SUSPICIOUSLY_LARGE_RESULT:
                    logger.warning(
                        "%s returned %d items (>%d) — double check this isn't a pagination "
                        "loop re-yielding the same data; run with --debug to inspect page-by-page.",
                        spec.key, len(result.items), SUSPICIOUSLY_LARGE_RESULT,
                    )
            except ApiError as exc:
                result.error = str(exc)
                logger.info("Resource %s unavailable: %s", spec.key, exc)
            dr.resources[spec.key] = result

        logger.progress("Fetching site/building/floor/zone tree...")
        dr.sites_tree = self._crawl_sites_tree(dr, domain_id)
        logger.progress("Site tree: %d site(s)", len(dr.sites_tree))

        logger.progress("Fetching alarm actions/events...")
        dr.alarm_details = self._crawl_alarm_details(dr, domain_id)

        logger.progress("Resolving cross-referenced IDs to names...")
        self._resolve_references(dr)
        return dr

    # -- nested/special resources --------------------------------------

    def _crawl_sites_tree(self, dr: DomainReport, domain_id: Optional[str]) -> list[dict]:
        sites_result = dr.resources.get("sites")
        if not sites_result or not sites_result.items:
            return []

        tree = []
        for site in sites_result.items:
            site_node = dict(site)
            buildings = self._safe_domain_list(f"/sites/{site['id']}/buildings", domain_id)
            building_nodes = []
            for building in buildings:
                b_node = dict(building)
                floors = self._safe_domain_list(
                    f"/sites/{site['id']}/buildings/{building['id']}/floors", domain_id
                )
                floor_nodes = []
                for floor in floors:
                    f_node = dict(floor)
                    zones = self._safe_domain_list(
                        f"/sites/{site['id']}/buildings/{building['id']}/floors/{floor['id']}/zones",
                        domain_id,
                    )
                    f_node["zones"] = zones
                    floor_nodes.append(f_node)
                b_node["floors"] = floor_nodes
                building_nodes.append(b_node)
            site_node["buildings"] = building_nodes
            tree.append(site_node)
        return tree

    def _crawl_alarm_details(self, dr: DomainReport, domain_id: Optional[str]) -> list[dict]:
        alarms_result = dr.resources.get("alarms")
        if not alarms_result or not alarms_result.items:
            return []

        details = []
        for alarm in alarms_result.items:
            node = dict(alarm)
            node["actions"] = self._safe_domain_list(f"/alarms/{alarm['id']}/actions", domain_id)
            node["events"] = self._safe_domain_list(f"/alarms/{alarm['id']}/events", domain_id)
            details.append(node)
        return details

    def _safe_domain_list(self, path: str, domain_id: Optional[str]) -> list[dict]:
        try:
            return list(self.client.paged_get(path, domain_id=domain_id))
        except ApiError as exc:
            logger.debug("Could not list %s: %s", path, exc)
            return []

    # -- reference resolution ---------------------------------------------

    def _resolve_references(self, dr: DomainReport) -> None:
        """Populate a `<field>_resolved` companion attribute on every item
        for any ref_field that points at a resource we also crawled, mapping
        id(s) to display name(s) instead of leaving them as opaque UUIDs.
        """
        # Build id -> name lookup across every resource in this domain.
        name_index: dict[str, str] = {}
        for result in dr.resources.values():
            name_field = result.spec.name_field
            for item in result.items:
                item_id = item.get("id")
                if item_id and name_field in item:
                    name_index[item_id] = item[name_field] or item_id

        logger.debug("Built name index with %d entries for reference resolution", len(name_index))

        for result in dr.resources.values():
            if not result.spec.ref_fields:
                continue
            for item in result.items:
                for ref_field in result.spec.ref_fields:
                    value = item.get(ref_field)
                    if value is None:
                        continue
                    if isinstance(value, list):
                        item[f"{ref_field}_resolved"] = [
                            name_index.get(v, v) for v in value
                        ]
                    elif isinstance(value, str):
                        item[f"{ref_field}_resolved"] = name_index.get(value, value)
