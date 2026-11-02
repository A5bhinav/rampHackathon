"""`dialback-core`: run the service, or manage datasets.

    dialback-core serve                                  API and worker (the default)
    dialback-core new-dataset --kind demo --label "Demo 3"  a new dataset and evidence chain
    dialback-core import-vendors --dataset <id>          initial import (baseline) or a full sweep
    dialback-core retire-dataset --dataset <id>          hide a dataset; nothing is deleted
    dialback-core run-job --dataset <id> --job reconcile run poll, full_sweep, reconcile or verify_chain now
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
from uuid import UUID

import httpx
import uvicorn

from .app import create_app
from .datasets import create_dataset, retire_dataset
from .db import open_pool, system_tx
from .detection import Keys, import_all
from .jobs import JOBS, Jobs
from .ramp import RampConnector
from .settings import Settings


async def _new_dataset(settings: Settings, kind: str, label: str, sim_now: str | None) -> None:
    pool = await open_pool(settings.database_url, max_size=2)
    try:
        d = await create_dataset(pool, kind, label, dt.datetime.fromisoformat(sim_now) if sim_now else None)
        print(f"dataset {d.id}\nchain   {d.chain_id}")
    finally:
        await pool.close()


async def _import(settings: Settings, dataset_id: UUID) -> None:
    pool = await open_pool(settings.database_url, max_size=3)
    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            n = await import_all(pool, RampConnector(settings, http).for_dataset(dataset_id),
                                 Keys(settings.fingerprint_key, settings.fingerprint_key_version), dataset_id)
            print(f"synced {n} vendors")
        finally:
            await pool.close()


async def _run_job(settings: Settings, dataset_id: UUID, job: str) -> None:
    pool = await open_pool(settings.database_url, max_size=3)
    async with httpx.AsyncClient(timeout=10.0) as http:
        try:
            jobs = Jobs(pool, settings, RampConnector(settings, http),
                        Keys(settings.fingerprint_key, settings.fingerprint_key_version), "cli")
            effects = await jobs.run(dataset_id, job, force=True)
            async with system_tx(pool) as conn:
                row = await (await conn.execute("select last_ok, last_error, last_result from app.job_runs "
                                                "where dataset_id = %s and job = %s", (dataset_id, job))).fetchone()
            print(f"{job}: ok={row['last_ok']} effects={effects} result={row['last_result']} error={row['last_error']}")
        finally:
            await pool.close()


async def _retire(settings: Settings, dataset_id: UUID) -> None:
    pool = await open_pool(settings.database_url, max_size=2)
    try:
        await retire_dataset(pool, dataset_id)
    finally:
        await pool.close()


def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(prog="dialback-core")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    nd = sub.add_parser("new-dataset")
    nd.add_argument("--kind", choices=("dev", "demo", "lab"), required=True)
    nd.add_argument("--label", required=True)
    nd.add_argument("--sim-now", help="lab only: start the dataset's simulated clock (ISO 8601)")
    for name in ("import-vendors", "retire-dataset"):
        sub.add_parser(name).add_argument("--dataset", type=UUID, required=True)
    rj = sub.add_parser("run-job")
    rj.add_argument("--dataset", type=UUID, required=True)
    rj.add_argument("--job", choices=JOBS, required=True)
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.cmd in (None, "serve"):
        uvicorn.run(create_app(settings), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8001")))
    elif args.cmd == "new-dataset":
        asyncio.run(_new_dataset(settings, args.kind, args.label, args.sim_now))
    elif args.cmd == "import-vendors":
        asyncio.run(_import(settings, args.dataset))
    elif args.cmd == "retire-dataset":
        asyncio.run(_retire(settings, args.dataset))
    elif args.cmd == "run-job":
        asyncio.run(_run_job(settings, args.dataset, args.job))


if __name__ == "__main__":
    run()
