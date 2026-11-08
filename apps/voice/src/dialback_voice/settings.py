"""Voice-service settings, read from the environment in one place. Unknown flag values fall back
to the safe path (AGENTS.md, "Flags").

`voice_mode` is the pure requested flag. The live transport is gated one level up by `twilio`:
`load_settings` assembles a `TwilioConfig` only when the mode is `live` and every required value
is present, and leaves it `None` otherwise (logging the missing keys). `wire.py` builds the live
`TwilioDeps` only when `twilio` is present, so a live-but-incomplete deployment mounts no Twilio
routes and runs the text line (spec 0014, decision 5; R7)."""
from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger(__name__)

VoiceMode = Literal["text", "live"]
AGENT_VERSION = "agent-0.1.0"

# The environment keys the live config needs, each mapped to the TwilioConfig field it fills.
# Secrets are kept as bytes (compared with hmac.compare_digest), plain settings as str.
_REQUIRED = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
             "DIALBACK_CALL_TOKEN_KEY", "DIALBACK_CORE_URL", "DIALBACK_INTERNAL_TOKEN",
             "DIALBACK_PUBLIC_BASE_URL", "DIALBACK_WS_BASE_URL")


@dataclass(frozen=True)
class TwilioConfig:
    """The validated live config: present only when the mode is `live` and every key above is set.
    Its presence is what turns live calling on; `wire.py` turns it into a `TwilioDeps`."""
    account_sid: str
    auth_token: bytes
    from_number: str
    call_token_key: bytes
    core_url: str
    internal_token: bytes
    public_base_url: str
    ws_base_url: str


@dataclass(frozen=True)
class Settings:
    voice_mode: VoiceMode = "text"
    agent_version: str = AGENT_VERSION
    twilio: TwilioConfig | None = None
    # The agent key, read only in live mode. It gates the live Claude agent (spec 0015), not the
    # Twilio routes: present, wire.py swaps in ClaudeAgent; absent, it keeps the ScriptedAgent double.
    anthropic_api_key: bytes | None = None


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    mode = env.get("DIALBACK_VOICE", "text")
    if mode not in ("text", "live"):
        mode = "text"  # any unknown value takes the safe default
    key = env.get("ANTHROPIC_API_KEY") if mode == "live" else None
    return Settings(voice_mode=mode, twilio=_load_twilio(env) if mode == "live" else None,
                    anthropic_api_key=key.encode() if key else None)


def _load_twilio(env: Mapping[str, str]) -> TwilioConfig | None:
    """The live config, or None when any required key is missing or empty. There is no half-live:
    one missing secret means the whole live path stays off and the service runs the text line."""
    missing = [key for key in _REQUIRED if not env.get(key)]
    if missing:
        log.warning("DIALBACK_VOICE=live but the live config is incomplete; running the text line. "
                    "Missing: %s", ", ".join(missing))
        return None
    return TwilioConfig(
        account_sid=env["TWILIO_ACCOUNT_SID"],
        auth_token=env["TWILIO_AUTH_TOKEN"].encode(),
        from_number=env["TWILIO_FROM_NUMBER"],
        call_token_key=env["DIALBACK_CALL_TOKEN_KEY"].encode(),
        core_url=env["DIALBACK_CORE_URL"],
        internal_token=env["DIALBACK_INTERNAL_TOKEN"].encode(),
        public_base_url=env["DIALBACK_PUBLIC_BASE_URL"],
        ws_base_url=env["DIALBACK_WS_BASE_URL"],
    )
