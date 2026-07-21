# InformaCast Fusion Configuration Report

Pulls the full configuration of a Singlewire InformaCast **Fusion** instance via its
cloud REST API and renders it as an HTML, Word (.docx), JSON, or PDF data report --
or as an instance-specific operational narrative (`--format narrative`, also .docx)
explaining how the pulled resources actually relate to each other.

This is a **read-only** tool — it only ever issues `GET` requests. It never creates,
updates, or deletes anything in your instance.

> See [`CHANGELOG.md`](./CHANGELOG.md) for the full history of what's changed and why —
> several rounds of real bugs were found and fixed (pagination, a Facility/Domain
> mix-up, wrong resource paths), and it's worth knowing which ones affect you.
>
> See [`docs/RESOURCE_MODEL.md`](./docs/RESOURCE_MODEL.md) for how the resources this
> tool pulls relate to each other operationally, *conceptually* — e.g. how a DialCast
> dial pattern actually leads to a message going out, or how a Message Template's five
> different recipient fields interact. For the same explanation populated with a real
> instance's actual configured data, generate one directly: `--format narrative`.

## What it does

1. Authenticates to the Fusion API with a bearer token.
2. Discovers every Facility the token's user can act in (if the instance uses multiple
   Facilities — this API's real multi-tenancy concept; see "The Facility/Domain bug" below).
3. For each facility, walks a registry of resources (Users, Distribution Lists, Message
   Templates, Scenarios, Sites/Buildings/Floors/Zones, Bell Schedules, Security Groups,
   Alarms, CUCM Clusters, DialCast, Inbound CAP/Email/RSS triggers, Gateways, IP Speakers,
   etc.) — see `informacast_report/resources.py` for the full list, or run
   `python main.py --list-resources`.
4. Follows pagination on every list endpoint until exhausted; fetches singleton
   config objects (e.g. `/settings`) directly rather than as a list.
5. Resolves cross-referenced IDs (e.g. a Message Template's `distributionListIds`) to
   human-readable names where possible.
6. Separately crawls each Site's Buildings/Floors/Zones, each Alarm's Actions/Events, and
   each Extension's Devices/Endpoints, since none of those exist as flat top-level lists.
7. Renders everything into a single report via Jinja2 (HTML), python-docx (Word),
   HTML→PDF (WeasyPrint), or structured JSON.

### Granular notification/device/recipient resources

These were added specifically to cover finer-grained config that a plain "Messaging" or
"Recipients" section wouldn't otherwise surface, and every path below is confirmed
against the real OpenAPI spec (see `CHANGELOG.md` for what that process caught):

| Key | Covers |
|---|---|
| `dial_cast`, `dial_cast_phone_exceptions` | DialCast dial-in triggers and their exceptions |
| `inbound_cap_rules` | Inbound Common Alerting Protocol triggers |
| `inbound_email` | Inbound email triggers (each with nested outbound reply rules) |
| `inbound_rss_feeds` | Inbound RSS feed triggers |
| `active_callaware_calls` | CallAware's only GET endpoint — live monitored-call state, not saved config |
| `gateways` | Paging/LPI and other gateway types |
| `ip_speakers`, `ip_speaker_sip_parameters`, `ip_speaker_jobs`, `ip_speaker_settings` | IP Speaker devices, their SIP registration, bulk jobs, and global settings |
| `tts_voices`, `tts_lexicons`, `tts_defaults` | Text-to-speech voices, custom pronunciation lexicons, and per-locale defaults |
| `incidents` | Runtime Incident instances (as opposed to `incident_plans`, which are the templates) |

Run `python main.py --list-resources` for the complete, current list across every group.

## Before you run this

You need:

- **A Fusion API bearer token.** In the Fusion admin console: Users → (a service/reporting
  account) → User Tokens → Add. Give it a descriptive name. Copy the token immediately —
  it's only shown once. As of late 2023, Singlewire no longer issues non-expiring tokens;
  new tokens default to a 1-year expiration, so plan to rotate this.
- **A user/role with broad read access.** A token inherits the permissions of the user
  it belongs to. For a complete report, that user's role should have read access across
  Users, Security Groups, Message Templates, Distribution Lists, Devices, Sites, Scenarios,
  Telephony/CUCM, and Alarms. If the account is scoped narrowly, sections it can't see
  will simply come back empty rather than erroring the whole run.
- **Network access** from wherever you run this script to
  `https://api.icmobile.singlewire.com` (or your instance's equivalent endpoint, if
  different — some deployments use a region-specific hostname; check your admin console
  or ask Singlewire support if unsure).

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and paste in your token
```

## Usage

```bash
# HTML report (default)
python main.py --format html --output report.html

# Word report
python main.py --format docx --output report.docx

# PDF report (requires WeasyPrint + its system dependencies, see requirements.txt)
python main.py --format pdf --output report.pdf

# Limit to specific resource groups (comma-separated) instead of everything
python main.py --format html --groups users,messaging,recipients

# Progress logging: one line per resource fetched, with item counts and
# timing, plus per-facility start/end. Good for watching a long run.
python main.py --format html --verbose

# Full trace: everything --verbose shows, plus raw HTTP status/timing per
# call, retry/backoff decisions, and per-page pagination internals (offset,
# partial, next). Use this to chase down a suspected loop or logic error.
python main.py --format html --debug

# See every resource key available (for --test / --groups)
python main.py --list-resources

# Test one specific resource/area in isolation -- no report is generated.
# Prints pages fetched, items collected, and the API's advertised total,
# and flags a clear MISMATCH if pagination didn't actually get everything.
python main.py --test users

# Test more than one at once, with full HTTP tracing:
python main.py --test users,message_templates,scenarios --debug

# Restrict a test to one specific Facility ID instead of every facility:
python main.py --test users --facility-id 159e9330-232a-11e4-8e47-685b358ea847

# Experimentally try the OTHER pagination style for a resource (cursor is
# the default now), without editing resources.py first:
python main.py --test users --pagination-style offset

# JSON output -- prints to stdout if --output isn't given, so it's
# pipeable straight into jq/other tools instead of requiring a file:
python main.py --format json
python main.py --format json --output report.json

# --unit restricts to exact resource key(s) shown by --list-resources --
# more precise than --groups (which pulls a whole category). Great paired
# with --format json to grab just one resource's raw data:
python main.py --format json --unit users
python main.py --format json --unit users,message_templates | jq '.facilities[0].resources.users.items'

# Instance-specific operational narrative (Word doc): explains how THIS
# instance's configured resources actually relate to each other -- e.g.
# which DialCast pattern fires which template to which recipients -- rather
# than a raw data dump:
python main.py --format narrative --output ops_narrative.docx
```

### Instance-specific operational narrative (`--format narrative`)

`docs/RESOURCE_MODEL.md` explains how these resources relate to each other
*conceptually* (schema-level, no instance data). `--format narrative` generates
the same kind of explanation, but populated with **this instance's actual
configured resources and their real cross-references** — resolved names, not
raw IDs, tracing genuine chains like "DialCast configuration X matches pattern
Y and sends Message Template Z to Distribution List W."

It deliberately **summarizes rather than dumps**: a Device Group's name,
membership mechanism, and device-type counts are included, but full membership
lists (every user, every individual device) are not — that's what
`--format json`/`html`/`docx` are for. It also flags concrete anomalies by
name — an empty Device Group, a Distribution List no Message Template
references, an unhealthy unmuted Alarm — rather than a generic checklist.

Output is a `.docx` styled to match `docs/templates/Claude_Word_Template.docx`
(the house style for operational documents in this project — see that file
for the source colors/sizes if you need to adjust `render_narrative_docx.py`).

```bash
python main.py --format narrative --output ops_narrative.docx
python main.py --format narrative --groups automation --output triggers_only.docx
```

### JSON output and `--unit`

`--format json` renders the same crawled data as the HTML/DOCX reports, but
as structured JSON instead of a formatted document -- useful for piping into
`jq`, diffing between two runs, or feeding into another script. If
`--output` isn't given, it prints to stdout (log messages go to stderr, so
piping stdout doesn't pick up any noise); if `--output` is given, it writes
to that file like the other formats.

`--unit` is a second way to narrow what gets crawled, alongside `--groups`:

| Flag | Granularity | Example |
|---|---|---|
| `--groups` | Whole category | `--groups access` pulls Users, Security Groups, Identity Providers, etc. |
| `--unit` | Exact resource(s) | `--unit users` pulls *only* Users |

`--unit` takes the same keys shown by `--list-resources` and takes
precedence over `--groups` if both are given. This is the fastest way to
pull just the data you need:

```bash
python main.py --format json --unit users,distribution_lists --output subset.json
```

The JSON structure mirrors the report's facility/resource organization:

```json
{
  "base_url": "...",
  "generated_at": "...",
  "facilities": [
    {
      "facility": null,
      "resources": {
        "users": {
          "key": "users", "label": "Users", "path": "/users",
          "group": "access", "pagination_style": "cursor", "notes": null,
          "error": null,
          "pagination_stats": {"pages": 1, "items": 2, "raw_items": 2,
                                "duplicates": 0, "advertised_total": 2,
                                "truncated": false},
          "item_count": 2,
          "items": [ {"id": "u1", "name": "..."}, ... ]
        }
      },
      "sites_tree": [],
      "alarm_details": []
    }
  ]
}
```

### Logging levels at a glance

| Flag | What you see |
|---|---|
| *(none)* | Milestones only: crawl start, one line per facility, final summary, output path. |
| `--verbose` | Adds: one line per resource (path, item count, elapsed time), site-tree/alarm-detail fetch progress, reference-resolution progress, render timing. |
| `--debug` | Adds: every HTTP GET (URL, params, status, latency, response size), retry/backoff decisions, and per-page pagination detail (offset, `partial`, `next`, `total`). |

### Testing a specific resource (`--test`)

If you suspect a specific area isn't coming through completely — e.g. "I
think Users isn't paginating" — `--test` fetches just that one resource,
across every facility, and prints exactly what happened instead of making you
dig through a full crawl or report:

```
$ python main.py --test users

======================================================================
Testing resource: users  (label: 'Users', group: access)
Path: /users   facility_scoped: True
======================================================================

-- Facility: (no facility / instance-level) --
  Envelope shape:       paginated
  Pages fetched:        3
  Unique items:         250
  API-advertised total: 250
  Time taken:           1.42s
  ✓ Item count matches the API's advertised total.
  Sample item fields:   ['id', 'name', 'email', 'isLocked', ...]
  First item:           {'id': 'u1', ...}
  Last item:            {'id': 'u250', ...}
```

If pagination stops early, this prints a `⚠ MISMATCH` line telling you the
collected count vs. the API's advertised total, and points you at `--debug`
for the full per-page trace. The exit code is non-zero if any tested
resource errored or mismatched, so `--test` is also usable as a quick
CI-style sanity check.

### The pagination bugs this replaced

This tool's pagination has been fixed three times now — worth documenting
all three, since each one looks similar on the surface but has a different
root cause and a different fix.

**Bug 1 — stopping too early.** The original logic decided when to stop
paging *solely* based on the response envelope's `partial`/`next` fields. If
an endpoint didn't set those reliably, the loop would quietly stop after the
first page and silently truncate results, with no error or warning.

**Bug 2 — never stopping (overshooting `total`).** The fix for Bug 1 changed
the stop condition to "keep paging if *any* of `partial`/`next`, a full page,
or `total` suggests more data" — safe against under-fetching, but it meant an
endpoint that *always* reports `partial: true` and *always* returns a full
page (regardless of the real dataset size) would never stop, because those
two signals kept outvoting `total`. This showed up as pagination still
fetching full 100-item pages at `offset=9000` when the API had reported
`total=448` on every single page.

**Bug 3 — duplicate/stuck pages (the same root cause as a separate
`list_device_groups.py` script's cursor-token bug, but for offset-based
pagination).** Some endpoints silently ignore the `offset` parameter — or
don't honor it correctly — and just keep re-serving the same page of data,
with the *same* `partial`/`next`/`total` values every time, so nothing in
the envelope flags it. Bug 2's fix alone doesn't catch this: if the repeated
page's item count keeps pushing the running total upward (even though it's
the same items over and over), the total-ceiling logic will eventually
"succeed" with a list full of duplicates and missing everything past
whichever page is stuck.

**The current logic**, in priority order:

1. **Duplicate/stuck-page detection**, checked first: every item id seen so
   far is tracked. If a page's ids *exactly* match the previous page's ids,
   pagination is unambiguously stuck (the offset had zero effect) — this
   raises immediately rather than waiting for the page cap. If a page only
   *partially* overlaps with ids already collected, that's logged as a
   `WARNING` and the duplicate items are filtered out of the result, so
   callers never see duplicate records; items without an `id` field can't be
   deduplicated this way and are always treated as new.
2. **Total-as-ceiling**: once `total` *unique* items have been collected
   (unique — duplicates from #1 don't count), pagination stops there,
   truncating the final page if needed, regardless of what `partial`/`next`
   claim. A truncation event is logged as a `WARNING`.
3. **No-total fallback**: only when an endpoint reports no `total` at all
   does the tool fall back to the `partial`/`next`/full-page heuristics.

**Bug 4 — assuming one pagination *mechanism* fits every endpoint.** Bugs 1–3
above were all about *when to stop* — but a separate, standalone script
(`list_device_groups.py`) confirmed that `/device-groups` doesn't use
offset-based pagination at all: it uses cursor-token pagination, where you
must echo back the previous response's `next` value verbatim as a `start`
query parameter — a computed offset is silently ignored (you just get page 1
back, forever, which looks exactly like Bug 3's symptoms). Since this main
tool's `device_groups` resource entry was using the same offset-based
`paged_get` as everything else, it had the identical bug — and follow-up
testing confirmed the same was true for `/users` as well, and very likely
every other resource.

`paged_get` takes a `pagination_style` argument (`"offset"` or `"cursor"`),
and each `ResourceSpec` in `resources.py` declares which one it needs.
**`"cursor"` is now the default for every resource** — confirmed against
the real API, not a guess. If a future resource turns out to genuinely need
offset-style pagination instead, set `pagination_style="offset"` on that
specific entry in `resources.py`.

You can also override the style for an entire run from the CLI, without
touching `resources.py` — useful to quickly compare or to force a whole run
back to offset-style if something changes:

```bash
# Override just the resource(s) being tested:
python main.py --test users --pagination-style offset

# Override EVERY resource in a full crawl:
python main.py --format json --pagination-style offset
```

The `--test <resource>` diagnostic mode (see above) reports pages fetched,
unique items collected, raw items received, duplicates filtered, the
advertised total, whether truncation happened, and which pagination style
was used — the fastest way to see which of these four failure modes (if
any) a given endpoint is hitting.

There's also a built-in pagination **loop guard**: if any single resource
pages past 2,000 requests without the API reporting completion, the tool
aborts that resource with a clear error rather than hanging indefinitely —
a strong signal of either a bug in how a response is being read or an
unexpected API behavior on that endpoint. `--debug` will show you exactly
what the last few pages looked like leading up to it.

**Update:** all of the above is now independently confirmed against the real
OpenAPI spec (`spec.json`, obtained from Singlewire's API Explorer) — every
single list endpoint in the spec (192 checked) uses `limit`/`start`
("cursor" style), and **zero** use `offset`. The original "offset" guess this
tool started with was wrong for every endpoint, not just the ones the
duplicate/stuck-page detector happened to catch.

### The Facility/Domain bug

Separately from pagination, this tool's multi-tenancy handling had a real bug
from the start, only caught once the real OpenAPI spec was available to check
against: **there is no `/domains` endpoint anywhere in this API.** The real
multi-tenancy concept is called **"Facility"** — listed at `/v1/facilities`,
scoped per-request via the header `x-singlewire-facility` (or a `facility`
query/body parameter) — not `/domains` / `x-singlewire-domain`, which is what
this tool sent from the beginning.

Because `/domains` always 404s, `list_domains()` (as it was then) silently
caught that as "this instance doesn't use multi-tenancy" and skipped
facility-scoping entirely on every run. For a single-facility instance this
is harmless — there's nothing to miss. But **any instance actually using
multiple Facilities has only ever had its default facility crawled**, with
every other facility's Users, Distribution Lists, Message Templates, etc.
silently absent from the report, with no error or warning to indicate
anything was missing.

This is now fixed throughout: `list_facilities()` calls `/v1/facilities`, the
`x-singlewire-facility` header is sent correctly, and the report/JSON output
use "Facility" terminology (`--facility-id` on the CLI, `facilities` in JSON,
etc.) to match the real API rather than a guessed concept.

If you're on a single-facility instance, this changes nothing about your
output. If you manage multiple Facilities, previous reports generated by this
tool should be treated as incomplete and re-generated.

## Extending it

**Any document this tool generates or is designed to generate should follow the
house style in [`docs/templates/Claude_Word_Template.docx`](./docs/templates/Claude_Word_Template.docx)**
— that's where the `--format narrative` colors/sizes/table styling in
`render_narrative_docx.py` came from (Title 26pt bold `#1F5C99`; Heading 1/2
`#2E74B5`; Heading 3 `#1F4D78`; table headers bold white on `#1F5C99` with
zebra-striped rows). If you add another document output format, pull the exact
values from that template the same way (open it with `python-docx` and read
`run.font.size`/`.color.rgb` directly, or unzip it and check
`word/theme/theme1.xml` for the base palette) rather than guessing new ones.

Every resource lives in `informacast_report/resources.py` as a small declarative entry:

```python
ResourceSpec(
    key="message_templates",
    label="Message Templates",
    path="/message-templates",
    group="messaging",
    name_field="name",
    ref_fields=["distributionListIds", "confirmationRequestId", "deviceGroupIds"],
),
```

Two other shapes exist beyond a normal paginated list:

- **Singleton resources** (a single config object, not a list — e.g. `/settings`):
  set `is_singleton=True`. The crawler fetches these with `client.get_one()` instead of
  `client.paged_get()` and wraps the result as a 1-item list so it renders like
  everything else.
- **Nested-only resources** that don't exist as a flat top-level list (e.g. Endpoints,
  which only exist per-Extension at `/extensions/{id}/endpoints`) aren't declared as a
  `ResourceSpec` at all — they're fetched specially in `crawler.py`, the same way
  Sites→Buildings→Floors→Zones and Alarms→Actions/Events are. See
  `Crawler._crawl_extension_tree` for the pattern to copy if you find another one.

**If you have access to the OpenAPI spec** (`spec.json`, downloadable from
https://openapi.icmobile.singlewire.com/ or via Singlewire support), validate any new
entry against it before assuming a path is right — this is exactly how the Facility bug
above, several wrong path guesses, and the `/settings` singleton bug were all caught:

```python
import json
spec = json.load(open("spec.json"))
paths = spec["paths"]
"/v1/your-guessed-path" in paths          # does it exist at all?
paths["/v1/your-guessed-path"]["get"]["parameters"]   # limit/start? offset? neither (singleton)?
```

Adding a new endpoint (Singlewire adds these fairly often — check the API's change log
at the bottom of https://api-docs.icmobile.singlewire.com/ or the OpenAPI docs at
https://openapi.icmobile.singlewire.com/) is usually just adding one more entry here;
the crawler, pagination, ID-resolution, and rendering are all generic.

## Known limitations / things to verify against your instance

- **Most paths are now confirmed against the real OpenAPI spec**, not guessed. Every
  resource's `path` in `resources.py` was checked directly against `spec.json` (obtained
  from Singlewire's API Explorer at https://openapi.icmobile.singlewire.com/) as of this
  writing — see "The Facility/Domain bug" below for how much this mattered. Singlewire
  ships changes periodically, so if a resource 404s in the future, check whether the
  path moved; `python main.py --test <key> --debug` is the fastest way to check one.
- **Some DialCast fields aren't resolved to names.** A DialCast Dialing Configuration's
  message/notification reference is nested inside a sub-object (`notification`) rather
  than a flat `messageTemplateId` field, so it isn't cross-referenced to a name the way
  most other resources are — see the raw item in the report for details.
- **Facility-level rate limiting**: `active_callaware_calls` and `incidents` return
  live/runtime data rather than static configuration and can be large on an active
  instance. Consider testing these individually first: `python main.py --test incidents`.
- **On-prem/legacy resources** (things that live on a local Fusion server appliance
  rather than purely in the cloud — some plugin configs) are exposed under an
  `/Fusion/V1/...` prefix and aren't present in the cloud OpenAPI spec at all, so they
  couldn't be validated the same way. A few representative entries
  (`fusion_recipient_groups`, `fusion_callaware`, `fusion_m2m`, `fusion_night_bell`) are
  stubbed in `resources.py`, explicitly flagged as unverified against the cloud spec —
  check these against your own on-prem Fusion server's API Explorer if you rely on them.
- **Rate limiting / large instances**: the client retries on 429/5xx with backoff, but
  a very large instance (thousands of users/devices) will take a while and make a lot
  of requests. Consider narrowing `--groups` or `--unit` for iterative testing.
