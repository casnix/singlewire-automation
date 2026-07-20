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


def fake_paged_get(self, path, domain_id=None, extra_params=None, limit=100, stats=None):
    data = FAKE_DATA.get(path)
    if data is None:
        raise ApiError(f"404 Not Found for {path}", 404, path)
    if stats is not None:
        stats.update(pages=1, items=len(data), advertised_total=len(data), envelope="paginated")
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


if __name__ == "__main__":
    main()
    test_logging_levels()
    test_pagination_loop_guard()
    test_unreliable_partial_next_flags()
    test_diagnostic_mode()
    print("\nALL SMOKE TESTS PASSED")
