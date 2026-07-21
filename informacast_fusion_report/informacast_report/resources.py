"""Declarative registry of Fusion API resources this tool reports on.

Each ResourceSpec describes one list (or singleton) endpoint. The crawler
is generic — to add a new resource you usually just add another entry
here.

`ref_fields` names attributes on each item that hold the id (or list of ids)
of another resource, so the crawler can annotate them with a resolved name
in addition to the raw id — e.g. a Message Template's `distributionListIds`
becomes readable instead of a wall of UUIDs.

`group` is just for organizing the report and for the --groups CLI filter.
It doesn't affect crawling.

`pagination_style` defaults to "cursor" (echo back the previous response's
`next` value verbatim as a `start` param) for every resource. This is now
CONFIRMED against the real OpenAPI spec (`spec.json`, obtained directly from
Singlewire's API Explorer) — every single list endpoint in the spec (192 of
192 checked) uses `limit`/`start`, and zero use `offset`. This also confirms
the "offset" assumption this tool started with was wrong everywhere, not
just for the resources caught by the duplicate/stuck-page detector.

`is_singleton` marks a resource that returns one config object directly,
not a paginated list envelope (e.g. `/settings`, `/ip-speaker-settings`) —
confirmed by their lack of `limit`/`start` parameters and lack of a
`data`/`total`/`partial` wrapper in the spec. These are fetched with
`client.get_one()` instead of `client.paged_get()`.

Every path below was checked directly against the real OpenAPI spec
(spec.json). Anything not yet confirmed says so explicitly in `notes`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ResourceSpec:
    key: str                     # internal identifier, also used as report anchor
    label: str                   # human-readable heading
    path: str                    # API path, relative to base_url
    group: str                   # report section grouping
    name_field: str = "name"     # field used when resolving this resource as a reference
    ref_fields: tuple = field(default_factory=tuple)   # fields on items that reference other resources
    facility_scoped: bool = True   # whether this resource is fetched per-facility
    notes: Optional[str] = None  # shown in the report to explain caveats
    pagination_style: str = "cursor"  # "cursor" (echo back `next` as `start`) or "offset" (compute offset=N)
    is_singleton: bool = False   # True for a single config object, not a paginated list


GROUPS = {
    "access": "Users, Roles & Access",
    "recipients": "Recipients (Distribution Lists, Devices, Groups)",
    "messaging": "Messaging (Templates, Confirmations, Profiles)",
    "automation": "Automation & Triggers (Scenarios, Bells, Schedules)",
    "locations": "Sites & Locations",
    "telephony": "Telephony / On-Prem Integration",
    "monitoring": "Monitoring & Alarms",
    "admin": "Platform Administration",
}

RESOURCES: list[ResourceSpec] = [
    # -- Access -----------------------------------------------------------
    ResourceSpec(
        key="facilities",
        label="Facilities",
        path="/facilities",
        group="access",
        facility_scoped=False,
        notes=(
            "Enumerated once up front; every other resource is then crawled per-facility. "
            "'Facility' is the real multi-tenancy concept in this API (confirmed against the "
            "OpenAPI spec) — there is no /domains endpoint. Earlier versions of this tool "
            "assumed 'Domain' terminology and an x-singlewire-domain header, both wrong; any "
            "instance actually using multiple Facilities was silently only crawled in its "
            "default facility until this was corrected."
        ),
    ),
    ResourceSpec(
        key="users",
        label="Users",
        path="/users",
        group="access",
        name_field="name",
    ),
    ResourceSpec(
        key="security_groups",
        label="Security Groups",
        path="/security-groups",
        group="access",
        ref_fields=("userIds",),
    ),
    ResourceSpec(
        key="identity_providers",
        label="Identity Providers",
        path="/idps",
        group="access",
        notes="Path corrected to /idps (confirmed via OpenAPI spec) — /identity-providers doesn't exist.",
    ),

    # -- Recipients ---------------------------------------------------------
    ResourceSpec(
        key="distribution_lists",
        label="Distribution Lists",
        path="/distribution-lists",
        group="recipients",
    ),
    ResourceSpec(
        key="device_groups",
        label="Device Groups",
        path="/device-groups",
        group="recipients",
        notes=(
            "This was the first endpoint confirmed (via a separate investigation) to need "
            "cursor-token pagination. Since confirmed via the OpenAPI spec: EVERY list "
            "endpoint in this API uses cursor pagination — there is no offset-based endpoint "
            "anywhere."
        ),
    ),
    ResourceSpec(
        key="collaboration_groups",
        label="Collaboration Groups",
        path="/collaboration-groups",
        group="recipients",
    ),
    ResourceSpec(
        key="areas_of_interest",
        label="Areas of Interest",
        path="/areas-of-interest",
        group="recipients",
    ),
    ResourceSpec(
        key="api_devices",
        label="API Devices",
        path="/api-devices",
        group="recipients",
    ),
    ResourceSpec(
        key="activation_groups",
        label="Activation Groups",
        path="/activation-groups",
        group="recipients",
        notes="Used for multicast-capable phone grouping (e.g. Poly devices).",
    ),
    ResourceSpec(
        key="ip_speakers",
        label="IP Speakers",
        path="/ip-speakers",
        group="recipients",
        ref_fields=("buildingId", "floorId"),
        notes="Registered IP paging speakers. Confirmed via OpenAPI spec.",
    ),
    ResourceSpec(
        key="ip_speaker_sip_parameters",
        label="IP Speaker SIP Parameters",
        path="/ip-speaker-sip-parameters",
        group="recipients",
        notes="Per-speaker SIP registration parameters. Confirmed via OpenAPI spec.",
    ),
    ResourceSpec(
        key="ip_speaker_jobs",
        label="IP Speaker Bulk Jobs",
        path="/ip-speaker-jobs",
        group="recipients",
        notes="Bulk provisioning/firmware jobs for IP Speakers. Confirmed via OpenAPI spec.",
    ),
    ResourceSpec(
        key="ip_speaker_settings",
        label="IP Speaker Settings",
        path="/ip-speaker-settings",
        group="recipients",
        is_singleton=True,
        notes=(
            "Global IP Speaker configuration (single object, not a list) — confirmed via "
            "OpenAPI spec: this endpoint has no limit/start pagination params and no "
            "data/total/partial envelope."
        ),
    ),

    # -- Messaging ----------------------------------------------------------
    ResourceSpec(
        key="message_templates",
        label="Message Templates",
        path="/message-templates",
        group="messaging",
        ref_fields=(
            "distributionListIds",
            "deviceGroupIds",
            "collaborationGroupIds",
            "areaOfInterestIds",
            "userIds",
            "confirmationRequestId",
            "notificationProfileId",
            "incidentPlanId",
        ),
    ),
    ResourceSpec(
        key="confirmation_requests",
        label="Confirmation Requests",
        path="/confirmation-requests",
        group="messaging",
    ),
    ResourceSpec(
        key="notification_profiles",
        label="Notification Profiles",
        path="/notification-profiles",
        group="messaging",
    ),
    ResourceSpec(
        key="tts_voices",
        label="Text-to-Speech Voices",
        path="/tts-voices",
        group="messaging",
        notes="Path corrected to the cloud-native /tts-voices (confirmed via OpenAPI spec) — the legacy /Fusion/V1/Admin/TtsVoices path is no longer used here.",
    ),
    ResourceSpec(
        key="tts_lexicons",
        label="Text-to-Speech Lexicons",
        path="/tts-lexicons",
        group="messaging",
        notes="Path corrected to the cloud-native /tts-lexicons (confirmed via OpenAPI spec) — the legacy /Fusion/V1/Admin/TtsLexicon path is no longer used here.",
    ),
    ResourceSpec(
        key="tts_defaults",
        label="Text-to-Speech Defaults",
        path="/tts-defaults",
        group="messaging",
        notes="Default TTS voice per locale/type. Confirmed via OpenAPI spec.",
    ),

    # -- Automation & Triggers ----------------------------------------------
    ResourceSpec(
        key="scenarios",
        label="Scenarios",
        path="/scenarios",
        group="automation",
    ),
    ResourceSpec(
        key="incident_plans",
        label="Incident Plans",
        path="/incident-plans",
        group="automation",
    ),
    ResourceSpec(
        key="incidents",
        label="Incidents",
        path="/incidents",
        group="automation",
        notes=(
            "Runtime incident instances (each tied to a Scenario/Incident Plan execution), "
            "not just configuration — this can be a large, date-filterable dataset on an "
            "active instance. Confirmed via OpenAPI spec. Consider `--unit incidents` on its "
            "own first if you only want to check this one."
        ),
    ),
    ResourceSpec(
        key="bell_schedules",
        label="Bell Schedules",
        path="/bell-schedules",
        group="automation",
    ),
    ResourceSpec(
        key="ring_lists",
        label="Ring Lists",
        path="/ring-lists",
        group="automation",
    ),
    ResourceSpec(
        key="scheduled_notifications",
        label="Scheduled Notifications",
        path="/scheduled-notifications",
        group="automation",
    ),
    ResourceSpec(
        key="clear_device_schedules",
        label="Clear Device Schedules",
        path="/clear-devices-schedules",
        group="automation",
        notes="Path corrected to /clear-devices-schedules (plural 'devices' — confirmed via OpenAPI spec).",
    ),
    ResourceSpec(
        key="rule_actions",
        label="Rule Actions (API Connectors)",
        path="/rule-actions",
        group="automation",
    ),
    ResourceSpec(
        key="dial_cast",
        label="DialCast Dialing Configurations",
        path="/dialcast-dialing-configurations",
        group="automation",
        ref_fields=("endpointIds",),
        notes=(
            "Dialing patterns that trigger a broadcast when a matching SIP number is dialed. "
            "Path confirmed via OpenAPI spec (previous guess had an extra hyphen: "
            "/dial-cast-dialing-configurations). Each configuration's notification/message "
            "reference is nested in a sub-object rather than a flat field, so it isn't "
            "resolved to a name here — see the raw item for details."
        ),
    ),
    ResourceSpec(
        key="dial_cast_phone_exceptions",
        label="DialCast Phone Exceptions",
        path="/dialcast-phone-exceptions",
        group="automation",
        notes=(
            "Numbers/patterns selectively exempted from global DialCast settings. Path "
            "confirmed via OpenAPI spec (previous guess had an extra hyphen)."
        ),
    ),
    ResourceSpec(
        key="inbound_cap_rules",
        label="Inbound CAP Rules (Common Alerting Protocol)",
        path="/inbound-cap-rules",
        group="automation",
        ref_fields=("messageTemplateId",),
        notes="Triggers a broadcast on matching CAP alert messages. Path confirmed via OpenAPI spec (previous guess was missing '-rules').",
    ),
    ResourceSpec(
        key="inbound_email",
        label="Inbound Email Triggers",
        path="/inbound-email",
        group="automation",
        notes=(
            "Triggers a broadcast from monitored inbound email accounts. Path confirmed "
            "correct via OpenAPI spec. Each item has a nested `outboundRules` list "
            "(auto-reply/forwarding rules) not separately crawled here."
        ),
    ),
    ResourceSpec(
        key="inbound_rss_feeds",
        label="Inbound RSS Feed Triggers",
        path="/inbound-rss-feeds",
        group="automation",
        ref_fields=("distributionListIds", "messageTemplateId"),
        notes="Triggers a broadcast from monitored RSS feeds. Path confirmed via OpenAPI spec (previous guess was missing '-feeds').",
    ),

    # -- Sites & Locations ----------------------------------------------------
    ResourceSpec(
        key="sites",
        label="Sites",
        path="/sites",
        group="locations",
    ),
    # Buildings/Floors/Zones are nested under a site in the real API
    # (/sites/{siteId}/buildings, etc). They're handled specially in the
    # crawler rather than as flat top-level specs — see crawler.py.

    # -- Telephony / On-Prem ----------------------------------------------
    ResourceSpec(
        key="cucm_clusters",
        label="Cisco Unified CM Clusters",
        path="/cucm-clusters",
        group="telephony",
    ),
    ResourceSpec(
        key="failover_pairs",
        label="Failover Pairs",
        path="/failover-pairs",
        group="telephony",
    ),
    ResourceSpec(
        key="extensions",
        label="Extensions (Integration Providers)",
        path="/extensions",
        group="telephony",
        notes=(
            "Extensions are the 'provider' objects behind non-phone/speaker recipients — "
            "SMS, email, conference call, SchoolMessenger, WordPress, script integrations, "
            "etc. Each Extension's nested Devices and Endpoints (confirmed via OpenAPI spec: "
            "/extensions/{id}/devices, /extensions/{id}/endpoints) are fetched specially in "
            "the crawler — see the Extensions Detail section of the report, not this table."
        ),
    ),
    ResourceSpec(
        key="gateways",
        label="Gateways",
        path="/gateways",
        group="telephony",
        ref_fields=("buildingId", "floorId"),
        notes=(
            "Generic Gateways resource — covers LPI paging gateways and other gateway types "
            "(filterable by model/connection status). Path corrected from a guessed "
            "/paging-gateways (confirmed via OpenAPI spec: no such endpoint exists, only the "
            "generic /gateways)."
        ),
    ),
    ResourceSpec(
        key="active_callaware_calls",
        label="Active CallAware Calls",
        path="/active-callaware-calls",
        group="monitoring",
        notes=(
            "This is CallAware's only GET endpoint in the OpenAPI spec — it's LIVE call "
            "state (calls currently being monitored/recorded), not saved configuration. "
            "A previous guess (/call-aware-redirects) doesn't exist; there is no separate "
            "endpoint for CallAware's call-redirect configuration rules, only 'dependent' "
            "lookups nested under Device Groups/Distribution Lists/Message Templates "
            "(e.g. /device-groups/{id}/callaware-call-redirect-dependents), which show "
            "which of those resources a given redirect rule uses, not a flat list of "
            "the rules themselves."
        ),
    ),
    ResourceSpec(
        key="fusion_recipient_groups",
        label="Recipient Groups (Legacy On-Prem)",
        path="/Fusion/V1/RecipientGroups",
        group="telephony",
        facility_scoped=False,
        notes="Legacy /Fusion/V1 path, not present in the cloud OpenAPI spec — verify this still applies to your on-prem Fusion server.",
    ),
    ResourceSpec(
        key="fusion_callaware",
        label="CallAware (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/CallAware",
        group="telephony",
        facility_scoped=False,
        notes="Legacy /Fusion/V1 path, not present in the cloud OpenAPI spec — verify this still applies to your on-prem Fusion server.",
    ),
    ResourceSpec(
        key="fusion_m2m",
        label="M2M Contact Closures (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/M2M",
        group="telephony",
        facility_scoped=False,
        notes="Legacy /Fusion/V1 path, not present in the cloud OpenAPI spec — verify this still applies to your on-prem Fusion server.",
    ),
    ResourceSpec(
        key="fusion_night_bell",
        label="Night Bell (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/NightBell",
        group="telephony",
        facility_scoped=False,
        notes="Legacy /Fusion/V1 path, not present in the cloud OpenAPI spec — verify this still applies to your on-prem Fusion server.",
    ),

    # -- Monitoring -----------------------------------------------------
    ResourceSpec(
        key="alarms",
        label="Alarms",
        path="/alarms",
        group="monitoring",
        notes="Alarm Actions/Events are nested per-alarm — fetched specially in the crawler.",
    ),

    # -- Platform Admin ---------------------------------------------------
    ResourceSpec(
        key="load_definitions",
        label="Load Definitions (Bulk Import Jobs)",
        path="/load-definitions",
        group="admin",
    ),
    ResourceSpec(
        key="brandings",
        label="Brandings",
        path="/brandings",
        group="admin",
    ),
    ResourceSpec(
        key="settings",
        label="Global Settings",
        path="/settings",
        group="admin",
        facility_scoped=False,
        is_singleton=True,
        notes=(
            "Single config object, not a list — confirmed via OpenAPI spec: this endpoint "
            "has no limit/start pagination params and no data/total/partial envelope. "
            "Earlier versions of this tool fetched it via paged_get() anyway, which "
            "silently returned zero items every time since there was never a `data` key "
            "to read from."
        ),
    ),
]


RESOURCE_BY_KEY: dict[str, ResourceSpec] = {r.key: r for r in RESOURCES}


def resources_for_groups(selected_groups: Optional[set] = None) -> list[ResourceSpec]:
    if not selected_groups:
        return RESOURCES
    return [r for r in RESOURCES if r.group in selected_groups]


def get_resource(key: str) -> ResourceSpec:
    """Look up a ResourceSpec by its key, raising a helpful error listing
    valid keys if it doesn't exist (used by the --test CLI option)."""
    try:
        return RESOURCE_BY_KEY[key]
    except KeyError:
        valid = ", ".join(sorted(RESOURCE_BY_KEY.keys()))
        raise KeyError(f"Unknown resource key {key!r}. Valid keys: {valid}")


def resources_for_keys(keys: list) -> list[ResourceSpec]:
    """Look up a list of ResourceSpecs by exact key (as shown by
    --list-resources), preserving the order given. Used by the --unit CLI
    filter, which is more precise than --groups: --groups pulls in every
    resource in a category, while --unit pulls in exactly the resource(s)
    named and nothing else. Raises KeyError (with a list of valid keys) on
    the first unrecognized key.
    """
    return [get_resource(k) for k in keys]
