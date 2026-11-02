from __future__ import annotations

import json
import uuid

import httpx
import pytest
from dialback_ramp_mock import tokens
from dialback_ramp_mock.app import create_app
from dialback_ramp_mock.db import open_pool
from dialback_ramp_mock.settings import Settings

SECRET = b"test-secret-not-for-production"


@pytest.fixture
def anyio_backend():
    return "asyncio"


class Receiver:
    """Stands in for the core's webhook endpoint and records what the mock sends."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.client = httpx.AsyncClient(transport=httpx.MockTransport(self._handle))

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)

    def payloads(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
async def pool(database_url):
    p = await open_pool(database_url, max_size=4)
    yield p
    await p.close()


@pytest.fixture
async def receiver():
    r = Receiver()
    yield r
    await r.client.aclose()


@pytest.fixture
async def api(pool, receiver):
    settings = Settings(database_url="unused-in-tests", token_secret=SECRET, deliver_webhooks=False, webhook_max_attempts=2)
    app = create_app(settings, pool=pool, http=receiver.client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://ramp-mock") as client:
        yield client


def auth(dataset_id: uuid.UUID) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens.mint(SECRET, dataset_id)}"}


def seed_body(**overrides) -> dict:
    """A small, fictional business: one clerk, one vendor with an old and a current account, two bills."""
    body = {
        "business_name": "Fictional Co",
        "users": [{"ref": "clerk", "email": "clerk@fictional.example", "first_name": "Pat", "last_name": "Clerk"}],
        "vendors": [{
            "ref": "acme",
            "name": "Acme Fasteners LLC",
            "accounts": [
                {"ref": "acct-old", "routing_number": "021000021", "account_number": "000011112222"},
                {"ref": "acct-cur", "routing_number": "121000248", "account_number": "000033334444"},
            ],
            "contacts": [{"first_name": "Jordan", "last_name": "Lee", "phone_number": "+14155550133", "is_primary": True}],
            "bills": [
                {"ref": "bill-1", "invoice_number": "INV-1", "amount_cents": 125000, "payment_date": "2026-11-05"},
                {"ref": "bill-2", "invoice_number": "INV-2", "amount_cents": 80000, "payment_date": "2026-11-12"},
            ],
        }],
        "webhook_subscriptions": [{"url": "http://core.test/webhooks/ramp/x", "secret": "whsec-test-0123456789"}],
    }
    body.update(overrides)
    return body


async def seeded(api: httpx.AsyncClient, **overrides) -> tuple[uuid.UUID, dict]:
    dataset_id = uuid.uuid4()
    r = await api.post("/_mock/seed", json=seed_body(**overrides), headers=auth(dataset_id))
    assert r.status_code == 200, r.text
    return dataset_id, r.json()["refs"]
