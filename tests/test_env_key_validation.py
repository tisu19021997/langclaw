"""Guard against silently-ignored, misspelled ``LANGCLAW__`` env vars.

Pydantic settings use ``extra="ignore"``, so a typo like
``LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN`` (real field is ``…__TOKEN``) is
dropped with no error. ``validate_env_keys`` walks the schema and surfaces any
``LANGCLAW__`` env var that doesn't map to a real field.
"""

from __future__ import annotations

import pytest


def test_unknown_langclaw_env_key_is_flagged():
    from langclaw.config.schema import validate_env_keys

    env = {"LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN": "123:abc"}
    assert "LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN" in validate_env_keys(env)


def test_provider_key_typo_is_flagged():
    from langclaw.config.schema import validate_env_keys

    env = {"LANGCLAW__PROVIDERS__OPENAI__API_KEY": "sk-..."}
    assert "LANGCLAW__PROVIDERS__OPENAI__API_KEY" in validate_env_keys(env)


def test_known_langclaw_env_keys_not_flagged():
    from langclaw.config.schema import validate_env_keys

    env = {
        "LANGCLAW__LOG_LEVEL": "INFO",
        "LANGCLAW__CHANNELS__TELEGRAM__TOKEN": "123:abc",
        "LANGCLAW__AGENTS__MODEL": "openai:gpt-4.1",
        "LANGCLAW__CRON__ENABLED": "true",
        "LANGCLAW__CRON__DATA_STORE__SQLITE__DB_PATH": "/tmp/x.db",  # deep nesting
        "LANGCLAW__CHANNELS__TELEGRAM__USER_ROLES": "123:admin,@a:editor",  # dict leaf
        "LANGCLAW__AGENTS__MODEL_KWARGS__TEMPERATURE": "0.4",  # per-key dict env
        "OPENAI_API_KEY": "sk-...",  # non-prefixed → must be ignored
    }
    assert validate_env_keys(env) == []


def test_case_insensitive_match():
    from langclaw.config.schema import validate_env_keys

    # pydantic-settings matches env case-insensitively; the validator must too.
    assert validate_env_keys({"langclaw__agents__model": "openai:gpt-4.1"}) == []


def test_empty_value_not_flagged():
    """Empty env vars are ignored (matches ``env_ignore_empty=True``)."""
    from langclaw.config.schema import validate_env_keys

    assert validate_env_keys({"LANGCLAW__BOGUS_KEY": ""}) == []


def test_strict_mode_raises_on_unknown():
    from langclaw.config.schema import validate_env_keys

    with pytest.raises(ValueError, match="Unknown LANGCLAW__"):
        validate_env_keys({"LANGCLAW__NOPE": "x"}, strict=True)


def test_warning_logged_for_unknown_key():
    """A non-strict unknown key emits a loguru WARNING naming the bad key."""
    from loguru import logger

    from langclaw.config.schema import validate_env_keys

    messages: list[str] = []
    handler_id = logger.add(messages.append, level="WARNING", format="{message}")
    try:
        validate_env_keys({"LANGCLAW__CHANNELS__TELEGRAM__BOT_TOKEN": "x"})
    finally:
        logger.remove(handler_id)

    assert any("BOT_TOKEN" in m and "Unknown LANGCLAW__" in m for m in messages)


def test_real_dotenv_has_no_unknown_keys():
    """The shipped repo .env / environment must not trip the validator —
    a regression here means the schema walk is incomplete."""
    from langclaw.config.schema import load_config, validate_env_keys

    load_config()  # loads .env into os.environ
    assert validate_env_keys() == []  # uses os.environ by default
