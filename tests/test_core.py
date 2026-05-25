"""Tests for the Serper HTTP client."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from serper_mcp_server.core import (
    GOOGLE_SERPER_BASE_URL,
    DEFAULT_AIOHTTP_TIMEOUT_SECONDS,
    SCRAPE_SERPER_URL,
    SERPER_API_KEY_ENV_VAR,
    SERPER_REQUEST_TIMEOUT_ENV_VAR,
    SerperClient,
    SerperClientError,
    SerperConfigurationError,
)
from serper_mcp_server.enums import SerperTools
from serper_mcp_server.metrics import MetricEvent
from serper_mcp_server.schemas import LensRequest, SearchRequest, WebpageRequest


class FakeResponse:
    """Async response test double."""

    def __init__(
        self,
        *,
        status: int = 200,
        text: str = "Example Domain",
        json_body: Any | None = None,
    ) -> None:
        self.status: int = status
        self._text: str = text
        self._json_body: Any = json_body or {"organic": []}

    async def __aenter__(self) -> FakeResponse:
        """Enter the async context manager.

        :return: The fake response.
        :rtype: FakeResponse
        """

        return self

    async def __aexit__(self, *_args: object) -> None:
        """Exit the async context manager.

        :return: None.
        :rtype: None
        """

    async def text(self) -> str:
        """Return fake response text.

        :return: Fake response text.
        :rtype: str
        """

        return self._text

    async def json(self, *_args: object, **_kwargs: object) -> Any:
        """Return fake response JSON.

        :return: Fake JSON body.
        :rtype: Any
        """

        return self._json_body


class FakeSession:
    """Async session test double."""

    closed: bool = False

    def __init__(self, response: FakeResponse) -> None:
        self.response: FakeResponse = response
        self.last_url: str | None = None
        self.last_headers: dict[str, str] | None = None
        self.last_json: dict[str, Any] | None = None

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> FakeResponse:
        """Record a fake POST request.

        :param url: Request URL.
        :type url: str
        :param headers: Request headers.
        :type headers: dict[str, str]
        :param json: Request JSON payload.
        :type json: dict[str, Any]
        :return: Fake response.
        :rtype: FakeResponse
        """

        self.last_url = url
        self.last_headers = headers
        self.last_json = json
        return self.response


class FakeMetricsRecorder:
    """Metrics recorder test double."""

    def __init__(self) -> None:
        self.events: list[MetricEvent] = []

    async def record_request(self, event: MetricEvent) -> None:
        """Record a metric event in memory.

        :param event: Metric event.
        :type event: MetricEvent
        :return: None.
        :rtype: None
        """

        self.events.append(event)


def run_async(awaitable: Any) -> Any:
    """Run an awaitable for tests.

    :param awaitable: Awaitable object.
    :type awaitable: Any
    :return: Awaitable result.
    :rtype: Any
    """

    return asyncio.run(awaitable)


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

    monkeypatch.delenv(SERPER_REQUEST_TIMEOUT_ENV_VAR, raising=False)
    client = SerperClient(api_key="test-key")

    assert client.timeout_seconds == DEFAULT_AIOHTTP_TIMEOUT_SECONDS == 30


def test_timeout_can_be_overridden(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeout can be overridden with a positive integer."""

    monkeypatch.setenv(SERPER_REQUEST_TIMEOUT_ENV_VAR, "45")
    client = SerperClient(api_key="test-key")

    assert client.timeout_seconds == 45


def test_invalid_timeout_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid timeout configuration is reported clearly."""

    monkeypatch.setenv(SERPER_REQUEST_TIMEOUT_ENV_VAR, "invalid")
    client = SerperClient(api_key="test-key")

    with pytest.raises(SerperConfigurationError, match="must be an integer"):
        _ = client.timeout_seconds


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_timeout_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """Non-positive timeout configuration is rejected."""

    monkeypatch.setenv(SERPER_REQUEST_TIMEOUT_ENV_VAR, value)
    client = SerperClient(api_key="test-key")

    with pytest.raises(SerperConfigurationError, match="must be greater than 0"):
        _ = client.timeout_seconds


def test_google_uses_serper_post_endpoint() -> None:
    """Google Search calls Serper with JSON authentication headers."""

    session = FakeSession(FakeResponse(json_body={"organic": []}))
    client = SerperClient(api_key="test-key", session=cast(Any, session))

    response = run_async(
        client.google(
            SerperTools.GOOGLE_SEARCH,
            SearchRequest(
                q="openai",
                gl=None,
                location=None,
                hl=None,
                page=1,
                tbs=None,
                num=5,
            ),
        )
    )

    assert response == {"organic": []}
    assert session.last_url == f"{GOOGLE_SERPER_BASE_URL}/search"
    assert session.last_headers == {
        "X-API-KEY": "test-key",
        "Content-Type": "application/json",
    }
    assert session.last_json == {"q": "openai", "page": 1, "num": 5}


def test_google_records_search_metric() -> None:
    """Google Search records a portable search metric event."""

    session = FakeSession(
        FakeResponse(
            json_body={
                "organic": [{"title": "Example", "link": "https://example.com"}],
            }
        )
    )
    metrics = FakeMetricsRecorder()
    client = SerperClient(
        api_key="test-key",
        session=cast(Any, session),
        metrics=metrics,
    )

    run_async(
        client.google(
            SerperTools.GOOGLE_SEARCH,
            SearchRequest(
                q="openai",
                gl=None,
                location=None,
                hl=None,
                page=1,
                tbs=None,
                num=5,
            ),
        )
    )

    assert len(metrics.events) == 1
    event = metrics.events[0]
    assert event.tool == "google_search"
    assert event.request_type == "search"
    assert event.succeeded is True
    assert event.status_code == 200
    assert event.query == "openai"
    assert event.result_count == 1


def test_google_records_specific_search_tool_metric() -> None:
    """Google-backed tools record metrics under their public tool name."""

    session = FakeSession(
        FakeResponse(
            json_body={
                "images": [{"title": "Example", "imageUrl": "https://example.com/i"}],
            }
        )
    )
    metrics = FakeMetricsRecorder()
    client = SerperClient(
        api_key="test-key",
        session=cast(Any, session),
        metrics=metrics,
    )

    run_async(
        client.google(
            SerperTools.GOOGLE_SEARCH_IMAGES,
            SearchRequest(
                q="openai",
                gl=None,
                location=None,
                hl=None,
                page=1,
                tbs=None,
                num=5,
            ),
        )
    )

    event = metrics.events[0]
    assert event.tool == "google_search_images"
    assert event.query == "openai"
    assert event.result_count == 1


def test_lens_records_url_metric() -> None:
    """Lens-style Serper requests record URL input for hashing."""

    session = FakeSession(FakeResponse(json_body={"images": []}))
    metrics = FakeMetricsRecorder()
    client = SerperClient(
        api_key="test-key",
        session=cast(Any, session),
        metrics=metrics,
    )

    run_async(
        client.google(
            SerperTools.GOOGLE_SEARCH_LENS,
            LensRequest(
                url="https://example.com/image.png",
                gl=None,
                hl=None,
            ),
        )
    )

    event = metrics.events[0]
    assert event.tool == "google_search_lens"
    assert event.query is None
    assert event.url == "https://example.com/image.png"


def test_scrape_records_scrape_metric() -> None:
    """Webpage scrape records a portable scrape metric event."""

    response_body = {
        "text": "Example Domain",
        "markdown": "# Example Domain",
        "metadata": {"title": "Example Domain"},
        "credits": 2,
    }
    session = FakeSession(FakeResponse(json_body=response_body))
    metrics = FakeMetricsRecorder()
    client = SerperClient(
        api_key="test-key",
        session=cast(Any, session),
        metrics=metrics,
    )

    response = run_async(
        client.scrape(WebpageRequest(url="https://example.com", includeMarkdown=True))
    )

    assert response == response_body
    assert session.last_url == SCRAPE_SERPER_URL
    assert len(metrics.events) == 1
    event = metrics.events[0]
    assert event.tool == "webpage_scrape"
    assert event.request_type == "scrape"
    assert event.succeeded is True
    assert event.status_code == 200
    assert event.url == "https://example.com"
    assert event.response_format == "markdown"
    assert event.returned_bytes == len(str(response_body).encode("utf-8"))


def test_google_records_failed_search_metric() -> None:
    """Expected HTTP failures record failed metrics before re-raising."""

    session = FakeSession(FakeResponse(status=429, text="rate limited"))
    metrics = FakeMetricsRecorder()
    client = SerperClient(
        api_key="test-key",
        session=cast(Any, session),
        metrics=metrics,
    )

    with pytest.raises(SerperClientError, match="HTTP 429"):
        run_async(
            client.google(
                SerperTools.GOOGLE_SEARCH,
                SearchRequest(
                    q="openai",
                    gl=None,
                    location=None,
                    hl=None,
                    page=1,
                    tbs=None,
                    num=5,
                ),
            )
        )

    event = metrics.events[0]
    assert event.tool == "google_search"
    assert event.succeeded is False
    assert event.status_code == 429
    assert event.query == "openai"
    assert event.error is not None
    assert "rate limited" in event.error
