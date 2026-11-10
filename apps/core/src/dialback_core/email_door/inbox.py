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
from .resend import ResendError, ResendPort

MAX_TRIES = 5


async def _open_row(pool: AsyncConnectionPool, delivery: dict[str, Any], notice_data: dict[str, Any]) -> dict[str, Any]:
    """The delivery's inbound_emails row, made if it is not there yet (a redelivered notice finds the first one)."""
    sender = notice_data.get("from")
    async with system_tx(pool) as conn:
        await conn.execute(
            """insert into app.inbound_emails (dataset_id, delivery_id, resend_email_id, message_id, sender, received_at,
                                               has_attachments, outcome, tries)
               values (%s, %s, %s, %s, %s, %s, false, 'pending', 0) on conflict (dataset_id, resend_email_id) do nothing""",
            (delivery["dataset_id"], delivery["id"], notice_data["email_id"], notice_data.get("message_id") or None,
             sender if isinstance(sender, str) else "", await dataset_now(conn, delivery["dataset_id"])))
        return await (await conn.execute(
            "select outcome, tries from app.inbound_emails where dataset_id = %s and resend_email_id = %s",
            (delivery["dataset_id"], notice_data["email_id"]))).fetchone()


async def _record_failure(pool: AsyncConnectionPool, delivery: dict[str, Any], email_id: str, exc: ResendError) -> None:
    async with system_tx(pool) as conn:
        await conn.execute(
            "update app.inbound_emails set outcome = 'failed', error = %s, tries = tries + 1, processed_at = %s "
            "where dataset_id = %s and resend_email_id = %s",
            (str(exc), await dataset_now(conn, delivery["dataset_id"]), delivery["dataset_id"], email_id))


async def process_inbound_emails(pool: AsyncConnectionPool, settings: Settings, resend: ResendPort, keys: Keys, limit: int = 20,
                                 dataset_id: UUID | None = None) -> int:
    """Fetch and record the stored Resend deliveries (all datasets, or one). Returns how many were finished."""
    handled, seen = 0, set()
    for _ in range(limit):
        async with system_tx(pool) as conn:
            row = await (await conn.execute(
                "select id, dataset_id, raw_body from app.inbound_webhooks "
                "where source = 'resend' and signature_valid and event_type = 'email.received' and processed_at is null "
                "and not (id = any(%s::bigint[])) and (%s::uuid is null or dataset_id = %s::uuid) "
                "order by received_at, id for no key update skip locked limit 1", (list(seen), dataset_id, dataset_id))).fetchone()
            if row is None:
                return handled
            seen.add(row["id"])
            # `no key update`: the inbound_emails row's foreign key to this delivery takes a key-share lock,
            # which a plain `for update` would make this claim wait on forever.
            error = None
            try:
                payload = json.loads(row["raw_body"])
                data = payload.get("data") if isinstance(payload, dict) else None
                email_id = data.get("email_id") if isinstance(data, dict) else None
            except ValueError:
                email_id = None
            if not isinstance(email_id, str) or not email_id:
                error = "ignored: the notice has no email id"
            else:
                state = await _open_row(pool, row, data)
                if state["outcome"] != "pending":
                    error = "duplicate: this email was already recorded"
                else:
                    try:
                        email = await resend.get_received(email_id)
                        await handle_email(pool, settings, keys, resend, row["dataset_id"], row["id"], email)
                    except ResendError as exc:
                        if exc.retryable and state["tries"] + 1 < MAX_TRIES:
                            async with system_tx(pool) as other:
                                await other.execute(
                                    "update app.inbound_emails set tries = tries + 1, error = %s "
                                    "where dataset_id = %s and resend_email_id = %s", (str(exc), row["dataset_id"], email_id))
                            await conn.execute("update app.inbound_webhooks set error = %s where id = %s", (str(exc), row["id"]))
                            continue
                        await _record_failure(pool, row, email_id, exc)
                        error = f"failed: {exc}"
            await conn.execute(
                "update app.inbound_webhooks set processed_at = app.now(dataset_id), error = %s where id = %s", (error, row["id"]))
            handled += 1
    return handled
