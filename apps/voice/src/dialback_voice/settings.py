"""Voice-service settings, read from the environment in one place. Unknown flag values fall back
to the safe path (AGENTS.md, "Flags")."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

VoiceMode = Literal["text", "live"]
AGENT_VERSION = "agent-0.1.0"


@dataclass(frozen=True)
class Settings:
    voice_mode: VoiceMode = "text"
    agent_version: str = AGENT_VERSION


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    mode = env.get("DIALBACK_VOICE", "text")
    if mode not in ("text", "live"):
        mode = "text"  # any unknown value takes the safe default
    return Settings(voice_mode=mode)
