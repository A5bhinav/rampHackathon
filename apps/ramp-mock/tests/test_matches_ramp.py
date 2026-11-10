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
from ramp_spec import (
    DOC_PATH,
    mock_inputs,
    operation_problems,
    payload_problems,
    ramp_examples,
    response_problems,
)

API = "/developer/v1"
VENDOR = f"{API}/vendors/{{vendor_id}}"
ACCOUNTS = f"{API}/vendors/{{vendor_id}}/accounts"
HOLD = f"{API}/vendors/{{vendor_id}}/hold"
RELEASE = f"{API}/vendors/{{vendor_id}}/release"
BILLS = f"{API}/bills"
BILL = f"{API}/bills/{{bill_id}}"
RECEIPT = f"{API}/bills/{{bill_id}}/remittance-receipt"
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
    # Ramp's token prefixes, from its own TokenResponse description: business and user, tok and jwt.
    assert set(re.findall(rb"ramp_[a-z]+_(?:tok|jwt)_[A-Za-z0-9]+", raw)) == {b"ramp_business_tok_EXAMPLE"}


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


def test_checker_reports_a_wrong_format():
    # By hand from Ramp's Bill: `id` is a uuid and `due_at` a date-time.
    problems = response_problems("get", BILLS, 200, page_of({"id": "not-a-uuid", "due_at": "2026-11-05"}))
    assert len(problems) == 2
    assert any("data.0.id:" in p for p in problems) and any("data.0.due_at:" in p for p in problems)


def test_checker_allows_null_on_any_field():
    # D1.5. By hand: is_default is a plain boolean Ramp doesn't mark nullable; user_details and payment
    # are nullable through an allOf wrapper; approval_status is a nullable enum that doesn't list null.
    assert response_problems("get", ACCOUNTS, 200, page_of(ACCOUNT | {"is_default": None})) == []
    assert response_problems("get", AUDIT, 200, page_of({"user_details": None, "primary_reference": None})) == []
    assert response_problems("get", BILLS, 200, page_of({"approval_status": None, "payment": None})) == []


def test_checker_still_names_an_unknown_field_whose_value_is_null():
    [problem] = response_problems("get", ACCOUNTS, 200, page_of(ACCOUNT | {"hold": None}))
    assert "'hold'" in problem


def test_checker_reports_a_null_body():
    [problem] = response_problems("get", ACCOUNTS, 200, b"null")
    assert "body is null" in problem


def test_checker_reports_every_problem_not_only_the_first():
    body = page_of(ACCOUNT | {"is_default": "yes"}, ACCOUNT | {"account_number_last4": "4444"})
    assert len(response_problems("get", ACCOUNTS, 200, body)) == 2
    assert len(operation_problems("get", AUDIT, query={"target_id", "event_type"}, body=set())) == 2


def test_checker_reports_an_unknown_path():
    nope = f"{API}/vendors/{{vendor_id}}/nope"
    [problem] = response_problems("get", nope, 200, b"{}")
    assert "not in Ramp's document" in problem
    [problem] = operation_problems("get", nope, query=set(), body=set())
    assert "not in Ramp's document" in problem


def test_checker_reports_an_unknown_method():
    # By hand: Ramp documents only GET on the accounts path.
    [problem] = response_problems("delete", ACCOUNTS, 200, b"{}")
    assert "Ramp has GET" in problem
    [problem] = operation_problems("delete", ACCOUNTS, query=set(), body=set())
    assert "Ramp has GET" in problem


def test_checker_reports_an_undocumented_status():
    # By hand: Ramp answers a hold with 202 or 204.
    [problem] = response_problems("post", HOLD, 200, b"{}")
    assert "202" in problem and "204" in problem


def test_checker_checks_the_body_even_when_the_status_is_wrong():
    # Ramp's one documented success body for a hold is EmptyObject (202), which allows no fields.
    problems = response_problems("post", HOLD, 200, b'{"id": "h1"}')
    assert len(problems) == 2
    assert "202" in problems[0] and "'id'" in problems[1]


def test_checker_expects_no_body_on_a_204():
    # Ramp documents a JSON body on this 204, but a 204 never carries one.
    assert response_problems("delete", BILL, 204, b"") == []
    [problem] = response_problems("delete", BILL, 204, b"{}")
    assert "no body" in problem


def test_checker_fails_closed_on_a_body_it_cannot_compare():
    # Ramp sends a PDF here; the checker can't compare it, so it says so instead of passing.
    [problem] = response_problems("get", RECEIPT, 200, b"")
    assert "application/pdf" in problem


def test_checker_reports_an_unknown_query_parameter():
    [problem] = operation_problems("get", AUDIT, query={"target_id", "page_size"}, body=set())
    assert "'target_id'" in problem


def test_checker_reports_an_unknown_body_field():
    [problem] = operation_problems("post", HOLD, query=set(), body={"reason", "note"})
    assert "'note'" in problem


def test_checker_reports_a_body_that_is_not_json():
    [problem] = response_problems("get", ACCOUNTS, 200, b"<html>busy</html>")
    assert "isn't JSON" in problem


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


# The mock's routes (D1, D2), each in the states the core depends on ---------------------------------

CHANGED, ON_HOLD, NO_ACTOR = "after a bank change", "on hold", "a change with no actor"
CHECKED = [
    ("get", f"{API}/vendors", CHANGED),
    ("get", VENDOR, CHANGED),
    ("get", VENDOR, ON_HOLD),
    ("get", ACCOUNTS, CHANGED),
    ("get", f"{API}/vendors/{{vendor_id}}/contacts", CHANGED),
    ("post", HOLD, CHANGED),
    ("post", RELEASE, ON_HOLD),
    ("get", BILLS, CHANGED),
    ("get", BILLS, ON_HOLD),
    ("patch", BILL, CHANGED),
    ("get", AUDIT, CHANGED),
    ("get", AUDIT, NO_ACTOR),
]
OPERATIONS = sorted({(method, path) for method, path, _ in CHECKED})
BODIES = {("post", HOLD): {"reason": "spec 0078 check"}, ("patch", BILL): {"payment_date": "2026-11-20"}}


def test_every_ramp_route_the_mock_serves_is_checked():
    served = {(method.lower(), path) for path, item in mock_doc()["paths"].items() if path.startswith(API)
              for method in item}
    assert served == set(OPERATIONS)


def test_ramps_own_examples_pass():
    """Ramp's examples for the schemas these routes use must pass; if not, the checker is wrong or Ramp is.

    Two known contradictions in Ramp's document, both checked by hand: the Bill example has an
    `accounting_sync_date` that Bill doesn't define, and its accounting field selections carry a
    "type" that ApiAccountingFieldSelection doesn't define.
    """
    problems = [p for name, example in ramp_examples(OPERATIONS) for p in payload_problems(name, json.dumps(example).encode())]
    known = [p for p in problems if "'accounting_sync_date' was unexpected" in p
             or ("accounting_field_selections" in p and "'type' was unexpected" in p)]
    assert [p for p in problems if p not in known] == []
    assert len(known) == 4, "Ramp's Bill example changed (Bill and its page each hit both): update the exceptions above"


async def make_change(api, state: str) -> tuple[dict, dict]:
    """Seed a business and change Acme's bank account, as the clerk unless the state says no actor."""
    dataset_id, refs = await seeded(api)
    headers = auth(dataset_id)
    change = {"routing_number": "026009593", "account_number": "000055556666"}
    if state != NO_ACTOR:
        change["actor_user_id"] = refs["clerk"]
    r = await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=headers, json=change)
    assert r.status_code == 200, r.text
    if state == ON_HOLD:
        r = await api.post(f"{API}/vendors/{refs['acme']}/hold", headers=headers)
        assert r.status_code < 300, r.text
    return headers, refs


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path", "state"), CHECKED,
                         ids=[f"{m.upper()} {p[len(API):]} ({s})" for m, p, s in CHECKED])
async def test_route_matches_ramp(api, method, path, state):
    headers, refs = await make_change(api, state)
    url = path.replace("{vendor_id}", refs["acme"]).replace("{bill_id}", refs["bill-1"])
    r = await api.request(method.upper(), url, headers=headers, json=BODIES.get((method, path)))
    if r.headers.get("content-type", "").startswith("application/json"):
        body = r.json()
        if isinstance(body, dict) and "data" in body:
            assert body["data"], "the mock returned no items, so nothing was checked"
    query, fields = mock_inputs(mock_doc(), method, path)
    problems = operation_problems(method, path, query=query, body=fields)
    problems += response_problems(method, path, r.status_code, r.content)
    assert not problems, "\n".join(problems)


@pytest.mark.anyio
async def test_webhook_matches_ramp(api, receiver):
    """The mock's webhook against Ramp's MockOutboundWebhookEventPayload, plus the header Ramp's webhooks
    guide documents: business events carry X-Ramp-Webhook-ID, equal to the payload's id."""
    headers, _ = await make_change(api, CHANGED)
    await api.post("/_mock/webhooks/deliver", headers=headers)
    assert receiver.requests, "the mock sent no webhook, so nothing was checked"
    problems = []
    for request in receiver.requests:
        problems += payload_problems("MockOutboundWebhookEventPayload", request.content)
        if request.headers.get("X-Ramp-Webhook-ID") != json.loads(request.content).get("id"):
            problems.append("webhook: no X-Ramp-Webhook-ID header equal to the payload's id")
    assert not problems, "\n".join(problems)
