"""Datasets: every demo reset and every attack-lab scenario starts a new dataset with its own
evidence chain, instead of deleting rows (design, "Data model")."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from .db import system_tx

KINDS = ("dev", "demo", "lab")


@dataclass(frozen=True)
class Dataset:
    id: UUID
    kind: str
    label: str
    chain_id: UUID


async def create_dataset(pool: AsyncConnectionPool, kind: str, label: str, sim_now: dt.datetime | None = None) -> Dataset:
    """A new dataset and its chain. `sim_now` starts a lab dataset's clock (spec 0002, D2)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if sim_now is not None and kind != "lab":
        raise ValueError("only lab datasets have a simulated clock")
    async with system_tx(pool) as conn:
        chain_id = (await (await conn.execute(
            "insert into audit.chains (label) values (%s) returning id", (label,))).fetchone())["id"]
        dataset_id = (await (await conn.execute(
            "insert into app.datasets (kind, label, chain_id) values (%s, %s, %s) returning id",
            (kind, label, chain_id))).fetchone())["id"]
        if sim_now is not None:
            await conn.execute("select app.advance_clock(%s, %s)", (dataset_id, sim_now))
    return Dataset(dataset_id, kind, label, chain_id)


async def retire_dataset(pool: AsyncConnectionPool, dataset_id: UUID) -> None:
    """Take a dataset out of the dashboard's list. Nothing is deleted."""
    async with system_tx(pool) as conn:
        await conn.execute("update app.datasets set retired_at = now() where id = %s and retired_at is null", (dataset_id,))
