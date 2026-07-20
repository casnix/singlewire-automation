"""Not part of the shipped tool — a throwaway harness to validate that the
crawler + both renderers work end-to-end without needing a real Fusion
instance. Uses canned responses shaped like real API payloads.
"""
import logging
import sys
sys.path.insert(0, ".")

from unittest.mock import patch, MagicMock

from informacast_report.api_client import ApiError, FusionApiClient, MAX_PAGES_PER_RESOURCE
from informacast_report.config import Settings
from informacast_report.crawler import Crawler
from informacast_report.logging_utils import setup_logging
from informacast_report.resources import RESOURCES

FAKE_DATA = {
    "/domains": [],  # no domains in this fake instance
    "/users": [
        {"id": "u1", "name": "Jane Admin", "email": "jane@example.com", "isLocked": False},
        {"id": "u2", "name": "API Service Account", "email": "svc@example.com", "isLocked": False},
    ],
    "/security-groups": [
        {"id": "sg1", "name": "Superuser", "userIds": ["u1"]},
    ],
    "/distribution-lists": [
        {"id": "dl1", "name": "All Staff", "createdAt": "2024-01-01T00:00:00Z"},
        {"id": "dl2", "name": "Front Desk", "createdAt": "2024-02-01T00:00:00Z"},
    ],
    "/message-templates": [
        {
            "id": "mt1", "name": "Severe Weather", "subject": "Severe weather alert",
            "body": "Take shelter now.", "distributionListIds": ["dl1"],
        },
    ],
    "/scenarios": [
        {"id": "sc1", "name": "Panic Button", "locationEnabled": True},
    ],
    "/sites": [
        {"id": "site1", "name": "Main Campus"},
    ],
    "/sites/site1/buildings": [
        {"id": "b1", "name": "West Building"},
    ],
    "/sites/site1/buildings/b1/floors": [
        {"id": "f1", "name": "1st Floor"},
    ],
    "/sites/site1/buildings/b1/floors/f1/zones": [
        {"id": "z1", "name": "Lobby"},
    ],
    "/alarms": [
        {"id": "al1", "type": "fusion_server_red", "status": "OK", "muted": False},
    ],
    "/alarms/al1/actions": [],
    "/alarms/al1/events": [],
}


def fake_paged_get(self, path, domain_id=None, extra_params=None, limit=100, stats=None, pagination_style="offset"):
    data = FAKE_DATA.get(path)
    if data is None:
        raise ApiError(f"404 Not Found for {path}", 404, path)
    if stats is not None:
        stats.update(pages=1, items=len(data), advertised_total=len(data), envelope="paginated", pagination_style=pagination_style)
    yield from data


def main():
    settings = Settings(token="fake-token", base_url="https://api.icmobile.singlewire.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    with patch.object(FusionApiClient, "paged_get", fake_paged_get):
        crawler = Crawler(client, specs=RESOURCES)
        report = crawler.run()

    print(f"Crawled {len(report.domains)} domain(s)")
    for dr in report.domains:
        for key, result in dr.resources.items():
            status = f"ERROR: {result.error}" if result.error else f"{len(result.items)} item(s)"
            print(f"  {key}: {status}")
        print(f"  sites_tree: {dr.sites_tree}")
        print(f"  alarm_details: {dr.alarm_details}")

    from informacast_report.render_html import render_html
    html = render_html(report)
    with open("/tmp/smoke_report.html", "w") as f:
        f.write(html)
    print(f"\nHTML report: {len(html)} chars written to /tmp/smoke_report.html")
    assert "Jane Admin" in html
    assert "Severe Weather" in html
    assert "All Staff" in html  # resolved from distributionListIds
    assert "Lobby" in html

    from informacast_report.render_docx import render_docx
    render_docx(report, "/tmp/smoke_report.docx")
    print("DOCX report written to /tmp/smoke_report.docx")

    print("\nSMOKE TEST PASSED (basic crawl + render)\n")


def test_logging_levels():
    """Confirm --verbose surfaces per-resource progress lines and --debug
    additionally surfaces raw HTTP/pagination detail, using the real
    paged_get pagination logic against a mocked HTTP layer (not a mocked
    paged_get) so we're actually exercising the pagination code path.
    """
    print("=" * 70)
    print("Testing --verbose output")
    print("=" * 70)
    setup_logging(verbose=True, debug=False)
    _run_two_page_fetch()

    print()
    print("=" * 70)
    print("Testing --debug output")
    print("=" * 70)
    setup_logging(verbose=False, debug=True)
    _run_two_page_fetch()


def _run_two_page_fetch():
    """Fetch a resource that spans two pages, using a real mocked HTTP
    session so pagination, not just the crawler, is exercised.
    """
    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    page1 = MagicMock(status_code=200, ok=True, content=b"x" * 500)
    page1.json.return_value = {
        "total": 3, "partial": True, "previous": None, "next": "1",
        "data": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
    }
    page2 = MagicMock(status_code=200, ok=True, content=b"x" * 200)
    page2.json.return_value = {
        "total": 3, "partial": False, "previous": "0", "next": None,
        "data": [{"id": "c", "name": "C"}],
    }
    client.session.get = MagicMock(side_effect=[page1, page2])

    items = list(client.paged_get("/fake-resource"))
    assert [i["id"] for i in items] == ["a", "b", "c"], items


def test_pagination_loop_guard():
    """A server that never stops reporting partial=True should be caught by
    MAX_PAGES_PER_RESOURCE rather than looping forever.
    """
    print()
    print("=" * 70)
    print("Testing pagination loop guard (should raise ApiError, not hang)")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    def infinite_page(*args, **kwargs):
        resp = MagicMock(status_code=200, ok=True, content=b"x")
        resp.json.return_value = {
            "total": 999999, "partial": True, "previous": None, "next": "1",
            "data": [{"id": "loop", "name": "Loop"}],
        }
        return resp

    client.session.get = MagicMock(side_effect=infinite_page)

    try:
        import informacast_report.api_client as api_client_mod
        original = api_client_mod.MAX_PAGES_PER_RESOURCE
        api_client_mod.MAX_PAGES_PER_RESOURCE = 5
        try:
            list(client.paged_get("/looping-resource"))
            raise AssertionError("Expected ApiError from loop guard, got no error")
        except ApiError as exc:
            print(f"Caught expected ApiError: {exc}")
        finally:
            api_client_mod.MAX_PAGES_PER_RESOURCE = original
    finally:
        pass

    print("Loop guard test PASSED")


def test_unreliable_partial_next_flags():
    """Regression test for the reported bug: an endpoint that returns full
    pages of data but never sets `partial`/`next` correctly (e.g. always
    partial=False, next=None, even when more data exists). The OLD logic
    would stop after page 1 and silently truncate. The FIXED logic must
    keep paging because a full page came back, and additionally cross-check
    against `total`.
    """
    print()
    print("=" * 70)
    print("Testing pagination with UNRELIABLE partial/next flags (the reported bug)")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    # 250 total users, page size 100, server ALWAYS reports partial=False
    # and next=None regardless of whether more data remains -- exactly the
    # kind of unreliable envelope that caused silent truncation before.
    all_users = [{"id": f"u{i}", "name": f"User {i}"} for i in range(250)]

    def broken_envelope_page(url, params=None, headers=None, timeout=None):
        offset = params["offset"]
        limit = params["limit"]
        page_data = all_users[offset: offset + limit]
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 100)
        resp.json.return_value = {
            "total": len(all_users),
            "partial": False,   # <-- always False, even mid-pagination (buggy server)
            "previous": None,
            "next": None,        # <-- always None, even mid-pagination (buggy server)
            "data": page_data,
        }
        return resp

    client.session.get = MagicMock(side_effect=broken_envelope_page)

    stats = {}
    items = list(client.paged_get("/users", limit=100, stats=stats))

    print(f"  Requested: 250 users across a server that never sets partial/next correctly")
    print(f"  Collected: {len(items)} items in {stats.get('pages')} page(s)")

    assert len(items) == 250, (
        f"BUG STILL PRESENT: only collected {len(items)}/250 items — pagination "
        f"stopped early because it trusted partial/next alone."
    )
    assert stats.get("pages") == 3, f"Expected 3 pages (100+100+50), got {stats.get('pages')}"
    assert [i["id"] for i in items] == [f"u{i}" for i in range(250)], "Items out of order or missing"

    print("  ✓ All 250 items collected across 3 pages despite unreliable partial/next flags.")
    print("Unreliable-envelope regression test PASSED")


def test_diagnostic_mode():
    """Exercise the --test resource diagnostic path directly."""
    print()
    print("=" * 70)
    print("Testing --test diagnostic mode against a mocked instance")
    print("=" * 70)

    from informacast_report.diagnostics import test_resource

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    call_log = []

    def router(url, params=None, headers=None, timeout=None):
        call_log.append(url)
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 50)
        if url.endswith("/domains"):
            resp.json.return_value = []  # no domains in use
        elif url.endswith("/users"):
            offset = params["offset"]
            data = [{"id": "u1", "name": "Jane"}, {"id": "u2", "name": "Bob"}][offset: offset + params["limit"]]
            resp.json.return_value = {"total": 2, "partial": False, "next": None, "data": data}
        else:
            resp.status_code = 404
            resp.ok = False
        return resp

    client.session.get = MagicMock(side_effect=router)

    ok = test_resource(client, "users")
    assert ok is True, "Expected test_resource to report success for consistent data"

    # Also confirm an unknown key is handled gracefully rather than raising.
    ok_unknown = test_resource(client, "not_a_real_resource")
    assert ok_unknown is False

    print("Diagnostic mode test PASSED")


def test_total_overshoot_bug():
    """Regression test for the bug reported this time: an endpoint that
    always claims partial=True (and always returns a full page) no matter
    how far past its own declared `total` you go — exactly the pasted log
    (total=448 but still fetching full pages at offset=9000). The OLD OR-
    based logic would keep going because partial/full-page always won. The
    FIXED logic must treat `total` as a hard ceiling: stop (truncating the
    final page if needed) the moment total_yielded reaches it, regardless
    of what partial/next claim.
    """
    print()
    print("=" * 70)
    print("Testing pagination OVERSHOOTING total (this session's reported bug)")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    TOTAL = 448
    LIMIT = 100
    call_count = {"n": 0}

    def always_full_page(url, params=None, headers=None, timeout=None):
        # Server ALWAYS returns a full 100-item page and ALWAYS claims
        # partial=True / next=<something>, no matter the offset -- it does
        # not actually respect offset for real data past a point, it just
        # keeps manufacturing full pages. This is what caused the runaway
        # in the pasted log (still full pages at offset=9000, total=448).
        call_count["n"] += 1
        offset = params["offset"]
        data = [{"id": f"u{offset + i}", "name": f"User {offset + i}"} for i in range(LIMIT)]
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 100)
        resp.json.return_value = {
            "total": TOTAL,
            "partial": True,     # <-- always true, forever, regardless of offset
            "previous": None,
            "next": str(offset + LIMIT),  # <-- always advances, never null
            "data": data,
        }
        return resp

    client.session.get = MagicMock(side_effect=always_full_page)

    stats = {}
    items = list(client.paged_get("/users", limit=LIMIT, stats=stats))

    print(f"  Server: always full pages, always partial=True, total={TOTAL}")
    print(f"  HTTP calls made: {call_count['n']}")
    print(f"  Items collected: {len(items)}  (pages: {stats.get('pages')})")
    print(f"  Truncated:       {stats.get('truncated')}")

    assert call_count["n"] == 5, (
        f"BUG STILL PRESENT: made {call_count['n']} HTTP calls instead of the expected 5 "
        f"(ceil(448/100)) — pagination is overshooting total again, same as the pasted log "
        f"running past offset=9000."
    )
    assert len(items) == TOTAL, f"Expected exactly {TOTAL} items, got {len(items)}"
    assert stats.get("truncated") is True, "Expected the final page to be flagged as truncated"
    # Confirm no duplicate/fabricated ids beyond the real total leaked through.
    assert items[-1]["id"] == f"u{TOTAL - 1}", f"Unexpected last item: {items[-1]}"

    print("  ✓ Stopped at exactly 5 pages / 448 items instead of running past total.")
    print("Total-overshoot regression test PASSED")


def test_stuck_page_repeat_bug():
    """Regression test for the bug reported this round: an endpoint that
    ignores `offset` entirely and just keeps re-serving page 1, forever.
    Same root cause as the device-groups cursor bug, but for offset-based
    pagination. Must raise immediately (not wait for MAX_PAGES) since the
    exact same ids repeating is an unambiguous signal.
    """
    print()
    print("=" * 70)
    print("Testing STUCK pagination: server ignores offset, always returns page 1")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    call_count = {"n": 0}

    def always_page_one(url, params=None, headers=None, timeout=None):
        call_count["n"] += 1
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 100)
        resp.json.return_value = {
            "total": 448,  # server claims a large total...
            "partial": True,
            "previous": None,
            "next": "100",
            # ...but always returns the exact same 100 ids regardless of offset.
            "data": [{"id": f"u{i}", "name": f"User {i}"} for i in range(100)],
        }
        return resp

    client.session.get = MagicMock(side_effect=always_page_one)

    try:
        list(client.paged_get("/users"))
        raise AssertionError("Expected ApiError for stuck pagination, got none")
    except ApiError as exc:
        print(f"  Caught expected ApiError after {call_count['n']} call(s): {exc}")
        assert call_count["n"] == 2, (
            f"Expected exactly 2 HTTP calls (page 1, then page 2 detected as an exact "
            f"repeat) before aborting, got {call_count['n']} — it kept going instead of "
            f"failing fast."
        )
        assert "exact same" in str(exc) and "stuck" in str(exc)

    print("Stuck-page regression test PASSED")


def test_partial_duplicate_overlap():
    """A page that partially overlaps with previously-seen ids (rather than
    being an exact repeat) should be filtered/de-duplicated and logged as a
    warning, not silently accepted and not treated as fatal.
    """
    print()
    print("=" * 70)
    print("Testing PARTIAL duplicate overlap across pages (warn + dedupe, not fatal)")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    # Page 1: users 0-4. Page 2: overlaps on users 3-4, then adds 5-7 (new).
    # Page 3: empty -> stop. No `total` field at all on this endpoint.
    pages = [
        {"partial": True, "next": "1", "data": [{"id": f"u{i}"} for i in range(0, 5)]},
        {"partial": True, "next": "2", "data": [{"id": f"u{i}"} for i in range(3, 8)]},
        {"partial": False, "next": None, "data": []},
    ]
    call = {"n": 0}

    def router(url, params=None, headers=None, timeout=None):
        idx = min(call["n"], len(pages) - 1)
        call["n"] += 1
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 50)
        resp.json.return_value = pages[idx]
        return resp

    client.session.get = MagicMock(side_effect=router)

    stats = {}
    items = list(client.paged_get("/users", limit=5, stats=stats))
    ids = [i["id"] for i in items]

    print(f"  Collected ids: {ids}")
    print(f"  Stats: {stats}")

    assert ids == [f"u{i}" for i in range(8)], f"Expected u0..u7 with no dupes, got {ids}"
    assert stats.get("duplicates") == 2, f"Expected 2 duplicates filtered (u3, u4 reappearing), got {stats.get('duplicates')}"
    assert stats.get("raw_items") == 10, f"Expected 10 raw items received (5+5), got {stats.get('raw_items')}"
    assert stats.get("items") == 8, f"Expected 8 unique items, got {stats.get('items')}"

    print("  ✓ Duplicates correctly filtered, unique count and raw count both correct.")
    print("Partial-overlap regression test PASSED")


def test_cursor_style_pagination():
    """Confirm pagination_style='cursor' actually works end-to-end: no
    offset param sent, `start` echoes back the prior `next` value, and it
    correctly stops when `next` goes null -- mirroring the standalone
    list_device_groups.py fix, but inside the main tool's generic client.
    """
    print()
    print("=" * 70)
    print("Testing pagination_style='cursor' (device-groups style)")
    print("=" * 70)
    setup_logging(verbose=False, debug=False)

    settings = Settings(token="fake-token", base_url="https://fake.example.com/api/v1", timeout=30)
    client = FusionApiClient(settings)

    pages = [
        {"total": 5, "next": "tokA", "data": [{"id": "g1"}, {"id": "g2"}]},
        {"total": 5, "next": "tokB", "data": [{"id": "g3"}, {"id": "g4"}]},
        {"total": 5, "next": None, "data": [{"id": "g5"}]},
    ]
    call_params = []

    def router(url, params=None, headers=None, timeout=None):
        call_params.append(dict(params))
        idx = len(call_params) - 1
        resp = MagicMock(status_code=200, ok=True, content=b"x" * 50)
        resp.json.return_value = pages[idx]
        return resp

    client.session.get = MagicMock(side_effect=router)

    items = list(client.paged_get("/device-groups", limit=2, pagination_style="cursor"))

    print(f"  Params sent per call: {call_params}")
    print(f"  Collected: {[i['id'] for i in items]}")

    assert [i["id"] for i in items] == ["g1", "g2", "g3", "g4", "g5"]
    # Crucially: no 'offset' key should ever be sent in cursor mode, and
    # 'start' should be absent on call 1, then exactly the prior 'next'.
    assert "offset" not in call_params[0]
    assert "start" not in call_params[0]
    assert call_params[1]["start"] == "tokA"
    assert call_params[2]["start"] == "tokB"

    print("  ✓ Cursor-style pagination correctly echoed 'next' back as 'start', no offset sent.")
    print("Cursor-style pagination test PASSED")


def test_device_groups_resource_is_cursor_style():
    """Guard against silently regressing the device_groups ResourceSpec back
    to offset-style pagination -- this was the concrete, confirmed fix.
    """
    print()
    print("=" * 70)
    print("Confirming resources.py: device_groups is configured as cursor-style")
    print("=" * 70)
    from informacast_report.resources import get_resource
    spec = get_resource("device_groups")
    assert spec.pagination_style == "cursor", (
        f"device_groups regressed to pagination_style={spec.pagination_style!r} -- "
        f"this endpoint is confirmed to need cursor-style pagination."
    )
    print("  ✓ device_groups is configured as pagination_style='cursor'")


if __name__ == "__main__":
    main()
    test_logging_levels()
    test_pagination_loop_guard()
    test_unreliable_partial_next_flags()
    test_total_overshoot_bug()
    test_stuck_page_repeat_bug()
    test_partial_duplicate_overlap()
    test_cursor_style_pagination()
    test_device_groups_resource_is_cursor_style()
    test_diagnostic_mode()
    print("\nALL SMOKE TESTS PASSED")
