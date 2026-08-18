"""Tests for the Serper HTTP client."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from typing_extensions import override

from serper_mcp_server.core import (
    DEFAULT_AIOHTTP_TIMEOUT_SECONDS,
    GOOGLE_SERPER_BASE_URL,
    SCRAPE_SERPER_URL,
    SERPER_API_KEY_ENV_VAR,
    SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR,
    SERPER_REQUEST_TIMEOUT_ENV_VAR,
    SerperClient,
    SerperClientError,
    SerperConcurrencyLimitError,
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


class BlockingResponse(FakeResponse):
    """Fake response that remains active until its session releases it.

    :param session: Session tracking active fake HTTP requests.
    :type session: BlockingSession
    """

    def __init__(self, session: BlockingSession) -> None:
        super().__init__(json_body={"organic": []})
        self.session: BlockingSession = session
        self.entered: bool = False

    @override
    async def __aenter__(self) -> BlockingResponse:
        """Start and block a fake HTTP request.

        :return: Active fake response.
        :rtype: BlockingResponse
        """

        self.entered = True
        self.session.active_requests += 1
        self.session.maximum_active_requests = max(
            self.session.maximum_active_requests,
            self.session.active_requests,
        )
        if self.session.active_requests >= self.session.expected_active_requests:
            self.session.expected_requests_started.set()
        try:
            await self.session.release_requests.wait()
        except BaseException:
            self.session.active_requests -= 1
            self.entered = False
            raise
        return self

    @override
    async def __aexit__(self, *_args: object) -> None:
        """Finish a fake HTTP request.

        :return: None.
        :rtype: None
        """

        if self.entered:
            self.session.active_requests -= 1
            self.entered = False


class BlockingSession:
    """Fake session that tracks and blocks simultaneous requests.

    :param expected_active_requests: Number of active requests that signals
        readiness.
    :type expected_active_requests: int
    """

    closed: bool = False

    def __init__(self, expected_active_requests: int) -> None:
        self.expected_active_requests: int = expected_active_requests
        self.active_requests: int = 0
        self.maximum_active_requests: int = 0
        self.submitted_urls: list[str] = []
        self.expected_requests_started: asyncio.Event = asyncio.Event()
        self.release_requests: asyncio.Event = asyncio.Event()

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any],
    ) -> BlockingResponse:
        """Create a blocking fake response and record its URL.

        :param url: Request URL.
        :type url: str
        :param headers: Request headers.
        :type headers: dict[str, str]
        :param json: Request JSON payload.
        :type json: dict[str, Any]
        :return: Blocking response context manager.
        :rtype: BlockingResponse
        """

        _ = headers, json
        self.submitted_urls.append(url)
        return BlockingResponse(self)


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


@pytest.fixture(autouse=True)
def clear_concurrency_limit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear concurrency configuration unless a test sets it explicitly.

    :param monkeypatch: Pytest monkeypatch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    :return: None.
    :rtype: None
    """

    monkeypatch.delenv(SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR, raising=False)


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


@pytest.mark.parametrize("limit_value", ["invalid", "0", "-1"])
def test_invalid_max_concurrent_requests_fails_client_creation(
    monkeypatch: pytest.MonkeyPatch,
    limit_value: str,
) -> None:
    """Invalid concurrency limits fail client creation clearly.

    :param monkeypatch: Pytest monkeypatch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    :param limit_value: Invalid environment value.
    :type limit_value: str
    :return: None.
    :rtype: None
    """

    monkeypatch.setenv(SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR, limit_value)

    with pytest.raises(
        SerperConfigurationError,
        match=(f"{SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR} must be a positive integer"),
    ):
        SerperClient(api_key="test-key")


def test_connector_limit_matches_configured_concurrency() -> None:
    """The HTTP connection pool does not impose a lower hidden queue limit."""

    async def inspect_connector_limit() -> int:
        client = SerperClient(
            api_key="test-key",
            max_concurrent_requests=125,
        )
        try:
            session = await client.get_session()
            assert session.connector is not None
            return session.connector.limit
        finally:
            await client.close()

    assert run_async(inspect_connector_limit()) == 125


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


def test_concurrency_limit_is_shared_and_rejects_without_queueing() -> None:
    """Search and scrape share one fail-fast outbound request limit."""

    async def exercise_concurrency_limit() -> tuple[
        BlockingSession,
        SerperConcurrencyLimitError,
    ]:
        session = BlockingSession(expected_active_requests=2)
        client = SerperClient(
            api_key="test-key",
            session=cast(Any, session),
            max_concurrent_requests=2,
        )
        search_request = SearchRequest(
            q="openai",
            gl=None,
            location=None,
            hl=None,
            page=1,
            tbs=None,
            num=5,
        )
        scrape_request = WebpageRequest(
            url="https://example.com",
            includeMarkdown=False,
        )
        search_task = asyncio.create_task(
            client.google(SerperTools.GOOGLE_SEARCH, search_request)
        )
        scrape_task = asyncio.create_task(client.scrape(scrape_request))
        await asyncio.wait_for(session.expected_requests_started.wait(), timeout=1)

        with pytest.raises(SerperConcurrencyLimitError) as error_info:
            await asyncio.wait_for(
                client.google(SerperTools.GOOGLE_SEARCH, search_request),
                timeout=0.1,
            )

        assert len(session.submitted_urls) == 2
        session.release_requests.set()
        await asyncio.gather(search_task, scrape_task)
        await client.google(SerperTools.GOOGLE_SEARCH, search_request)
        return session, error_info.value

    session, error = run_async(exercise_concurrency_limit())

    assert session.maximum_active_requests == 2
    assert session.submitted_urls == [
        f"{GOOGLE_SERPER_BASE_URL}/search",
        SCRAPE_SERPER_URL,
        f"{GOOGLE_SERPER_BASE_URL}/search",
    ]
    assert "WARNING: The maximum of 2 simultaneous" in str(error)
    assert "not submitted or queued" in str(error)
    assert "Submit no more than 2" in str(error)


def test_cancelled_request_releases_concurrency_slot() -> None:
    """Cancellation returns an outbound request slot to the shared limiter."""

    async def cancel_and_retry() -> tuple[int, int]:
        session = BlockingSession(expected_active_requests=1)
        client = SerperClient(
            api_key="test-key",
            session=cast(Any, session),
            max_concurrent_requests=1,
        )
        request = SearchRequest(
            q="openai",
            gl=None,
            location=None,
            hl=None,
            page=1,
            tbs=None,
            num=5,
        )
        request_task = asyncio.create_task(
            client.google(SerperTools.GOOGLE_SEARCH, request)
        )
        await asyncio.wait_for(session.expected_requests_started.wait(), timeout=1)
        request_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request_task

        active_requests_after_cancellation = (
            client.concurrent_request_limiter.active_requests
        )
        session.release_requests.set()
        await client.google(SerperTools.GOOGLE_SEARCH, request)
        active_requests_after_retry = client.concurrent_request_limiter.active_requests
        return active_requests_after_cancellation, active_requests_after_retry

    assert run_async(cancel_and_retry()) == (0, 0)
