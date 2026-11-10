"""What the door does with a fetched email (specs 0085 and 0087; design sections 3 to 7).

In order: the door must be on (the current demo dataset, with door vendors); the email is flagged
(the rule, or the quarantined model, in a thread); it is matched to a vendor and its bank details are
read, each mail part on its own (pure code, also for an unflagged email: one from a door vendor that
names bank details but not the account on file is a silent change, and counts as flagged). Then,
ledger first, it is filed as the vendor's change request and committed, so the selection rule sees it
(R2); and in a second transaction, under the vendor's lock, the requested account and the change are
written and `change_detected` opens or joins the case. Ramp is never written. Every stop is an outcome
on the email's row, and only a row still `pending` is ever changed, so an email is acted on once.

ADR-8: the full account number lives only in `handle_email`'s memory, long enough to fingerprint it.
It is written nowhere and logged nowhere. An email whose Message-ID, thread ID or sender domain holds
one of its accounts fails before anything is filed, and those stored headers are scrubbed. An error
from the ledger write on is recorded by its type name only, with no case (R7). The email's text is
data, never instructions (R9).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import unicodedata
from bisect import bisect_left
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
from .rules import (
    MAX_CHARS,
    account_spans,
    find_accounts,
    flag_email,
    ledger_text,
    requested_fingerprint,
    routing_spans,
)

if TYPE_CHECKING:
    from .classifier import ChangeClassifier

log = logging.getLogger(__name__)

EXTRACTOR = "email-door-v1"
PAGE_LINES = 51   # a span at y = 72 + 14 * i stays on the 792-point page for i < 51; later lines start a new page
PAIR_REACH = 200  # off its line, an account's routing number is the nearest within this many characters
MASK_REACH = 1_000_000   # accounts that mask the ledger's copy, or reveal a planted header, are read this far per part
REUSED_MESSAGE_ID = "this Message-ID already made a change for another account or vendor"
PLANTED_ACCOUNT = "an account number in the email is also in its Message-ID, thread ID or sender domain; those were scrubbed"
SCRUBBED = "scrubbed"


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


# Bank details, per mail part ------------------------------------------------------------------------

_LINE_BREAK = re.compile("[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")   # what str.splitlines splits on
_CLAUSE_END = re.compile(r";|[.!?](?=\s|$)")


@dataclass(frozen=True)
class _Found:
    part: int     # 0: the text part, 1: the HTML part's text
    start: int
    end: int
    digits: str   # for an account, the full number: memory only (ADR-8)


@dataclass(frozen=True)
class _Details:
    routings: list[_Found]
    pairs: list[tuple[str | None, _Found]]   # each account, in order, with its routing number (None: the email has none)


def _bank_details(parts: tuple[str, ...], on_file_routing: str | None) -> _Details:
    """The routing numbers and accounts of each part, read on its own (each up to rules.MAX_CHARS), and each
    account paired with a routing number: the nearest one on the same line, else the nearest within
    PAIR_REACH characters on either side, else the first that is not on file, else the first. "Nearest"
    prefers a routing number in the same clause (no `;` or sentence end between them) and then the fewest
    characters between, so "Account A, Routing 1; Account B, Routing 2" pairs B with 2 although `; Account `
    is shorter than `, Routing `. Only the nearest routing number on each side can win (a farther one on
    that side is farther and crosses every break the nearer one does), so pairing costs a binary search per
    account."""
    routings = [_Found(i, s, e, d) for i, part in enumerate(parts) for s, e, d in routing_spans(part)]
    by_part = [[r for r in routings if r.part == i] for i in range(len(parts))]
    starts = [[r.start for r in rs] for rs in by_part]
    capped = [part[:MAX_CHARS] for part in parts]
    lines = [[m.start() for m in _LINE_BREAK.finditer(part)] for part in capped]
    clauses = [[m.start() for m in _CLAUSE_END.finditer(part)] for part in capped]

    def crosses(points: list[int], lo: int, hi: int) -> bool:
        return bisect_left(points, lo) < bisect_left(points, hi)

    def routing_for(a: _Found) -> str | None:
        if not routings:
            return None
        rs, i = by_part[a.part], bisect_left(starts[a.part], a.start)
        sides = [(rs[i - 1], rs[i - 1].end, a.start, 0)] if i > 0 else []
        sides += [(rs[i], a.end, rs[i].start, 1)] if i < len(rs) else []
        ranked = sorted((crosses(clauses[a.part], lo, hi), hi - lo, after, crosses(lines[a.part], lo, hi), r.digits)
                        for r, lo, hi, after in sides)
        same_line = [c for c in ranked if not c[3]]
        within = [c for c in ranked if c[1] <= PAIR_REACH]
        if same_line or within:
            return (same_line or within)[0][4]
        return next((r.digits for r in routings if r.digits != on_file_routing), routings[0].digits)

    accounts = [_Found(i, s, e, d) for i, part in enumerate(parts) for s, e, d in account_spans(part)]
    return _Details(routings, [(routing_for(a), a) for a in accounts])


def _digits(value: str | None) -> str:
    return "".join(str(unicodedata.decimal(c)) for c in strip_nul(value or "") if c.isdecimal())


def _planted(accounts: list[str], values: list[str | None]) -> bool:
    """Whether a found account of 5 or more digits, as written or without its leading zeros, is among the
    digits of one of `values`. Each value is scanned once per account length, not once per account."""
    secrets = {s for a in accounts for s in (a, a.lstrip("0")) if len(s) >= 5}
    lengths = {len(s) for s in secrets}
    for value in values:
        d = _digits(value)
        if any(d[i:i + n] in secrets for n in lengths for i in range(len(d) - n + 1)):
            return True
    return False


# The email's row ----------------------------------------------------------------------------------

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
    scrubbed: bool = False   # the Message-ID, thread ID and sender domain are stored as SCRUBBED


async def _record(conn: AsyncConnection, dataset_id: UUID, email: ReceivedEmail, facts: _Facts) -> str:
    """Write the outcome to the email's row if it is still pending; otherwise report what is there."""
    headers = ((SCRUBBED, SCRUBBED, SCRUBBED) if facts.scrubbed else
               (thread_id_of(email), strip_nul(sender_domain(email.sender) or "") or None, strip_nul(email.message_id) or None))
    done = await conn.execute(
        """update app.inbound_emails set raw_sha256 = %s, has_attachments = %s, subject = %s, thread_id = %s,
                  sender_domain = %s, message_id = coalesce(%s, message_id), outcome = %s, flagged = %s, vendor_id = %s,
                  case_id = %s, change_event_id = %s, routing_number = %s, last4 = %s, red_flags = %s, error = %s,
                  processed_at = %s
            where dataset_id = %s and resend_email_id = %s and outcome = 'pending'""",
        (email.raw_sha256, email.has_attachments, mask_digit_runs(strip_nul(email.subject)), *headers, facts.outcome,
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


def _without_message_id(raw_body: str, email_id: str) -> str | None:
    """The stored notice without data.message_id, when it is this email's notice and has one; else None."""
    try:
        payload = json.loads(raw_body)
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not (isinstance(data, dict) and data.get("email_id") == email_id and "message_id" in data):
        return None
    del data["message_id"]
    return json.dumps(payload)


async def _scrub_notices(conn: AsyncConnection, dataset_id: UUID, email_id: str) -> None:
    """Drop data.message_id from this email's stored Resend notices (the route kept it as sent). A notice
    the inbox has claimed is locked and skipped here, never waited on: the inbox holds that lock while it
    waits for handle_email, and scrubs its own notice with scrub_claimed_notice."""
    rows = await (await conn.execute(
        "select id, raw_body from app.inbound_webhooks where source = 'resend' and dataset_id = %s and strpos(raw_body, %s) > 0 "
        "for update skip locked", (dataset_id, email_id))).fetchall()
    for row in rows:
        scrubbed = _without_message_id(row["raw_body"], email_id)
        if scrubbed is not None:
            await conn.execute("update app.inbound_webhooks set raw_body = %s where id = %s", (scrubbed, row["id"]))


async def scrub_claimed_notice(conn: AsyncConnection, delivery_id: int, dataset_id: UUID, email_id: str) -> None:
    """On the inbox's claim connection: when this email's row was scrubbed, drop data.message_id from the
    claimed delivery's stored notice too (a duplicate notice that arrives later included)."""
    row = await (await conn.execute(
        """select w.raw_body from app.inbound_webhooks w
             join app.inbound_emails e on e.dataset_id = w.dataset_id and e.resend_email_id = %s and e.message_id = %s
            where w.id = %s and w.dataset_id = %s""", (email_id, SCRUBBED, delivery_id, dataset_id))).fetchone()
    scrubbed = _without_message_id(row["raw_body"], email_id) if row else None
    if scrubbed is not None:
        await conn.execute("update app.inbound_webhooks set raw_body = %s where id = %s", (scrubbed, delivery_id))


# Reading the database -----------------------------------------------------------------------------

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


async def _repeat(conn: AsyncConnection, dataset_id: UUID, vendor_id: UUID, source_event_id: str, fingerprint: str,
                  key_version: int) -> tuple[str, UUID | None, str | None] | None:
    """(outcome, case, error) when this request must write nothing; None when it is new.

    The same Message-ID already made a change: `repeat_request` with its case when that change was for this
    vendor and account, else `failed` (two requests can't share a Message-ID). Otherwise, the vendor's
    unfinished case has an email change for this account: `repeat_request` with that case."""
    same = await (await conn.execute(
        """select ce.vendor_id, b.account_fingerprint, b.fingerprint_key_version, r.case_id
             from app.bank_change_events ce
             join app.bank_accounts b on b.id = ce.new_account_id
             left join app.case_event_receipts r on r.idempotency_key = ce.id::text || ':detected'
            where ce.dataset_id = %s and ce.source_event_id = %s""", (dataset_id, source_event_id))).fetchone()
    if same is not None:
        if (same["vendor_id"], same["account_fingerprint"], same["fingerprint_key_version"]) == (vendor_id, fingerprint, key_version):
            return "repeat_request", same["case_id"], None
        return "failed", None, REUSED_MESSAGE_ID
    case = await (await conn.execute(
        """select c.id from app.cases c
            where c.dataset_id = %s and c.vendor_id = %s and c.state <> 'released'
              and exists (select 1 from app.case_event_receipts r
                            join app.bank_change_events ce on r.idempotency_key = ce.id::text || ':detected'
                            join app.bank_accounts b on b.id = ce.new_account_id
                           where r.case_id = c.id and r.event_type = 'change_detected' and ce.detected_via = 'email'
                             and b.account_fingerprint = %s and b.fingerprint_key_version = %s)""",
        (dataset_id, vendor_id, fingerprint, key_version))).fetchone()
    return ("repeat_request", case["id"], None) if case else None


# The door ---------------------------------------------------------------------------------------

async def handle_email(pool: AsyncConnectionPool, settings: Settings, keys: Keys, resend: ResendPort, dataset_id: UUID,
                       delivery_id: int, email: ReceivedEmail, classifier: ChangeClassifier | None = None) -> str:
    """Fill in the delivery's inbound_emails row from the fetched email, act on it, and return its outcome.
    The subject and sender are stored with long digit runs masked (ADR-8); the body is not stored at all.
    A ClassifierFailed, or any error before the ledger write, propagates, so the inbox retries the email."""
    subject, text, html = strip_nul(email.subject), strip_nul(email.text), strip_nul(email.html_text)
    async with system_tx(pool) as conn:
        state = await (await conn.execute(
            """select e.outcome, e.received_at, e.message_id, d.email_door_vendors as door,
                      d.kind = 'demo' and d.retired_at is null as current_demo
                 from app.inbound_emails e join app.datasets d on d.id = e.dataset_id
                where e.dataset_id = %s and e.resend_email_id = %s""", (dataset_id, email.email_id))).fetchone()
    if state is None:
        raise RuntimeError(f"delivery {delivery_id} has no inbound_emails row for its email")
    if state["outcome"] != "pending":
        return state["outcome"]   # another delivery of this email got here first: act once

    # 1. ADR-8, before anything else (the door switch, the model, which can fail five times): an account
    # planted where the door keeps headers as they are fails the email, and those headers are scrubbed. The
    # accounts are read from each part and the subject up to MASK_REACH characters; they also mask the
    # ledger's copy below.
    secrets = list(dict.fromkeys(find_accounts(text, limit=MASK_REACH) + find_accounts(html, limit=MASK_REACH)
                                 + find_accounts(subject, limit=MASK_REACH)))
    if _planted(secrets, [state["message_id"], email.message_id, email.headers.get("message-id"), thread_id_of(email),
                          sender_domain(email.sender)]):
        log.warning("email door: inbound email %s has an account number in its headers; failed and scrubbed", email.email_id)
        async with system_tx(pool) as conn:
            await _scrub_notices(conn, dataset_id, email.email_id)
            return await _record(conn, dataset_id, email, _Facts("failed", error=PLANTED_ACCOUNT, scrubbed=True))
    door = set(state["door"])
    if not (state["current_demo"] and door):
        return await _finish(pool, dataset_id, email, _Facts("door_off"))

    # 2. Flag, in a thread: the model's SDK client is synchronous and the worker shares the app's event loop.
    # Then match and read the bank details, which is pure: an unflagged email can still be a silent change.
    decision = await asyncio.to_thread(flag_email, subject, text, html, classifier)
    async with system_tx(pool) as conn:
        match = match_vendor(email.sender, subject, text + "\n" + html, await _vendor_refs(conn, dataset_id))
        current = None if match.vendor_id is None else await (await conn.execute(
            "select id, routing_number, account_last4 from app.bank_accounts where vendor_id = %s and is_current "
            "order by first_seen_at desc, created_at desc limit 1", (match.vendor_id,))).fetchone()
    on_file = (current["routing_number"], current["account_last4"]) if current else None
    details = _bank_details((text, html), on_file and on_file[0])
    routings, pairs = details.routings, details.pairs
    flagged_by = decision.by if decision.flagged else None
    if (flagged_by is None and match.vendor_id in door and routings and pairs
            and on_file not in {(r, a.digits[-4:]) for r, a in pairs}):
        flagged_by = "on_file_mismatch"   # an email naming bank details, but not the account on file
    if flagged_by is None:
        return await _finish(pool, dataset_id, email, _Facts("not_flagged"))

    # 3. The requested account: the longest whose pair (routing number, last four) is not the account on
    # file's. Red flags, and the ledger's copy: rebuilt phone lines only, every account found masked, and the
    # HTML part only when it says something the text part doesn't.
    requested = max(((r, a) for r, a in pairs if (r, a.digits[-4:]) != on_file), key=lambda p: len(p[1].digits), default=None)
    shown = requested or (pairs[0] if pairs else None)
    routing = shown[0] if shown else (routings[0].digits if routings else None)
    lines = ledger_text(text, also_mask=secrets)
    if " ".join(html.split()) != " ".join(text.split()):
        lines += "\n" + ledger_text(html, also_mask=secrets)
    document = ledger_document(email, lines, mask_digit_runs(subject), state["received_at"], thread_id_of(email))
    red_flags = [*match.red_flags, *_header_flags(email)]
    if match.vendor_id is not None:
        phones = {p for page in document["pages"] for span in page["spans"] for p in parse_phone(span["text"])}
        async with system_tx(pool) as conn:
            if await _unknown_phone(conn, dataset_id, match.vendor_id, phones):
                red_flags.append("unknown_phone")
    facts = _Facts("", flagged=True, vendor_id=match.vendor_id, routing_number=routing,
                   last4=shown[1].digits[-4:] if shown else None, red_flags=tuple(red_flags))
    log.info("email door: inbound email %s flagged by %s, vendor match %s", email.email_id, flagged_by, match.how)

    # 4. Enough information? (design section 6)
    stop = ("no_vendor" if match.how == "none" else "several_vendors" if match.how == "several"
            else "not_demo_vendor" if match.vendor_id not in door else "no_routing_number" if not routings
            else "no_account_number" if not pairs else "same_as_on_file" if requested is None else None)
    if stop is not None:
        return await _finish(pool, dataset_id, email, replace(facts, outcome=stop))
    vendor_id = match.vendor_id
    fingerprint = requested_fingerprint(keys.fingerprint_key, routing, requested[1].digits)   # ADR-8: nothing below uses the full number
    source_event_id = f"email:{_message_key(email)}"

    # 5. The same email again, or the same account while the vendor's case is unfinished: nothing written.
    # Checked here so a repeat files nothing in the ledger, and again under the vendor's lock below.
    async with system_tx(pool) as conn:
        repeat = await _repeat(conn, dataset_id, vendor_id, source_event_id, fingerprint, keys.fingerprint_key_version)
        if repeat:
            return await _record(conn, dataset_id, email, replace(facts, outcome=repeat[0], case_id=repeat[1], error=repeat[2]))

    # 6. Ledger first, committed: the rule only uses a change request recorded before the case opened. Then
    # the case. From here any error is `failed` with no case (R7), recorded by its type name only.
    try:
        async with system_tx(pool) as conn:
            await write_ingest(conn, dataset_id=dataset_id, vendor_id=vendor_id,
                               result=ingest_all([document], extractor_version=EXTRACTOR))
            await mark_change_request(conn, dataset_id=dataset_id, vendor_id=vendor_id, document_id=document["id"])
        return await _case_transaction(pool, dataset_id, email, facts, keys, fingerprint, source_event_id,
                                       current["id"] if current else None)
    except Exception as exc:  # noqa: BLE001  fail closed (R7); the message can hold the email's values (ADR-8)
        log.warning("email door: opening a case for inbound email %s failed: %s", email.email_id, type(exc).__name__)
        return await _finish(pool, dataset_id, email, replace(facts, outcome="failed", error=type(exc).__name__))


async def _case_transaction(pool: AsyncConnectionPool, dataset_id: UUID, email: ReceivedEmail, facts: _Facts, keys: Keys,
                            fingerprint: str, source_event_id: str, old_account_id: UUID | None) -> str:
    """One transaction: lock the email's row (one worker acts on it) and the vendor's (the lock
    apply_case_event takes, so requests for one vendor take turns), check for a repeat again, then write
    the requested account and the change and open or join the case. The requested account is never
    `is_current` and has no Ramp id, so neither the Ramp sync nor the clerk portal takes it for the
    account on file."""
    async with system_tx(pool) as conn:
        outcome = await _settled(conn, dataset_id, email, lock=True)
        if outcome != "pending":
            return outcome
        await conn.execute("select 1 from app.vendors where id = %s for update", (facts.vendor_id,))
        repeat = await _repeat(conn, dataset_id, facts.vendor_id, source_event_id, fingerprint, keys.fingerprint_key_version)
        if repeat:
            return await _record(conn, dataset_id, email, replace(facts, outcome=repeat[0], case_id=repeat[1], error=repeat[2]))
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
        change_id = (await (await conn.execute(
            """insert into app.bank_change_events (dataset_id, vendor_id, old_account_id, new_account_id, detected_via,
                      source_event_id, changed_by, change_thread_id, detected_at)
               values (%s, %s, %s, %s, 'email', %s, null, %s, %s) returning id""",
            (dataset_id, facts.vendor_id, old_account_id, account_id, source_event_id, thread_id_of(email), now))).fetchone())["id"]
        result = await apply_event(conn, None, "change_detected", f"{change_id}:detected", {
            "change_event_id": str(change_id), "policy_version": POLICY_VERSION,
            "detected_via": "email", "source_event_id": source_event_id,
        })
        outcome = "case_opened" if result.from_state is None else "joined_case"
        return await _record(conn, dataset_id, email, replace(facts, outcome=outcome, case_id=result.case_id, change_event_id=change_id))
