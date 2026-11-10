"""The email door's one fixed reply and when it is held back (spec 0088; design section 8).

Every flagged email gets the same words, whatever the door did with it, so the reply tells a sender
nothing (not whether a vendor matched, a case opened, or what was missing). It goes only to the address
in `From`, never `Reply-To`, only when the receiving server saw DMARC or DKIM pass, never to robots,
lists or ourselves, at most once per sender per day and 20 an hour. With `settings.reply_from` unset
nothing is sent and the row says `not_configured`. No email text or address is logged; the reply holds
nothing from the email except its subject in `Re: ...`, with digit runs masked (ADR-8).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from email.utils import parseaddr
from typing import TYPE_CHECKING
from uuid import UUID

from ..db import dataset_now, system_tx
from .masking import mask_digit_runs, strip_nul
from .resend import ReceivedEmail, ResendPort

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from ..settings import Settings

log = logging.getLogger(__name__)

REPLY_TEXT = ("We never change payment details because of an email. Every change is confirmed using contact details "
              "we already have for you, not ones sent in the request. To change your payment details, contact your "
              "account contact at {company} using details you already have for them. We will never ask you to confirm "
              "by replying to an email or calling a number someone sent you.\n\n{url}")
MAX_SUBJECT = 200
PER_SENDER_PER_DAY = 1
PER_HOUR = 20
NO_REPLY_LOCAL_PARTS = frozenset({"noreply", "no-reply", "mailer-daemon"})
BULK_PRECEDENCE = frozenset({"bulk", "list", "junk"})


@dataclass(frozen=True)
class ReplyDecision:
    send: bool
    status: str   # a value of app.inbound_emails.reply


def sender_address(sender: str) -> str | None:
    """The lowercase address of a From header, or None if there is not exactly one plain address."""
    address = parseaddr(strip_nul(sender))[1].strip().lower()
    local, at, domain = address.rpartition("@")
    if not (local and at and domain) or any(c.isspace() or c in "<>,;\"" or ord(c) < 32 for c in address):
        return None
    return address


def decide_reply(email: ReceivedEmail, *, inbox_address: str | None, configured: bool,
                 sent_to_sender_24h: int, sent_last_hour: int) -> ReplyDecision:
    if not configured:
        return ReplyDecision(False, "not_configured")
    address = sender_address(email.sender)
    headers = email.headers
    auto = headers.get("auto-submitted")
    if (address is None
            or (auto is not None and auto.strip().lower() != "no")
            or headers.get("precedence", "").strip().lower() in BULK_PRECEDENCE
            or "list-id" in headers
            or address == (inbox_address or "").strip().lower()
            or address.rpartition("@")[0] in NO_REPLY_LOCAL_PARTS):
        return ReplyDecision(False, "suppressed")
    auth = email.authentication
    if auth is None or not (auth.get("dmarc") == "pass" or auth.get("dkim") == "pass"):
        return ReplyDecision(False, "unauthenticated")
    if sent_to_sender_24h >= PER_SENDER_PER_DAY or sent_last_hour >= PER_HOUR:
        return ReplyDecision(False, "rate_limited")
    return ReplyDecision(True, "sent")


def compose_reply(email: ReceivedEmail, company: str, url: str) -> tuple[str, str]:
    """(subject, text). The subject is the original with line breaks and NUL gone, capped, digit runs masked."""
    original = " ".join(strip_nul(email.subject).replace("\r", " ").replace("\n", " ").split())[:MAX_SUBJECT]
    original = mask_digit_runs(original) or "(no subject)"
    subject = original if original[:3].lower() == "re:" else f"Re: {original}"
    return subject, REPLY_TEXT.format(company=company, url=url).strip()


async def send_reply(pool: AsyncConnectionPool, settings: Settings, resend: ResendPort, dataset_id: UUID,
                     email: ReceivedEmail) -> None:
    """Decide and send the reply for an email whose outcome is final. Never raises: a reply failure must not
    undo the outcome. Only a flagged row whose `reply` is still `not_flagged` is touched, so it happens once.
    The reply is recorded as `sent` before it is sent, under a lock per dataset, so the counts are right even
    with several workers; a send error then changes it to `failed`."""
    try:
        async with system_tx(pool) as conn:
            row = await (await conn.execute(
                "select id, sender, flagged, reply, outcome from app.inbound_emails "
                "where dataset_id = %s and resend_email_id = %s for update", (dataset_id, email.email_id))).fetchone()
            if row is None or not row["flagged"] or row["reply"] != "not_flagged" or row["outcome"] == "pending":
                return
            await conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (f"email-reply:{dataset_id}",))
            now = await dataset_now(conn, dataset_id)
            stored = sender_address(row["sender"])   # the masked form the row stores
            senders = await (await conn.execute(
                "select sender from app.inbound_emails where dataset_id = %s and reply = 'sent' "
                "and received_at > %s - interval '24 hours'", (dataset_id, now))).fetchall()
            hour = (await (await conn.execute(
                "select count(*) as n from app.inbound_emails where dataset_id = %s and reply = 'sent' "
                "and received_at > %s - interval '1 hour'", (dataset_id, now))).fetchone())["n"]
            decision = decide_reply(
                email, inbox_address=settings.inbox_address, configured=bool(settings.reply_from),
                sent_to_sender_24h=sum(1 for s in senders if stored is not None and sender_address(s["sender"]) == stored),
                sent_last_hour=hour)
            await conn.execute("update app.inbound_emails set reply = %s where id = %s", (decision.status, row["id"]))
        if not decision.send:
            return
        address = sender_address(email.sender)
        subject, text = compose_reply(email, settings.company_name, settings.bank_changes_url or "")
        message_id = strip_nul(email.message_id)
        in_reply_to = message_id if message_id and not any(c in message_id for c in "\r\n") else None
        try:
            await resend.send(sender=settings.reply_from or "", to=address or "", subject=subject, text=text,
                              in_reply_to=in_reply_to, idempotency_key=f"reply-{email.email_id}")
        except Exception as exc:  # noqa: BLE001  the text can hold the address; keep only the type name
            log.warning("email door: the reply to inbound email %s failed: %s", email.email_id, type(exc).__name__)
            async with system_tx(pool) as conn:
                await conn.execute("update app.inbound_emails set reply = 'failed' where id = %s", (row["id"],))
    except Exception as exc:  # noqa: BLE001  never undo the case outcome
        log.warning("email door: deciding the reply to inbound email %s failed: %s", email.email_id, type(exc).__name__)

