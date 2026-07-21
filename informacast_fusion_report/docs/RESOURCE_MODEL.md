# InformaCast Fusion — Resource Model & Operational Guide

This document explains **how the resources this tool pulls relate to each other and
how they're used operationally** — it's a conceptual map, not a data dump. If you're
trying to understand why a notification fired, why it went to who it went to, or how
a piece of config gets from "someone dials a number" to "a message goes out," this is
the document to read first. Field names below are taken directly from the real
OpenAPI spec, not guessed.

It assumes you're already looking at (or about to generate) a report from this tool
and want to understand what you're looking at, rather than reading raw JSON cold.

## The one thing to understand before anything else: Facility scoping

Every resource in this document lives inside a **Facility** — the real multi-tenancy
boundary in this API (see `CHANGELOG.md` for how that was confirmed; it is *not*
called "Domain" anywhere in the actual API, despite what you might expect). If an
instance uses multiple Facilities, config in one Facility is invisible from another
by default — a Distribution List built in Facility A cannot be referenced by a
Message Template in Facility B. When troubleshooting "why can't this template see
that list," Facility mismatch is the first thing to rule out, before touching
permissions or naming.

Most resources carry a `facilityId` field for exactly this reason.

---

## The mental model, top to bottom

Think of a notification's life in five layers, top being "who gets it" and bottom
being "what actually causes it to fire":

```
 WHO/WHAT RECEIVES IT     Users, Distribution Lists, Device Groups,
                          Areas of Interest, Collaboration Groups
            │
            ▼
 HOW IT REACHES THEM      Extensions → Devices/Endpoints, IP Speakers,
                          API Devices, CUCM-registered phones
            │
            ▼
 WHAT IS SENT             Message Templates (the payload), referencing
                          Confirmation Requests, Notification Profiles,
                          TTS Voices/Lexicons for audio generation
            │
            ▼
 WHAT CAUSES IT TO FIRE   Scenarios, Incident Plans → Incidents, DialCast,
                          Inbound CAP/Email/RSS, Bell Schedules, Ring Lists,
                          Scheduled Notifications, Rule Actions
            │
            ▼
 THE PHYSICAL/TELEPHONY   CUCM Clusters, Failover Pairs, Gateways,
 BACKBONE THAT CARRIES IT Fusion Servers, legacy on-prem plugins
```

Everything below expands each layer with the actual fields that link them.

---

## Layer 1: Recipients — who or what a message can go to

A Message Template doesn't point at one type of recipient — it can carry **any
combination** of five recipient reference fields simultaneously
(`distributionListIds`, `deviceGroupIds`, `collaborationGroupIds`,
`areaOfInterestIds`, `userIds`). Understanding the difference between these five is
the single most useful thing for reading a template correctly:

- **Distribution Lists** (`distribution_lists`) — the simplest recipient type: a
  named list of Users, mainly for mobile-app/email/SMS-style delivery. Fields like
  `isSubscribable` and `loadSourceId` mean a list can be self-service (people opt in)
  or synced from an external system (e.g. an SIS/HR feed) rather than manually
  curated — worth checking `loadSourceId` before assuming a list is manually managed.
- **Device Groups** (`device_groups`) — recipients defined by **hardware**, not
  people: phones, speakers, IDNs, plugins. Critically, these are not just flat lists:
  - `additionIds` / `additions` — explicitly added devices
  - `exclusionIds` / `exclusions` — explicitly removed devices (evaluated *after*
    additions and filters — a device can be filter-matched or added, then excluded)
  - `baseDeviceGroupIds` — other Device Groups nested inside this one (composable
    groups — a group can be "everything in Group A and Group B, minus X")
  - `filters` / `filterType` / `logicalExpression` — **dynamic** membership rules
    (e.g. "all phones where Name CONTAINS 'Lobby'"), evaluated at send time rather
    than a fixed list. This is why a Device Group's *displayed* member count can
    genuinely differ between two sends — filter-based groups aren't static, and the
    API doesn't expose the resolved current membership, only the rule.
  - `numPhones`/`numSpeakers`/`numIdns`/`numPlugins` — cached counts by device type
- **Areas of Interest** (`areas_of_interest`) — **geofenced** recipients: a
  `geometryType`/`geometryValue` (a shape, e.g. a polygon or radius) plus
  `syncedDeviceTypes`. A message sent to an Area of Interest targets whatever devices
  currently fall inside that shape, not a fixed roster — the most dynamic recipient
  type of all.
- **Collaboration Groups** (`collaboration_groups`) — a hybrid: carries **both**
  `distributionListIds` and `userIds` plus a `type` field, used for group
  chat/collaboration-style channels rather than one-shot broadcasts.
- **Users** (`users`) directly — a Message Template can also target specific Users
  by ID with no list/group wrapper at all, for one-off or highly targeted sends.

**Security Groups** (`security_groups`) are *not* a recipient type — they're access
control (`policyId`, `superGroup`, and a list of `userIds`), governing who can see or
trigger what, not who receives a notification.

---

## Layer 2: Devices & integration channels — how a message physically reaches someone

- **Extensions** (`extensions`) are the **provider/integration objects** behind
  anything that isn't a phone or IP speaker directly registered to Fusion — SMS
  gateways, email delivery, conference call bridges, SchoolMessenger, WordPress,
  custom script integrations. An Extension is *not* itself a recipient — its nested
  **Devices** and **Endpoints** are (fetched via `/extensions/{id}/devices` and
  `/extensions/{id}/endpoints`, surfaced in this tool's "Extensions Detail" report
  section since neither exists as a flat top-level list). Read this as: **Extension
  = the integration/provider, Endpoint = one configured contact point through it**
  (e.g. one SchoolMessenger account, one specific outbound-email address, one Quick
  URL).
- **IP Speakers** (`ip_speakers`) plus their **SIP Parameters**
  (`ip_speaker_sip_parameters`, per-speaker registration detail), bulk **Jobs**
  (`ip_speaker_jobs`, provisioning/firmware operations across many speakers at once),
  and global **Settings** (`ip_speaker_settings`, a singleton, not a list — applies
  instance-wide) are the paging-hardware side of the device layer.
- **API Devices** (`api_devices`) are devices that receive notifications via direct
  API calls rather than any of the above — used for custom integrations that poll or
  get pushed to programmatically.
- **CUCM Clusters** (`cucm_clusters`) are *not themselves recipients* — they're the
  **telephony backbone** that makes phone-based Device Groups possible at all. A
  cluster's `axlAddress`/`axlUser` (AXL API access), `tftpAddress` (device config
  provisioning), and `capfAddress` (certificate trust for secure phones) are what let
  Fusion discover and register CUCM-managed phones as Device Group members in the
  first place. `cucmClusterFusionServers` links a cluster to the specific on-prem
  Fusion Server appliance(s) that maintain that connection — if phones from a
  cluster aren't showing up as available devices, this linkage (not the Device
  Group's own config) is usually where to look first.
- **Gateways** (`gateways`) are paging/LPI and other physical gateway hardware,
  filterable by model/connection status — the network-side complement to IP
  Speakers for legacy analog/overhead paging systems.
- **Failover Pairs** (`failover_pairs`) define redundancy relationships between
  Fusion Servers, so a CUCM cluster's connectivity survives a single appliance
  outage.

---

## Layer 3: The message itself

**Message Templates** (`message_templates`) are the actual payload definition, and
they're more configurable than "recipients + text." Nearly every field has three
variants — the base field (`body`), a `*Customizable` flag (can the person
*triggering* this template override it at send time?), and a `*Display` flag (is
this field shown to the triggering user at all?). This means a single Message
Template can be authored once by an admin but behave differently depending on who
triggers it and what they're allowed to touch — worth checking the `*Customizable`
flags before assuming a template's `body`/`distributionListIds`/etc. are what
actually gets sent every time.

Beyond recipients (Layer 1) and delivery mechanism (Layer 2), a template also
references:

- **`confirmationRequestId`** → a **Confirmation Request** (`confirmation_requests`):
  defines `dynamicReplies` (reply options presented to recipients),
  `escalationRules` (what happens if nobody confirms in time), and
  `expirationPeriod`. This is what turns a one-way broadcast into a
  confirm/acknowledge workflow.
- **`notificationProfileId`** → a **Notification Profile** (`notification_profiles`):
  a reusable bundle of delivery `settings` (e.g. retry/channel behavior), with a
  `default` flag marking the instance's fallback profile.
- **`incidentPlanId`** → ties a template to an **Incident Plan** (Layer 4) so
  triggering it can start a structured, potentially multi-step incident rather than
  a single message.
- **`ttsVoiceId`** / `ttsType` / `ttsCustomContent` / `ttsSpeed` → the **TTS Voices**
  (`tts_voices`), **TTS Lexicons** (`tts_lexicons`, custom pronunciation overrides —
  e.g. teaching the engine to say a building name or acronym correctly), and **TTS
  Defaults** (`tts_defaults`, the per-locale/type fallback voice if a template
  doesn't specify one) all feed into audio generation for phone/speaker delivery.
- **`messageTemplateCancellationIds`** — other templates this one can cancel when
  sent, for "all clear"-style follow-ups.

---

## Layer 4: What causes a message to fire

This is the layer with the most moving parts, and where most of this round's new
resources live.

### Scenarios — the manual/interactive trigger
**Scenarios** (`scenarios`) are what a person or a hotkey/DTMF trigger invokes
directly. A Scenario carries its own `messages` (embedded message configuration,
distinct from a standalone Message Template — a Scenario can define ad hoc content
rather than pointing at a saved template), an optional `incidentPlanId` (so
triggering it starts a full Incident Plan, not just a single send), a
`confirmationType` (`none`/`pin`/`prompt` — does triggering it require a PIN or
verbal confirmation first?), and `deleteRuleActions` (Rule Actions run when the
scenario is deactivated/cleared, the mirror image of activation). `phoneNumber` on a
Scenario means it can *also* be triggered by dialing in directly, independent of
DialCast (see below) — worth distinguishing these two when both exist on an
instance, since they're separate mechanisms that can look similar operationally.

### Incident Plans → Incidents — the structured/multi-step trigger
**Incident Plans** (`incident_plans`) are the reusable *template* for a
multi-stage event (e.g. "Active Threat" with distinct Lockdown → All Clear stages).
`allowMultipleActiveIncidents` controls whether a second instance can run
concurrently; `rostering` embeds the Roll Call / accountability check-in
configuration directly on the plan (this is why there's no separate flat
"Roll Call" resource — it isn't a standalone object, it's plan-level config plus
per-incident/per-user runtime state).

**Incidents** (`incidents`) are the *runtime instances* — an actual execution of an
Incident Plan, or a Scenario/DialCast/etc. that happened to be configured with an
`incidentPlanId`. Treat `incident_plans` as configuration and `incidents` as
operational history/live state — the distinction this tool's README flags for
`active_callaware_calls` too (config vs. runtime data).

### DialCast — the CUCM-dial-pattern trigger (worked example below)
See the dedicated worked example section — this is the flow you specifically asked
about.

### Inbound CAP / Email / RSS — external-system triggers
- **Inbound CAP Rules** (`inbound_cap_rules`) match incoming Common Alerting
  Protocol messages (the standard used by NOAA/weather services and many emergency
  systems) against a rule, and fire a `messageTemplateId` when matched.
- **Inbound Email** (`inbound_email`) monitors a mailbox; each item also has a
  nested `outboundRules` list (`/inbound-email/{id}/outbound-rules`, not separately
  crawled by this tool) governing auto-replies — worth checking manually if an
  inbound-email trigger seems to also be auto-responding unexpectedly.
- **Inbound RSS Feeds** (`inbound_rss_feeds`) poll a feed URL and fire a
  `messageTemplateId`/`distributionListIds` combination when new matching entries
  appear.

All three are "the outside world causes an InformaCast message," as opposed to
DialCast/Scenarios which are "someone inside the phone system causes one."

### Bell Schedules + Ring Lists — recurring scheduled audio
**Bell Schedules** (`bell_schedules`) carry `bellScheduleEntries` (the actual
ring times) and `bellScheduleExceptions` (holiday/early-release overrides), scoped
to a date range (`startDate`/`endDate`) and repeating pattern (`numWeeks`). **Ring
Lists** (`ring_lists`) are a separate, simpler `entries`-based construct for what
actually rings, often referenced by a Bell Schedule entry — think of Bell Schedules
as "when" and Ring Lists as "what" for recurring bell/tone audio.

### Scheduled Notifications + Clear Device Schedules — one-off/recurring sends
**Scheduled Notifications** (`scheduled_notifications`) directly carry
`messageTemplateId` plus `deviceGroupIds`/`distributionListIds` and a `schedule`
(with `nextFireTime` precomputed) — the most literal "send this template to these
recipients at this time" resource, no indirection through a Scenario or Incident
Plan. **Clear Device Schedules** (`clear_device_schedules`) are the inverse: they
clear/reset devices (e.g. turn off a strobe or clear a persistent display) on a
schedule rather than sending new content.

### Rule Actions — the generic glue underneath most of the above
**Rule Actions** (`rule_actions`) are a reusable `rule`/`action`
condition-and-effect pair (with `ruleResources`/`actionResources` specifying what
they read and touch). They aren't a trigger type on their own — they're the
mechanism *other* triggers attach to: DialCast Dialing Configurations have their own
nested Rule Actions (`/dialcast-dialing-configurations/{id}/rule-actions`), and
Scenarios reference them via `deleteRuleActions`. If a DialCast pattern or Scenario
fires but doesn't seem to do everything expected, checking its Rule Actions (not
just its `notification` field) is the next step.

---

## Layer 5: The telephony/on-prem backbone

Two parallel worlds exist here, and it matters which one a given instance is
actually running:

- **Cloud-native**, confirmed against the real OpenAPI spec: `cucm_clusters`,
  `failover_pairs`, `extensions`, `gateways`, `tts_voices`/`tts_lexicons`.
- **Legacy on-prem** (`/Fusion/V1/...` paths, *not present in the cloud spec at
  all*, so unverified by this tool's spec-validation pass): `fusion_recipient_groups`,
  `fusion_callaware`, `fusion_m2m`, `fusion_night_bell`. These exist on Fusion Server
  appliances directly and may or may not apply to a given cloud-connected instance —
  check against that specific on-prem server's own API Explorer, not the cloud one.

### CallAware specifically
CallAware ties into CUCM call monitoring/redirect behavior, but **its configuration
is not exposed as a flat, readable list in the cloud API** — the only direct GET
endpoint is `active_callaware_calls` (live, currently-monitored calls, not saved
config). CallAware's actual redirect *rules* only show up indirectly, as
"dependents" lookups nested under Device Groups, Distribution Lists, and Message
Templates (e.g. `/device-groups/{id}/callaware-call-redirect-dependents`) — which
tell you *which of those resources a given redirect rule references*, not a
standalone list of the rules themselves. In practice: if you need to audit CallAware
behavior, you'll be cross-referencing those dependents endpoints per-resource rather
than reading one table, or checking the legacy on-prem `fusion_callaware` path if
this instance still exposes it.

---

## Layer 6: Multi-tenancy & access

- **Facilities** (`facilities`) — see the top of this document.
- **Security Groups** (`security_groups`) — access control; `policyId` +
  `superGroup` + member `userIds`.
- **Identity Providers** (`identity_providers`) — SSO/SAML configuration; governs how
  Users authenticate, separate from what they're authorized to do once in.
- **Users** (`users`) — sit at the intersection of Layer 1 (can be a recipient) and
  this layer (can be a security principal via Security Group membership) — the same
  User ID can appear in both a Distribution List's members and a Security Group's
  `userIds`, for different reasons.

---

## Layer 7: Monitoring vs. operational/runtime data

Don't confuse these — they answer different questions:

- **Alarms** (`alarms`) — **system health**: is the platform itself working
  (`status`, `alarmThresholds`, `muted`)? This is about Fusion's own operational
  state, not about any message that was sent.
- **Incidents** (`incidents`) and **Active CallAware Calls**
  (`active_callaware_calls`) — **runtime/operational history**: what has actually
  happened (an incident that ran, a call currently being monitored) — these are
  facts about events, not configuration you'd edit.

If you're trying to understand *why* something is configured a certain way, look at
the config resources (Layers 1–5). If you're trying to understand *what actually
happened or is happening right now*, these are the ones to check instead.

---

## Layer 8: Platform administration

- **Settings** (`settings`) — a **singleton**, not a list: instance-wide
  configuration that applies globally rather than per-resource.
- **Brandings** (`brandings`) — visual/organizational branding applied across
  templates and interfaces.
- **Load Definitions** (`load_definitions`) — bulk import job definitions, typically
  how Distribution Lists/Users get synced from an external system (tying back to a
  Distribution List's `loadSourceId` in Layer 1).

---

## Worked example: DialCast, end to end

This is the flow you specifically asked about, spelled out field-by-field —
corrected here against the actual `notification` sub-schema, since an earlier
version of this section guessed wrong about how it works:

1. Someone dials a number that CUCM routes to Fusion (via the CUCM Cluster/AXL
   integration in Layer 5 — CUCM has to know to send this call to Fusion at all,
   which is itself dependent on that cluster's registration being healthy).
2. Fusion checks the dialed pattern against every **DialCast Dialing Configuration**
   (`dial_cast`)'s `dialingPatternRegex`. `endpointIds`/`includeEndpointIds`
   optionally scope which endpoints/extensions this configuration even applies to,
   so the same dialed digits can mean different things from different extensions.
3. If matched, the configuration's **`notification`** field determines what's sent —
   and this is more flexible than "one embedded message": it carries
   `messageTemplateId` (send a shared Message Template, in which case editing that
   template elsewhere *does* affect this configuration), **or** its own direct
   `distributionListIds`/`deviceGroupIds` (an ad hoc recipient set with no shared
   template involved), **or** `messageTemplateNamePattern`/`recipientNamePattern`
   (resolve the template/recipient dynamically by name pattern — e.g. derived from
   the extension or caller ID, so one configuration can behave differently depending
   on context rather than always sending the same fixed content). Which of these a
   given configuration actually uses is worth checking explicitly rather than
   assuming — don't assume it's self-contained *or* that it's template-based without
   looking.
4. There's also a **`fallbackNotification`** (a separate, similarly-shaped object)
   sent if the primary one fails (e.g. bad TTS content or a delivery failure).
5. Separately, the configuration's nested **Rule Actions**
   (`/dialcast-dialing-configurations/{id}/rule-actions`) can trigger additional
   effects beyond just the notification — commonly a webhook call — this is the
   piece easy to miss if you only look at the `notification` field and wonder why
   something else also happened (or didn't).
6. If the embedded notification uses TTS, `ttsVoiceId`/lexicon/defaults from Layer 3
   apply the same way they would for a standalone Message Template.
7. `authentication` on the configuration can require the caller to authenticate
   (e.g. enter a PIN) before the pattern is honored at all — worth checking before
   assuming a pattern match alone is sufficient.
8. **DialCast Phone Exceptions** (`dial_cast_phone_exceptions`) are a *separate*
   mechanism from the dialed-pattern matching above — they key on
   `callingPartyRegex` (the **caller's** number, not the dialed number) and override
   authentication/system-greeting behavior (`authenticationOverrideEnabled`,
   `systemGreetingEnable`/`systemGreetingBreakKeyEnable`) for calls from matching
   numbers. Think of them as "this caller gets different call-handling behavior,"
   not "this dialed pattern doesn't apply to certain lines."

The operationally important takeaway: **don't assume you know which recipient
mechanism a DialCast configuration uses without checking** — it can be a shared
Message Template, direct recipients, or a dynamic name-pattern lookup, and each
behaves differently when something elsewhere in the instance changes.

## Worked example: Scenario → Incident Plan → Incident

1. A **Scenario** is triggered (hotkey, DTMF, phone dial-in via its own
   `phoneNumber`, or API).
2. If the Scenario has an `incidentPlanId`, this starts a new **Incident** governed
   by that **Incident Plan**'s rules — including `allowMultipleActiveIncidents` (can
   a second one start while this is active?) and its embedded `rostering` config (is
   there a roll-call/accountability step?).
3. The Scenario's own `messages` field (not a separate Message Template) defines
   what's actually sent at each stage, unless the Incident Plan's stages reference
   templates directly.
4. `deleteRuleActions` on the Scenario fire when it's deactivated/cleared — the
   "stand down" side of the same mechanism DialCast uses for its Rule Actions.
5. The running Incident shows up in `incidents` as it happens; once resolved, it
   remains there as historical/operational record, separate from the Incident
   Plan's own (unchanged) configuration.

## Worked example: Inbound Email → Message Template

1. **Inbound Email** (`inbound_email`) polls a monitored mailbox.
2. A message matching its rules triggers whatever `messageTemplateId` /
   recipient combination is configured — this one *does* reference a standalone
   **Message Template** (unlike DialCast's embedded `notification`), so editing that
   template elsewhere *does* change what this trigger sends.
3. The nested `outboundRules` (`/inbound-email/{id}/outbound-rules`) can fire an
   automated reply back to the original sender, independent of whatever broadcast
   the inbound rule itself triggered — two separate effects from one inbound email.

---

## Practical troubleshooting checklist

When something "isn't working" or "isn't showing up," in rough order of what to
check first:

1. **Facility** — is the resource you're looking for even in the Facility context
   you're checking? (`--facility-id` / `--test <key> --facility-id <id>`)
2. **Recipients** — for a Message Template or trigger with no obvious recipients,
   remember it can carry *any combination* of five recipient types (Layer 1) — check
   all five id fields, not just `distributionListIds`.
3. **Dynamic vs. static membership** — if a Device Group's apparent membership
   doesn't match expectations, check `filters`/`filterType`/`logicalExpression`
   before assuming `additions`/`exclusions` are the whole story; filter-based
   membership isn't resolved by this tool (or generally by the API) into a flat
   list.
4. **Embedded vs. referenced content** — DialCast and Scenarios carry their own
   embedded message content (`notification` / `messages`); most other triggers
   reference a shared `messageTemplateId`. Editing a Message Template only affects
   the latter.
5. **Rule Actions** — for DialCast and Scenarios, the visible `notification`/
   `messages` field is not the whole story; check nested Rule Actions for additional
   effects.
6. **Config vs. runtime** — `incident_plans`/`fusion_callaware`-style resources are
   configuration; `incidents`/`active_callaware_calls`/`alarms` are operational
   state. Don't expect to edit the latter, and don't expect the former to tell you
   what's happening *right now*.
7. **Cloud vs. legacy on-prem** — if a `/Fusion/V1/...`-prefixed resource in this
   tool's output is unexpectedly empty/404, that's expected for a cloud-only
   instance; it's only meaningful for on-prem Fusion Server deployments, and even
   then wasn't independently verified against the OpenAPI spec.

## Quick reference

| Resource key(s) | What it is | Directly relates to |
|---|---|---|
| `facilities` | Multi-tenancy boundary | Scopes every other resource |
| `users`, `distribution_lists`, `device_groups`, `areas_of_interest`, `collaboration_groups` | Recipients | Referenced by Message Templates, Scheduled Notifications |
| `security_groups`, `identity_providers` | Access control / auth | Governs Users, not a recipient type |
| `extensions` → devices/endpoints | Integration channels | Non-phone/speaker delivery (email, SMS, SchoolMessenger, etc.) |
| `ip_speakers`, `ip_speaker_*` | Paging hardware | Device Group membership, IP Speaker Settings apply globally |
| `cucm_clusters`, `failover_pairs`, `gateways` | Telephony backbone | Makes phone/speaker Device Groups possible at all |
| `message_templates` | The payload | References recipients, `confirmation_requests`, `notification_profiles`, `tts_*`, `incident_plans` |
| `confirmation_requests`, `notification_profiles` | Delivery behavior | Referenced by Message Templates |
| `tts_voices`, `tts_lexicons`, `tts_defaults` | Audio generation | Referenced by Message Templates and DialCast notifications |
| `scenarios` | Manual/interactive trigger | Embeds own messages; optional `incident_plans` link |
| `incident_plans` → `incidents` | Structured trigger → runtime instance | Config vs. operational history |
| `dial_cast`, `dial_cast_phone_exceptions` | CUCM dial-pattern trigger | Embedded notification + nested Rule Actions |
| `inbound_cap_rules`, `inbound_email`, `inbound_rss_feeds` | External-system triggers | Reference `message_templates` |
| `bell_schedules`, `ring_lists` | Recurring scheduled audio | Bell Schedule entries often reference Ring Lists |
| `scheduled_notifications`, `clear_device_schedules` | One-off/recurring send or clear | Reference `message_templates` + recipients directly |
| `rule_actions` | Generic condition→action glue | Attached to DialCast, Scenarios |
| `alarms` | System health | Not related to message content |
| `active_callaware_calls` | Live call monitoring state | CallAware config lives in "dependents" lookups, not a flat list |
| `settings`, `brandings`, `load_definitions` | Platform admin | Global singleton config, org branding, bulk import jobs |
