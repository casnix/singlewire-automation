"""Walks a Fusion instance's Facilities and Resources, building a single
in-memory report model that the renderers (HTML/DOCX/JSON) consume.

Design notes:
- Resource errors (403 "you can't see this", 404 "doesn't exist on this
  instance/version") are caught per-resource so one missing/forbidden
  endpoint doesn't kill the whole report. They show up in the report as
  a visible "Not available" note instead.
- ID -> name resolution happens in a second pass, after every resource has
  been fetched, so it doesn't matter what order resources were crawled in.
- "Facility" is the real multi-tenancy concept in this API (confirmed
  against the OpenAPI spec) — there is no `/domains` endpoint. Earlier
  versions of this tool guessed "Domain" terminology and an
  `x-singlewire-domain` header; both were wrong, meaning any instance
  actually using multiple Facilities was silently only ever crawled in its
  default facility. Fixed throughout to use `/facilities` and
  `x-singlewire-facility`.
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


def list_facilities(client: FusionApiClient) -> list[dict]:
    """List every Facility the current token's user can act in. Returns an
    empty list (not an error) if the instance doesn't use multiple
    Facilities at all (single-facility users/tokens don't require one to
    be specified). Shared between the full crawl and the --test diagnostic
    mode so both behave identically with respect to multi-facility
    instances.
    """
    try:
        return list(client.paged_get("/facilities"))
    except ApiError as exc:
        logger.info("Could not list /facilities (%s) — treating as unused.", exc)
        return []


@dataclass
class ResourceResult:
    spec: ResourceSpec
    items: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    pagination_stats: dict = field(default_factory=dict)


@dataclass
class FacilityReport:
    facility: Optional[dict]  # None for the "no facilities configured" case
    resources: dict[str, ResourceResult] = field(default_factory=dict)
    sites_tree: list[dict] = field(default_factory=list)  # sites -> buildings -> floors -> zones
    alarm_details: list[dict] = field(default_factory=list)  # alarms with nested actions/events
    extension_tree: list[dict] = field(default_factory=list)  # extensions -> devices + endpoints
    dial_cast_rule_actions: list[dict] = field(default_factory=list)  # each dial_cast config's nested rule actions
    name_index: dict = field(default_factory=dict)  # id -> display name, across every resource (including nested)


@dataclass
class InstanceReport:
    base_url: str
    facilities: list[FacilityReport] = field(default_factory=list)
    generated_at: str = ""


class Crawler:
    def __init__(self, client: FusionApiClient, specs: Optional[list[ResourceSpec]] = None):
        self.client = client
        self.specs = specs if specs is not None else RESOURCES

    # -- top level ---------------------------------------------------------

    def run(self) -> InstanceReport:
        run_start = time.monotonic()
        report = InstanceReport(base_url=self.client.base_url)

        facilities = list_facilities(self.client)
        if not facilities:
            logger.info("No Facilities in use — crawling instance with default context.")
            report.facilities.append(self._crawl_facility(None))
        else:
            logger.info(
                "Found %d facilit%s: %s", len(facilities), "y" if len(facilities) == 1 else "ies",
                ", ".join(f.get("name", f.get("id", "?")) for f in facilities),
            )
            for i, f in enumerate(facilities, start=1):
                logger.info("Crawling facility %d/%d: %s", i, len(facilities), f.get("name", f.get("id")))
                report.facilities.append(self._crawl_facility(f))

        logger.info(
            "Crawl complete: %d facilit%s in %.1fs",
            len(report.facilities), "y" if len(report.facilities) == 1 else "ies",
            time.monotonic() - run_start,
        )
        return report

    # -- per facility ----------------------------------------------------------

    def _crawl_facility(self, facility: Optional[dict]) -> FacilityReport:
        facility_id = facility["id"] if facility else None
        fr = FacilityReport(facility=facility)

        for spec in self.specs:
            if spec.key == "facilities":
                # Already handled at the instance level; still show the count.
                continue

            result = ResourceResult(spec=spec)
            fetch_start = time.monotonic()
            try:
                fetch_facility_id = facility_id if spec.facility_scoped else None
                if spec.is_singleton:
                    # Not a paginated list -- a single config object (e.g.
                    # /settings, /ip-speaker-settings). Wrap it as a 1-item
                    # list so it renders the same way as everything else.
                    single = self.client.get_one(spec.path, facility_id=fetch_facility_id)
                    result.items = [single] if single else []
                    result.pagination_stats = {
                        "pages": 1, "items": len(result.items), "envelope": "singleton",
                    }
                else:
                    result.items = list(
                        self.client.paged_get(
                            spec.path, facility_id=fetch_facility_id, stats=result.pagination_stats,
                            pagination_style=spec.pagination_style,
                        )
                    )
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
            fr.resources[spec.key] = result

        logger.progress("Fetching site/building/floor/zone tree...")
        fr.sites_tree = self._crawl_sites_tree(fr, facility_id)
        logger.progress("Site tree: %d site(s)", len(fr.sites_tree))

        logger.progress("Fetching alarm actions/events...")
        fr.alarm_details = self._crawl_alarm_details(fr, facility_id)

        logger.progress("Fetching extension devices/endpoints...")
        fr.extension_tree = self._crawl_extension_tree(fr, facility_id)
        logger.progress("Extension tree: %d extension(s)", len(fr.extension_tree))

        logger.progress("Fetching DialCast rule actions...")
        fr.dial_cast_rule_actions = self._crawl_dialcast_rule_actions(fr, facility_id)

        logger.progress("Resolving cross-referenced IDs to names...")
        self._resolve_references(fr)
        return fr

    # -- nested/special resources --------------------------------------

    def _crawl_sites_tree(self, fr: FacilityReport, facility_id: Optional[str]) -> list[dict]:
        sites_result = fr.resources.get("sites")
        if not sites_result or not sites_result.items:
            return []

        tree = []
        for site in sites_result.items:
            site_node = dict(site)
            buildings = self._safe_facility_list(f"/sites/{site['id']}/buildings", facility_id)
            building_nodes = []
            for building in buildings:
                b_node = dict(building)
                floors = self._safe_facility_list(
                    f"/sites/{site['id']}/buildings/{building['id']}/floors", facility_id
                )
                floor_nodes = []
                for floor in floors:
                    f_node = dict(floor)
                    zones = self._safe_facility_list(
                        f"/sites/{site['id']}/buildings/{building['id']}/floors/{floor['id']}/zones",
                        facility_id,
                    )
                    f_node["zones"] = zones
                    floor_nodes.append(f_node)
                b_node["floors"] = floor_nodes
                building_nodes.append(b_node)
            site_node["buildings"] = building_nodes
            tree.append(site_node)
        return tree

    def _crawl_alarm_details(self, fr: FacilityReport, facility_id: Optional[str]) -> list[dict]:
        alarms_result = fr.resources.get("alarms")
        if not alarms_result or not alarms_result.items:
            return []

        details = []
        for alarm in alarms_result.items:
            node = dict(alarm)
            node["actions"] = self._safe_facility_list(f"/alarms/{alarm['id']}/actions", facility_id)
            node["events"] = self._safe_facility_list(f"/alarms/{alarm['id']}/events", facility_id)
            details.append(node)
        return details

    def _crawl_extension_tree(self, fr: FacilityReport, facility_id: Optional[str]) -> list[dict]:
        """Extensions are the "provider" objects (SMS, email, conference
        call, SchoolMessenger, WordPress, script integrations, etc.) — each
        one has nested Devices and Endpoints, confirmed via the OpenAPI spec
        (`/extensions/{id}/devices`, `/extensions/{id}/endpoints`). There is
        no flat top-level list of Endpoints; they only exist per-Extension.
        """
        extensions_result = fr.resources.get("extensions")
        if not extensions_result or not extensions_result.items:
            return []

        tree = []
        for ext in extensions_result.items:
            ext_node = dict(ext)
            ext_node["devices"] = self._safe_facility_list(
                f"/extensions/{ext['id']}/devices", facility_id
            )
            ext_node["endpoints"] = self._safe_facility_list(
                f"/extensions/{ext['id']}/endpoints", facility_id
            )
            tree.append(ext_node)
        return tree

    def _crawl_dialcast_rule_actions(self, fr: FacilityReport, facility_id: Optional[str]) -> list[dict]:
        """Each DialCast Dialing Configuration can have its own nested Rule
        Actions (`/dialcast-dialing-configurations/{id}/rule-actions`,
        confirmed via the OpenAPI spec) -- these are easy to miss if only
        looking at the configuration's own `notification` field, since they
        can trigger additional effects beyond the visible notification.
        """
        dial_cast_result = fr.resources.get("dial_cast")
        if not dial_cast_result or not dial_cast_result.items:
            return []

        details = []
        for config in dial_cast_result.items:
            rule_actions = self._safe_facility_list(
                f"/dialcast-dialing-configurations/{config['id']}/rule-actions", facility_id
            )
            details.append({
                "dial_cast_id": config["id"],
                "dial_cast_name": config.get("name", config["id"]),
                "rule_actions": rule_actions,
            })
        return details

    def _safe_facility_list(self, path: str, facility_id: Optional[str]) -> list[dict]:
        try:
            return list(self.client.paged_get(path, facility_id=facility_id))
        except ApiError as exc:
            logger.debug("Could not list %s: %s", path, exc)
            return []

    # -- reference resolution ---------------------------------------------

    def _resolve_references(self, fr: FacilityReport) -> None:
        """Populate a `<field>_resolved` companion attribute on every item
        for any ref_field that points at a resource we also crawled, mapping
        id(s) to display name(s) instead of leaving them as opaque UUIDs.

        The name index also includes nested resources (Sites' Buildings/
        Floors/Zones, Extensions' Devices/Endpoints) even though those aren't
        top-level ResourceSpecs — otherwise a reference like a Gateway's
        `buildingId` or a DialCast config's `endpointIds` would never
        resolve, since their targets only exist inside `sites_tree`/
        `extension_tree`, not `fr.resources`. The resolved index is stored
        on the FacilityReport itself (`fr.name_index`) so other consumers
        (e.g. the narrative generator) can reuse it without rebuilding it.
        """
        name_index: dict[str, str] = {}
        for result in fr.resources.values():
            name_field = result.spec.name_field
            for item in result.items:
                item_id = item.get("id")
                if item_id and name_field in item:
                    name_index[item_id] = item[name_field] or item_id

        for site in fr.sites_tree:
            if site.get("id"):
                name_index[site["id"]] = site.get("name", site["id"])
            for building in site.get("buildings", []):
                if building.get("id"):
                    name_index[building["id"]] = building.get("name", building["id"])
                for floor in building.get("floors", []):
                    if floor.get("id"):
                        name_index[floor["id"]] = floor.get("name", floor["id"])
                    for zone in floor.get("zones", []):
                        if zone.get("id"):
                            name_index[zone["id"]] = zone.get("name", zone["id"])

        for ext in fr.extension_tree:
            if ext.get("id"):
                name_index[ext["id"]] = ext.get("name", ext["id"])
            for device in ext.get("devices", []):
                if device.get("id"):
                    name_index[device["id"]] = device.get("name", device["id"])
            for endpoint in ext.get("endpoints", []):
                if endpoint.get("id"):
                    name_index[endpoint["id"]] = endpoint.get("name", endpoint["id"])

        fr.name_index = name_index
        logger.debug("Built name index with %d entries for reference resolution", len(name_index))

        for result in fr.resources.values():
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
