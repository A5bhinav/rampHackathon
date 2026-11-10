"""The worker's half of receiving (spec 0085): fetch each stored `email.received` notice's email from Resend.

Per delivery: the `inbound_emails` row is created first, from the stored notice, with outcome `pending`;
then the email is fetched and `door.handle_email` fills the row in. A retryable Resend error counts a
try on the row and leaves the delivery for the next tick; the fifth try records outcome `failed` and
marks the delivery processed, with no case opened (R7). A non-retryable error fails it at once.
The email's body is held in memory only.
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


async def _record_failure(pool: AsyncConnectionPool, delivery: dict[str, Any], email_id: str, reason: str, give_up: bool) -> bool:
    """Count a failed try. Give up (outcome `failed`) or schedule the next try. Returns True when it gave up.
    `reason` never holds an email's content."""
    async with system_tx(pool) as conn:
        now = await dataset_now(conn, delivery["dataset_id"])
        tries = (await (await conn.execute(
            "select tries from app.inbound_emails where dataset_id = %s and resend_email_id = %s",
            (delivery["dataset_id"], email_id))).fetchone())["tries"] + 1
        if give_up or tries > len(RETRY_SECONDS):
            await conn.execute(
                "update app.inbound_emails set outcome = 'failed', error = %s, tries = %s, processed_at = %s, next_try_at = null "
                "where dataset_id = %s and resend_email_id = %s", (reason, tries, now, delivery["dataset_id"], email_id))
            return True
        await conn.execute(
            "update app.inbound_emails set error = %s, tries = %s, next_try_at = %s + make_interval(secs => %s) "
            "where dataset_id = %s and resend_email_id = %s",
            (reason, tries, now, RETRY_SECONDS[tries - 1], delivery["dataset_id"], email_id))
        return False


async def process_inbound_emails(pool: AsyncConnectionPool, settings: Settings, resend: ResendPort, keys: Keys, limit: int = 20,
                                 dataset_id: UUID | None = None) -> int:
    """Fetch and record the stored Resend deliveries (all datasets, or one). Returns how many were finished.

    Any error on one email counts a try for that email only; it never stops the others or the rest of the tick.
    """
    handled, seen = 0, set()
    for _ in range(limit):
        async with system_tx(pool) as conn:
            # `for no key update`: the inbound_emails row's foreign key to a delivery takes a key-share lock,
            # which a plain `for update` would make this claim wait on forever. A delivery whose email waits
            # for its next try stays unprocessed and is skipped until then.
            row = await (await conn.execute(
                "select w.id, w.dataset_id, w.raw_body from app.inbound_webhooks w "
                "where w.source = 'resend' and w.signature_valid and w.event_type = 'email.received' and w.processed_at is null "
                "and not (w.id = any(%s::bigint[])) and (%s::uuid is null or w.dataset_id = %s::uuid) "
                "and not exists (select 1 from app.inbound_emails e where e.dataset_id = w.dataset_id "
                "and e.resend_email_id = (w.raw_body::jsonb)->'data'->>'email_id' and e.next_try_at > app.now(w.dataset_id)) "
                "order by w.received_at, w.id for no key update of w skip locked limit 1",
                (list(seen), dataset_id, dataset_id))).fetchone()
            if row is None:
                return handled
            seen.add(row["id"])
            error = None
            try:
                payload = json.loads(row["raw_body"])
                data = payload.get("data") if isinstance(payload, dict) else None
                email_id = data.get("email_id") if isinstance(data, dict) else None
            except ValueError:
                email_id = None
            if not isinstance(email_id, str) or not strip_nul(email_id):
                error = "ignored: the notice has no email id"
            else:
                data["email_id"] = email_id = strip_nul(email_id)
                try:
                    state = await _open_row(pool, row, data)
                except Exception as exc:  # the notice cannot be recorded, so there is no row to retry
                    state, error = None, f"failed: {type(exc).__name__}"
                if state is not None and state["outcome"] != "pending":
                    error = "duplicate: this email was already recorded"
                elif state is not None:
                    try:
                        email = await resend.get_received(email_id)
                        await handle_email(pool, settings, keys, resend, row["dataset_id"], row["id"], email)
                    except Exception as exc:
                        # Only our own ResendError text is kept; any other error is kept as its type name, because
                        # its message can hold the email's values (ADR-8).
                        if isinstance(exc, ResendError):
                            reason, retryable = str(exc), exc.retryable
                        else:
                            reason, retryable = type(exc).__name__, True
                        if not await _record_failure(pool, row, email_id, reason, give_up=not retryable):
                            await conn.execute("update app.inbound_webhooks set error = %s where id = %s", (reason, row["id"]))
                            continue
                        error = f"failed: {reason}"
            await conn.execute(
                "update app.inbound_webhooks set processed_at = app.now(dataset_id), error = %s where id = %s", (error, row["id"]))
            handled += 1
    return handled
