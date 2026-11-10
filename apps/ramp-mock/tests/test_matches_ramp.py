"""Spec 0078: the mock's Ramp routes match Ramp's published Developer API document.

The checker tests run without a database and prove the checker can both pass and fail. The route
tests run the mock and list every way each route differs from Ramp.
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import cache

import pytest
from conftest import SECRET, auth, seeded
from dialback_ramp_mock.app import create_app
from dialback_ramp_mock.settings import Settings
from ramp_spec import DOC_PATH, mock_inputs, operation_problems, response_problems

API = "/developer/v1"
ACCOUNTS = f"{API}/vendors/{{vendor_id}}/accounts"
HOLD = f"{API}/vendors/{{vendor_id}}/hold"
RELEASE = f"{API}/vendors/{{vendor_id}}/release"
BILL = f"{API}/bills/{{bill_id}}"
AUDIT = f"{API}/audit-logs/events"
COMMITTED_SHA256 = "64321b18337bf39d10d86d1fa9903c24f48268cd8ea0d2bc2b6a04a602d458da"

# A bank account written by hand from Ramp's ApiVendorBankAccountResource: nulls in two nullable
# fields, and `currency`, which Ramp requires, left out (D1.4 allows that).
ACCOUNT = {"id": "6f1c1d7e-5b0a-4c2e-9d3f-0123456789ab", "account_number_last_four": None, "is_default": True,
           "payment_method": "ACH", "proposal_id": None, "status": "active"}


def page_of(*items: dict) -> bytes:
    return json.dumps({"data": list(items), "page": {"next": None}}).encode()


# The committed document ------------------------------------------------------------------------

def test_the_committed_document_is_unchanged():
    raw = DOC_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == COMMITTED_SHA256
    assert set(re.findall(rb"ramp_business_tok_[A-Za-z0-9]+", raw)) == {b"ramp_business_tok_EXAMPLE"}


# The checker, on its own (D3) ------------------------------------------------------------------

def test_checker_accepts_a_ramp_shaped_body():
    assert response_problems("get", ACCOUNTS, 200, page_of(ACCOUNT)) == []
    assert response_problems("post", HOLD, 204, b"") == []
    assert response_problems("post", HOLD, 202, b"{}") == []


def test_checker_reports_an_unknown_field():
    [problem] = response_problems("get", ACCOUNTS, 200, page_of(ACCOUNT | {"account_number_last4": "4444"}))
    assert "account_number_last4" in problem


def test_checker_reports_a_wrong_type():
    [problem] = response_problems("get", ACCOUNTS, 200, page_of(ACCOUNT | {"is_default": "yes"}))
    assert "is_default" in problem


def test_checker_reports_every_problem_not_only_the_first():
    body = page_of(ACCOUNT | {"is_default": "yes"}, ACCOUNT | {"account_number_last4": "4444"})
    assert len(response_problems("get", ACCOUNTS, 200, body)) == 2


def test_checker_reports_an_unknown_path():
    nope = f"{API}/vendors/{{vendor_id}}/nope"
    assert response_problems("get", nope, 200, b"{}")
    assert operation_problems("get", nope, query=set(), body=set())


def test_checker_reports_an_unknown_method():
    assert response_problems("delete", ACCOUNTS, 200, b"{}")
    assert operation_problems("delete", ACCOUNTS, query=set(), body=set())


def test_checker_reports_an_undocumented_status():
    [problem] = response_problems("post", HOLD, 200, b"{}")
    assert "200" in problem


def test_checker_reports_an_unknown_query_parameter():
    [problem] = operation_problems("get", AUDIT, query={"target_id", "page_size"}, body=set())
    assert "target_id" in problem


def test_checker_reports_an_unknown_body_field():
    [problem] = operation_problems("post", HOLD, query=set(), body={"reason", "note"})
    assert "'note'" in problem


def test_checker_reports_a_body_that_is_not_json():
    assert response_problems("get", ACCOUNTS, 200, b"<html>busy</html>")


# What the mock accepts, read from the mock's own OpenAPI ---------------------------------------

@cache
def mock_doc() -> dict:
    settings = Settings(database_url="unused-in-tests", token_secret=SECRET, deliver_webhooks=False,
                        webhook_max_attempts=2)
    return create_app(settings).openapi()


def test_mock_inputs_reads_what_the_mock_accepts():
    # By hand from app.py: audit_events takes target_id, from_occurred_at, event_type and page_size;
    # place_hold takes a HoldRequest with `reason`; update_bill takes a BillUpdate with `payment_date`.
    assert mock_inputs(mock_doc(), "get", AUDIT) == ({"target_id", "from_occurred_at", "event_type", "page_size"}, set())
    assert mock_inputs(mock_doc(), "post", HOLD) == (set(), {"reason"})
    assert mock_inputs(mock_doc(), "patch", BILL) == (set(), {"payment_date"})


# The mock's routes (D1, D2) ----------------------------------------------------------------------

CHECKED = [
    ("get", f"{API}/vendors"),
    ("get", f"{API}/vendors/{{vendor_id}}"),
    ("get", ACCOUNTS),
    ("get", f"{API}/vendors/{{vendor_id}}/contacts"),
    ("post", HOLD),
    ("post", RELEASE),
    ("get", f"{API}/bills"),
    ("patch", BILL),
    ("get", AUDIT),
]
BODIES = {("post", HOLD): {"reason": "spec 0078 check"}, ("patch", BILL): {"payment_date": "2026-11-20"}}


def test_every_ramp_route_the_mock_serves_is_checked():
    served = {(method.lower(), path) for path, item in mock_doc()["paths"].items() if path.startswith(API)
              for method in item}
    assert served == set(CHECKED)


async def call(api, method: str, path: str):
    """Seed a business, change Acme's bank account as the clerk, then make this one call."""
    dataset_id, refs = await seeded(api)
    headers = auth(dataset_id)
    vendor_id = refs["acme"]
    r = await api.post(f"/_mock/vendors/{vendor_id}/bank-change", headers=headers, json={
        "routing_number": "026009593", "account_number": "000055556666", "actor_user_id": refs["clerk"]})
    assert r.status_code == 200, r.text
    url = path.replace("{vendor_id}", vendor_id).replace("{bill_id}", refs["bill-1"])
    if path == RELEASE:
        r = await api.post(url.removesuffix("/release") + "/hold", headers=headers)
        assert r.status_code < 300, r.text
    return await api.request(method.upper(), url, headers=headers, json=BODIES.get((method, path)))


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), CHECKED, ids=[f"{m.upper()} {p[len(API):]}" for m, p in CHECKED])
async def test_route_matches_ramp(api, method, path):
    query, body = mock_inputs(mock_doc(), method, path)
    r = await call(api, method, path)
    problems = operation_problems(method, path, query=query, body=body)
    problems += response_problems(method, path, r.status_code, r.content)
    assert not problems, "\n".join(problems)
