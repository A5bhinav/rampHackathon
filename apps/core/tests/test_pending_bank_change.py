"""Spec 0093: a bank change entered in Ramp opens the case while it waits for review, and Ramp's
approval of it isn't a second change.

Ramp's sandbox, as the fake answers it (spec 0097): the vendor Acme has an active account ending
4444. A change entered in the dashboard is a `pending_approval` account; when it is approved, Ramp
lists it as `active` under a new id with no proposal id (seen on the sandbox, spec 0097 Q204).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
from types import SimpleNamespace

import httpx
import pytest
from conftest import FINGERPRINT_KEY, core_settings
from dialback_core.db import system_tx
from dialback_core.detection import Keys, import_all, sync_vendor
from dialback_core.jobs import Jobs
from dialback_core.ramp.connector import RampConnector
from dialback_core.ramp.http import TokenBucket
from test_ramp_sandbox_shapes import SANDBOX, SandboxRamp, V, _no_sleep, account, sandbox_dataset

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(pool):
    fake = SandboxRamp()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
        d = await sandbox_dataset(pool)
        connector = RampConnector(core_settings(ramp_sandbox=SANDBOX), http, limiter=TokenBucket(100_000),
                                  sleep=_no_sleep, pool=pool)
        w = SimpleNamespace(fake=fake, pool=pool, d=d, port=connector.for_dataset(d.id), keys=Keys(FINGERPRINT_KEY))
        yield w


async def baseline(w) -> None:
    """The dataset's first import: what Ramp shows then is what Dialback knows, not a change."""
    await import_all(w.pool, w.port, w.keys, w.d.id)


async def sync(w):
    return await sync_vendor(w.pool, w.port, w.keys, w.d.id, V, "poll")  # the sweep records "poll" too


def pending(account_id: str, last_four: str) -> dict:
    return account(account_id, last_four, "pending_approval") | {"proposal_id": f"prop-{account_id}"}


async def changes(w) -> list[str]:
    """The Ramp account each recorded bank change is about, oldest first."""
    async with system_tx(w.pool) as conn:
        rows = await (await conn.execute(
            "select n.ramp_account_id from app.bank_change_events e join app.bank_accounts n on n.id = e.new_account_id "
            "where e.dataset_id = %s order by e.detected_at, e.id", (w.d.id,))).fetchall()
    return [r["ramp_account_id"] for r in rows]


async def mirror(w) -> dict[str, tuple[bool, bool]]:
    async with system_tx(w.pool) as conn:
        rows = await (await conn.execute(
            "select b.ramp_account_id, b.is_current, b.ramp_pending from app.bank_accounts b "
            "join app.vendors v on v.id = b.vendor_id where v.dataset_id = %s and v.ramp_vendor_id = %s", (w.d.id, V))).fetchall()
    return {r["ramp_account_id"]: (r["is_current"], r["ramp_pending"]) for r in rows}


async def test_a_change_entered_in_ramp_opens_a_case_while_it_waits_for_review(world):
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    [found] = await sync(world)
    assert found.ramp_account_id == "acct-p" and found.case_id is not None and found.status == "applied"
    assert await changes(world) == ["acct-p"]
    assert (await mirror(world))["acct-p"] == (False, True), "pending: not the account Ramp pays"


@pytest.mark.parametrize("watch", [(V,), ()])
async def test_the_poll_opens_a_case_for_a_pending_change_only_on_a_watched_vendor(pool, watch):
    # Spec 0102: a pending change writes no event and moves no timestamp, so the poll can't see it
    # unless the vendor is on the demo watch list.
    fake = SandboxRamp()
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)) as http:
        d = await sandbox_dataset(pool)
        settings = core_settings(ramp_sandbox=dataclasses.replace(SANDBOX, watch_vendors=watch))
        connector = RampConnector(settings, http, limiter=TokenBucket(100_000), sleep=_no_sleep, pool=pool)
        keys = Keys(FINGERPRINT_KEY)
        await import_all(pool, connector.for_dataset(d.id), keys, d.id)
        fake.accounts[V].append(pending("acct-p", "4321"))
        found = await Jobs(pool, settings, connector, keys).poll(d.id, {"watermark": dt.datetime(2026, 11, 10, 11, tzinfo=dt.UTC)})
    assert found.effects == (1 if watch else 0)


async def test_a_pending_account_already_known_is_not_a_change_again(world):
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    assert await sync(world) == [] and await changes(world) == ["acct-p"]


async def test_its_approval_is_not_a_second_change(world):
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    world.fake.accounts[V] = [a for a in world.fake.accounts[V] if a["id"] != "acct-p"] + [account("acct-a", "4321")]
    assert await sync(world) == []
    assert await changes(world) == ["acct-p"], "the pending change's case carries on"
    assert (await mirror(world))["acct-a"] == (True, False), "the approved account is current"


@pytest.mark.parametrize("outcome", ["approved", "rejected"])
async def test_a_later_account_with_the_same_last_four_is_a_new_change(world, outcome):
    # Found by review: the pending mark must be used up once, or a later account ending the same
    # (a fraudster can choose the last four) would pass as "the approval", with no case and no hold.
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    kept = [a for a in world.fake.accounts[V] if a["id"] != "acct-p"]
    world.fake.accounts[V] = kept + ([account("acct-a", "4321")] if outcome == "approved" else [])
    assert await sync(world) == []
    world.fake.accounts[V].append(account("acct-later", "4321"))
    found = await sync(world)
    assert [c.ramp_account_id for c in found] == ["acct-later"], "a later account is a new change"
    assert (await changes(world))[-1] == "acct-later"
    assert (await mirror(world))["acct-p"] == (False, False), "Ramp no longer lists it: used up"


@pytest.mark.parametrize("case", ["other last four", "pending still listed", "pending seen before any change"])
async def test_an_active_account_that_doesnt_follow_a_pending_change_is_a_change(world, case):
    if case == "pending seen before any change":
        world.fake.accounts[V].append(pending("acct-p", "4321"))  # there at the first import: no case for it
    await baseline(world)
    if case != "pending seen before any change":
        world.fake.accounts[V].append(pending("acct-p", "4321"))
        await sync(world)
    listed = [a for a in world.fake.accounts[V] if case == "pending still listed" or a["id"] != "acct-p"]
    world.fake.accounts[V] = listed + [account("acct-a", "5555" if case == "other last four" else "4321")]
    [found] = await sync(world)
    assert found.ramp_account_id == "acct-a"
    assert (await changes(world))[-1] == "acct-a"


async def test_an_earlier_active_account_gone_from_ramp_is_no_pending_change(world):
    # An approved change whose account Ramp later archived: a new account with its last four is a
    # change, never "its approval" (only a pending row can be approved into an account).
    await baseline(world)
    world.fake.accounts[V].append(account("acct-x", "4321"))
    await sync(world)  # a change, for an active account
    world.fake.accounts[V] = [a for a in world.fake.accounts[V] if a["id"] != "acct-x"] + [account("acct-a", "4321")]
    [found] = await sync(world)
    assert found.ramp_account_id == "acct-a" and await changes(world) == ["acct-x", "acct-a"]


async def test_a_pending_change_edited_into_a_new_pending_account_is_checked_again(world):
    # Spec 0093 Q213's other side: the edit gives a new pending id, so it is a new change (verified again).
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    world.fake.accounts[V] = [a for a in world.fake.accounts[V] if a["id"] != "acct-p"] + [pending("acct-p2", "4321")]
    [found] = await sync(world)
    assert found.ramp_account_id == "acct-p2" and await changes(world) == ["acct-p", "acct-p2"]


async def test_an_approval_under_the_same_id_is_mirrored_and_not_a_change(world):
    # Not seen on the sandbox (it gives a new id), but if Ramp ever kept the id: no new change.
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    for a in world.fake.accounts[V]:
        if a["id"] == "acct-p":
            a["status"] = "active"
    assert await sync(world) == [] and (await mirror(world))["acct-p"] == (True, False)


async def test_one_pending_change_is_approved_into_one_account(world):
    # Two new active accounts with the pending one's last four: only one can be its approval.
    await baseline(world)
    world.fake.accounts[V].append(pending("acct-p", "4321"))
    await sync(world)
    world.fake.accounts[V] = [a for a in world.fake.accounts[V] if a["id"] != "acct-p"] + [
        account("acct-a", "4321"), account("acct-b", "4321")]
    [found] = await sync(world)
    assert found.ramp_account_id == "acct-b" and await changes(world) == ["acct-p", "acct-b"]
