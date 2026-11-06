"""DIALBACK_VOICE picks the transport; any unknown value falls back to the safe path (text)."""
from __future__ import annotations

from dialback_voice.settings import load_settings


def test_default_is_text_when_unset():
    assert load_settings({}).voice_mode == "text"


def test_live_is_honoured():
    assert load_settings({"DIALBACK_VOICE": "live"}).voice_mode == "live"


def test_unknown_value_falls_back_to_text():
    # The flags table (AGENTS.md): an unknown value always takes the safe default.
    assert load_settings({"DIALBACK_VOICE": "twilio-maybe"}).voice_mode == "text"
