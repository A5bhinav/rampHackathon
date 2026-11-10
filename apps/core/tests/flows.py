"""Shared steps for core tests: drive a case through the money path the way the services do."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from conftest import CONTACT_POINT, INTERNAL_TOKEN
from dialback_core.db import person_tx, system_tx
from dialback_core.email_door.resend import ReceivedEmail, ResendError

CLEAN_CHECKS = {"disclosure_first": True, "answers_grounded": True, "agent_spoke_account_digits": False,
                "new_number_accepted": False, "policy_violations": 0, "speech_guard_trips": 0}


async def one(pool, sql: str, *args):
    async with system_tx(pool) as conn:
        return await (await conn.execute(sql, args)).fetchone()


async def case_of(world):
    return await one(world.pool, "select * from app.cases where dataset_id = %s order by opened_at desc limit 1", world.dataset.id)


async def add_vendor(world, ref, name, accounts=(), bills=()) -> str:
    """Add a vendor to this test's mock business (the mock's seed only adds). Returns its Ramp id.
    With no accounts it is a vendor paid by check: no bank account on file (spec 0050)."""
    body = {"vendors": [{"ref": ref, "name": name, "accounts": list(accounts), "bills": list(bills)}]}
    r = await world.mock.post("/_mock/seed", json=body, headers=world.auth)
    assert r.status_code == 200, r.text
    world.refs |= r.json()["refs"]
    return world.refs[ref]


async def change_and_detect(world, account_number="000055556666", actor="clerk"):
    await world.bank_change(account_number=account_number, actor=actor)
    await world.deliver_webhooks()
    await world.worker.drain(world.dataset.id)


def answer(slot, value, turn):
    """One answer as the voice service reports it, captured in turn order."""
    return {"slot": slot, "value": value, "turn_id": turn, "tool_call_id": f"tc_{turn}",
            "captured_at": f"2026-11-03T17:02:{turn:02d}Z", "capture_method": "keypad" if slot == "account_last4" else "speech"}


def transit(call_id, digits="6666", company="Acme Fasteners", change="yes"):
    return {"schema_version": "call-answers/v0", "call_id": str(call_id), "answers": [
        answer("recording_consent", "yes", 1), answer("company_named", company, 3), answer("speaker_name", "Jordan Lee", 5),
        answer("speaker_role", "AR manager", 5), answer("change_requested", change, 7), answer("account_last4", digits, 9)]}


async def report(world, case_id, call_id, attempt=1, result="answers_recorded", answers=None, token=INTERNAL_TOKEN, key=True,
                 contact_point=CONTACT_POINT, retry_at=None, telephony=None):
    """`telephony` overrides the voice service's Twilio facts; {} sends none (an older report)."""
    body = {"call_id": str(call_id), "case_id": str(case_id), "result": result, "attempt": attempt,
            "agent_version": "agent-test", "checks": CLEAN_CHECKS}
    if telephony is None:
        telephony = {"direction": "outbound", "dialed_number_is_pick": True}
        if result == "answers_recorded":
            telephony["answered_by"] = "human"
    body |= telephony
    if result == "answers_recorded" or answers is not None:
        body["answers"] = answers or transit(call_id)
    if contact_point is not None:
        body["contact_point_id"] = str(contact_point)
    if retry_at is not None:
        body["retry_at"] = retry_at
    headers = {"Authorization": f"Bearer {token.decode()}"}
    if key:
        headers["Idempotency-Key"] = f"{case_id}:call:{attempt}:ended"
    return await world.core.post(f"/internal/calls/{call_id}/ended", json=body, headers=headers)


async def release(world, case_id, version, who="approver1"):
    async with person_tx(world.pool, world.users[who], {}) as conn:
        return (await (await conn.execute("select app.release_case(%s, %s, 'approve', %s) as r",
                                          (case_id, version, "Confirmed on the ledger's number; digits match."))).fetchone())["r"]


async def bills(world):
    return {b.id: b for b in await world.ramp.list_bills_for_vendor(world.refs["acme"])}


async def pay(world, as_of: str) -> list[str]:
    r = await world.mock.post("/_mock/payment-run", json={"as_of": as_of}, headers=world.auth)
    return r.json()["paid_bill_ids"]


async def _types(world, case_id):
    async with system_tx(world.pool) as conn:
        return await (await conn.execute("select type from audit.evidence_events where case_id = %s order by seq", (case_id,))).fetchall()


async def assert_nowhere(pool, caplog, needle: str) -> None:
    """`needle` is in no base table of app, audit, ledger, voice or lab, and in no captured log line."""
    assert needle not in caplog.text
    async with system_tx(pool) as conn:
        tables = await (await conn.execute(
            "select table_schema, table_name from information_schema.tables "
            "where table_schema in ('app', 'audit', 'ledger', 'voice', 'lab') and table_type = 'BASE TABLE'")).fetchall()
        for t in tables:
            hit = await (await conn.execute(
                f'select count(*) as n from "{t["table_schema"]}"."{t["table_name"]}" x where x::text like %s', (f"%{needle}%",))).fetchone()
            assert hit["n"] == 0, f"{t['table_schema']}.{t['table_name']} holds {needle!r}"


RESEND_SECRET = "whsec_" + base64.b64encode(b"resend-test-signing-key-0123456789").decode()


def svix_headers(raw: bytes, msg_id: str, ts: int, secret: str = RESEND_SECRET) -> dict[str, str]:
    """The headers Resend (through Svix) signs a notice with."""
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + raw, hashlib.sha256).digest()
    return {"svix-id": msg_id, "svix-timestamp": str(ts), "svix-signature": "v1," + base64.b64encode(mac).decode()}


async def deliver(w, email_id: str | None = None, sender: str = "Dana <ar@vendor.example>", kind: str = "email.received",
                  message_id: str | None = None) -> str:
    """One signed Resend notice through POST /webhooks/email (the core's settings must use RESEND_SECRET).
    Returns the email id."""
    email_id = email_id or f"em_{uuid.uuid4().hex}"
    raw = json.dumps({"type": kind, "created_at": "2026-11-10T00:00:00Z",
                      "data": {"email_id": email_id, "from": sender, "to": ["inbox@x.example"],
                               "message_id": message_id or f"<{email_id}@v.example>"}}).encode()
    r = await w.core.post("/webhooks/email", content=raw, headers=svix_headers(raw, f"msg_{uuid.uuid4().hex}", int(time.time())))
    assert r.status_code == 200, r.text
    return email_id


def received(email_id: str, **changes) -> ReceivedEmail:
    base = {"email_id": email_id, "message_id": f"<{email_id}@vendor.example>", "sender": "Dana <ar@vendor.example>",
            "reply_to": (), "to": ("inbox@dialback.example",), "subject": "New bank details",
            "text": "Please update our account.", "html_text": "", "headers": {},
            "authentication": {"spf": "pass", "dkim": "pass", "dmarc": "pass"}, "raw_sha256": "ab" * 32,
            "has_attachments": False}
    email = base | changes
    email["headers"] = {"from": email["sender"]} | email["headers"]   # the raw From header, as Resend keeps it
    return ReceivedEmail(**email)


class FakeResend:
    """TEST DOUBLE for Resend: `emails` maps an email id to what get_received returns or raises; `sent` records replies."""

    def __init__(self, emails: dict | None = None) -> None:
        self.emails: dict[str, ReceivedEmail | Exception] = emails or {}
        self.fetched: list[str] = []
        self.sent: list[dict] = []
        self.send_error: Exception | None = None   # when set, send raises it

    async def get_received(self, email_id: str) -> ReceivedEmail:
        self.fetched.append(email_id)
        found = self.emails.get(email_id, ResendError("resend answered 404"))
        if isinstance(found, Exception):
            raise found
        return found

    async def send(self, *, sender, to, subject, text, in_reply_to, idempotency_key) -> str:
        if self.send_error is not None:
            raise self.send_error
        self.sent.append({"sender": sender, "to": to, "subject": subject, "text": text, "in_reply_to": in_reply_to,
                          "idempotency_key": idempotency_key})
        return f"sent_{len(self.sent)}"
