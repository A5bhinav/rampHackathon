"""Core tests run the core and the Ramp mock in process, wired to each other over HTTP
transports: the core's real RampHttpAdapter talks to the mock, and the mock's signed
webhooks arrive at the core's real endpoint. Each test gets its own dataset and mock
business, as a lab scenario would."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import httpx
import pytest
from dialback_core.app import create_app
from dialback_core.datasets import Dataset, create_dataset
from dialback_core.db import open_pool, system_tx
from dialback_core.detection import Keys
from dialback_core.flags import RampMode
from dialback_core.ports import PreDialRefused, Selection
from dialback_core.ramp import RampConnector
from dialback_core.ramp.connector import mock_token
from dialback_core.ramp.http import TokenBucket
from dialback_core.settings import Settings
from dialback_core.worker import Worker
from dialback_ramp_mock.app import create_app as create_mock_app
from dialback_ramp_mock.settings import Settings as MockSettings

MOCK_SECRET = b"mock-secret-for-tests-only"
WEBHOOK_SECRET = b"whsec-for-tests-only-0123"
FINGERPRINT_KEY = b"fingerprint-key-for-tests-only-0123456789"
DIGITS_KEY = b"digits-key-for-tests-only-0123456789abcd"
INTERNAL_TOKEN = b"internal-token-for-tests-only-0123"


def core_settings(**overrides) -> Settings:
    base = {"database_url": "unused-in-tests", "ramp_mode": RampMode.MOCK, "ramp_base_url": "http://ramp-mock",
            "ramp_webhook_secret": WEBHOOK_SECRET, "fingerprint_key": FINGERPRINT_KEY, "mock_ramp_secret": MOCK_SECRET,
            "digits_key": DIGITS_KEY, "internal_api_token": INTERNAL_TOKEN, "run_worker": False,
            "command_backoff_seconds": 0.0, "calling_window": "00:00-23:59", "calling_days": "all"}
    return Settings(**(base | overrides))


class StandInResolver:
    """TEST DOUBLE for Inesh's select_number (spec 0002, D10): picks one fixed number, or none."""

    def __init__(self, pick: bool = True) -> None:
        self.pick, self.calls = pick, []

    async def select_number(self, *, case_id, dataset_id, vendor_id, cutoff) -> Selection:
        self.calls.append(case_id)
        if not self.pick:
            return Selection(uuid.uuid4(), "test-double", None, reason="No number met the rule.")
        return Selection(uuid.uuid4(), "test-double", CONTACT_POINT, source_types=("contract", "listing"))


class StandInVoice:
    """TEST DOUBLE for Sami's voice service (internal.yaml placeCall): accepts every call."""

    def __init__(self) -> None:
        self.placed: list[dict] = []
        self.refuse: str | None = None

    async def place_call(self, *, case_id, attempt, idempotency_key) -> uuid.UUID:
        if self.refuse:
            raise PreDialRefused(self.refuse)
        call_id = uuid.uuid4()
        self.placed.append({"case_id": case_id, "attempt": attempt, "key": idempotency_key, "call_id": call_id})
        return call_id


CONTACT_POINT = uuid.UUID("5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def pool(database_url):
    p = await open_pool(database_url, max_size=6)
    yield p
    await p.close()


@dataclass
class World:
    pool: object
    core: httpx.AsyncClient
    mock: httpx.AsyncClient
    connector: RampConnector
    keys: Keys
    dataset: Dataset
    refs: dict[str, str] = field(default_factory=dict)
    users: dict[str, uuid.UUID] = field(default_factory=dict)
    worker: Worker | None = None
    resolver: StandInResolver | None = None
    voice: StandInVoice | None = None

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {mock_token(MOCK_SECRET, self.dataset.id)}"}

    @property
    def ramp(self):
        return self.connector.for_dataset(self.dataset.id)

    async def bank_change(self, vendor_ref: str = "acme", account_number: str = "000055556666", actor: str | None = "clerk") -> str:
        body = {"routing_number": "026009593", "account_number": account_number}
        if actor:
            body["actor_user_id"] = self.refs[actor]
        r = await self.mock.post(f"/_mock/vendors/{self.refs[vendor_ref]}/bank-change", json=body, headers=self.auth)
        assert r.status_code == 200, r.text
        return r.json()["account_id"]

    async def deliver_webhooks(self) -> int:
        return (await self.mock.post("/_mock/webhooks/deliver", headers=self.auth)).json()["delivered"]

    async def mock_settings(self, **changes) -> None:
        assert (await self.mock.patch("/_mock/settings", json=changes, headers=self.auth)).status_code == 200


def seed(dataset_id: uuid.UUID) -> dict:
    return {
        "business_name": "Fictional Co",
        "users": [{"ref": "clerk", "email": "clerk@fictional.example", "first_name": "Pat", "last_name": "Clerk"}],
        "vendors": [{
            "ref": "acme", "name": "Acme Fasteners LLC",
            "accounts": [{"routing_number": "021000021", "account_number": "000011112222"},
                         {"routing_number": "121000248", "account_number": "000033334444"}],
            "bills": [{"ref": "bill-1", "invoice_number": "INV-1", "amount_cents": 125000, "payment_date": "2026-11-05"},
                      {"ref": "bill-2", "invoice_number": "INV-2", "amount_cents": 80000, "payment_date": "2026-11-12"}],
        }],
        "webhook_subscriptions": [{"url": f"http://core/webhooks/ramp/{dataset_id}", "secret": WEBHOOK_SECRET.decode()}],
    }


async def make_world(pool, kind: str = "dev", sim_now=None, settings: Settings | None = None):
    settings = settings or core_settings()
    mock_app = create_mock_app(MockSettings(database_url="unused", token_secret=MOCK_SECRET, deliver_webhooks=False), pool=pool)
    to_mock = httpx.AsyncClient(transport=httpx.ASGITransport(app=mock_app), base_url="http://ramp-mock")
    connector = RampConnector(settings, to_mock, limiter=TokenBucket(100_000))
    core_app = create_app(settings, pool=pool, connector=connector)
    to_core = httpx.AsyncClient(transport=httpx.ASGITransport(app=core_app), base_url="http://core")
    mock_app.state.http = to_core  # the mock's webhooks land on the core's real endpoint

    dataset = await create_dataset(pool, kind, f"test-{uuid.uuid4().hex[:8]}", sim_now)
    w = World(pool, to_core, to_mock, connector, Keys(FINGERPRINT_KEY), dataset)
    r = await to_mock.post("/_mock/seed", json=seed(dataset.id), headers=w.auth)
    assert r.status_code == 200, r.text
    w.refs = r.json()["refs"]

    # Dialback users. The clerk is mapped to the mock user who makes bank changes.
    async with system_tx(pool) as conn:
        for name, role, ramp_ref in (("clerk", "clerk", "clerk"), ("approver1", "approver", None), ("approver2", "approver", None)):
            uid = uuid.uuid4()
            await conn.execute("insert into app.user_profiles (user_id, display_name, ramp_user_id) values (%s, %s, %s)",
                               (uid, name.title(), w.refs.get(ramp_ref) if ramp_ref else None))
            await conn.execute("insert into app.user_roles (user_id, role) values (%s, %s)", (uid, role))
            w.users[name] = uid
    w.resolver, w.voice = StandInResolver(), StandInVoice()
    w.worker = Worker(pool, settings, connector, w.resolver, w.voice, sleep=_no_sleep)
    return w


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def world(pool):
    w = await make_world(pool)
    yield w
    await w.core.aclose()
    await w.mock.aclose()
