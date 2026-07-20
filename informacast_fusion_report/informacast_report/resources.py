"""Declarative registry of Fusion API resources this tool reports on.

Each ResourceSpec describes one list endpoint. The crawler is generic — to
add a new resource (Singlewire adds these periodically; check the change
log at the bottom of https://api-docs.icmobile.singlewire.com/) you usually
just add another entry here.

`ref_fields` names attributes on each item that hold the id (or list of ids)
of another resource, so the crawler can annotate them with a resolved name
in addition to the raw id — e.g. a Message Template's `distributionListIds`
becomes readable instead of a wall of UUIDs.

`group` is just for organizing the report and for the --groups CLI filter.
It doesn't affect crawling.
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
    domain_scoped: bool = True   # whether this resource is fetched per-domain
    notes: Optional[str] = None  # shown in the report to explain caveats


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
        key="domains",
        label="Domains",
        path="/domains",
        group="access",
        domain_scoped=False,
        notes="Enumerated once up front; every other resource is then crawled per-domain.",
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
        path="/identity-providers",
        group="access",
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
        path="/Fusion/V1/Admin/TtsVoices",
        group="messaging",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path — verify against your instance's API Explorer.",
    ),
    ResourceSpec(
        key="tts_lexicon",
        label="Text-to-Speech Lexicon",
        path="/Fusion/V1/Admin/TtsLexicon",
        group="messaging",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path — verify against your instance's API Explorer.",
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
        path="/clear-device-schedules",
        group="automation",
    ),
    ResourceSpec(
        key="rule_actions",
        label="Rule Actions (API Connectors)",
        path="/rule-actions",
        group="automation",
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
        label="Extensions (On-Prem Endpoints)",
        path="/extensions",
        group="telephony",
    ),
    ResourceSpec(
        key="fusion_recipient_groups",
        label="Recipient Groups (Legacy On-Prem)",
        path="/Fusion/V1/RecipientGroups",
        group="telephony",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path for on-prem-synced recipient groups — verify path against your instance.",
    ),
    ResourceSpec(
        key="fusion_callaware",
        label="CallAware (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/CallAware",
        group="telephony",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path — verify against your instance's API Explorer.",
    ),
    ResourceSpec(
        key="fusion_m2m",
        label="M2M Contact Closures (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/M2M",
        group="telephony",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path — verify against your instance's API Explorer.",
    ),
    ResourceSpec(
        key="fusion_night_bell",
        label="Night Bell (Legacy On-Prem)",
        path="/Fusion/V1/Plugins/NightBell",
        group="telephony",
        domain_scoped=False,
        notes="Legacy /Fusion/V1 path — verify against your instance's API Explorer.",
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
        domain_scoped=False,
    ),
]


def resources_for_groups(selected_groups: Optional[set] = None) -> list[ResourceSpec]:
    if not selected_groups:
        return RESOURCES
    return [r for r in RESOURCES if r.group in selected_groups]
