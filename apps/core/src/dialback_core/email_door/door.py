"""What the door does with a fetched email (spec 0085). Task 4: record it only; Task 8 adds the flag and match logic."""
from __future__ import annotations

from uuid import UUID

from dialback_ledger.lookalike import sender_domain
from psycopg_pool import AsyncConnectionPool

from ..db import dataset_now, system_tx
from ..detection import Keys
from ..settings import Settings
from .masking import mask_digit_runs, strip_nul
from .resend import ReceivedEmail, ResendPort


def thread_id_of(email: ReceivedEmail) -> str | None:
    """The first ID in References, else In-Reply-To, else Message-ID."""
    for name in ("references", "in-reply-to"):
        ids = strip_nul(email.headers.get(name) or "").split()
        if ids:
            return ids[0]
    return strip_nul(email.message_id or email.headers.get("message-id") or "") or None


async def handle_email(pool: AsyncConnectionPool, settings: Settings, keys: Keys, resend: ResendPort, dataset_id: UUID,
                       delivery_id: int, email: ReceivedEmail) -> str:
    """Fill in the delivery's inbound_emails row from the fetched email and set its outcome. The subject is
    stored with long digit runs masked (ADR-8); the body is not stored at all."""
    async with system_tx(pool) as conn:
        vendors = (await (await conn.execute(
            "select email_door_vendors from app.datasets where id = %s", (dataset_id,))).fetchone())["email_door_vendors"]
        outcome = "not_flagged" if vendors else "door_off"
        done = await conn.execute(
            """update app.inbound_emails set raw_sha256 = %s, has_attachments = %s, subject = %s, thread_id = %s,
                      sender_domain = %s, message_id = coalesce(%s, message_id), outcome = %s, processed_at = %s
               where dataset_id = %s and resend_email_id = %s and outcome = 'pending'""",
            (email.raw_sha256, email.has_attachments, mask_digit_runs(strip_nul(email.subject)), thread_id_of(email),
             strip_nul(sender_domain(email.sender) or "") or None, strip_nul(email.message_id) or None, outcome,
             await dataset_now(conn, dataset_id), dataset_id, email.email_id))
        if done.rowcount == 1:
            return outcome
        # Not pending any more: another delivery of this email got here first. Act once; report what it decided.
        existing = await (await conn.execute(
            "select outcome from app.inbound_emails where dataset_id = %s and resend_email_id = %s",
            (dataset_id, email.email_id))).fetchone()
        if existing is None:
            raise RuntimeError(f"delivery {delivery_id} has no inbound_emails row for its email")
        return existing["outcome"]
