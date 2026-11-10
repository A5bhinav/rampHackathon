"""The email door's receiving route (spec 0085): Resend's signed `email.received` notices.
Svix signature checks, then the raw delivery is stored once and answered at once. The body of
the email is never in the notice."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from conftest import core_settings, make_world
from dialback_core.db import system_tx
from dialback_core.email_door.signature import verify
from dialback_core.flags import EmailMode
from dialback_core.settings import ConfigurationError, Settings

pytestmark = pytest.mark.anyio

SECRET = "whsec_" + base64.b64encode(b"resend-test-signing-key-0123456789").decode()
MSG_ID, TS = "msg_2Lh9KRb0pzN3LePmFinf1Vsl8Nh", 1_731_200_000
BODY = json.dumps({"type": "email.received", "created_at": "2026-11-10T00:00:00Z",
                   "data": {"email_id": "em_1", "from": "a@b.example", "to": ["inbox@x.example"],
                            "subject": "New bank details", "message_id": "<m1@b.example>", "attachments": []}}).encode()


def sign(raw: bytes = BODY, msg_id: str = MSG_ID, ts: int = TS, secret: str = SECRET) -> dict[str, str]:
    key = base64.b64decode(secret.removeprefix("whsec_"))
    mac = hmac.new(key, f"{msg_id}.{ts}.".encode() + raw, hashlib.sha256).digest()
    return {"svix-id": msg_id, "svix-timestamp": str(ts), "svix-signature": "v1," + base64.b64encode(mac).decode()}


def test_verify_accepts_svixs_own_example():
    headers = sign()
    assert verify(SECRET, headers, BODY, now=TS)
    # Several signatures (a key rotation): one good item among bad ones is enough.
    headers["svix-signature"] = "v1,AAAA " + headers["svix-signature"]
    assert verify(SECRET, headers, BODY, now=TS + 10)


def test_verify_refuses_a_stale_timestamp():
    assert verify(SECRET, sign(), BODY, now=TS + 300)
    assert not verify(SECRET, sign(), BODY, now=TS + 301)
    assert not verify(SECRET, sign(), BODY, now=TS - 301)


def test_verify_refuses_a_changed_body():
    assert not verify(SECRET, sign(), BODY + b" ", now=TS)
    assert not verify(SECRET, {}, BODY, now=TS)
    assert not verify(SECRET, sign() | {"svix-timestamp": "soon"}, BODY, now=TS)


@pytest.fixture
async def demo(pool):
    """A current demo dataset with the door switched on. Earlier tests' demo datasets are retired."""
    async with system_tx(pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where kind = 'demo' and retired_at is null")
    w = await make_world(pool, kind="demo", settings=core_settings(email_mode=EmailMode.RESEND, resend_api_key="re_test",
                                                                   resend_webhook_secret=SECRET))
    yield w
    await w.core.aclose()
    await w.mock.aclose()


async def deliveries(pool, dataset_id=None) -> list[dict]:
    """The resend deliveries of one dataset (each test has its own); all of them when none is given."""
    async with system_tx(pool) as conn:
        return await (await conn.execute(
            "select * from app.inbound_webhooks where source = 'resend' and (%s::uuid is null or dataset_id = %s::uuid) order by id",
            (dataset_id, dataset_id))).fetchall()


async def post(w, raw: bytes = BODY, headers: dict | None = None):
    return await w.core.post("/webhooks/email", content=raw, headers=headers if headers is not None else sign(raw, msg_id=f"msg_{uuid.uuid4().hex}", ts=_now()))


def _now() -> int:
    import time
    return int(time.time())


async def test_off_is_404(pool):
    w = await make_world(pool, kind="demo")  # email_mode defaults to off
    try:
        assert (await post(w)).status_code == 404
        assert await deliveries(pool) == []
    finally:
        await w.core.aclose()
        await w.mock.aclose()


async def test_bad_signature_is_401_stored_unsigned_and_alerted(demo):
    r = await post(demo, headers=sign(b"something else", ts=_now()))
    assert r.status_code == 401
    (row,) = await deliveries(demo.pool, demo.dataset.id)
    assert row["signature_valid"] is False and row["dataset_id"] == demo.dataset.id
    async with system_tx(demo.pool) as conn:
        alert = await (await conn.execute("select kind, severity from app.alerts where dataset_id = %s", (demo.dataset.id,))).fetchone()
    assert (alert["kind"], alert["severity"]) == ("bad_webhook_signature", "critical")


async def test_good_delivery_is_stored_once_even_when_repeated(demo):
    msg_id = f"msg_{uuid.uuid4().hex}"  # event ids are unique across the test database
    headers = sign(msg_id=msg_id, ts=_now())
    assert (await post(demo, headers=headers)).status_code == 200
    assert (await post(demo, headers=headers)).status_code == 200
    (row,) = await deliveries(demo.pool, demo.dataset.id)
    assert (row["event_id"], row["event_type"], row["signature_valid"], row["dataset_id"]) == (msg_id, "email.received", True, demo.dataset.id)
    assert row["processed_at"] is None and row["raw_body"] == BODY.decode()


async def test_no_current_demo_dataset_is_503(demo):
    async with system_tx(demo.pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where id = %s", (demo.dataset.id,))
    assert (await post(demo)).status_code == 503
    assert await deliveries(demo.pool, demo.dataset.id) == []


async def test_an_oversized_body_is_413(demo):
    assert (await post(demo, raw=b"x" * (64 * 1024 + 1))).status_code == 413


async def test_the_ramp_worker_leaves_a_resend_delivery_alone(demo):
    assert (await post(demo)).status_code == 200
    await demo.worker.tick(demo.dataset.id)
    (row,) = await deliveries(demo.pool, demo.dataset.id)
    assert row["processed_at"] is None and row["error"] is None


def test_resend_mode_without_secrets_refuses_to_start():
    env = {"DATABASE_URL": "postgresql://x", "RAMP_BASE_URL": "http://ramp", "RAMP_WEBHOOK_SECRET": "w" * 16,
           "FINGERPRINT_KEY": "f" * 32, "MOCK_RAMP_SECRET": "m" * 16, "DIGITS_KEY": "d" * 32,
           "INTERNAL_API_TOKEN": "i" * 24, "DIALBACK_JWT_SECRET": "j" * 32}
    assert Settings.from_env(env).email_mode is EmailMode.OFF
    assert Settings.from_env(env | {"DIALBACK_EMAIL": "bogus"}).email_mode is EmailMode.OFF
    with pytest.raises(ConfigurationError, match="RESEND_API_KEY"):
        Settings.from_env(env | {"DIALBACK_EMAIL": "resend", "RESEND_WEBHOOK_SECRET": SECRET})
    with pytest.raises(ConfigurationError, match="RESEND_WEBHOOK_SECRET"):
        Settings.from_env(env | {"DIALBACK_EMAIL": "resend", "RESEND_API_KEY": "re_x"})
    ok = Settings.from_env(env | {"DIALBACK_EMAIL": "resend", "RESEND_API_KEY": "re_x", "RESEND_WEBHOOK_SECRET": SECRET})
    assert ok.email_mode is EmailMode.RESEND and ok.company_name == "the company you're writing to"
