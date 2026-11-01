"""The Ramp mock behaves like Ramp where Dialback depends on it."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from conftest import auth, seed_body, seeded
from dialback_ramp_mock.webhooks import deliver_due

pytestmark = pytest.mark.anyio
API = "/developer/v1"


async def test_requests_need_a_valid_token_for_a_seeded_business(api):
    assert (await api.get(f"{API}/vendors")).status_code == 401
    forged = {"Authorization": f"Bearer mock.{uuid.uuid4()}.{'0' * 64}"}
    assert (await api.get(f"{API}/vendors", headers=forged)).status_code == 401
    r = await api.get(f"{API}/vendors", headers=auth(uuid.uuid4()))
    assert r.status_code == 401 and "seed it first" in r.json()["error"]["message"]


async def test_accounts_are_masked_and_only_the_latest_is_active(api):
    ds, refs = await seeded(api)
    r = await api.get(f"{API}/vendors/{refs['acme']}/accounts", headers=auth(ds))
    assert r.status_code == 200
    accounts = r.json()["data"]
    assert [(a["account_number_last4"], a["is_active"]) for a in accounts] == [("2222", False), ("4444", True)]
    assert "000033334444" not in r.text and "000011112222" not in r.text
    vendor = (await api.get(f"{API}/vendors/{refs['acme']}", headers=auth(ds))).json()
    assert vendor["name"] == "Acme Fasteners LLC" and vendor["hold"] is None


async def test_a_token_only_sees_its_own_dataset(api):
    ds_a, refs_a = await seeded(api)
    _, refs_b = await seeded(api)
    assert (await api.get(f"{API}/vendors/{refs_b['acme']}", headers=auth(ds_a))).status_code == 404
    ids = [v["id"] for v in (await api.get(f"{API}/vendors", headers=auth(ds_a))).json()["data"]]
    assert ids == [refs_a["acme"]]


async def test_bank_change_is_audited_and_sends_a_signed_webhook(api, receiver):
    ds, refs = await seeded(api)
    r = await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=auth(ds),
                       json={"routing_number": "026009593", "account_number": "000055556666", "actor_user_id": refs["clerk"]})
    assert r.status_code == 200, r.text
    new_account = r.json()["account_id"]

    accounts = (await api.get(f"{API}/vendors/{refs['acme']}/accounts", headers=auth(ds))).json()["data"]
    assert [a["id"] for a in accounts if a["is_active"]] == [new_account]
    events = (await api.get(f"{API}/audit-logs/events", headers=auth(ds), params={"target_id": refs["acme"]})).json()["data"]
    change = [e for e in events if e["event_type"] == "VENDOR_BANK_ACCOUNT_UPDATED"]
    assert len(change) == 1 and change[0]["actor_id"] == refs["clerk"] and change[0]["payload"]["new_account_id"] == new_account

    assert (await api.post("/_mock/webhooks/deliver", headers=auth(ds))).json() == {"delivered": 1}
    [request] = receiver.requests
    expected = hmac.new(b"whsec-test-0123456789", request.content, hashlib.sha256).hexdigest()
    assert request.headers["X-Ramp-Signature"] == expected
    payload = json.loads(request.content)
    assert payload["type"] == "vendors.updated" and payload["data"]["vendor_id"] == refs["acme"]
    assert request.headers["X-Ramp-Event-Id"] == payload["id"]


async def test_duplicate_webhooks_share_an_event_id_and_silent_changes_send_none(api, receiver):
    ds, refs = await seeded(api, settings={"duplicate_webhooks": True})
    await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=auth(ds),
                   json={"routing_number": "026009593", "account_number": "000055556666"})
    await api.post("/_mock/webhooks/deliver", headers=auth(ds))
    assert len(receiver.requests) == 2 and len({p["id"] for p in receiver.payloads()}) == 1

    await api.patch("/_mock/settings", headers=auth(ds), json={"duplicate_webhooks": False, "silent_changes": True})
    await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=auth(ds),
                   json={"routing_number": "026009593", "account_number": "000077778888"})
    await api.post("/_mock/webhooks/deliver", headers=auth(ds))
    assert len(receiver.requests) == 2


async def test_hold_clears_dates_payment_run_skips_held_bills_and_release_does_not_restore(api):
    ds, refs = await seeded(api)
    h = auth(ds)
    r = await api.post(f"{API}/vendors/{refs['acme']}/hold", headers=h, json={"reason": "bank change under verification"})
    assert r.status_code == 200, r.text
    bills = (await api.get(f"{API}/bills", headers=h, params={"vendor_id": refs["acme"]})).json()["data"]
    assert all(b["is_on_hold"] and b["payment_date"] is None for b in bills)
    assert (await api.get(f"{API}/vendors/{refs['acme']}", headers=h)).json()["hold"]["id"] == r.json()["id"]
    assert (await api.post("/_mock/payment-run", headers=h, json={"as_of": "2026-12-31"})).json() == {"paid_bill_ids": []}

    assert (await api.post(f"{API}/vendors/{refs['acme']}/release", headers=h)).json()["released"] is True
    bills = (await api.get(f"{API}/bills", headers=h, params={"vendor_id": refs["acme"]})).json()["data"]
    assert all(not b["is_on_hold"] and b["payment_date"] is None for b in bills), "a release does not reschedule"

    r = await api.patch(f"{API}/bills/{refs['bill-1']}", headers=h, json={"payment_date": "2026-11-05"})
    assert r.status_code == 200 and r.json()["payment_date"] == "2026-11-05"
    assert (await api.post("/_mock/payment-run", headers=h, json={"as_of": "2026-11-05"})).json() == {"paid_bill_ids": [refs["bill-1"]]}


async def test_fail_next_hold_fires_once_and_stays_spent(api):
    ds, refs = await seeded(api, settings={"fail_next_hold": True})
    first = await api.post(f"{API}/vendors/{refs['acme']}/hold", headers=auth(ds))
    assert first.status_code == 500
    assert (await api.get(f"{API}/vendors/{refs['acme']}", headers=auth(ds))).json()["hold"] is None
    assert (await api.post(f"{API}/vendors/{refs['acme']}/hold", headers=auth(ds))).status_code == 200


async def test_rate_limit_answers_429_with_retry_after(api):
    ds, _ = await seeded(api, settings={"rate_limit_per_10s": 3})
    codes = [(await api.get(f"{API}/vendors", headers=auth(ds))).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = await api.get(f"{API}/vendors", headers=auth(ds))
    assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1


async def test_bills_page_with_a_next_link(api):
    vendors = seed_body()["vendors"]
    vendors[0]["bills"].append({"invoice_number": "INV-3", "amount_cents": 100, "payment_date": "2026-11-20"})
    ds, _ = await seeded(api, vendors=vendors)
    first = (await api.get(f"{API}/bills", headers=auth(ds), params={"page_size": 2})).json()
    assert len(first["data"]) == 2 and first["page"]["next"]
    second = (await api.get(first["page"]["next"], headers=auth(ds))).json()
    assert [b["invoice_number"] for b in second["data"]] == ["INV-3"] and second["page"]["next"] is None


async def test_failed_deliveries_retry_then_give_up(api, pool, receiver):
    ds, refs = await seeded(api)
    receiver.status = 500
    await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=auth(ds),
                   json={"routing_number": "026009593", "account_number": "000055556666"})
    assert await deliver_due(pool, receiver.client, max_attempts=2, dataset_id=ds) == 1
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "select d.status, d.attempt, d.last_error from mock_ramp.webhook_deliveries d "
            "join mock_ramp.webhook_subscriptions s on s.id = d.subscription_id "
            "join mock_ramp.businesses b on b.id = s.business_id where b.dataset_id = %s", (ds,))).fetchone()
        assert (row["status"], row["attempt"], row["last_error"]) == ("scheduled", 2, "HTTP 500")
        await conn.execute("update mock_ramp.webhook_deliveries set scheduled_at = now() where status = 'scheduled'")
    assert await deliver_due(pool, receiver.client, max_attempts=2, dataset_id=ds) == 1
    async with pool.connection() as conn:
        row = await (await conn.execute(
            "select d.status from mock_ramp.webhook_deliveries d join mock_ramp.webhook_subscriptions s on s.id = d.subscription_id "
            "join mock_ramp.businesses b on b.id = s.business_id where b.dataset_id = %s", (ds,))).fetchone()
        assert row["status"] == "failed"


async def test_requests_are_validated(api):
    ds, refs = await seeded(api)
    r = await api.post(f"/_mock/vendors/{refs['acme']}/bank-change", headers=auth(ds),
                       json={"routing_number": "12", "account_number": "x", "extra": 1})
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_request"
