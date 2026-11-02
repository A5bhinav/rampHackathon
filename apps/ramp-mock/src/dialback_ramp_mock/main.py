"""Run the mock: `dialback-ramp-mock` (reads DATABASE_URL, MOCK_RAMP_SECRET, PORT)."""
from __future__ import annotations

import os

import uvicorn

from .app import create_app
from .settings import Settings


def run() -> None:
    app = create_app(Settings.from_env())
    uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8003")))


if __name__ == "__main__":
    run()
