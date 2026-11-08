"""Demo mode (spec 0011): one command per beat. A reset starts a fresh demo company and deletes
nothing; a change is the linked clerk's (or nobody's), on the current demo only."""
from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from pathlib import Path

import psycopg
import pytest
from conftest import core_settings, make_world
from dialback_core.db import system_tx
from dialback_core.demo import DemoEnv, DemoError, change, load_seed, reset
from dialback_core.flags import RampMode
from flows import one, release, report, transit

pytestmark = pytest.mark.anyio

SEED = Path(__file__).resolve().parents[3] / "scripts" / "demo" / "seed.json"
TODAY = dt.date(2026, 11, 9)


@pytest.fixture
async def demo(pool, database_url):
    """An in-process core and mock (as in every core test), a stand-in auth.users holding the
    test accounts' emails, and the demo commands wired to them."""
    settings = core_settings(core_public_url="http://core")
    world = await make_world(pool, settings=settings)
    tag = uuid.uuid4().hex[:8]
    emails = {name: f"{name}.{tag}@judges.example" for name in world.users}
    async with await psycopg.AsyncConnection.connect(database_url, autocommit=True) as owner:
        await owner.execute("create schema if not exists auth")
        await owner.execute("create table if not exists auth.users (id uuid primary key, email text)")
        for name, uid in world.users.items():
            await owner.execute("insert into auth.users (id, email) values (%s, %s)", (uid, emails[name]))
    world.env = DemoEnv(pool=pool, settings=settings, mock=world.mock, connector=world.connector, owner_url=database_url)
    world.emails = emails
    yield world
    await world.core.aclose()
    await world.mock.aclose()


async def fresh(demo, **links):
    links = links or {"clerk": demo.emails["clerk"]}
    return await reset(demo.env, load_seed(SEED), links, today=TODAY)


async def demo_case(demo, dataset_id):
    return await one(demo.pool, "select * from app.cases where dataset_id = %s order by opened_at desc limit 1", dataset_id)


async def detect(demo, dataset_id):
    """The mock's webhook reaches the core (sent by `demo change`), and the worker runs."""
    await demo.worker.drain(dataset_id)
    return await demo_case(demo, dataset_id)


async def test_reset_starts_a_fresh_demo_without_deleting_anything(demo):
    first = await fresh(demo)
    before = await one(demo.pool, "select count(*) as n from app.datasets")
    second = await fresh(demo)
    assert second.dataset.kind == "demo" and second.dataset.id != first.dataset.id
    assert second.dataset.chain_id != first.dataset.chain_id, "a new evidence chain"
    assert (await one(demo.pool, "select count(*) as n from app.datasets"))["n"] == before["n"] + 1, "nothing deleted"
    old = await one(demo.pool, "select retired_at from app.datasets where id = %s", first.dataset.id)
    assert old["retired_at"] is not None, "the earlier demo is hidden"
    live = await one(demo.pool, "select count(*) as n from app.datasets where kind = 'demo' and retired_at is null")
    assert live["n"] == 1
    assert (await one(demo.pool, "select retired_at from app.datasets where id = %s", demo.dataset.id))["retired_at"] is None, \
        "a dev dataset is left alone"

    # Seeded, with relative dates resolved against today, and imported.
    ramp = demo.connector.for_dataset(second.dataset.id)
    acme = await ramp.list_bills_for_vendor(second.refs["acme"])
    assert sorted(b.payment_date for b in acme) == [TODAY + dt.timedelta(days=3), TODAY + dt.timedelta(days=10)]
    vendors = await one(demo.pool, "select count(*) as n from app.vendors where dataset_id = %s", second.dataset.id)
    assert vendors["n"] == 3

    # Linked: the clerk's Dialback account now maps to the new business's seeded clerk.
    profile = await one(demo.pool, "select ramp_user_id from app.user_profiles where user_id = %s", demo.users["clerk"])
    assert profile["ramp_user_id"] == second.refs["clerk"] and second.linked == {"clerk": demo.users["clerk"]}

    # Webhooks are subscribed to the core's public URL.
    sub = await one(demo.pool, "select s.url from mock_ramp.webhook_subscriptions s join mock_ramp.businesses b "
                    "on b.id = s.business_id where b.dataset_id = %s", second.dataset.id)
    assert sub["url"] == f"http://core/webhooks/ramp/{second.dataset.id}"


async def test_reset_without_a_link_warns_and_without_a_public_url_subscribes_nothing(demo):
    env = dataclasses.replace(demo.env, settings=dataclasses.replace(demo.env.settings, core_public_url=None))
    result = await reset(env, load_seed(SEED), {}, today=TODAY)
    assert any("two approvers" in w for w in result.warnings)
    sub = await one(demo.pool, "select count(*) as n from mock_ramp.webhook_subscriptions s join mock_ramp.businesses b "
                    "on b.id = s.business_id where b.dataset_id = %s", result.dataset.id)
    assert sub["n"] == 0


async def test_reset_refuses_the_sandbox_and_bad_links(demo):
    before = await one(demo.pool, "select count(*) as n from app.datasets")
    sandbox = dataclasses.replace(demo.env, settings=dataclasses.replace(demo.env.settings, ramp_mode=RampMode.SANDBOX))
    with pytest.raises(DemoError, match="sandbox"):
        await reset(sandbox, load_seed(SEED), {}, today=TODAY)
    with pytest.raises(DemoError, match="nobody"):
        await fresh(demo, nobody=demo.emails["clerk"])
    with pytest.raises(DemoError, match="ghost"):
        await fresh(demo, clerk="ghost@judges.example")
    stray = uuid.uuid4()  # in auth.users, but no Dialback profile
    async with await psycopg.AsyncConnection.connect(demo.env.owner_url, autocommit=True) as owner:
        await owner.execute("insert into auth.users (id, email) values (%s, %s)", (stray, f"stray.{stray.hex[:6]}@judges.example"))
    with pytest.raises(DemoError, match="no Dialback account"):
        await fresh(demo, clerk=f"stray.{stray.hex[:6]}@judges.example")
    assert (await one(demo.pool, "select count(*) as n from app.datasets"))["n"] == before["n"], "a refused reset creates nothing"


async def test_the_demo_change_is_the_clerks_and_they_cant_release_it(demo):
    r = await fresh(demo)
    await change(demo.env, load_seed(SEED), "acme")
    case = await detect(demo, r.dataset.id)
    assert case["state"] == "calling" and case["has_unknown_changer"] is False
    assert (await demo.connector.for_dataset(r.dataset.id).get_vendor(r.refs["acme"])).hold_id is not None, "the hold is on"

    call_id = demo.voice.placed[-1]["call_id"]
    assert (await report(demo, case["id"], call_id, answers=transit(call_id, digits="0001"))).status_code == 204
    await demo.worker.drain(r.dataset.id)
    case = await demo_case(demo, r.dataset.id)
    assert (case["state"], case["outcome"]) == ("awaiting_release", "verified")

    async with system_tx(demo.pool) as conn:  # even holding the approver role, the changer can't release (R5)
        await conn.execute("insert into app.user_roles (user_id, role) values (%s, 'approver') on conflict do nothing",
                           (demo.users["clerk"],))
    with pytest.raises(psycopg.Error, match="R5"):
        await release(demo, case["id"], case["version"], who="clerk")
    assert (await release(demo, case["id"], case["version"]))["to_state"] == "releasing"


async def test_an_unknown_author_needs_two_approvers(demo):
    r = await fresh(demo)
    await change(demo.env, load_seed(SEED), "acme", unknown=True)
    case = await detect(demo, r.dataset.id)
    assert case["has_unknown_changer"] is True
    call_id = demo.voice.placed[-1]["call_id"]
    await report(demo, case["id"], call_id, answers=transit(call_id, digits="0001"))
    await demo.worker.drain(r.dataset.id)
    case = await demo_case(demo, r.dataset.id)
    first = await release(demo, case["id"], case["version"], who="approver1")
    assert first["status"] == "awaiting_second_approver"
    assert (await release(demo, case["id"], case["version"], who="approver2"))["to_state"] == "releasing"


async def test_a_change_refuses_an_unknown_vendor_or_author(demo):
    await fresh(demo)
    with pytest.raises(DemoError, match="zeta"):
        await change(demo.env, load_seed(SEED), "zeta")
    with pytest.raises(DemoError, match="--by nobody"):
        await change(demo.env, load_seed(SEED), "acme", by="nobody")


async def test_every_command_refuses_the_sandbox(demo):
    await fresh(demo)
    sandbox = dataclasses.replace(demo.env, settings=dataclasses.replace(demo.env.settings, ramp_mode=RampMode.SANDBOX))
    for command in (reset(sandbox, load_seed(SEED), {}, today=TODAY), change(sandbox, load_seed(SEED), "acme")):
        with pytest.raises(DemoError, match="sandbox"):
            await command


async def test_a_reset_that_fails_partway_leaves_the_previous_demo_current(demo):
    before = await fresh(demo)
    bad = load_seed(SEED)
    bad["vendors"][1]["accounts"][0]["routing_number"] = "not-a-number"  # the mock refuses the seed
    with pytest.raises(DemoError, match="refused"):
        await reset(demo.env, bad, {}, today=TODAY)
    current = await one(demo.pool, "select id from app.datasets where kind = 'demo' and retired_at is null")
    assert current["id"] == before.dataset.id


def test_a_malformed_link_is_a_refusal_not_a_crash():
    from dialback_core.main import _link
    assert _link("clerk=pat@judges.example") == ("clerk", "pat@judges.example")
    for raw in ("clerk", "=pat@judges.example", "clerk=pat"):
        with pytest.raises(DemoError, match="--link"):
            _link(raw)


async def test_every_command_needs_a_current_demo(pool, database_url):
    world = await make_world(pool)
    async with system_tx(pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where kind = 'demo' and retired_at is null")
    env = DemoEnv(pool=pool, settings=core_settings(), mock=world.mock, connector=world.connector, owner_url=database_url)
    with pytest.raises(DemoError, match="demo reset"):
        await change(env, load_seed(SEED), "acme")
    await world.core.aclose()
    await world.mock.aclose()
