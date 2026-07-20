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
  Items collected:      250
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

### The pagination bug this replaced

Earlier versions of this tool decided when to stop paging **solely** based
on the response envelope's `partial`/`next` fields (`{total, partial,
previous, next, data}`). That's fragile: if a given endpoint doesn't set
those fields reliably — which happens in practice, even against documented
APIs — the loop would quietly stop after the first page and silently
truncate results, with no error or warning.

The fix cross-checks three independent signals and keeps paging if *any* of
them suggests there's more data:
1. The documented `partial`/`next` fields, as before.
2. Whether a **full page** came back (`len(data) == limit`) — a short or
   empty page is the only truly reliable "this was the last page" signal
   for classic offset-based pagination.
3. The envelope's `total` field, if present, compared against how many
   items have been collected so far.

It also now warns explicitly if the final item count doesn't match the
API's advertised `total`, so a mismatch is visible even during a normal
full-report run, not just when using `--test`.

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
