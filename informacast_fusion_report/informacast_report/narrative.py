"""Builds an instance-specific operational narrative from a crawled
InstanceReport -- the same kind of relationship explanation as
docs/RESOURCE_MODEL.md, but populated with this instance's actual configured
resources and their real cross-references (resolved names, not raw IDs).

This module only builds a rendering-agnostic content model (NarrativeDoc /
NarrativeSection / NarrativeTable). See render_narrative_docx.py for the
Word renderer, which follows the house style extracted from
Claude_Word_Template.docx.

Deliberately summarizes rather than dumps: group-level facts (a Device
Group's name, membership mechanism, and device-type counts) are included,
but full membership lists (every user in a list, every device in a group)
are not -- that's what the JSON/HTML/DOCX data reports are for. Large
tables are capped with an explicit "+N more" note rather than growing
unbounded on a big instance.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Optional

from .crawler import FacilityReport, InstanceReport

MAX_TABLE_ROWS = 40  # cap per table; a "+N more" note is added beyond this


@dataclass
class NarrativeTable:
    headers: list
    rows: list  # list of list[str]
    truncated_count: int = 0  # how many additional rows exist beyond what's shown


@dataclass
class NarrativeSection:
    heading: str
    level: int = 1  # 1, 2, or 3 -- maps to Heading 1/2/3
    paragraphs: list = field(default_factory=list)
    notes: list = field(default_factory=list)  # italic callouts, e.g. anomalies/warnings
    tables: list = field(default_factory=list)  # list[NarrativeTable]


@dataclass
class NarrativeDoc:
    title: str
    org_line: str
    subtitle: str
    meta: dict  # Document-Control-style key/value pairs
    intro_note: str
    sections: list  # list[NarrativeSection]


# -- small helpers -----------------------------------------------------------

def _res(fr: FacilityReport, key: str):
    """Items for a resource key, or None if it wasn't crawled/errored/empty."""
    result = fr.resources.get(key)
    if result is None or result.error:
        return None
    return result.items


def _name(item: dict, fallback_key: str = "id") -> str:
    return item.get("name") or item.get(fallback_key, "Unnamed")


def _resolved_list(item: dict, field_name: str) -> list:
    """Prefer the crawler's pre-resolved `<field>_resolved` companion; fall
    back to the raw ids if resolution wasn't available for this field."""
    resolved = item.get(f"{field_name}_resolved")
    if resolved is not None:
        return resolved if isinstance(resolved, list) else [resolved]
    raw = item.get(field_name)
    if raw is None:
        return []
    return raw if isinstance(raw, list) else [raw]


def _resolve_via_index(fr: FacilityReport, obj: dict, field_name: str) -> list:
    """Resolve id(s) to names using fr.name_index directly, for fields on
    NESTED sub-objects (e.g. a DialCast config's embedded `notification`)
    that the crawler's _resolve_references never sees -- it only walks
    top-level resource items, not arbitrary nested dicts within them.
    """
    raw = obj.get(field_name) if obj else None
    if raw is None:
        return []
    ids = raw if isinstance(raw, list) else [raw]
    return [fr.name_index.get(i, i) for i in ids if i]


def _join_names(names: list, limit: int = 6) -> str:
    names = [str(n) for n in names if n]
    if not names:
        return "none configured"
    if len(names) > limit:
        shown = ", ".join(names[:limit])
        return f"{shown}, and {len(names) - limit} more"
    return ", ".join(names)


def _cap_table(headers: list, rows: list) -> NarrativeTable:
    if len(rows) > MAX_TABLE_ROWS:
        return NarrativeTable(headers=headers, rows=rows[:MAX_TABLE_ROWS], truncated_count=len(rows) - MAX_TABLE_ROWS)
    return NarrativeTable(headers=headers, rows=rows)


def _truncate_text(text: Optional[str], max_len: int = 90) -> str:
    if not text:
        return "(empty)"
    text = str(text)
    return text if len(text) <= max_len else text[: max_len - 1] + "…"


# -- top level ----------------------------------------------------------------

def build_narrative(report: InstanceReport) -> NarrativeDoc:
    generated_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    meta = {
        "Instance": report.base_url,
        "Generated": generated_at,
        "Facilities covered": str(len(report.facilities)),
        "Classification": "INTERNAL — review before distribution",
    }

    intro_note = (
        "This document was generated automatically from a live read of the "
        "Fusion API as of the timestamp above. It reflects configuration at a "
        "single point in time and will drift out of date as the instance "
        "changes — treat it as a snapshot for onboarding/troubleshooting, not "
        "a source of truth to edit against. Full membership lists (every "
        "user, every individual device) are intentionally omitted here; use "
        "the accompanying JSON/HTML/DOCX data report for that level of detail."
    )

    sections: list[NarrativeSection] = []
    for i, fr in enumerate(report.facilities, start=1):
        facility_label = fr.facility["name"] if fr.facility else "Instance (no Facilities configured)"
        sections.append(NarrativeSection(
            heading=f"Facility {i}: {facility_label}",
            level=1,
            paragraphs=[_facility_overview_paragraph(fr)],
        ))
        sections.extend(_recipients_sections(fr))
        sections.extend(_devices_sections(fr))
        sections.extend(_messaging_sections(fr))
        sections.extend(_automation_sections(fr))
        sections.extend(_telephony_sections(fr))
        sections.extend(_monitoring_sections(fr))
        sections.extend(_admin_sections(fr))
        sections.append(_anomalies_section(fr))

    return NarrativeDoc(
        title="InformaCast Fusion — Operational Narrative",
        org_line=report.base_url,
        subtitle="Instance-specific configuration relationships, generated from live API data",
        meta=meta,
        intro_note=intro_note,
        sections=sections,
    )


def _facility_overview_paragraph(fr: FacilityReport) -> str:
    total_items = sum(len(r.items) for r in fr.resources.values())
    errored = [k for k, r in fr.resources.items() if r.error]
    parts = [f"This facility has {total_items} configured item(s) across {len(fr.resources)} resource type(s) crawled."]
    if errored:
        parts.append(
            f"{len(errored)} resource type(s) were not accessible with this token or don't exist on this "
            f"instance ({', '.join(errored[:8])}{', ...' if len(errored) > 8 else ''}) — see the data report "
            "for the specific errors."
        )
    return " ".join(parts)


# -- Recipients ----------------------------------------------------------------

def _recipients_sections(fr: FacilityReport) -> list:
    sections = []

    dist_lists = _res(fr, "distribution_lists")
    device_groups = _res(fr, "device_groups")
    aois = _res(fr, "areas_of_interest")
    collab_groups = _res(fr, "collaboration_groups")

    if not any([dist_lists, device_groups, aois, collab_groups]):
        return sections

    para = []
    if dist_lists is not None:
        synced = sum(1 for d in dist_lists if d.get("loadSourceId"))
        para.append(f"{len(dist_lists)} Distribution List(s) ({synced} synced from an external load source, "
                     f"{len(dist_lists) - synced} manually managed).")
    if device_groups is not None:
        dynamic = sum(1 for g in device_groups if g.get("filters") or g.get("logicalExpression"))
        composed = sum(1 for g in device_groups if g.get("baseDeviceGroupIds"))
        para.append(f"{len(device_groups)} Device Group(s) ({dynamic} use dynamic filter-based membership, "
                     f"{composed} are composed from other groups via baseDeviceGroupIds).")
    if aois is not None:
        para.append(f"{len(aois)} Area(s) of Interest (geofenced recipient targets).")
    if collab_groups is not None:
        para.append(f"{len(collab_groups)} Collaboration Group(s).")

    sec = NarrativeSection(heading="Recipients", level=2, paragraphs=[" ".join(para)])
    sections.append(sec)

    if device_groups:
        rows = []
        for g in device_groups:
            membership = []
            if g.get("filters") or g.get("logicalExpression"):
                membership.append("dynamic/filtered")
            if g.get("additionIds"):
                membership.append(f"{len(g['additionIds'])} explicit addition(s)")
            if g.get("baseDeviceGroupIds"):
                membership.append(f"composed of {len(g['baseDeviceGroupIds'])} other group(s)")
            if not membership:
                membership.append("empty — no additions, filters, or base groups")
            counts = f"{g.get('numPhones', 0)} phone(s), {g.get('numSpeakers', 0)} speaker(s), {g.get('numIdns', 0)} IDN(s)"
            rows.append([_name(g), "; ".join(membership), counts])
        sec.tables.append(_cap_table(["Device Group", "Membership mechanism", "Device counts"], rows))

    if dist_lists:
        rows = [
            [_name(d), "synced" if d.get("loadSourceId") else "manual",
             "subscribable" if d.get("isSubscribable") else "curated"]
            for d in dist_lists
        ]
        sec.tables.append(_cap_table(["Distribution List", "Source", "Subscription model"], rows))

    return sections


# -- Devices & delivery channels ------------------------------------------------

def _devices_sections(fr: FacilityReport) -> list:
    sections = []
    sec = NarrativeSection(heading="Devices & Delivery Channels", level=2, paragraphs=[])

    if fr.extension_tree:
        para = (f"{len(fr.extension_tree)} Extension(s) (integration providers — SMS, email, conference call, "
                "SchoolMessenger, WordPress, script integrations, etc.), each with its own Devices and Endpoints:")
        sec.paragraphs.append(para)
        rows = []
        for ext in fr.extension_tree:
            disabled = " (disabled)" if ext.get("disabled") else ""
            rows.append([
                f"{_name(ext)}{disabled}",
                str(len(ext.get("devices", []))),
                str(len(ext.get("endpoints", []))),
            ])
        sec.tables.append(_cap_table(["Extension", "Devices", "Endpoints"], rows))
    else:
        sec.paragraphs.append("No Extensions configured (or not visible to this API token).")

    ip_speakers = _res(fr, "ip_speakers")
    if ip_speakers:
        sec.paragraphs.append(f"{len(ip_speakers)} IP Speaker(s) registered.")
    ip_settings = _res(fr, "ip_speaker_settings")
    if ip_settings:
        sec.paragraphs.append("IP Speaker global settings are configured (singleton — applies instance-wide).")

    api_devices = _res(fr, "api_devices")
    if api_devices:
        sec.paragraphs.append(f"{len(api_devices)} API Device(s) — devices integrated via direct API calls.")

    sections.append(sec)
    return sections


# -- Messaging -------------------------------------------------------------------

def _messaging_sections(fr: FacilityReport) -> list:
    sections = []
    templates = _res(fr, "message_templates")
    if not templates:
        return sections

    sec = NarrativeSection(
        heading="Messaging: Templates and What They Reference",
        level=2,
        paragraphs=[
            f"{len(templates)} Message Template(s). Each can reference any combination of five recipient "
            "types (Distribution Lists, Device Groups, Collaboration Groups, Areas of Interest, Users) plus "
            "optional Confirmation Request, Notification Profile, TTS Voice, and Incident Plan — this table "
            "shows what each one actually points at in this instance."
        ],
    )

    rows = []
    orphaned = []
    for t in templates:
        recipient_bits = []
        for f in ("distributionListIds", "deviceGroupIds", "collaborationGroupIds", "areaOfInterestIds", "userIds"):
            names = _resolved_list(t, f)
            if names:
                recipient_bits.append(_join_names(names, limit=3))
        recipients_str = "; ".join(recipient_bits) if recipient_bits else "none configured"
        if not recipient_bits:
            orphaned.append(_name(t))

        extras = []
        cr = _resolved_list(t, "confirmationRequestId")
        if cr:
            extras.append(f"confirmation: {cr[0]}")
        np = _resolved_list(t, "notificationProfileId")
        if np:
            extras.append(f"profile: {np[0]}")
        ip = _resolved_list(t, "incidentPlanId")
        if ip:
            extras.append(f"incident plan: {ip[0]}")
        if t.get("ttsVoiceId"):
            extras.append("uses TTS")

        rows.append([_name(t), recipients_str, "; ".join(extras) if extras else "—"])

    sec.tables.append(_cap_table(["Message Template", "Recipients", "Linked config"], rows))
    if orphaned:
        sec.notes.append(
            f"{len(orphaned)} template(s) have no recipients configured on the template itself: "
            f"{_join_names(orphaned)}. This is normal if recipients are supplied at send time "
            "(e.g. by a Scenario or DialCast override) — otherwise these may be unused or incomplete."
        )
    sections.append(sec)

    confirmations = _res(fr, "confirmation_requests")
    profiles = _res(fr, "notification_profiles")
    tts_voices = _res(fr, "tts_voices")
    tts_lexicons = _res(fr, "tts_lexicons")
    if any([confirmations, profiles, tts_voices, tts_lexicons]):
        bits = []
        if confirmations:
            bits.append(f"{len(confirmations)} Confirmation Request(s)")
        if profiles:
            default_profile = next((_name(p) for p in profiles if p.get("default")), None)
            bits.append(f"{len(profiles)} Notification Profile(s)"
                        + (f" (default: {default_profile})" if default_profile else ""))
        if tts_voices:
            bits.append(f"{len(tts_voices)} TTS Voice(s)")
        if tts_lexicons:
            bits.append(f"{len(tts_lexicons)} TTS Lexicon(s) (custom pronunciations)")
        sections.append(NarrativeSection(
            heading="Delivery Behavior & Text-to-Speech Assets",
            level=3,
            paragraphs=[", ".join(bits) + "."],
        ))

    return sections


# -- Automation & triggers -------------------------------------------------------

def _automation_sections(fr: FacilityReport) -> list:
    sections = []

    # -- DialCast: the flagship worked example --
    dial_cast = _res(fr, "dial_cast")
    if dial_cast:
        rule_actions_by_id = {d["dial_cast_id"]: d["rule_actions"] for d in fr.dial_cast_rule_actions}
        phone_exceptions = _res(fr, "dial_cast_phone_exceptions") or []

        sec = NarrativeSection(
            heading="DialCast: Dial-Pattern Triggers",
            level=2,
            paragraphs=[
                f"{len(dial_cast)} DialCast Dialing Configuration(s), each matching a dialed pattern from CUCM "
                "and firing a notification (either embedded directly, or via a referenced Message Template) "
                "plus any nested Rule Actions."
                + (f" {len(phone_exceptions)} Phone Exception(s) also apply, overriding authentication/greeting "
                   "behavior for specific calling numbers." if phone_exceptions else "")
            ],
        )
        rows = []
        for config in dial_cast:
            notif = config.get("notification") or {}
            template_names = _resolve_via_index(fr, notif, "messageTemplateId") if isinstance(notif, dict) else []
            if template_names:
                content_desc = f"template: {template_names[0]}"
            else:
                recips = []
                if isinstance(notif, dict):
                    recips = (_resolve_via_index(fr, notif, "distributionListIds")
                              + _resolve_via_index(fr, notif, "deviceGroupIds"))
                content_desc = f"embedded notification → {_join_names(recips, limit=2)}" if recips else "embedded notification"

            endpoint_names = _resolved_list(config, "endpointIds")
            n_actions = len(rule_actions_by_id.get(config["id"], []))
            fallback = "yes" if config.get("fallbackNotification") else "no"

            rows.append([
                _name(config),
                config.get("dialingPatternRegex", "—"),
                _join_names(endpoint_names, limit=2),
                content_desc,
                str(n_actions),
                fallback,
            ])
        sec.tables.append(_cap_table(
            ["DialCast Config", "Dial Pattern", "Endpoint(s)", "What it sends", "Rule Actions", "Has fallback?"],
            rows,
        ))

        no_action_configs = [
            _name(c) for c in dial_cast
            if not rule_actions_by_id.get(c["id"]) and not c.get("fallbackNotification")
        ]
        if no_action_configs:
            sec.notes.append(
                f"{len(no_action_configs)} configuration(s) have neither Rule Actions nor a fallback "
                f"notification: {_join_names(no_action_configs)}. If the embedded notification fails to "
                "send (e.g. bad TTS content), there's nothing else to fall back on for these."
            )
        sections.append(sec)

    # -- Scenarios --
    scenarios = _res(fr, "scenarios")
    if scenarios:
        rows = []
        for s in scenarios:
            ip_names = _resolved_list(s, "incidentPlanId")
            rows.append([
                _name(s),
                s.get("confirmationType", "none"),
                ip_names[0] if ip_names else "—",
                "yes" if s.get("phoneNumber") else "no",
            ])
        sec = NarrativeSection(
            heading="Scenarios: Manual/Interactive Triggers",
            level=2,
            paragraphs=[f"{len(scenarios)} Scenario(s) — each carries its own embedded message content "
                        "(not a shared Message Template), optionally tied to an Incident Plan."],
        )
        sec.tables.append(_cap_table(
            ["Scenario", "Confirmation required?", "Incident Plan", "Also dial-in triggerable?"], rows
        ))
        sections.append(sec)

    # -- Incident Plans / Incidents --
    incident_plans = _res(fr, "incident_plans")
    incidents = _res(fr, "incidents")
    if incident_plans or incidents:
        bits = []
        if incident_plans:
            rostering = sum(1 for p in incident_plans if p.get("rostering"))
            bits.append(f"{len(incident_plans)} Incident Plan(s) ({rostering} with roll-call/rostering enabled)")
        if incidents:
            ongoing = sum(1 for i in incidents if not i.get("resolvedAt") and not i.get("endedAt"))
            bits.append(f"{len(incidents)} Incident(s) on record" + (f", {ongoing} currently ongoing" if ongoing else ""))
        sections.append(NarrativeSection(
            heading="Incident Plans & Incidents",
            level=2,
            paragraphs=[". ".join(bits) + "."],
        ))

    # -- Inbound triggers --
    inbound_cap = _res(fr, "inbound_cap_rules")
    inbound_email = _res(fr, "inbound_email")
    inbound_rss = _res(fr, "inbound_rss_feeds")
    if any([inbound_cap, inbound_email, inbound_rss]):
        sec = NarrativeSection(heading="External-System Triggers (Inbound CAP / Email / RSS)", level=2, paragraphs=[])
        rows = []
        for label, items in (("CAP Rule", inbound_cap), ("Inbound Email", inbound_email), ("RSS Feed", inbound_rss)):
            for item in (items or []):
                template_names = _resolved_list(item, "messageTemplateId")
                rows.append([label, _name(item), template_names[0] if template_names else "—"])
        if rows:
            sec.paragraphs.append(f"{len(rows)} external-system trigger(s) configured, each firing a Message Template when matched:")
            sec.tables.append(_cap_table(["Type", "Name", "Message Template"], rows))
        sections.append(sec)

    # -- Bell Schedules / Ring Lists --
    bell_schedules = _res(fr, "bell_schedules")
    ring_lists = _res(fr, "ring_lists")
    if bell_schedules or ring_lists:
        bits = []
        if bell_schedules:
            bits.append(f"{len(bell_schedules)} Bell Schedule(s)")
        if ring_lists:
            bits.append(f"{len(ring_lists)} Ring List(s)")
        sections.append(NarrativeSection(
            heading="Bell Schedules & Ring Lists",
            level=2,
            paragraphs=[", ".join(bits) + " — Bell Schedules define *when* recurring audio fires; "
                        "Ring Lists define *what* actually rings."],
        ))

    # -- Scheduled Notifications / Clear Device Schedules --
    scheduled = _res(fr, "scheduled_notifications")
    clear_schedules = _res(fr, "clear_device_schedules")
    if scheduled or clear_schedules:
        sec = NarrativeSection(heading="Scheduled Notifications & Clear Device Schedules", level=2, paragraphs=[])
        if scheduled:
            rows = []
            for s in scheduled:
                template_names = _resolved_list(s, "messageTemplateId")
                recips = _resolved_list(s, "deviceGroupIds") + _resolved_list(s, "distributionListIds")
                rows.append([
                    _name(s),
                    template_names[0] if template_names else "—",
                    _join_names(recips, limit=2),
                    "disabled" if s.get("disabled") else s.get("nextFireTime", "—"),
                ])
            sec.paragraphs.append(f"{len(scheduled)} Scheduled Notification(s) — direct template + recipient + schedule, no Scenario/Incident Plan indirection:")
            sec.tables.append(_cap_table(["Name", "Template", "Recipients", "Next fire / status"], rows))
        if clear_schedules:
            sec.paragraphs.append(f"{len(clear_schedules)} Clear Device Schedule(s) — reset/clear devices on a schedule rather than sending new content.")
        sections.append(sec)

    return sections


# -- Telephony backbone -----------------------------------------------------------

def _telephony_sections(fr: FacilityReport) -> list:
    sections = []
    cucm = _res(fr, "cucm_clusters")
    failover = _res(fr, "failover_pairs")
    gateways = _res(fr, "gateways")

    if not any([cucm, failover, gateways]):
        return sections

    bits = []
    if cucm:
        bits.append(f"{len(cucm)} CUCM Cluster(s) — the AXL/TFTP/CAPF integration that makes phone-based "
                    "Device Group membership possible at all")
    if failover:
        bits.append(f"{len(failover)} Failover Pair(s) providing Fusion Server redundancy")
    if gateways:
        bits.append(f"{len(gateways)} Gateway(s) (paging/LPI and other gateway hardware)")

    sections.append(NarrativeSection(
        heading="Telephony Backbone",
        level=2,
        paragraphs=[". ".join(bits) + "."],
    ))
    return sections


# -- Monitoring ---------------------------------------------------------------------

def _monitoring_sections(fr: FacilityReport) -> list:
    sections = []
    alarms = _res(fr, "alarms")
    active_calls = _res(fr, "active_callaware_calls")

    if not alarms and not active_calls:
        return sections

    sec = NarrativeSection(heading="Monitoring & Operational State", level=2, paragraphs=[])
    if alarms:
        unhealthy = [a for a in alarms if a.get("status") not in (None, "OK") and not a.get("muted")]
        sec.paragraphs.append(f"{len(alarms)} Alarm type(s) tracked; {len(unhealthy)} currently unhealthy and unmuted.")
        if unhealthy:
            rows = [[a.get("type", "—"), a.get("status", "—")] for a in unhealthy]
            sec.tables.append(_cap_table(["Alarm Type", "Status"], rows))
            sec.notes.append(
                f"{len(unhealthy)} alarm(s) are active and NOT muted — worth checking before relying on this "
                "instance's monitoring/paging path being fully healthy."
            )
    if active_calls:
        sec.paragraphs.append(f"{len(active_calls)} CallAware call(s) currently being monitored (live state, not configuration).")
    sections.append(sec)
    return sections


# -- Admin ----------------------------------------------------------------------------

def _admin_sections(fr: FacilityReport) -> list:
    sections = []
    settings = _res(fr, "settings")
    brandings = _res(fr, "brandings")
    load_defs = _res(fr, "load_definitions")

    if not any([settings, brandings, load_defs]):
        return sections

    bits = []
    if settings:
        bits.append("Global Settings are configured (singleton)")
    if brandings:
        bits.append(f"{len(brandings)} Branding(s)")
    if load_defs:
        bits.append(f"{len(load_defs)} Load Definition(s) (bulk import job definitions — often the source for synced Distribution Lists)")

    sections.append(NarrativeSection(
        heading="Platform Administration",
        level=2,
        paragraphs=[", ".join(bits) + "."],
    ))
    return sections


# -- Anomalies ------------------------------------------------------------------------

def _anomalies_section(fr: FacilityReport) -> NarrativeSection:
    """Concrete, this-instance-specific things worth an engineer's attention
    -- not a generic checklist, actual flagged items with names.
    """
    sec = NarrativeSection(heading="Things Worth Verifying in This Facility", level=2, paragraphs=[])

    device_groups = _res(fr, "device_groups") or []
    empty_groups = [
        _name(g) for g in device_groups
        if not g.get("additionIds") and not g.get("filters") and not g.get("logicalExpression")
        and not g.get("baseDeviceGroupIds")
    ]
    if empty_groups:
        sec.notes.append(
            f"{len(empty_groups)} Device Group(s) appear empty (no additions, filters, or composed groups): "
            f"{_join_names(empty_groups)}. These may be unused, or misconfigured."
        )

    dist_lists = _res(fr, "distribution_lists") or []
    templates = _res(fr, "message_templates") or []
    referenced_list_ids = set()
    for t in templates:
        referenced_list_ids.update(t.get("distributionListIds") or [])
    unreferenced_lists = [_name(d) for d in dist_lists if d.get("id") not in referenced_list_ids]
    if unreferenced_lists and dist_lists:
        sec.notes.append(
            f"{len(unreferenced_lists)} of {len(dist_lists)} Distribution List(s) aren't referenced by any "
            f"Message Template directly: {_join_names(unreferenced_lists)}. This is normal if they're used "
            "via Scenarios/DialCast/API calls instead — otherwise worth confirming they're still needed."
        )

    if not sec.notes:
        sec.paragraphs.append("No obvious anomalies detected among the resource types crawled.")

    return sec
