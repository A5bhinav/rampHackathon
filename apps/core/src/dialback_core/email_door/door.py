"""What the door does with a fetched email (specs 0085 and 0087; design sections 3 to 7).

In order: the door must be on (the current demo dataset, with door vendors); the email is flagged
(the rule, or the quarantined model, in a thread); it is matched to a vendor and its bank details are
read (pure code, also for an unflagged email: one from a door vendor naming an account other than the
one on file is a silent change, and counts as flagged). Then, ledger first, it is filed as the
vendor's change request and committed, so the selection rule sees it (R2); and in a second
transaction the requested account and the change are written and `change_detected` opens or joins
the case. Ramp is never written. Every stop is an outcome on the email's row, and only a row still
`pending` is ever changed, so an email is acted on once.

ADR-8: the full account number lives only in `handle_email`'s memory, long enough to fingerprint it.
It is written nowhere and logged nowhere; an error from the ledger write on is recorded by its type
name only, with no case (R7). The email's text is data, never instructions (R9).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, replace
from email.utils import parseaddr
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, UUID, uuid5

from dialback_ledger.candidates import parse_phone
from dialback_ledger.ingest import ingest_all
from dialback_ledger.lookalike import sender_domain
from dialback_ledger.store import mark_change_request, write_ingest
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from ..db import dataset_now, system_tx
from ..detection import POLICY_VERSION, Keys
from ..events import apply_event
from ..settings import Settings
from .masking import mask_digit_runs, strip_nul
from .match import VendorRef, match_vendor
from .resend import ReceivedEmail, ResendPort
from .rules import find_accounts, find_routing, flag_email, ledger_text, requested_fingerprint

if TYPE_CHECKING:
    from .classifier import ChangeClassifier

log = logging.getLogger(__name__)

EXTRACTOR = "email-door-v1"
PAGE_LINES = 51   # a span at y = 72 + 14 * i stays on the 792-point page for i < 51; later lines start a new page
REUSED_MESSAGE_ID = "this Message-ID already made a change for another account or vendor"


def thread_id_of(email: ReceivedEmail) -> str | None:
    """The first ID in References, else In-Reply-To, else Message-ID."""
    for name in ("references", "in-reply-to"):
        ids = strip_nul(email.headers.get(name) or "").split()
        if ids:
            return ids[0]
    return strip_nul(email.message_id or email.headers.get("message-id") or "") or None


def _message_key(email: ReceivedEmail) -> str:
    """What makes two deliveries the same email: its Message-ID, or Resend's id when it has none."""
    return strip_nul(email.message_id) or f"resend:{email.email_id}"


def ledger_document(email: ReceivedEmail, ledger_lines: str, masked_subject: str, received_at: dt.datetime,
                    thread_id: str | None) -> dict:
    """The email as a ledger document, in the shape of a `request` in demo_seed.json: one span per line of
    `ledger_lines` (rules.ledger_text's rebuilt phone lines, nothing else from the body), black, size 10,
    x 72, y 72 plus 14 per line, on 612 by 792 pages. Custody: channel `email`, our receive time, the
    sender with digit runs masked, the thread ID and the masked subject stored in app.inbound_emails."""
    lines = [line for line in ledger_lines.split("\n") if line]
    pages = [{"width": 612, "height": 792,
              "spans": [{"text": line, "x": 72, "y": 72 + 14 * i, "size": 10, "color": "#000000"}
                        for i, line in enumerate(lines[start:start + PAGE_LINES])]}
             for start in range(0, max(len(lines), 1), PAGE_LINES)]
    return {
        "id": str(uuid5(NAMESPACE_URL, "email:" + _message_key(email))),
        "kind": "email", "claimed_date": None, "header_date": None, "scan": False,
        "custody": {"channel": "email", "received_at": received_at.isoformat(),
                    "sender": mask_digit_runs(strip_nul(email.sender)) or None, "thread_id": thread_id,
                    "subject": masked_subject, "parent_document_id": None},
        "pages": pages,
    }


@dataclass(frozen=True)
class _Facts:
    """What the door decided about one email: the outcome and what its inbound_emails row records."""
    outcome: str
    flagged: bool = False
    vendor_id: UUID | None = None
    case_id: UUID | None = None
    change_event_id: UUID | None = None
    routing_number: str | None = None
    last4: str | None = None
    red_flags: tuple[str, ...] = ()
    error: str | None = None


async def _record(conn: AsyncConnection, dataset_id: UUID, email: ReceivedEmail, facts: _Facts) -> str:
    """Write the outcome to the email's row if it is still pending; otherwise report what is there."""
    done = await conn.execute(
        """update app.inbound_emails set raw_sha256 = %s, has_attachments = %s, subject = %s, thread_id = %s,
                  sender_domain = %s, message_id = coalesce(%s, message_id), outcome = %s, flagged = %s, vendor_id = %s,
                  case_id = %s, change_event_id = %s, routing_number = %s, last4 = %s, red_flags = %s, error = %s,
                  processed_at = %s
            where dataset_id = %s and resend_email_id = %s and outcome = 'pending'""",
        (email.raw_sha256, email.has_attachments, mask_digit_runs(strip_nul(email.subject)), thread_id_of(email),
         strip_nul(sender_domain(email.sender) or "") or None, strip_nul(email.message_id) or None, facts.outcome,
         facts.flagged, facts.vendor_id, facts.case_id, facts.change_event_id, facts.routing_number, facts.last4,
         list(facts.red_flags), facts.error, await dataset_now(conn, dataset_id), dataset_id, email.email_id))
    if done.rowcount == 1:
        return facts.outcome
    return await _settled(conn, dataset_id, email)


async def _settled(conn: AsyncConnection, dataset_id: UUID, email: ReceivedEmail, lock: bool = False) -> str:
    row = await (await conn.execute(
        "select outcome from app.inbound_emails where dataset_id = %s and resend_email_id = %s" + (" for update" if lock else ""),
        (dataset_id, email.email_id))).fetchone()
    if row is None:
        raise RuntimeError("the email has no inbound_emails row")
    return row["outcome"]


async def _finish(pool: AsyncConnectionPool, dataset_id: UUID, email: ReceivedEmail, facts: _Facts) -> str:
    async with system_tx(pool) as conn:
        return await _record(conn, dataset_id, email, facts)


async def _vendor_refs(conn: AsyncConnection, dataset_id: UUID) -> list[VendorRef]:
    rows = await (await conn.execute(
        """select v.id, v.legal_name, v.trade_names,
                  coalesce(array_agg(d.domain order by d.domain) filter (where d.domain is not null), '{}') as domains
             from app.vendors v
             left join ledger.vendor_domains d on d.dataset_id = v.dataset_id and d.vendor_id = v.id
            where v.dataset_id = %s group by v.id""", (dataset_id,))).fetchall()
    return [VendorRef(r["id"], r["legal_name"], tuple(r["trade_names"]), tuple(r["domains"])) for r in rows]


def _header_flags(email: ReceivedEmail) -> list[str]:
    flags = []
    sender = parseaddr(strip_nul(email.sender))[1].lower()
    if any(parseaddr(strip_nul(r))[1].lower() != sender for r in email.reply_to):
        flags.append("reply_to_differs")
    auth = email.authentication or {}
    if "pass" not in {str(auth.get(k) or "").strip().lower() for k in ("dmarc", "dkim")}:
        flags.append("sender_unauthenticated")
    return flags


async def _unknown_phone(conn: AsyncConnection, dataset_id: UUID, vendor_id: UUID, phones: set[str]) -> bool:
    """Whether a phone in the email is not among the vendor's ledger sightings so far, leaving out change
    requests (a number first seen in one is not on file)."""
    if not phones:
        return False
    known = {r["value_normalized"] for r in await (await conn.execute(
        """select distinct o.value_normalized from ledger.observations o
            where o.dataset_id = %s and o.vendor_id = %s and o.kind = 'phone'
              and not exists (select 1 from ledger.change_requests r where r.document_id = o.document_id)""",
        (dataset_id, vendor_id))).fetchall()}
    return bool(phones - known)


async def _same_email(conn: AsyncConnection, dataset_id: UUID, vendor_id: UUID, source_event_id: str,
                      fingerprint: str, key_version: int) -> tuple[str, UUID | None] | None:
    """A change this Message-ID already made: ("repeat_request", its case) when it was for this vendor and
    account, ("failed", None) when it was for another; None when it made none."""
    row = await (await conn.execute(
        """select ce.vendor_id, b.account_fingerprint, b.fingerprint_key_version, r.case_id
             from app.bank_change_events ce
             join app.bank_accounts b on b.id = ce.new_account_id
             left join app.case_event_receipts r on r.idempotency_key = ce.id::text || ':detected'
            where ce.dataset_id = %s and ce.source_event_id = %s""", (dataset_id, source_event_id))).fetchone()
    if row is None:
        return None
    same = (row["vendor_id"], row["account_fingerprint"], row["fingerprint_key_version"]) == (vendor_id, fingerprint, key_version)
    return ("repeat_request", row["case_id"]) if same else ("failed", None)


async def _repeat_case(conn: AsyncConnection, dataset_id: UUID, vendor_id: UUID, fingerprint: str, key_version: int) -> UUID | None:
    """The vendor's unfinished case, when one of its email changes asked for this account."""
    row = await (await conn.execute(
        """select c.id from app.cases c
            where c.dataset_id = %s and c.vendor_id = %s and c.state <> 'released'
              and exists (select 1 from app.case_event_receipts r
                            join app.bank_change_events ce on r.idempotency_key = ce.id::text || ':detected'
                            join app.bank_accounts b on b.id = ce.new_account_id
                           where r.case_id = c.id and r.event_type = 'change_detected' and ce.detected_via = 'email'
                             and b.account_fingerprint = %s and b.fingerprint_key_version = %s)""",
        (dataset_id, vendor_id, fingerprint, key_version))).fetchone()
    return row["id"] if row else None


async def handle_email(pool: AsyncConnectionPool, settings: Settings, keys: Keys, resend: ResendPort, dataset_id: UUID,
                       delivery_id: int, email: ReceivedEmail, classifier: ChangeClassifier | None = None) -> str:
    """Fill in the delivery's inbound_emails row from the fetched email, act on it, and return its outcome.
    The subject and sender are stored with long digit runs masked (ADR-8); the body is not stored at all.
    A ClassifierFailed, or any error before the ledger write, propagates, so the inbox retries the email."""
    subject, text, html = strip_nul(email.subject), strip_nul(email.text), strip_nul(email.html_text)
    async with system_tx(pool) as conn:
        state = await (await conn.execute(
            """select e.outcome, e.received_at, d.email_door_vendors as door,
                      d.kind = 'demo' and d.retired_at is null as current_demo
                 from app.inbound_emails e join app.datasets d on d.id = e.dataset_id
                where e.dataset_id = %s and e.resend_email_id = %s""", (dataset_id, email.email_id))).fetchone()
    if state is None:
        raise RuntimeError(f"delivery {delivery_id} has no inbound_emails row for its email")
    if state["outcome"] != "pending":
        return state["outcome"]   # another delivery of this email got here first: act once
    door = set(state["door"])
    if not (state["current_demo"] and door):
        return await _finish(pool, dataset_id, email, _Facts("door_off"))

    # 1. Flag, in a thread: the model's SDK client is synchronous and the worker shares the app's event loop.
    # Then match and extract, which are pure: an unflagged email from a door vendor can still be a silent change.
    decision = await asyncio.to_thread(flag_email, subject, text, html, classifier)
    body = text + "\n" + html
    async with system_tx(pool) as conn:
        match = match_vendor(email.sender, subject, body, await _vendor_refs(conn, dataset_id))
        current = None if match.vendor_id is None else await (await conn.execute(
            "select id, routing_number, account_last4 from app.bank_accounts where vendor_id = %s and is_current "
            "order by first_seen_at desc, created_at desc limit 1", (match.vendor_id,))).fetchone()
    routings, accounts = find_routing(body), find_accounts(body)
    routing = routings[0] if routings else None
    account = max(accounts, key=len) if accounts else None   # the first longest: a last-four shape can come first
    last4 = account[-4:] if account else None
    on_file = current is not None and (routing, last4) == (current["routing_number"], current["account_last4"])
    flagged_by = decision.by if decision.flagged else None
    if flagged_by is None and match.vendor_id in door and routing and account and not on_file:
        flagged_by = "on_file_mismatch"
    if flagged_by is None:
        return await _finish(pool, dataset_id, email, _Facts("not_flagged"))

    # 2. Red flags, and the ledger's copy: rebuilt phone lines only, with every account found masked.
    masked_subject = mask_digit_runs(subject)
    secrets = accounts + find_accounts(subject)
    document = ledger_document(email, ledger_text(text, also_mask=secrets) + "\n" + ledger_text(html, also_mask=secrets),
                               masked_subject, state["received_at"], thread_id_of(email))
    red_flags = [*match.red_flags, *_header_flags(email)]
    if match.vendor_id is not None:
        phones = {p for page in document["pages"] for span in page["spans"] for p in parse_phone(span["text"])}
        async with system_tx(pool) as conn:
            if await _unknown_phone(conn, dataset_id, match.vendor_id, phones):
                red_flags.append("unknown_phone")
    facts = _Facts("", flagged=True, vendor_id=match.vendor_id, routing_number=routing, last4=last4, red_flags=tuple(red_flags))
    log.info("email door: inbound email %s flagged by %s, vendor match %s", email.email_id, flagged_by, match.how)

    # 3. Enough information? (design section 6)
    stop = ("no_vendor" if match.how == "none" else "several_vendors" if match.how == "several"
            else "not_demo_vendor" if match.vendor_id not in door else "no_routing_number" if routing is None
            else "no_account_number" if account is None else "same_as_on_file" if on_file else None)
    if stop is not None:
        return await _finish(pool, dataset_id, email, replace(facts, outcome=stop))
    vendor_id = match.vendor_id
    fingerprint = requested_fingerprint(keys.fingerprint_key, routing, account)   # ADR-8: nothing below uses the full number
    source_event_id = f"email:{_message_key(email)}"

    # 4. The same email again, or the same account while the vendor's case is unfinished: nothing written.
    async with system_tx(pool) as conn:
        same = await _same_email(conn, dataset_id, vendor_id, source_event_id, fingerprint, keys.fingerprint_key_version)
        repeat = None if same else await _repeat_case(conn, dataset_id, vendor_id, fingerprint, keys.fingerprint_key_version)
        if same:
            return await _record(conn, dataset_id, email, replace(
                facts, outcome=same[0], case_id=same[1], error=REUSED_MESSAGE_ID if same[0] == "failed" else None))
        if repeat:
            return await _record(conn, dataset_id, email, replace(facts, outcome="repeat_request", case_id=repeat))

    # 5. Ledger first, committed: the rule only uses a change request recorded before the case opened. Then
    # the case. From here any error is `failed` with no case (R7), recorded by its type name only.
    try:
        async with system_tx(pool) as conn:
            await write_ingest(conn, dataset_id=dataset_id, vendor_id=vendor_id,
                               result=ingest_all([document], extractor_version=EXTRACTOR))
            await mark_change_request(conn, dataset_id=dataset_id, vendor_id=vendor_id, document_id=document["id"])
        async with system_tx(pool) as conn:
            outcome = await _settled(conn, dataset_id, email, lock=True)   # held to the end: one worker opens the case
            if outcome != "pending":
                return outcome
            return await _open_case(conn, dataset_id, email, facts, keys, fingerprint, source_event_id,
                                    current["id"] if current else None, thread_id_of(email))
    except Exception as exc:  # noqa: BLE001  fail closed (R7); the message can hold the email's values (ADR-8)
        log.warning("email door: opening a case for inbound email %s failed: %s", email.email_id, type(exc).__name__)
        return await _finish(pool, dataset_id, email, replace(facts, outcome="failed", error=type(exc).__name__))


async def _open_case(conn: AsyncConnection, dataset_id: UUID, email: ReceivedEmail, facts: _Facts, keys: Keys,
                     fingerprint: str, source_event_id: str, old_account_id: UUID | None, thread_id: str | None) -> str:
    """Write the requested account and the change, open or join the case, and record the outcome, in the
    caller's transaction. The requested account is never `is_current` and has no Ramp id, so neither the
    Ramp sync nor the clerk portal ever takes it for the account on file."""
    now = await dataset_now(conn, dataset_id)
    account_id = (await (await conn.execute(
        """insert into app.bank_accounts (dataset_id, vendor_id, ramp_account_id, routing_number, account_last4,
                  account_fingerprint, fingerprint_key_version, first_seen_at, last_seen_at, is_current)
           values (%s, %s, null, %s, %s, %s, %s, %s, %s, false)
           on conflict (dataset_id, vendor_id, account_fingerprint, fingerprint_key_version) do update
             set last_seen_at = excluded.last_seen_at
           returning id""",
        (dataset_id, facts.vendor_id, facts.routing_number, facts.last4, fingerprint, keys.fingerprint_key_version, now, now),
    )).fetchone())["id"]
    inserted = await (await conn.execute(
        """insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via,
                  source_event_id, changed_by, change_thread_id, detected_at)
           values (%s, %s, %s, %s, 'email', %s, null, %s, %s)
           on conflict (dataset_id, source_event_id) do nothing returning id""",
        (dataset_id, facts.vendor_id, old_account_id, account_id, source_event_id, thread_id, now))).fetchone()
    if inserted is None:   # another delivery of this Message-ID made the change since the check above
        outcome, case_id = await _same_email(conn, dataset_id, facts.vendor_id, source_event_id, fingerprint,
                                             keys.fingerprint_key_version) or ("failed", None)
        return await _record(conn, dataset_id, email, replace(
            facts, outcome=outcome, case_id=case_id, error=REUSED_MESSAGE_ID if outcome == "failed" else None))
    change_id = inserted["id"]
    result = await apply_event(conn, None, "change_detected", f"{change_id}:detected", {
        "change_event_id": str(change_id), "policy_version": POLICY_VERSION,
        "detected_via": "email", "source_event_id": source_event_id,
    })
    outcome = "case_opened" if result.from_state is None else "joined_case"
    return await _record(conn, dataset_id, email, replace(facts, outcome=outcome, case_id=result.case_id, change_event_id=change_id))
