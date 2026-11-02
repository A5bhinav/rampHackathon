"""Run the core: `dialback-core` (configuration from the environment; see settings.py)."""
from __future__ import annotations

import logging
import os

import uvicorn

from .app import create_app
from .settings import Settings


def run() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    uvicorn.run(create_app(Settings.from_env()), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8001")))


if __name__ == "__main__":
    run()
