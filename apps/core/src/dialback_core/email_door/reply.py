"""The email door's one fixed reply and when it is held back (spec 0088; design section 8).

Every flagged email gets the same words, whatever the door did with it, so the reply tells a sender
nothing (not whether a vendor matched, a case opened, or what was missing). It goes only to the address
in `From`, never `Reply-To`, only when the receiving server saw DMARC or DKIM pass, never to robots,
lists or ourselves, at most once per sender per day and 20 an hour. With `settings.reply_from` unset
nothing is sent and the row says `not_configured` (so does a missing `bank_changes_url`: the reply carries
the link). No email text or address is logged; the reply holds nothing from the incoming email, not even its
subject (ADR-8).

Who the reply goes to: the address in the raw `From` header (`headers["from"]`, with its display name), which
must hold exactly one mailbox and must equal the bare address Resend reports as `sender`; any difference is
suppressed. Authenticated means Resend saw DMARC or DKIM pass on its own mail server. Resend reports dkim as
"gray" when the signing domain does not match From, so a dkim "pass" is already aligned with From.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from email.utils import getaddresses
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
SUBJECT = "Re: your message about payment details"
MAX_MESSAGE_ID = 250
MESSAGE_ID = re.compile(r"<[\x21-\x7e]+>")   # printable ASCII, no spaces
PER_SENDER_PER_DAY = 1
PER_HOUR = 20
NO_REPLY_LOCAL_PARTS = frozenset({"noreply", "no-reply", "mailer-daemon"})
BULK_PRECEDENCE = frozenset({"bulk", "list", "junk"})


@dataclass(frozen=True)
class ReplyDecision:
    send: bool
    status: str   # a value of app.inbound_emails.reply


def sender_address(sender: str) -> str | None:
    """The lowercase address of a From header holding exactly one plain mailbox, else None."""
    found = getaddresses([strip_nul(sender)], strict=True)
    if len(found) != 1:
        return None
    address = found[0][1].strip().lower()
    local, at, domain = address.rpartition("@")
    if not (local and at and domain) or any(c.isspace() or c in "<>,;\"" or ord(c) < 32 for c in address):
        return None
    return address


def is_authenticated(authentication: dict[str, str] | None) -> bool:
    """Resend saw DMARC or DKIM pass (dkim is "gray" when the signing domain does not match From)."""
    auth = authentication or {}
    return any(str(auth.get(k) or "").strip().lower() == "pass" for k in ("dmarc", "dkim"))


def reply_address(email: ReceivedEmail) -> str | None:
    """Where the reply would go: the one mailbox of the raw From header, if it is the address Resend reports as sender."""
    raw = sender_address(email.headers.get("from", ""))
    return raw if raw is not None and raw == sender_address(email.sender.strip()) else None


def decide_reply(email: ReceivedEmail, *, inbox_address: str | None, configured: bool,
                 sent_to_sender_24h: int, sent_last_hour: int, reply_from: str | None = None) -> ReplyDecision:
    if not configured:
        return ReplyDecision(False, "not_configured")
    address = reply_address(email)
    ours = {a for a in (sender_address(inbox_address or ""), sender_address(reply_from or "")) if a}
    domains = {a.rpartition("@")[2] for a in (sender_address(inbox_address or ""),) if a}
    headers = email.headers
    auto = headers.get("auto-submitted")
    if (address is None
            or (auto is not None and auto.strip().lower() != "no")
            or headers.get("precedence", "").strip().lower() in BULK_PRECEDENCE
            or "list-id" in headers
            or address in ours or address.rpartition("@")[2] in domains
            or address.rpartition("@")[0] in NO_REPLY_LOCAL_PARTS):
        return ReplyDecision(False, "suppressed")
    if not is_authenticated(email.authentication):
        return ReplyDecision(False, "unauthenticated")
    if sent_to_sender_24h >= PER_SENDER_PER_DAY or sent_last_hour >= PER_HOUR:
        return ReplyDecision(False, "rate_limited")
    return ReplyDecision(True, "sent")


def compose_reply(company: str, url: str) -> tuple[str, str]:
    """(subject, text): both fixed; nothing of the incoming email is in them."""
    return SUBJECT, REPLY_TEXT.format(company=company, url=url).strip()


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
            address = reply_address(email)
            wanted = mask_digit_runs(address) if address else None   # the masked form rows store
            senders = await (await conn.execute(
                "select sender from app.inbound_emails where dataset_id = %s and reply = 'sent' "
                "and received_at > %s - interval '24 hours'", (dataset_id, now))).fetchall()
            hour = (await (await conn.execute(
                "select count(*) as n from app.inbound_emails where dataset_id = %s and reply = 'sent' "
                "and received_at > %s - interval '1 hour'", (dataset_id, now))).fetchone())["n"]
            decision = decide_reply(
                email, inbox_address=settings.inbox_address, reply_from=settings.reply_from,
                configured=bool(settings.reply_from and settings.bank_changes_url),
                sent_to_sender_24h=sum(1 for s in senders if _same_sender(s["sender"], wanted)),
                sent_last_hour=hour)
            await conn.execute("update app.inbound_emails set reply = %s where id = %s", (decision.status, row["id"]))
        if not decision.send:
            return
        subject, text = compose_reply(settings.company_name, settings.bank_changes_url or "")
        message_id = strip_nul(email.message_id)
        in_reply_to = message_id if len(message_id) <= MAX_MESSAGE_ID and MESSAGE_ID.fullmatch(message_id) else None
        try:
            await resend.send(sender=settings.reply_from or "", to=address or "", subject=subject, text=text,
                              in_reply_to=in_reply_to, idempotency_key=f"reply-{email.email_id}")
        except Exception as exc:  # noqa: BLE001  the text can hold the address; keep only the type name
            log.warning("email door: the reply to inbound email %s failed: %s", email.email_id, type(exc).__name__)
            async with system_tx(pool) as conn:
                await conn.execute("update app.inbound_emails set reply = 'failed' where id = %s", (row["id"],))
    except Exception as exc:  # noqa: BLE001  never undo the case outcome
        log.warning("email door: deciding the reply to inbound email %s failed: %s", email.email_id, type(exc).__name__)


def _same_sender(stored: str, wanted: str | None) -> bool:
    """Whether a stored (masked) sender is the address we reply to. One that cannot be read counts as a match."""
    mine = sender_address(stored)
    return mine is None or wanted is None or mine == wanted
