"""Tests for the Serper HTTP client."""

from __future__ import annotations

import pytest

from serper_mcp_server.core import (
    AIOHTTP_TIMEOUT_ENV_VAR,
    DEFAULT_AIOHTTP_TIMEOUT_SECONDS,
    SERPER_API_KEY_ENV_VAR,
    SerperClient,
    SerperConfigurationError,
)


def test_api_key_is_read_lazily_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client reads API key configuration after construction."""

    monkeypatch.delenv(SERPER_API_KEY_ENV_VAR, raising=False)
    client = SerperClient()

    monkeypatch.setenv(SERPER_API_KEY_ENV_VAR, " env-key ")

    assert client.api_key == "env-key"


def test_timeout_default_is_used(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default timeout is used when no environment value exists."""

    monkeypatch.delenv(AIOHTTP_TIMEOUT_ENV_VAR, raising=False)
    client = SerperClient(api_key="test-key")

    assert client.timeout_seconds == DEFAULT_AIOHTTP_TIMEOUT_SECONDS


def test_invalid_timeout_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid timeout configuration is reported clearly."""

    monkeypatch.setenv(AIOHTTP_TIMEOUT_ENV_VAR, "invalid")
    client = SerperClient(api_key="test-key")

    with pytest.raises(SerperConfigurationError, match="must be an integer"):
        _ = client.timeout_seconds
