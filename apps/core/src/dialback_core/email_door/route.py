"""POST /webhooks/email: Resend's signed `email.received` notices (spec 0085).

Same shape as the Ramp webhook: verify the signature over the raw body before parsing, store the
delivery (unique on Svix's message id), answer at once, 2xx for duplicates too. The notice
carries no email body; the worker fetches the email later. Check order: off (404), too large
(413), no current demo dataset (503, so Resend retries), signature (401), store (200).

What is stored (ADR-8: a subject line or a forged body can carry a full account number): a signed
delivery keeps only the notice's type, created_at and data.email_id, message_id, from and to. A bad
delivery is stored as the hash and length of its body, never the body, and with the names of its
headers, never their values. This route has no secret in
its path, so a bad delivery raises at most one alert per dataset per 10 minutes and at most 100
are stored per dataset per hour; beyond that it answers 401 and stores nothing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg.types.json import Jsonb

from ..db import system_tx
from ..flags import EmailMode
from . import signature
from .masking import mask_digit_runs, strip_nul

log = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY = 64 * 1024
KEPT_HEADERS = ("content-type", "svix-id", "svix-timestamp", "user-agent")  # never the signature itself
SIGNED_HEADERS = ("svix-id", "svix-timestamp", "svix-signature")
ALERT_EVERY_SECONDS = 600
MAX_UNSIGNED_PER_HOUR = 100
ALERT_MESSAGE = "A Resend webhook arrived with a bad signature and was refused."


def _reply(status: int, body: dict) -> JSONResponse:
    return JSONResponse(body, status_code=status)


def _no_nul(value):
    """The same value with NUL removed from every string: Postgres text and jsonb cannot hold it."""
    if isinstance(value, str):
        return strip_nul(value)
    if isinstance(value, list):
        return [_no_nul(v) for v in value]
    if isinstance(value, dict):
        return {strip_nul(k): _no_nul(v) for k, v in value.items()}
    return value


def _kept(raw: bytes) -> str:
    """The parts of a signed notice worth keeping; the subject and attachments are read after the fetch."""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return json.dumps({"unreadable": True, "sha256": hashlib.sha256(raw).hexdigest(), "length": len(raw)})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    kept = {k: data.get(k) for k in ("email_id", "message_id", "from", "to") if k in data}
    if isinstance(kept.get("from"), str):  # a display name is typed by a person: it can carry an account number (ADR-8)
        kept["from"] = mask_digit_runs(kept["from"])
    return json.dumps(_no_nul({"type": payload.get("type"), "created_at": payload.get("created_at"), "data": kept}))


@router.post("/webhooks/email")
async def email_webhook(request: Request) -> JSONResponse:
    settings, pool = request.app.state.settings, request.app.state.pool
    if settings.email_mode is not EmailMode.RESEND or not settings.resend_webhook_secret:
        return _reply(404, {"error": "not found"})
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return _reply(413, {"error": "payload too large"})
    headers = {k: v for k, v in request.headers.items() if k.lower() in KEPT_HEADERS}
    # Verify and store the same header values: a repeated signing header is a refusal.
    unambiguous = all(len(request.headers.getlist(name)) <= 1 for name in SIGNED_HEADERS)

    async with system_tx(pool) as conn:
        dataset = await (await conn.execute(
            "select id from app.datasets where kind = 'demo' and retired_at is null order by created_at desc limit 1")).fetchone()
        if dataset is None:
            return _reply(503, {"error": "no current demo dataset"})
        dataset_id = dataset["id"]

        if not unambiguous or not signature.verify(settings.resend_webhook_secret, request.headers, raw, int(time.time())):
            stored = (await (await conn.execute(
                """select count(*) as n from app.inbound_webhooks where source = 'resend' and dataset_id = %s
                   and not signature_valid and received_at > app.now(%s) - interval '1 hour'""", (dataset_id, dataset_id))).fetchone())["n"]
            if stored < MAX_UNSIGNED_PER_HOUR:
                digest = hashlib.sha256(raw).hexdigest()
                await conn.execute(
                    """insert into app.inbound_webhooks (source, dataset_id, event_id, signature_valid, headers, raw_body, error, received_at)
                       values ('resend', %s, %s, false, %s, %s, 'bad signature', app.now(%s)) on conflict (source, event_id) do nothing""",
                    (dataset_id, "unsigned:" + digest, Jsonb({"present": sorted(k.lower() for k in headers)}), json.dumps({"sha256": digest, "length": len(raw)}), dataset_id))
            recent = await (await conn.execute(
                """select 1 from app.alerts where dataset_id = %s and kind = 'bad_webhook_signature' and message = %s
                   and raised_at > app.now(%s) - make_interval(secs => %s)""",
                (dataset_id, ALERT_MESSAGE, dataset_id, ALERT_EVERY_SECONDS))).fetchone()
            if recent is None:
                await conn.execute(
                    """insert into app.alerts (dataset_id, kind, severity, message, raised_at)
                       values (%s, 'bad_webhook_signature', 'critical', %s, app.now(%s))""", (dataset_id, ALERT_MESSAGE, dataset_id))
            log.warning("refused a Resend webhook with a bad signature for dataset %s", dataset_id)
            return _reply(401, {"error": "bad signature"})

        try:
            payload = json.loads(raw)
            event_type = payload.get("type") if isinstance(payload, dict) else None
        except ValueError:
            event_type = None
        inserted = await (await conn.execute(
            """insert into app.inbound_webhooks (source, dataset_id, event_id, event_type, signature_valid, headers, raw_body, received_at)
               values ('resend', %s, %s, %s, true, %s, %s, app.now(%s)) on conflict (source, event_id) do nothing returning id""",
            (dataset_id, request.headers["svix-id"], event_type, Jsonb(headers), _kept(raw), dataset_id))).fetchone()
    return _reply(200, {"status": "accepted" if inserted else "duplicate"})
