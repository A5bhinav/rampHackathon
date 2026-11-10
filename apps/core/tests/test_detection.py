"""Webhooks are hints, the account diff is the truth, and every door converges on one case."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from conftest import WEBHOOK_SECRET
from dialback_core import detection
from dialback_core.db import system_tx
from dialback_core.detection import import_all, process_inbound_webhooks, sync_vendor
from dialback_core.ramp import RampError
from flows import add_vendor

pytestmark = pytest.mark.anyio

CEDAR_ACCOUNT = {"routing_number": "021000021", "account_number": "000099990000"}


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


# Spec 0048: first sight is the vendor, not its accounts ---------------------------------------

async def counts(world) -> tuple[int, int]:
    """How many change rows and cases this test's dataset has."""
    changes = await one(world.pool, "select count(*) as n from app.bank_change_events where dataset_id = %s", world.dataset.id)
    cases = await one(world.pool, "select count(*) as n from app.cases where dataset_id = %s", world.dataset.id)
    return changes["n"], cases["n"]


async def mirrored(world, ref: str) -> list[tuple[str, bool]]:
    found = await rows(world.pool, "select b.account_last4, b.is_current from app.bank_accounts b join app.vendors v on v.id = b.vendor_id "
                       "where v.dataset_id = %s and v.ramp_vendor_id = %s order by b.first_seen_at", world.dataset.id, world.refs[ref])
    return [(a["account_last4"], a["is_current"]) for a in found]


class AuditLogDown:
    """The real Ramp port, except its audit log can't be read."""

    def __init__(self, port) -> None:
        self.port = port

    def __getattr__(self, name):
        return getattr(self.port, name)

    async def get_audit_events(self, *args, **kwargs):
        raise RampError("audit log unavailable", status=503, retryable=True)


async def test_a_known_vendor_with_no_account_gets_its_first_account_and_a_case(world):
    await add_vendor(world, "birch", "Birch Supply Co")  # paid by check: no bank account on file
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    vendor = await one(world.pool, "select id from app.vendors where dataset_id = %s and ramp_vendor_id = %s",
                       world.dataset.id, world.refs["birch"])
    assert vendor is not None and await mirrored(world, "birch") == [] and await counts(world) == (0, 0), "the import is a baseline"

    new_account = await world.bank_change(vendor_ref="birch", actor="clerk")  # "we're moving from checks to ACH"
    assert await world.deliver_webhooks() == 1
    assert await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id) == 1

    case = await one(world.pool, "select * from app.cases where dataset_id = %s", world.dataset.id)
    assert case is not None, "a known vendor's first account is a change, not a baseline"
    assert (case["vendor_id"], case["state"], case["version"]) == (vendor["id"], "open", 1)
    assert case["entered_by"] == world.users["clerk"] and not case["has_unknown_changer"]
    change = await one(world.pool, "select ce.*, n.account_last4 as new4 from app.bank_change_events ce "
                       "join app.bank_accounts n on n.id = ce.new_account_id where ce.id = %s", case["change_event_id"])
    assert change["old_account_id"] is None, "there was no account before"
    assert (change["detected_via"], change["new4"], change["source_event_id"]) == ("webhook", "6666", f"ramp-account:{new_account}")
    hold = await one(world.pool, "select idempotency_key, status from app.outbox where case_id = %s and command = 'place_hold'", case["id"])
    assert (hold["idempotency_key"], hold["status"]) == (f"{case['id']}:hold:v1", "pending")

    again = await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs["birch"], "poll")
    assert again == [], "the mirrored account is known now"
    assert await counts(world) == (1, 1)
    assert (await one(world.pool, "select count(*) as n from audit.evidence_events where dataset_id = %s and type = 'change_detected'",
                      world.dataset.id))["n"] == 1


async def test_a_first_account_with_no_known_author_has_an_unknown_changer(world):
    await add_vendor(world, "birch", "Birch Supply Co")
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(vendor_ref="birch", actor=None)  # vendor-supplied, or an ERP sync
    await world.deliver_webhooks()
    await process_inbound_webhooks(world.pool, world.connector, world.keys, dataset_id=world.dataset.id)
    case = await one(world.pool, "select entered_by, has_unknown_changer from app.cases where dataset_id = %s", world.dataset.id)
    assert case is not None, "a known vendor's first account is a change, not a baseline"
    assert case["entered_by"] is None and case["has_unknown_changer"] is True, "two approvers (ADR-10)"


async def test_first_sight_of_a_vendor_with_an_account_is_still_a_baseline(world):
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)  # the dataset's initial import
    await add_vendor(world, "cedar", "Cedar Logistics Inc", accounts=[CEDAR_ACCOUNT])  # a vendor added later
    assert await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs["cedar"], "webhook") == []
    assert await mirrored(world, "cedar") == [("0000", True)]
    assert await counts(world) == (0, 0)


async def test_a_webhook_and_a_poll_racing_at_first_sight_make_no_change(world, monkeypatch):
    """The webhook's sync reads what we know (phase 1) before the poll's sync mirrors the new vendor,
    and writes (phase 3) after it. It must still see first sight, not a known vendor whose accounts
    are all new."""
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await add_vendor(world, "cedar", "Cedar Logistics Inc", accounts=[CEDAR_ACCOUNT])
    real_now, race = detection.dataset_now, {"ran": False, "poll": None}

    async def poll_wins_the_race(conn, dataset_id):
        if not race["ran"]:
            race["ran"] = True
            race["poll"] = await sync_vendor(world.pool, world.ramp, world.keys, dataset_id, world.refs["cedar"], "poll")
        return await real_now(conn, dataset_id)

    monkeypatch.setattr(detection, "dataset_now", poll_wins_the_race)
    assert await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs["cedar"], "webhook") == []
    assert race == {"ran": True, "poll": []}, "the poll synced between the webhook's read and its write"
    assert await mirrored(world, "cedar") == [("0000", True)]
    assert await counts(world) == (0, 0)


async def test_an_unchanged_re_sync_creates_nothing(world):
    await add_vendor(world, "birch", "Birch Supply Co")
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    assert await import_all(world.pool, world.ramp, world.keys, world.dataset.id) == 2  # the nightly sweep
    for ref in ("acme", "birch"):
        assert await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs[ref], "webhook") == [], ref
    assert await mirrored(world, "birch") == []
    assert await counts(world) == (0, 0)


async def test_a_failed_audit_lookup_leaves_the_first_account_new_for_the_next_sync(world):
    await add_vendor(world, "birch", "Birch Supply Co")
    await import_all(world.pool, world.ramp, world.keys, world.dataset.id)
    await world.bank_change(vendor_ref="birch")
    with pytest.raises(RampError):
        await sync_vendor(world.pool, AuditLogDown(world.ramp), world.keys, world.dataset.id, world.refs["birch"], "webhook")
    assert await mirrored(world, "birch") == [] and await counts(world) == (0, 0), "a failed sync writes nothing"

    [change] = await sync_vendor(world.pool, world.ramp, world.keys, world.dataset.id, world.refs["birch"], "poll")
    assert change.status == "applied"
    case = await one(world.pool, "select c.state, ce.old_account_id from app.cases c "
                     "join app.bank_change_events ce on ce.id = c.change_event_id where c.id = %s", change.case_id)
    assert (case["state"], case["old_account_id"]) == ("open", None)
