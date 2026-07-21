# Changelog

All notable changes to this tool, in reverse chronological order (most recent first).
There's no formal release/versioning process — this just tracks what changed and why,
since several rounds of this were genuine bugs worth understanding rather than routine
feature work.

## Instance-specific operational narrative (`--format narrative`)

`docs/RESOURCE_MODEL.md` explained how these resources relate to each other
*conceptually* (schema-level, no instance data). This adds the same explanation
populated with a real instance's **actual configured resources and their real
cross-references** — resolved names, not raw IDs, tracing genuine chains rather
than describing the schema abstractly.

### Added
- `informacast_report/narrative.py`: builds a rendering-agnostic content model
  (sections, paragraphs, tables, callout notes) from a crawled `InstanceReport`,
  covering Recipients, Devices, Messaging, Automation/Triggers (DialCast fully
  traced: pattern → endpoint → notification → recipients/template → Rule Actions →
  fallback), Telephony, Monitoring, and Admin — plus a concrete "Things Worth
  Verifying" section that flags specific anomalies by name (empty Device Groups,
  Distribution Lists no Message Template references, unhealthy unmuted Alarms),
  not a generic checklist.
- `informacast_report/render_narrative_docx.py`: renders that model as a Word
  document styled to match `docs/templates/Claude_Word_Template.docx` (the
  project's house style for operational documents) — exact colors/sizes
  extracted and verified programmatically (Title 26pt bold `#1F5C99`; Heading
  1/2 `#2E74B5`; Heading 3 `#1F4D78`; table headers bold white on `#1F5C99`
  with zebra-striped data rows).
- `--format narrative` wired into `main.py`, defaulting to `narrative.docx`.
- Deliberately summarizes rather than dumps: group-level facts (a Device Group's
  membership mechanism, device-type counts) are included, full membership lists
  are not — large tables cap at 40 rows with an explicit "+N more" note.

### Fixed
- The crawler's name-resolution index only covered top-level resources, not
  nested ones (Sites → Buildings/Floors/Zones, Extensions → Devices/Endpoints) —
  a reference like a Gateway's `buildingId` or a DialCast's `endpointIds` would
  never have resolved to a name. `_resolve_references` now folds `sites_tree`
  and `extension_tree` into the index too, and the resulting index is exposed
  on `FacilityReport.name_index` for reuse.
- Added a nested crawl for each DialCast configuration's own Rule Actions
  (`/dialcast-dialing-configurations/{id}/rule-actions`, confirmed via the
  OpenAPI spec) — previously not crawled at all, so the "does this config have
  anything beyond its visible notification" question had no answer.
- **Corrected a real inaccuracy in `docs/RESOURCE_MODEL.md`** discovered while
  building this: it claimed a DialCast configuration's embedded `notification`
  is "self-contained, never a template reference." Checking the actual schema
  showed it can be a shared Message Template (`messageTemplateId`), direct
  recipients (`distributionListIds`/`deviceGroupIds`), *or* a dynamic
  name-pattern lookup (`messageTemplateNamePattern`/`recipientNamePattern`) —
  and DialCast Phone Exceptions key on the **caller's** number
  (`callingPartyRegex`), not carve-outs from the dialed pattern as previously
  described. Both corrected with the real field names.

## OpenAPI spec validation: Facility/Domain fix, corrected paths, new resource types

A real OpenAPI spec (`spec.json`, from Singlewire's API Explorer) became available and
was used to validate every existing resource and fix the previous round's guesses.

### Fixed
- **Critical: there is no `/domains` endpoint anywhere in this API.** The real
  multi-tenancy concept is called **"Facility"** (`/v1/facilities`, header
  `x-singlewire-facility`) — not `/domains` / `x-singlewire-domain`, which this tool had
  sent since the very first version. Because `/domains` always 404s, the crawler had
  silently treated every instance as if it had no multi-tenancy, meaning **any instance
  actually using multiple Facilities only ever had its default facility crawled**, with
  every other facility's data silently missing. Renamed throughout: `list_domains` →
  `list_facilities`, `DomainReport` → `FacilityReport`, `InstanceReport.domains` →
  `.facilities`, `--domain-id` → `--facility-id`, JSON `domains` key → `facilities`,
  `domain_scoped` field → `facility_scoped`.
- `/settings` was silently returning **zero items on every run** — it's a singleton
  config object, not a paginated list (no `limit`/`start` params, no `data` envelope).
  Added `is_singleton` support: singleton resources are now fetched via
  `client.get_one()` and wrapped as a 1-item list instead of being run through
  `paged_get()`, which could never have found a `data` key to read from.
- Corrected wrong paths from the previous round's guesses:
  - `identity_providers`: `/identity-providers` → `/idps`
  - `clear_device_schedules`: `/clear-device-schedules` → `/clear-devices-schedules`
  - `tts_voices` / `tts_lexicons`: legacy `/Fusion/V1/Admin/...` → cloud-native
    `/tts-voices` / `/tts-lexicons`
  - `dial_cast`: `/dial-cast-dialing-configurations` → `/dialcast-dialing-configurations`
    (no hyphen in "dialcast")
  - `dial_cast_phone_exceptions`: `/dial-cast-phone-exceptions` →
    `/dialcast-phone-exceptions`
  - `inbound_cap` → renamed `inbound_cap_rules`, path `/inbound-cap` → `/inbound-cap-rules`
  - `inbound_rss` → renamed `inbound_rss_feeds`, path `/inbound-rss` → `/inbound-rss-feeds`
- Removed resources that don't exist in the real API at all: `call_aware_redirects`
  (only `/active-callaware-calls`, live state, actually exists), top-level `endpoints`
  (only exists nested per-Extension), `recipient_group_tags`, `desktop_notifiers`,
  `roll_call` (real API calls this "Rostering," nested per-incident/per-user, no flat
  list), `paging_gateways` (replaced by the generic `/gateways`).

### Added
- New resources confirmed via the spec: `active_callaware_calls`, `gateways`,
  `ip_speaker_settings` (singleton), `ip_speaker_sip_parameters`, `ip_speaker_jobs`,
  `tts_defaults`, `incidents`, `facilities` (the Domain→Facility rename itself).
- Nested Extension → Devices/Endpoints crawl (`Crawler._crawl_extension_tree`), since
  Endpoints (Conference Call, Outbound Email, Quick URL, SchoolMessenger, WordPress,
  Script) don't exist as a flat top-level resource — they're only reachable per-Extension.
  New "Extensions Detail" section in both the HTML and DOCX reports.
- `spec.json`-driven validation workflow documented in the README's "Extending it"
  section for anyone adding a resource in the future.

## Granular notification/device/recipient resources (first pass — later corrected above)

Added DialCast, DialCast Phone Exceptions, Inbound CAP/Email/RSS triggers, CallAware call
redirects, a generic Endpoints resource, Recipient Group Tags, IP Speakers, Desktop
Notifiers, Paging Gateways, and Roll Call — as best-effort path guesses based on
Singlewire's naming conventions, explicitly flagged as unconfirmed pending access to the
real API Explorer. Most of these guesses turned out wrong or nonexistent once the real
spec became available (see above).

## Cursor pagination made the universal default

### Changed
- `ResourceSpec.pagination_style` default changed from `"offset"` to `"cursor"` for
  every resource, based on confirmed real-world behavior (later independently verified
  against the spec: 192/192 list endpoints use `limit`/`start`, zero use `offset`).
- `--pagination-style` can now override the style for an **entire crawl**, not just
  `--test` — applied via `dataclasses.replace()` across every resolved `ResourceSpec`
  without mutating the canonical definitions in `resources.py`.

## JSON output (`--format json`) and `--unit` resource filter

### Added
- `--format json`: renders the same crawled data as HTML/DOCX but as structured JSON.
  Prints to stdout if `--output` isn't given (log output goes to stderr, so piping
  stdout into `jq` etc. stays clean).
- `--unit KEY[,KEY...]`: filters to exact resource key(s) as shown by `--list-resources`,
  more precise than `--groups` (which pulls a whole category). Takes precedence over
  `--groups` if both are given.

## Pagination generalized across every resource + the `list_device_groups.py` cursor bug

A separate standalone script (`list_device_groups.py`) surfaced a bug where an endpoint
that ignores its pagination-advancement parameter just silently re-serves the same page
forever. The fix (duplicate-id tracking, exact-repeat detection, defensive dedup) was
built there first, then generalized into this tool's `api_client.paged_get`.

### Fixed
- **Duplicate/stuck-page detection**, added to `paged_get`: tracks every item id seen;
  raises immediately if a page's ids exactly match the previous page's (pagination is
  provably stuck), or warns and filters duplicates if a page only partially overlaps.
- `pagination_style` field added per-resource ("offset" vs "cursor"); `device_groups`
  set to `"cursor"`, confirmed via the standalone script's investigation into why
  Device Groups kept duplicating page 1.

### Added
- `--verbose` / `--debug` also extended to cover the duplicate-detection warnings.

## Pagination bug fixes (three rounds — each looked similar but had a different cause)

- **Bug 1 — stopping too early**: the original logic decided when to stop paging
  *solely* based on the response envelope's `partial`/`next` fields. If an endpoint
  didn't set those reliably, the loop silently stopped after page 1 and truncated
  results with no error or warning.
- **Bug 2 — never stopping (overshooting `total`)**: the fix for Bug 1 changed the stop
  condition to "keep paging if *any* of `partial`/`next`, a full page, or `total`
  suggests more data" — but that meant an endpoint that *always* claims `partial: true`
  and *always* returns a full page (regardless of real dataset size) never stopped,
  since those two signals kept outvoting `total`. Symptom: still fetching full 100-item
  pages at `offset=9000` when the API had reported `total=448` the entire time. Fixed by
  making `total` an authoritative ceiling: once reached, stop and truncate the final
  page if needed, regardless of what `partial`/`next` claim.
- Added a pagination **loop guard** (`MAX_PAGES_PER_RESOURCE`): aborts a resource with a
  clear error after 2,000 pages without reaching a stopping point, instead of hanging
  indefinitely.

## `--test` diagnostic mode, `--list-resources`

### Added
- `--test KEY[,KEY...]`: fetches one or more specific resources in isolation — no report
  generated — and prints pages fetched, items collected, the API's advertised total, and
  a clear `MISMATCH` warning if they disagree. Built specifically to answer "is
  pagination actually grabbing everything for this one resource" without digging through
  a full crawl or `--debug` output from an entire run.
- `--list-resources`: prints every known resource key, its path, and pagination style.
- `--domain-id` (later renamed `--facility-id`, see above) to restrict `--test` to one
  specific facility.

## `--verbose` / `--debug` logging levels

### Added
- Three-tier logging via a custom `PROGRESS` level between `INFO` and `DEBUG`:
  default (milestones only), `--verbose` (per-resource progress with item counts and
  timing), `--debug` (full HTTP request/response tracing and per-page pagination
  internals). Built specifically to make it possible to spot loops or logic errors
  without reading through code.

## Initial scaffold

The original version of this tool: a Python CLI (`main.py`) that authenticates to the
Fusion API with a bearer token, crawls a declarative registry of resources
(`resources.py`), resolves cross-referenced IDs to names, and renders the result as an
HTML or Word (.docx) report. Included a generic paginated GET client, per-resource error
isolation (a 403/404 on one resource doesn't break the rest of the crawl), and a
mocked-data smoke test harness (`smoke_test.py`) since no live instance was available to
test against directly.
