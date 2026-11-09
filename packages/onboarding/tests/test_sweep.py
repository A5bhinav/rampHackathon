"""Spec 0041: the read-only sweep (acceptance items 4 to 6). A local stand-in for the Ramp API records
every request, so the tests see exactly what the sweep reads and that it writes nothing."""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from dialback_onboarding.anchor import e164
from dialback_onboarding.cli import main
from dialback_onboarding.sweep import FICTIONAL, load_sources

PACKAGE = Path(__file__).resolve().parents[1]
DS = uuid.UUID(int=7)
SECRET = "test-secret"
SINCE, AS_OF = "2026-10-01T00:00:00+00:00", "2026-11-09T12:00:00+00:00"
REAL_LOOKING = "+1201" + "5551234"     # built in pieces so the file scan below does not see it


def vid(n: int) -> str:
    return str(uuid.UUID(int=n))


VENDORS = [
    {"id": vid(1), "name": "Old Supplier Co", "state": "CA", "created_at": "2025-01-05T00:00:00+00:00"},
    {"id": vid(2), "name": "Acme Fasteners LLC", "state": "CA", "created_at": "2026-11-01T09:00:00+00:00"},
    {"id": vid(3), "name": "Brightline Office Supply Inc", "state": "NY", "created_at": "2026-11-02T09:00:00+00:00"},
    {"id": vid(4), "name": "Cobalt Freight LLC", "state": None, "created_at": "2026-11-03T09:00:00+00:00"},
]
CONTACTS = {vid(2): [{"phone_number": "+14155550120"}], vid(3): [{"phone_number": "+12125550150"}]}
SOURCES = {"vendors": {
    "Acme Fasteners LLC": {
        "tax_id": "00-0000101",
        "registry": {"legal_name": "Acme Fasteners LLC", "state": "CA", "tax_id": "00-0000101",
                     "status": "active", "filed_at": "2012-04-17", "phone": "(415) 555-0120"},
        "website": {"domain": "acme-fasteners.example", "registered_at": "2012-05-01", "phone": "415-555-0120"}},
    "Brightline Office Supply Inc": {
        "tax_id": "00-0000102",
        "registry": {"legal_name": "Brightline Office Supply, Inc.", "state": "NY", "tax_id": "000000102",
                     "status": "Active", "filed_at": "2009-09-09", "phone": "(212) 555-0151"},
        "website": {"domain": "brightline.example", "registered_at": "2010-01-01", "phone": "212.555.0151"}},
}}


@dataclasses.dataclass
class FakeRamp:
    url: str = ""
    next_base: str = ""
    vendors: list = dataclasses.field(default_factory=lambda: [dict(v) for v in VENDORS])
    contacts: dict = dataclasses.field(default_factory=lambda: {k: list(v) for k, v in CONTACTS.items()})
    fail: dict = dataclasses.field(default_factory=dict)       # path prefix -> (status, body)
    requests: list = dataclasses.field(default_factory=list)  # (method, path, authorization)


def _handler(state: FakeRamp):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status: int, body) -> None:
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            state.requests.append(("GET", self.path, self.headers.get("Authorization")))
            url = urlsplit(self.path)
            for prefix, (status, body) in state.fail.items():
                if url.path.startswith(prefix):
                    return self._send(status, body)
            if url.path == "/developer/v1/vendors":
                start = parse_qs(url.query).get("start", [None])[0]
                ids = [v["id"] for v in state.vendors]
                i = 0 if start is None else ids.index(start) + 1
                chunk = state.vendors[i:i + 2]
                more = i + 2 < len(state.vendors)
                nxt = f"{state.next_base}/developer/v1/vendors?start={chunk[-1]['id']}" if more else None
                return self._send(200, {"data": chunk, "page": {"next": nxt}})
            if m := re.fullmatch(r"/developer/v1/vendors/([^/]+)/contacts", url.path):
                return self._send(200, {"data": state.contacts.get(m[1], []), "page": {"next": None}})
            return self._send(404, {"error": "not_found"})

        def _refuse(self):
            state.requests.append((self.command, self.path, self.headers.get("Authorization")))
            self._send(405, {"error": "read_only"})

        do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    return Handler


def _serve():
    state = FakeRamp()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    state.url = state.next_base = f"http://127.0.0.1:{server.server_address[1]}"
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return state, server


@pytest.fixture
def ramp():
    state, server = _serve()
    yield state
    server.shutdown()
    server.server_close()


other_host = ramp     # a second, separate server: somewhere the token must never go


@pytest.fixture
def sources(tmp_path) -> Path:
    path = tmp_path / "sources.json"
    path.write_text(json.dumps(SOURCES), encoding="utf-8")
    return path


def sweep(ramp: FakeRamp, sources: Path, out: Path, env: dict | None = None, ramp_url: str | None = None) -> int:
    return main(["sweep", "--dataset", str(DS), "--since", SINCE, "--out", str(out), "--sources", str(sources),
                 "--ramp-url", ramp_url or ramp.url, "--as-of", AS_OF],
                env={"MOCK_RAMP_SECRET": SECRET} if env is None else env)


# The report --------------------------------------------------------------------------------------------

def test_sweep_reports_each_new_vendor(ramp, sources, tmp_path):
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out) == 0
    report = json.loads(out.read_text())
    assert report["dataset_id"] == str(DS) and report["as_of"] == AS_OF and report["since"] == SINCE
    by_name = {v["name"]: v for v in report["vendors"]}
    assert list(by_name) == ["Acme Fasteners LLC", "Brightline Office Supply Inc", "Cobalt Freight LLC"]
    acme, bright, cobalt = by_name.values()
    assert (acme["outcome"], acme["anchor"], acme["flags"]) == ("anchored", "+14155550120", [])
    assert (bright["outcome"], bright["anchor"]) == ("anchored", "+12125550151")
    assert bright["flags"] == ["supplied_number_unconfirmed"] and bright["supplied_numbers"] == ["+12125550150"]
    assert cobalt["outcome"] == "no_anchor" and "no_registry_entry" in cobalt["reasons"]
    assert report["counts"] == {"vendors": 3, "anchored": 2, "no_anchor": 1}


# 5. Read-only and deterministic ----------------------------------------------------------------------

def test_sweep_only_reads(ramp, sources, tmp_path):
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out) == 0
    token = f"mock.{DS}.{hmac.new(SECRET.encode(), str(DS).encode(), hashlib.sha256).hexdigest()}"
    assert {method for method, _, _ in ramp.requests} == {"GET"}
    assert {auth for _, _, auth in ramp.requests} == {f"Bearer {token}"}
    paths = sorted({urlsplit(p).path for _, p, _ in ramp.requests})
    assert paths == ["/developer/v1/vendors", *(f"/developer/v1/vendors/{vid(n)}/contacts" for n in (2, 3, 4))]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["report.json", "sources.json"]


def test_report_is_deterministic(ramp, sources, tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert sweep(ramp, sources, a) == 0 and sweep(ramp, sources, b) == 0
    assert a.read_bytes() == b.read_bytes()


# 4. Errors exit non-zero and write nothing --------------------------------------------------------------

API_FAILURES = [
    ("contacts answer 500", f"/developer/v1/vendors/{vid(3)}/contacts", 500, {"error": "internal_error"}),
    ("vendors answer 401", "/developer/v1/vendors", 401, {"error": "unauthorized"}),
    ("vendors answer 429", "/developer/v1/vendors", 429, {"error": "rate_limited"}),
    ("contacts are not JSON", f"/developer/v1/vendors/{vid(2)}/contacts", 200, b"<html>"),
    ("contacts are not a list", f"/developer/v1/vendors/{vid(2)}/contacts", 200, {"data": "none"}),
    ("vendors are not a list", "/developer/v1/vendors", 200, {"data": {"id": vid(2)}}),
]


@pytest.mark.parametrize(("why", "prefix", "status", "body"), API_FAILURES, ids=[f[0] for f in API_FAILURES])
def test_api_error_exits_non_zero(ramp, sources, tmp_path, why, prefix, status, body):
    ramp.fail[prefix] = (status, body)
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out) != 0
    assert not out.exists()


def test_unreadable_vendor_exits_non_zero(ramp, sources, tmp_path):
    ramp.vendors[2]["created_at"] = "yesterday"
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out) != 0 and not out.exists()


def test_unreachable_api_exits_non_zero(ramp, sources, tmp_path):
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out, ramp_url="http://127.0.0.1:1") != 0 and not out.exists()


def test_page_link_to_another_host_is_not_followed(ramp, other_host, sources, tmp_path):
    # The token must never go anywhere but the configured API, even when the API's own answer says so.
    ramp.next_base = other_host.url
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out) != 0 and not out.exists()
    assert other_host.requests == []


def test_missing_secret_or_url_exits_non_zero(ramp, sources, tmp_path):
    out = tmp_path / "report.json"
    assert sweep(ramp, sources, out, env={}) != 0
    assert main(["sweep", "--dataset", str(DS), "--since", SINCE, "--out", str(out), "--sources", str(sources)],
                env={"MOCK_RAMP_SECRET": SECRET}) != 0
    assert not out.exists() and ramp.requests == []


def test_unreadable_sources_file_exits_non_zero(ramp, tmp_path):
    bad = tmp_path / "sources.json"
    for text in ("{", json.dumps({"vendors": []}),
                 json.dumps({"vendors": {"Acme Fasteners LLC": {}, "ACME FASTENERS, LLC": {}}})):
        bad.write_text(text, encoding="utf-8")
        out = tmp_path / "report.json"
        assert sweep(ramp, bad, out) != 0 and not out.exists()


# 6. Fictional numbers only -----------------------------------------------------------------------------

def test_refuses_real_looking_numbers(ramp, sources, tmp_path):
    out = tmp_path / "report.json"
    ramp.contacts[vid(2)] = [{"phone_number": REAL_LOOKING}]
    assert sweep(ramp, sources, out) != 0 and not out.exists()

    ramp.contacts[vid(2)] = list(CONTACTS[vid(2)])
    data = json.loads(sources.read_text())
    data["vendors"]["Acme Fasteners LLC"]["website"]["phone"] = REAL_LOOKING
    sources.write_text(json.dumps(data), encoding="utf-8")
    assert sweep(ramp, sources, out) != 0 and not out.exists()


def test_fixtures_hold_only_fictional_numbers():
    entries = load_sources(PACKAGE / "fixtures" / "sources.json")
    assert entries
    phones = [e.get(kind, {}).get("phone") for e in entries.values() for kind in ("registry", "website")]
    assert phones and all(FICTIONAL.match(e164(p) or "") for p in phones), phones
    found = {}
    for path in PACKAGE.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".md"} and ".venv" not in path.parts:
            bad = sorted({n for n in re.findall(r"\+1\d{10}", path.read_text(encoding="utf-8"))
                          if not FICTIONAL.match(n)})
            if bad:
                found[str(path.relative_to(PACKAGE))] = bad
    assert found == {}, found
