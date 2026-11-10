"""The worker's half of receiving (spec 0085): fetch each stored `email.received` notice's email from Resend.

Per delivery: the `inbound_emails` row is created first, from the stored notice, with outcome `pending`;
then the email is fetched and `door.handle_email` fills the row in. Any error on one email (a Resend
error or anything else) counts a try on its row and never stops the tick. Tries are spaced by
RETRY_SECONDS through `next_try_at`; a delivery whose email is waiting is left unprocessed and skipped.
The fifth failure records outcome `failed` and marks the delivery processed, with no case opened (R7);
a non-retryable Resend error fails it at once; a failure before the row exists marks the delivery
failed without a row. A failure only ever changes a row that is still `pending`. What is kept of an
error is a fixed text (for a Resend error) or the exception's type name, never its message, because
a message can hold an email's values (ADR-8). The email's body is held in memory only.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from ..db import dataset_now, system_tx
from ..detection import Keys
from ..settings import Settings
from .door import handle_email
from .masking import mask_digit_runs, strip_nul
from .resend import ResendError, ResendPort

# Seconds to wait after failed try 1, 2, 3, 4. The fifth failure records outcome `failed`.
RETRY_SECONDS = (30, 120, 600, 1800)


def _clean(value: Any) -> str:
    """A notice string as it is stored: no NUL, and digit runs masked (ADR-8)."""
    return mask_digit_runs(strip_nul(value)) if isinstance(value, str) else ""


async def _open_row(pool: AsyncConnectionPool, delivery: dict[str, Any], notice_data: dict[str, Any]) -> dict[str, Any]:
    """The delivery's inbound_emails row, made if it is not there yet (a redelivered notice finds the first one)."""
    message_id = notice_data.get("message_id")
    async with system_tx(pool) as conn:
        await conn.execute(
            """insert into app.inbound_emails (dataset_id, delivery_id, resend_email_id, message_id, sender, received_at,
                                               has_attachments, outcome, tries)
               values (%s, %s, %s, %s, %s, %s, false, 'pending', 0) on conflict (dataset_id, resend_email_id) do nothing""",
            (delivery["dataset_id"], delivery["id"], notice_data["email_id"],
             strip_nul(message_id) or None if isinstance(message_id, str) else None,
             _clean(notice_data.get("from")), await dataset_now(conn, delivery["dataset_id"])))
        return await (await conn.execute(
            "select outcome, tries from app.inbound_emails where dataset_id = %s and resend_email_id = %s",
            (delivery["dataset_id"], notice_data["email_id"]))).fetchone()


async def _record_failure(pool: AsyncConnectionPool, delivery: dict[str, Any], email_id: str, reason: str, give_up: bool) -> str:
    """Count a failed try on a still-pending row, in one statement. Returns "failed" when it gave up, "waiting" when
    it scheduled the next try, "settled" when the row was no longer pending (nothing is changed then).
    `reason` never holds an email's content."""
    async with system_tx(pool) as conn:
        now = await dataset_now(conn, delivery["dataset_id"])
        stop = "(%(give_up)s::boolean or tries + 1 > cardinality(%(delays)s::int[]))"
        row = await (await conn.execute(
            f"""update app.inbound_emails set tries = tries + 1, error = %(reason)s,
                       outcome = case when {stop} then 'failed' else outcome end,
                       processed_at = case when {stop} then %(now)s else processed_at end,
                       next_try_at = case when {stop} then null else %(now)s + make_interval(secs => (%(delays)s::int[])[tries + 1]) end
                where dataset_id = %(dataset)s and resend_email_id = %(email)s and outcome = 'pending'
                returning outcome""",
            {"give_up": give_up, "delays": list(RETRY_SECONDS), "reason": reason, "now": now,
             "dataset": delivery["dataset_id"], "email": email_id})).fetchone()
    return "settled" if row is None else ("failed" if row["outcome"] == "failed" else "waiting")


async def _waiting(pool: AsyncConnectionPool, dataset_id: UUID, email_id: str) -> bool:
    """True when this email's next try is still in the future."""
    async with system_tx(pool) as conn:
        row = await (await conn.execute(
            "select next_try_at > app.now(dataset_id) as waiting from app.inbound_emails where dataset_id = %s and resend_email_id = %s",
            (dataset_id, email_id))).fetchone()
    return bool(row and row["waiting"])


async def process_inbound_emails(pool: AsyncConnectionPool, settings: Settings, resend: ResendPort, keys: Keys, limit: int = 20,
                                 dataset_id: UUID | None = None) -> int:
    """Fetch and record the stored Resend deliveries (all datasets, or one). Returns how many were finished.

    Any error on one email counts a try for that email only; it never stops the others or the rest of the tick.
    """
    handled, claimed, seen = 0, 0, set()
    while claimed < limit:  # every claimed row counts, so a tick's work is bounded whatever the fetches do
        async with system_tx(pool) as conn:
            # No casts in the claim: whatever the stored text holds, it must never make the claim itself fail.
            # `for no key update`: the inbound_emails row's foreign key to a delivery takes a key-share lock,
            # which a plain `for update` would make this claim wait on forever.
            # Due and never-tried deliveries come before waiting and often-tried ones, so a few failing emails
            # can't use up a tick's budget ahead of fresh mail. `of w`: only the delivery is locked.
            row = await (await conn.execute(
                "select w.id, w.dataset_id, w.raw_body from app.inbound_webhooks w "
                "left join app.inbound_emails e on e.delivery_id = w.id "
                "where w.source = 'resend' and w.signature_valid and w.event_type = 'email.received' and w.processed_at is null "
                "and not (w.id = any(%s::bigint[])) and (%s::uuid is null or w.dataset_id = %s::uuid) "
                "order by coalesce(e.next_try_at > app.now(w.dataset_id), false), coalesce(e.tries, 0), w.received_at, w.id "
                "for no key update of w skip locked limit 1", (list(seen), dataset_id, dataset_id))).fetchone()
            if row is None:
                return handled
            seen.add(row["id"])
            claimed += 1
            error = None
            try:
                payload = json.loads(row["raw_body"])
                data = payload.get("data") if isinstance(payload, dict) else None
                email_id = data.get("email_id") if isinstance(data, dict) else None
            except Exception:  # noqa: BLE001  not JSON, or not JSON this process can read: this delivery only
                email_id = None
            if not isinstance(email_id, str) or not strip_nul(email_id):
                error = "ignored: the notice has no readable email id"
            elif await _waiting(pool, row["dataset_id"], strip_nul(email_id)):
                continue
            else:
                data["email_id"] = email_id = strip_nul(email_id)
                try:
                    state = await _open_row(pool, row, data)
                except Exception as exc:  # noqa: BLE001  the notice cannot be recorded, so there is no row to retry
                    state, error = None, f"failed: {type(exc).__name__}"
                if state is not None and state["outcome"] != "pending":
                    error = "duplicate: this email was already recorded"
                elif state is not None:
                    try:
                        email = await resend.get_received(email_id)
                        await handle_email(pool, settings, keys, resend, row["dataset_id"], row["id"], email)
                    except Exception as exc:  # noqa: BLE001  one email's failure must not stop the worker
                        # Only our own ResendError text is kept; any other error is kept as its type name, because
                        # its message can hold the email's values (ADR-8).
                        if isinstance(exc, ResendError):
                            reason, retryable = ("resend: retryable error" if exc.retryable else "resend: refused"), exc.retryable
                        else:
                            reason, retryable = type(exc).__name__, True
                        result = await _record_failure(pool, row, email_id, reason, give_up=not retryable)
                        if result == "waiting":
                            await conn.execute("update app.inbound_webhooks set error = %s where id = %s", (reason, row["id"]))
                            continue
                        error = f"failed: {reason}" if result == "failed" else "duplicate: this email was already recorded"
            await conn.execute(
                "update app.inbound_webhooks set processed_at = app.now(dataset_id), error = %s where id = %s", (error, row["id"]))
            handled += 1
    return handled
