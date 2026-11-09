"""DIALBACK_VOICE picks the transport; any unknown value falls back to the safe path (text).

The live config (Twilio, core and URL settings) is assembled only when the mode is `live` and
every required value is present (spec 0014, decision 5). `voice_mode` stays the pure requested
flag; whether live actually runs is gated by `settings.twilio` being present, so a live-but-
incomplete deployment mounts no Twilio routes and runs the text line (fail closed)."""
from __future__ import annotations

from dialback_voice.settings import load_settings

# A complete live environment: every key decision 5 requires, with throwaway test values.
LIVE_ENV = {
    "DIALBACK_VOICE": "live",
    "TWILIO_ACCOUNT_SID": "ACtest00000000000000000000000000",
    "TWILIO_AUTH_TOKEN": "auth-token-test-value",
    "TWILIO_FROM_NUMBER": "+14155550100",
    "DIALBACK_CALL_TOKEN_KEY": "call-token-key-test-value",
    "DIALBACK_CORE_URL": "https://core.internal",
    "DIALBACK_INTERNAL_TOKEN": "internal-service-token-test-value",
    "DIALBACK_PUBLIC_BASE_URL": "https://voice.example",
    "DIALBACK_WS_BASE_URL": "wss://voice.example",
}


def test_default_is_text_when_unset():
    assert load_settings({}).voice_mode == "text"


def test_live_is_honoured():
    assert load_settings({"DIALBACK_VOICE": "live"}).voice_mode == "live"


def test_unknown_value_falls_back_to_text():
    # The flags table (AGENTS.md): an unknown value always takes the safe default.
    assert load_settings({"DIALBACK_VOICE": "twilio-maybe"}).voice_mode == "text"


def test_live_config_is_absent_when_incomplete():
    # Live requested but nothing else set: the flag is honoured, but no live config is assembled,
    # so wire.py mounts no Twilio routes (fail closed). Also check a config missing just one key.
    assert load_settings({"DIALBACK_VOICE": "live"}).twilio is None
    assert load_settings(LIVE_ENV | {"TWILIO_AUTH_TOKEN": ""}).twilio is None
    missing_one = {k: v for k, v in LIVE_ENV.items() if k != "DIALBACK_PUBLIC_BASE_URL"}
    assert load_settings(missing_one).twilio is None


def test_live_config_is_absent_in_text_mode_even_if_keys_present():
    # The keys are all there, but the mode is text: no live config, no routes.
    assert load_settings({k: v for k, v in LIVE_ENV.items() if k != "DIALBACK_VOICE"}).twilio is None


def test_live_config_is_assembled_when_complete():
    s = load_settings(LIVE_ENV)
    assert s.voice_mode == "live"
    assert s.twilio is not None
    assert s.twilio.account_sid == "ACtest00000000000000000000000000"
    assert s.twilio.auth_token == b"auth-token-test-value"
    assert s.twilio.from_number == "+14155550100"
    assert s.twilio.call_token_key == b"call-token-key-test-value"
    assert s.twilio.core_url == "https://core.internal"
    assert s.twilio.internal_token == b"internal-service-token-test-value"
    assert s.twilio.public_base_url == "https://voice.example"
    assert s.twilio.ws_base_url == "wss://voice.example"


def test_live_reads_the_anthropic_key_when_present():
    # The agent key is separate from the Twilio config: it swaps in the live Claude agent (spec 0015),
    # while a missing key leaves the ScriptedAgent placeholder and still mounts the Twilio routes.
    assert load_settings(LIVE_ENV | {"ANTHROPIC_API_KEY": "sk-abc"}).anthropic_api_key == b"sk-abc"


def test_anthropic_key_is_absent_without_it_or_in_text_mode():
    assert load_settings(LIVE_ENV).anthropic_api_key is None   # live, Twilio set, no agent key
    assert load_settings({"DIALBACK_VOICE": "text", "ANTHROPIC_API_KEY": "sk"}).anthropic_api_key is None


def test_internal_token_is_read_in_both_modes():
    # POST /calls is authenticated on the text line too, so the token is read whatever the mode.
    assert load_settings(LIVE_ENV).internal_api_token == b"internal-service-token-test-value"
    assert load_settings({"DIALBACK_VOICE": "text", "DIALBACK_INTERNAL_TOKEN": "t"}).internal_api_token == b"t"
    assert load_settings({}).internal_api_token == b""   # unset: empty, so create_app rejects every call


def test_live_reads_database_url_and_the_disclosure_names():
    # The live lookup needs the database and the fixed disclosure context (spec 0027, decision 3, 4):
    # the DB connection string, the customer whose AP team is calling, and the callback number.
    s = load_settings(LIVE_ENV | {
        "DATABASE_URL": "postgresql://u:p@h:5432/db",
        "DIALBACK_CUSTOMER_NAME": "Northwind Traders",
        "DIALBACK_AP_CALLBACK_NUMBER": "(415) 555-0100",
    })
    assert s.database_url == "postgresql://u:p@h:5432/db"
    assert s.customer_name == "Northwind Traders"
    assert s.callback_number == "(415) 555-0100"


def test_database_url_and_names_are_not_read_in_text_mode():
    # Text mode runs no DB lookup, so these are left at their safe defaults even when present.
    env = {k: v for k, v in LIVE_ENV.items() if k != "DIALBACK_VOICE"}
    s = load_settings(env | {"DATABASE_URL": "postgresql://x", "DIALBACK_CUSTOMER_NAME": "X",
                             "DIALBACK_AP_CALLBACK_NUMBER": "Y"})
    assert s.database_url is None and s.customer_name == "" and s.callback_number == ""


def test_live_without_database_url_or_names_keeps_safe_defaults():
    # Live, Twilio complete, but no DB or names set: the fields default, so the lookup factory and
    # dial_from_row fail closed rather than dialing a half-known call (R7).
    s = load_settings(LIVE_ENV)
    assert s.database_url is None and s.customer_name == "" and s.callback_number == ""
