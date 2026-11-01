"""Webhooks are hints, the account diff is the truth, and every door converges on one case."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from conftest import WEBHOOK_SECRET
from dialback_core.db import system_tx
from dialback_core.detection import import_all, process_inbound_webhooks, sync_vendor

pytestmark = pytest.mark.anyio


async def one(pool, sql: str, *args):
    async with system_tx(pool) as conn:
        return await (await conn.execute(sql, args)).fetchone()


async def rows(pool, sql: str, *args):
    async with system_tx(pool) as conn:
        return await (await conn.execute(sql, args)).fetchall()


def signed(body: dict) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(body).encode()
    return raw, {"X-Ramp-Signature": hmac.new(WEBHOOK_SECRET, raw, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}


async def test_webhook_endpoint_verifies_stores_and_dedupes(world):
    url = f"/webhooks/ramp/{world.dataset.id}"
    raw, headers = signed({"id": "evt-1", "type": "vendors.updated", "data": {"vendor_id": world.refs["acme"]}})
    assert (await world.core.post(url, content=raw, headers=headers)).json() == {"status": "accepted"}
    assert (await world.core.post(url, content=raw, headers=headers)).json() == {"status": "duplicate"}

    tampered = raw.replace(b"evt-1", b"evt-2")
    r = await world.core.post(url, content=tampered, headers=headers)
    assert r.status_code == 401
    alert = await one(world.pool, "select kind, severity from app.alerts where dataset_id = %s", world.dataset.id)
    assert (alert["kind"], alert["severity"]) == ("bad_webhook_signature", "critical")
    stored = await rows(world.pool, "select event_id, signature_valid from app.inbound_webhooks where dataset_id = %s order by id",
                        world.dataset.id)
    assert (stored[0]["event_id"], stored[0]["signature_valid"]) == ("evt-1", True)
    assert stored[1]["event_id"].startswith("unsigned:") and stored[1]["signature_valid"] is False

    assert (await world.core.post(f"/webhooks/ramp/{uuid.uuid4()}", content=raw, headers=headers)).status_code == 404


async def test_initial_import_is_a_baseline_not_a_change(world):
    assert await import_all(world.pool, world.ramp, world.keys, world.dataset.id) == 1
    accounts = await rows(world.pool, "select account_last4, is_current from app.bank_accounts where dataset_id = %s order by first_seen_at",
                          world.dataset.id)
    assert [(a["account_last4"], a["is_current"]) for a in accounts] == [("2222", False), ("4444", True)]
    assert (await one(world.pool, "select count(*) as n from app.cases where dataset_id = %s", world.dataset.id))["n"] == 0


async def test_a_bank_change_webhook_opens_one_case_and_queues_the_hold(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    new_account = await world.bank_change(actor="clerk")
    assert await world.deliver_webhooks() == 1
    assert await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id) == 1

    case = await one(world.pool, "select * from app.cases where dataset_id = %s", world.dataset.id)
    assert case["state"] == "open" and case["version"] == 1
    assert case["entered_by"] == world.users["clerk"] and not case["has_unknown_changer"]
    change = await one(world.pool,
                       """select ce.detected_via, ce.source_event_id, o.account_last4 as old4, n.account_last4 as new4
                            from app.bank_change_events ce
                            join app.bank_accounts n on n.id = ce.new_account_id
                            left join app.bank_accounts o on o.id = ce.old_account_id
                           where ce.id = %s""", case["change_event_id"])
    assert (change["detected_via"], change["old4"], change["new4"]) == ("webhook", "4444", "6666")
    assert change["source_event_id"] == f"ramp-account:{new_account}"
    hold = await one(world.pool, "select idempotency_key, status from app.outbox where case_id = %s and command = 'place_hold'", case["id"])
    assert (hold["idempotency_key"], hold["status"]) == (f"{case['id']}:hold:v1", "pending")
    processed = await one(world.pool, "select processed_at, error from app.inbound_webhooks where dataset_id = %s", world.dataset.id)
    assert processed["processed_at"] is not None and processed["error"] is None


async def test_duplicate_webhooks_and_a_poll_converge_on_one_case(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.mock_settings(duplicate_webhooks=True)
    await world.bank_change()
    assert await world.deliver_webhooks() == 2
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    again = await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs["acme"], "poll")
    assert again == [], "a known account is not a new change"

    assert (await one(world.pool, "select count(*) as n from app.inbound_webhooks where dataset_id = %s", world.dataset.id))["n"] == 1
    assert (await one(world.pool, "select count(*) as n from app.bank_change_events where dataset_id = %s", world.dataset.id))["n"] == 1
    assert (await one(world.pool, "select count(*) as n from app.cases where dataset_id = %s", world.dataset.id))["n"] == 1
    assert (await one(world.pool, "select count(*) as n from audit.evidence_events where dataset_id = %s and type = 'change_detected'",
                      world.dataset.id))["n"] == 1


async def test_a_vendor_supplied_change_has_an_unknown_changer(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(actor=None)
    await world.deliver_webhooks()
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    case = await one(world.pool, "select entered_by, has_unknown_changer from app.cases where dataset_id = %s", world.dataset.id)
    assert case["entered_by"] is None and case["has_unknown_changer"] is True


async def test_a_silent_change_is_found_by_the_sweep(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.mock_settings(silent_changes=True)
    await world.bank_change()
    assert await world.deliver_webhooks() == 0
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    case = await one(world.pool,
                     "select c.state, ce.detected_via from app.cases c join app.bank_change_events ce on ce.id = c.change_event_id "
                     "where c.dataset_id = %s", world.dataset.id)
    assert (case["state"], case["detected_via"]) == ("open", "poll")


async def test_a_second_change_joins_the_open_case(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(account_number="000055556666")
    await world.deliver_webhooks()
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    await world.bank_change(account_number="000077778888", actor=None)
    await world.deliver_webhooks()
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    cases = await rows(world.pool, "select version, has_unknown_changer, state from app.cases where dataset_id = %s", world.dataset.id)
    assert [(c["version"], c["has_unknown_changer"], c["state"]) for c in cases] == [(2, True, "open")]
