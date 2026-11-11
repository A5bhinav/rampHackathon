"""Change detection: turn Ramp's signals into exactly one case per bank change (spec 0002, D4).

Webhooks are hints; comparing Ramp's accounts with Dialback's own history is the truth,
because Ramp has no bank-account history endpoint. The same comparison runs for a webhook,
the poller and the nightly sweep, and all of them converge on one change row and one case:

- a change's `source_event_id` is `ramp-account:<ramp account id>`, unique per dataset;
- the case event's idempotency key is `<change_event_id>:detected`.

A sync runs in three phases so no transaction waits on Ramp: read what we know, ask Ramp
who made each new account, then write the mirror, the change and the case event together.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from .db import dataset_now, system_tx
from .events import apply_event
from .ramp import RampAccount, RampConnector, RampError, RampPort

log = logging.getLogger(__name__)

POLICY_VERSION = "policy-v0"
BANK_CHANGE_EVENT = "VENDOR_BANK_ACCOUNT_UPDATED"  # confirm against Ramp's audit-log types


@dataclass(frozen=True)
class Keys:
    fingerprint_key: bytes
    fingerprint_key_version: int = 1


@dataclass(frozen=True)
class DetectedChange:
    change_event_id: UUID
    case_id: UUID
    ramp_account_id: str
    changed_by: UUID | None
    status: str          # applied, or already_applied when another door found it first


def fingerprint(keys: Keys, account: RampAccount) -> str:
    """HMAC over what Ramp gives us: routing number, last four and Ramp's account id.

    Ramp masks account numbers, so ADR-8's HMAC(routing + full number) can't be computed
    from the API (spec 0002, D4 and Q1). This still never holds a full number.
    """
    material = f"{account.routing_number}|{account.last4}|{account.id}".encode()
    return hmac.new(keys.fingerprint_key, material, hashlib.sha256).hexdigest()


async def sync_vendor(pool: AsyncConnectionPool, ramp: RampPort, keys: Keys, dataset_id: UUID,
                      ramp_vendor_id: str, detected_via: str) -> list[DetectedChange]:
    vendor = await ramp.get_vendor(ramp_vendor_id)
    accounts = await ramp.list_vendor_accounts(ramp_vendor_id)

    # Phase 1: have we synced this vendor before, and which of Ramp's accounts do we know?
    # One statement, so both answers come from one snapshot: a webhook and a poll racing at
    # first sight can't see the vendor row without the accounts written with it (spec 0050).
    async with system_tx(pool) as conn:
        rows = await (await conn.execute(
            "select b.ramp_account_id from app.vendors v left join app.bank_accounts b on b.vendor_id = v.id "
            "where v.dataset_id = %s and v.ramp_vendor_id = %s", (dataset_id, ramp_vendor_id))).fetchall()
    known = {r["ramp_account_id"] for r in rows if r["ramp_account_id"] is not None}
    # First sight of the vendor itself (no row yet) is a baseline, not a change. That is only
    # safe after the dataset's initial import (spec 0002, D4 and Q2). A vendor we synced before
    # is never a baseline, even with no account on file: its first account is a change with no
    # previous account ("we're moving from checks to ACH", spec 0050).
    baseline = not rows
    new_active = [] if baseline else [a for a in accounts if a.is_active and a.id not in known]

    # Phase 2: who made each new account, from Ramp's audit log.
    actors: dict[str, str | None] = {}
    if new_active:
        events = await ramp.get_audit_events(ramp_vendor_id, BANK_CHANGE_EVENT)
        by_account = {str(e.payload.get("new_account_id")): e.actor_id for e in events if e.event_type == BANK_CHANGE_EVENT}
        actors = {a.id: by_account.get(a.id) for a in new_active}

    # Phase 3: mirror, record each change, open or join the case. One transaction.
    changes: list[DetectedChange] = []
    async with system_tx(pool) as conn:
        now = await dataset_now(conn, dataset_id)
        vendor_id = (await (await conn.execute(
            """insert into app.vendors (dataset_id, ramp_vendor_id, legal_name, ramp_status, ramp_updated_at, synced_at)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (dataset_id, ramp_vendor_id) do update
                 set legal_name = excluded.legal_name, ramp_status = excluded.ramp_status,
                     ramp_updated_at = excluded.ramp_updated_at, synced_at = excluded.synced_at
               returning id""",
            (dataset_id, vendor.id, vendor.name, vendor.status, vendor.updated_at, now))).fetchone())["id"]
        previous = await (await conn.execute(
            "select id from app.bank_accounts where vendor_id = %s and is_current order by first_seen_at desc, created_at desc limit 1",
            (vendor_id,))).fetchone()
        account_ids: dict[str, UUID] = {}
        for a in accounts:
            account_ids[a.id] = (await (await conn.execute(
                """insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4,
                          account_fingerprint, fingerprint_key_version, bank_name, account_type, first_seen_at, last_seen_at, is_current)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (dataset_id, vendor_id, account_fingerprint, fingerprint_key_version) do update
                     set last_seen_at = excluded.last_seen_at, is_current = excluded.is_current
                   returning id""",
                (dataset_id, vendor_id, a.id, a.routing_number, a.last4, fingerprint(keys, a), keys.fingerprint_key_version,
                 a.bank_name, a.account_type, (a.created_at or now) if baseline else now, now, a.is_active))).fetchone())["id"]

        for a in new_active:
            ramp_actor = actors.get(a.id)
            changed_by = None
            if ramp_actor:
                profile = await (await conn.execute(
                    "select user_id from app.user_profiles where ramp_user_id = %s", (ramp_actor,))).fetchone()
                changed_by = profile["user_id"] if profile else None
            source_event_id = f"ramp-account:{a.id}"
            inserted = await (await conn.execute(
                """insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via,
                          source_event_id, changed_by, changed_by_ramp_user_id, detected_at)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                   on conflict (dataset_id, source_event_id) do nothing returning id""",
                (dataset_id, vendor_id, previous["id"] if previous else None, account_ids[a.id], detected_via,
                 source_event_id, changed_by, ramp_actor, now))).fetchone()
            change_id = inserted["id"] if inserted else (await (await conn.execute(
                "select id from app.bank_change_events where dataset_id = %s and source_event_id = %s",
                (dataset_id, source_event_id))).fetchone())["id"]
            result = await apply_event(conn, None, "change_detected", f"{change_id}:detected", {
                "change_event_id": str(change_id), "policy_version": POLICY_VERSION,
                "detected_via": detected_via, "source_event_id": source_event_id,
            })
            changes.append(DetectedChange(change_id, result.case_id, a.id, changed_by, result.status))
            log.info("bank change %s for vendor %s: case %s %s", source_event_id, vendor.id, result.case_id, result.status)
    return changes


async def import_all(pool: AsyncConnectionPool, ramp: RampPort, keys: Keys, dataset_id: UUID) -> int:
    """Sync every vendor. On a fresh dataset this is the initial import (a baseline); afterwards
    it is the nightly full sweep that catches changes no webhook announced."""
    vendors = await ramp.list_vendors_updated_since(None)
    for v in vendors:
        await sync_vendor(pool, ramp, keys, dataset_id, v.id, "poll")
    return len(vendors)


async def process_inbound_webhooks(pool: AsyncConnectionPool, connector: RampConnector, keys: Keys, limit: int = 20,
                                   dataset_id: UUID | None = None) -> int:
    """Process stored, verified webhook deliveries (all datasets, or one). Returns how many were handled.

    A retryable Ramp error leaves the delivery for the next tick (the poller is the backstop);
    anything else marks it processed with the error, so one bad delivery can't wedge the queue.
    """
    handled, seen = 0, set()
    for _ in range(limit):
        async with system_tx(pool) as conn:
            row = await (await conn.execute(
                "select id, dataset_id, event_type, raw_body from app.inbound_webhooks "
                "where processed_at is null and signature_valid and not (id = any(%s::bigint[])) "
                "and (%s::uuid is null or dataset_id = %s::uuid) "
                "order by received_at, id for update skip locked limit 1", (list(seen), dataset_id, dataset_id))).fetchone()
            if row is None:
                return handled
            seen.add(row["id"])
            error = None
            try:
                payload = json.loads(row["raw_body"])
                vendor_id = (payload.get("data") or {}).get("vendor_id")
                if row["event_type"] == "vendors.updated" and vendor_id:
                    await sync_vendor(pool, connector.for_dataset(row["dataset_id"]), keys, row["dataset_id"], str(vendor_id), "webhook")
                else:
                    error = f"ignored: {row['event_type']} without a vendor id" if row["event_type"] == "vendors.updated" \
                        else f"ignored event type {row['event_type']}"
            except RampError as exc:
                if exc.retryable:
                    await conn.execute("update app.inbound_webhooks set error = %s where id = %s", (str(exc), row["id"]))
                    continue
                error = str(exc)
            except ValueError as exc:
                error = f"unreadable payload: {exc}"
            await conn.execute(
                "update app.inbound_webhooks set processed_at = app.now(dataset_id), error = %s where id = %s", (error, row["id"]))
            handled += 1
    return handled
