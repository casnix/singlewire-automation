# InformaCast Fusion Configuration Report

Pulls the full configuration of a Singlewire InformaCast **Fusion** instance via its
cloud REST API and renders it as an HTML, Word (.docx), or PDF report.

This is a **read-only** tool — it only ever issues `GET` requests. It never creates,
updates, or deletes anything in your instance.

## What it does

1. Authenticates to the Fusion API with a bearer token.
2. Discovers every Domain the token's user can act in (if the instance uses Domains).
3. For each domain, walks a registry of resources (Users, Distribution Lists, Message
   Templates, Scenarios, Sites/Buildings/Floors/Zones, Bell Schedules, Security Groups,
   Alarms, CUCM Clusters, etc.) — see `informacast_report/resources.py` for the full list.
4. Follows pagination on every list endpoint until exhausted.
5. Resolves cross-referenced IDs (e.g. a Message Template's `distributionListIds`) to
   human-readable names where possible.
6. Renders everything into a single report via Jinja2 (HTML), python-docx (Word), or
   HTML→PDF (WeasyPrint).

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
# timing, plus per-domain start/end. Good for watching a long run.
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

# Restrict a test to one specific Domain ID instead of every domain:
python main.py --test users --domain-id 159e9330-232a-11e4-8e47-685b358ea847
```

### Logging levels at a glance

| Flag | What you see |
|---|---|
| *(none)* | Milestones only: crawl start, one line per domain, final summary, output path. |
| `--verbose` | Adds: one line per resource (path, item count, elapsed time), site-tree/alarm-detail fetch progress, reference-resolution progress, render timing. |
| `--debug` | Adds: every HTTP GET (URL, params, status, latency, response size), retry/backoff decisions, and per-page pagination detail (offset, `partial`, `next`, `total`). |

### Testing a specific resource (`--test`)

If you suspect a specific area isn't coming through completely — e.g. "I
think Users isn't paginating" — `--test` fetches just that one resource,
across every domain, and prints exactly what happened instead of making you
dig through a full crawl or report:

```
$ python main.py --test users

======================================================================
Testing resource: users  (label: 'Users', group: access)
Path: /users   domain_scoped: True
======================================================================

-- Domain: (no domain / instance-level) --
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

The `--test <resource>` diagnostic mode (see above) reports pages fetched,
unique items collected, raw items received, duplicates filtered, the
advertised total, and whether truncation happened — the fastest way to see
which of these three failure modes (if any) a given endpoint is hitting.

There's also a built-in pagination **loop guard**: if any single resource
pages past 2,000 requests without the API reporting completion, the tool
aborts that resource with a clear error rather than hanging indefinitely —
a strong signal of either a bug in how a response is being read or an
unexpected API behavior on that endpoint. `--debug` will show you exactly
what the last few pages looked like leading up to it.

## Extending it

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

Adding a new endpoint (Singlewire adds these fairly often — check the API's change log
at the bottom of https://api-docs.icmobile.singlewire.com/ or the OpenAPI docs at
https://openapi.icmobile.singlewire.com/) is usually just adding one more entry here;
the crawler, pagination, ID-resolution, and rendering are all generic.

## Known limitations / things to verify against your instance

- **Endpoint paths are based on Singlewire's published API docs** as of this writing.
  Singlewire ships breaking changes periodically (see their change log) — if a resource
  404s, check whether the path moved.
- **On-prem/legacy resources** (things that live on a local Fusion server appliance
  rather than purely in the cloud — CUCM telephony config, LPI paging, some plugin
  configs) are exposed under an `/Fusion/V1/...` prefix in newer API versions rather
  than the plain `/v1/...` cloud paths used for cloud-native resources. A few
  representative entries are stubbed in `resources.py`; you'll likely need to adjust
  these against your own instance's API Explorer.
- **Domains**: if your instance doesn't use Domains, the domain loop just runs once
  with the default context — no configuration needed either way.
- **Rate limiting / large instances**: the client retries on 429/5xx with backoff, but
  a very large instance (thousands of users/devices) will take a while and make a lot
  of requests. Consider narrowing `--groups` for iterative testing.
