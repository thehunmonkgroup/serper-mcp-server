"""Tests for the Serper MCP server."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock

from mcp import Client, types
import pytest
from typing_extensions import override

from serper_mcp_server.core import (
    SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR,
    SerperClient,
    SerperClientError,
    SerperConfigurationError,
)
from serper_mcp_server.enums import SerperTools
from serper_mcp_server.metrics import (
    METRICS_ENABLED_ENV_VAR,
    METRICS_HOST_ENV_VAR,
    METRICS_PORT_ENV_VAR,
    MetricsService,
    NullMetricsRecorder,
)
from serper_mcp_server.schemas import WebpageRequest
from serper_mcp_server.server import SerperMcpApplication, create_mcp_server

pytestmark = pytest.mark.anyio


class FakeSerperClient(SerperClient):
    """Serper client test double returning deterministic responses."""

    def __init__(self) -> None:
        super().__init__(api_key="test-key")
        self.last_tool: SerperTools | None = None
        self.last_payload: dict[str, Any] | None = None

    @override
    async def google(
        self,
        tool: SerperTools,
        request: Any,
    ) -> dict[str, Any]:
        """Return a fake Google response.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated request model.
        :type request: Any
        :return: Fake Serper response.
        :rtype: dict[str, Any]
        """

        self.last_tool = tool
        self.last_payload = cast(dict[str, Any], request.model_dump())
        return {
            "searchParameters": {"q": self.last_payload["q"]},
            "organic": [{"title": "Example", "link": "https://example.com"}],
            "credits": 1,
        }

    @override
    async def scrape(self, request: WebpageRequest) -> dict[str, Any]:
        """Return a fake scrape response.

        :param request: Validated webpage request.
        :type request: WebpageRequest
        :return: Fake Serper scrape response.
        :rtype: dict[str, Any]
        """

        self.last_payload = request.model_dump()
        return {
            "text": "Example Domain",
            "markdown": "# Example Domain",
            "metadata": {"title": "Example Domain"},
            "credits": 2,
        }


class FailingSerperClient(SerperClient):
    """Serper client test double raising expected configuration errors."""

    @override
    async def google(
        self,
        tool: SerperTools,
        request: Any,
    ) -> dict[str, Any]:
        """Raise a configuration error.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated request model.
        :type request: Any
        :return: Never returns.
        :rtype: dict[str, Any]
        :raises SerperConfigurationError: Always raised for this test double.
        """

        raise SerperConfigurationError("SERPER_API_KEY is empty")


class IntermittentFailingSerperClient(FakeSerperClient):
    """Serper client test double that fails once before succeeding."""

    def __init__(self) -> None:
        super().__init__()
        self.should_fail_search: bool = True

    @override
    async def google(
        self,
        tool: SerperTools,
        request: Any,
    ) -> dict[str, Any]:
        """Fail once, then return a fake Google response.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated request model.
        :type request: Any
        :return: Fake Serper response.
        :rtype: dict[str, Any]
        :raises SerperClientError: On the first search call.
        """

        if self.should_fail_search:
            self.should_fail_search = False
            raise SerperClientError("Serper API returned HTTP 500")
        return await super().google(tool, request)


class BlockingSerperClient(FakeSerperClient):
    """Client test double that exposes simultaneous tool execution.

    :param expected_active_calls: Number of active calls that signals readiness.
    :type expected_active_calls: int
    """

    def __init__(self, expected_active_calls: int) -> None:
        super().__init__()
        self.expected_active_calls: int = expected_active_calls
        self.active_calls: int = 0
        self.maximum_active_calls: int = 0
        self.expected_calls_started: asyncio.Event = asyncio.Event()
        self.release_calls: asyncio.Event = asyncio.Event()
        self.call_finished: asyncio.Event = asyncio.Event()

    async def wait_for_release(self) -> None:
        """Run one tracked call until the test releases it.

        :return: None.
        :rtype: None
        """

        with self.concurrent_request_limiter.claim_request_slot():
            self.active_calls += 1
            self.maximum_active_calls = max(
                self.maximum_active_calls,
                self.active_calls,
            )
            if self.active_calls >= self.expected_active_calls:
                self.expected_calls_started.set()
            try:
                await self.release_calls.wait()
            finally:
                self.active_calls -= 1
                self.call_finished.set()

    @override
    async def google(
        self,
        tool: SerperTools,
        request: Any,
    ) -> dict[str, Any]:
        """Block and then return a fake Google response.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated request model.
        :type request: Any
        :return: Fake Serper response.
        :rtype: dict[str, Any]
        """

        await self.wait_for_release()
        return await super().google(tool, request)

    @override
    async def scrape(self, request: WebpageRequest) -> dict[str, Any]:
        """Block and then return a fake scrape response.

        :param request: Validated webpage request.
        :type request: WebpageRequest
        :return: Fake Serper scrape response.
        :rtype: dict[str, Any]
        """

        await self.wait_for_release()
        return await super().scrape(request)


class FakeMetricsService:
    """Metrics service test double."""

    def __init__(self) -> None:
        self.started_host: str | None = None
        self.started_port: int | None = None
        self.closed: bool = False

    async def start_http_server(self, host: str, port: int) -> None:
        """Record sidecar startup arguments.

        :param host: Host to bind.
        :type host: str
        :param port: Port to bind.
        :type port: int
        :return: None.
        :rtype: None
        """

        self.started_host = host
        self.started_port = port

    async def close(self) -> None:
        """Record service closure.

        :return: None.
        :rtype: None
        """

        self.closed = True


@pytest.fixture(autouse=True)
def clear_session_limit_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear session limit configuration unless a test sets it explicitly.

    :param monkeypatch: Pytest monkeypatch fixture.
    :type monkeypatch: pytest.MonkeyPatch
    :return: None.
    :rtype: None
    """

    for tool_name in SerperTools:
        env_var_name = SerperMcpApplication.session_limit_env_var_name(tool_name)
        monkeypatch.delenv(env_var_name, raising=False)
    monkeypatch.delenv(SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR, raising=False)


class ServerFactory:
    """Keep real in-process MCP connections open for the duration of a test."""

    def __init__(self, stack: AsyncExitStack, mode: str) -> None:
        self.stack: AsyncExitStack = stack
        self.mode: str = mode

    async def connect(self, client: SerperClient) -> Client:
        """Start a server and connect through the SDK's public client API."""

        return await self.stack.enter_async_context(
            Client(create_mcp_server(client), mode=self.mode)
        )


@pytest.fixture
def anyio_backend() -> str:
    """Exercise the asyncio backend used by the server."""

    return "asyncio"


@pytest.fixture(params=["legacy", "auto"])
async def server_factory(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> AsyncIterator[ServerFactory]:
    """Close all client transports and lifespans after each test."""

    monkeypatch.setenv("MCP_METRICS_ENABLED", "false")
    async with AsyncExitStack() as stack:
        yield ServerFactory(stack, request.param)


async def call_tool_result_async(
    mcp_server: Client,
    tool_name: SerperTools,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    """Call through the public client API, including wire error conversion."""

    return await mcp_server.call_tool(tool_name.value, arguments)


def call_tool_text(call_result: types.CallToolResult) -> str:
    """Return the first text content item from a tool result.

    :param call_result: MCP call tool result.
    :type call_result: types.CallToolResult
    :return: First text content string.
    :rtype: str
    """

    content = call_result.content[0]
    assert isinstance(content, types.TextContent)
    return content.text


async def test_tool_list_contains_expected_metadata(
    server_factory: ServerFactory,
) -> None:
    """All public tools expose useful metadata and annotations."""

    mcp_server = await server_factory.connect(FakeSerperClient())
    tools = (await mcp_server.list_tools()).tools
    tools_by_name = {tool.name: tool for tool in tools}
    expected_tool_descriptions = {
        SerperTools.GOOGLE_SEARCH.value: "Search Google web results.",
        SerperTools.GOOGLE_SEARCH_IMAGES.value: "Search Google image results.",
        SerperTools.GOOGLE_SEARCH_VIDEOS.value: "Search Google video results.",
        SerperTools.GOOGLE_SEARCH_PLACES.value: "Search Google places results.",
        SerperTools.GOOGLE_SEARCH_MAPS.value: "Search Google Maps results.",
        SerperTools.GOOGLE_SEARCH_REVIEWS.value: "Search Google review results.",
        SerperTools.GOOGLE_SEARCH_NEWS.value: "Search Google news results.",
        SerperTools.GOOGLE_SEARCH_SHOPPING.value: ("Search Google shopping results."),
        SerperTools.GOOGLE_SEARCH_LENS.value: (
            "Search Google Lens results from an image URL."
        ),
        SerperTools.GOOGLE_SEARCH_SCHOLAR.value: ("Search Google Scholar results."),
        SerperTools.GOOGLE_SEARCH_PATENTS.value: "Search Google patents results.",
        SerperTools.GOOGLE_SEARCH_AUTOCOMPLETE.value: (
            "Fetch Google autocomplete suggestions."
        ),
        SerperTools.WEBPAGE_SCRAPE.value: "Scrape a webpage URL.",
    }

    assert set(tools_by_name) == {tool.value for tool in SerperTools}
    assert {
        name: tool.description for name, tool in tools_by_name.items()
    } == expected_tool_descriptions

    search_tool = tools_by_name[SerperTools.GOOGLE_SEARCH.value]
    assert search_tool.title == "Google Search"
    assert search_tool.output_schema is not None
    assert search_tool.annotations is not None
    assert search_tool.annotations.read_only_hint is True
    assert search_tool.annotations.destructive_hint is False
    assert search_tool.annotations.idempotent_hint is False
    assert search_tool.annotations.open_world_hint is True
    search_properties = search_tool.input_schema["properties"]
    assert search_properties["page"]["type"] == "integer"
    assert search_properties["page"]["minimum"] == 1
    assert search_properties["page"]["default"] == 1
    assert search_properties["num"]["type"] == "integer"
    assert search_properties["num"]["minimum"] == 1
    assert search_properties["num"]["maximum"] == 100
    assert search_properties["num"]["default"] == 10
    expected_search_descriptions = {
        "q": "Google search query.",
        "gl": "Two-letter country code, such as us, uk, or ca.",
        "location": "Search origin location, such as San Francisco, CA, USA.",
        "hl": "Language code, such as en, es, or fr.",
        "page": "One-based results page; 1 is the first page.",
        "tbs": ("Google time/search filter, such as qdr:d, qdr:w, qdr:m, or qdr:y."),
        "num": "Number of results to request.",
    }
    assert {
        name: schema["description"] for name, schema in search_properties.items()
    } == expected_search_descriptions

    places_tool = tools_by_name[SerperTools.GOOGLE_SEARCH_PLACES.value]
    places_properties = places_tool.input_schema["properties"]
    assert places_properties["autocorrect"]["type"] == "boolean"
    assert places_properties["autocorrect"]["default"] is True
    assert places_properties["autocorrect"]["description"] == (
        "Whether Serper should autocorrect the query."
    )

    maps_tool = tools_by_name[SerperTools.GOOGLE_SEARCH_MAPS.value]
    maps_properties = maps_tool.input_schema["properties"]
    assert maps_properties["ll"]["description"] == (
        "Google Maps latitude, longitude, and zoom string, such as "
        "@40.7504178,-73.9824837,14z."
    )
    assert maps_properties["placeId"]["description"] == (
        "Google place ID used to target a place."
    )
    assert maps_properties["cid"]["description"] == (
        "Google customer ID used to target a place."
    )

    reviews_tool = tools_by_name[SerperTools.GOOGLE_SEARCH_REVIEWS.value]
    reviews_properties = reviews_tool.input_schema["properties"]
    assert reviews_properties["fid"]["description"] == (
        "Google reviews feature ID for the place."
    )
    assert reviews_properties["sortBy"]["enum"] == [
        "mostRelevant",
        "newest",
        "highestRating",
        "lowestRating",
    ]
    assert reviews_properties["sortBy"]["default"] == "mostRelevant"
    assert reviews_properties["sortBy"]["description"] == (
        "Review sort order: mostRelevant, newest, highestRating, or lowestRating."
    )
    assert reviews_properties["topicId"]["description"] == (
        "Review topic ID used to filter reviews."
    )
    assert reviews_properties["nextPageToken"]["description"] == (
        "Token for the next page of reviews."
    )

    lens_tool = tools_by_name[SerperTools.GOOGLE_SEARCH_LENS.value]
    lens_properties = lens_tool.input_schema["properties"]
    assert lens_properties["url"]["description"] == (
        "Absolute image URL to search with Google Lens."
    )

    scrape_tool = tools_by_name[SerperTools.WEBPAGE_SCRAPE.value]
    include_markdown_schema = scrape_tool.input_schema["properties"]["includeMarkdown"]
    assert include_markdown_schema["type"] == "boolean"
    assert include_markdown_schema["default"] is False
    assert include_markdown_schema["description"] == (
        "Include Markdown in the scrape response."
    )
    assert scrape_tool.input_schema["properties"]["url"]["description"] == (
        "Absolute URL to scrape."
    )


async def test_google_search_returns_structured_content(
    server_factory: ServerFactory,
) -> None:
    """Successful search calls return structured content."""

    client = FakeSerperClient()
    mcp_server = await server_factory.connect(client)

    result = await mcp_server.call_tool(
        SerperTools.GOOGLE_SEARCH.value,
        {"q": "openai", "num": 5, "page": 1},
    )

    assert result.structured_content is not None
    assert result.structured_content["credits"] == 1
    assert result.structured_content["organic"][0]["title"] == "Example"
    assert json.loads(call_tool_text(result)) == result.structured_content
    assert (
        result.model_dump(by_alias=True)["structuredContent"]
        == result.structured_content
    )
    assert result.model_dump(by_alias=True)["isError"] is False
    assert client.last_tool == SerperTools.GOOGLE_SEARCH
    assert client.last_payload is not None
    assert client.last_payload["num"] == 5


def test_forced_env_var_name_converts_parameter_names() -> None:
    """Forced env var names are derived consistently from tool parameter names."""

    assert SerperMcpApplication.force_env_var_name("gl") == "SERPER_FORCE_GL"
    assert SerperMcpApplication.force_env_var_name("includeMarkdown") == (
        "SERPER_FORCE_INCLUDE_MARKDOWN"
    )
    assert SerperMcpApplication.force_env_var_name("nextPageToken") == (
        "SERPER_FORCE_NEXT_PAGE_TOKEN"
    )
    assert SerperMcpApplication.force_env_var_name("placeId") == (
        "SERPER_FORCE_PLACE_ID"
    )


async def test_google_search_uses_forced_environment_values(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search calls prefer forced environment values over caller arguments."""

    monkeypatch.setenv("SERPER_FORCE_GL", "us")
    monkeypatch.setenv("SERPER_FORCE_HL", "en")
    monkeypatch.setenv("SERPER_FORCE_NUM", "7")
    client = FakeSerperClient()
    mcp_server = await server_factory.connect(client)

    await mcp_server.call_tool(
        SerperTools.GOOGLE_SEARCH.value,
        {"q": "openai", "gl": "ca", "hl": "fr", "num": 5},
    )

    assert client.last_payload is not None
    assert client.last_payload["gl"] == "us"
    assert client.last_payload["hl"] == "en"
    assert client.last_payload["num"] == 7


async def test_webpage_scrape_returns_structured_content(
    server_factory: ServerFactory,
) -> None:
    """Successful scrape calls return structured content."""

    client = FakeSerperClient()
    mcp_server = await server_factory.connect(client)

    result = await mcp_server.call_tool(
        SerperTools.WEBPAGE_SCRAPE.value,
        {"url": "https://example.com", "includeMarkdown": True},
    )

    assert result.structured_content is not None
    assert result.structured_content["credits"] == 2
    assert result.structured_content["metadata"]["title"] == "Example Domain"
    assert json.loads(call_tool_text(result)) == result.structured_content
    assert (
        result.model_dump(by_alias=True)["structuredContent"]
        == result.structured_content
    )
    assert result.model_dump(by_alias=True)["isError"] is False
    assert client.last_payload is not None
    assert client.last_payload["includeMarkdown"] is True


async def test_webpage_scrape_uses_forced_environment_values(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrape calls prefer forced environment values over caller arguments."""

    monkeypatch.setenv("SERPER_FORCE_INCLUDE_MARKDOWN", "true")
    client = FakeSerperClient()
    mcp_server = await server_factory.connect(client)

    await mcp_server.call_tool(
        SerperTools.WEBPAGE_SCRAPE.value,
        {"url": "https://example.com", "includeMarkdown": False},
    )

    assert client.last_payload is not None
    assert client.last_payload["includeMarkdown"] is True


async def test_google_search_session_limit_errors_after_success(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search limits apply after the configured number of successful calls."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = await server_factory.connect(FakeSerperClient())

    first_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    second_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert first_result.is_error is False
    assert second_result.is_error is True
    second_result_text = call_tool_text(second_result)
    assert "usage limit reached" in second_result_text
    assert "Do not call google_search again" in second_result_text


async def test_session_limit_allows_calls_to_run_concurrently(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Session quota reservations do not serialize outbound tool calls."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "2",
    )
    client = BlockingSerperClient(expected_active_calls=2)
    mcp_server = await server_factory.connect(client)

    async def exercise_session_limit() -> list[types.CallToolResult]:
        first_call = asyncio.create_task(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "first"},
            )
        )
        second_call = asyncio.create_task(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "second"},
            )
        )
        await asyncio.wait_for(client.expected_calls_started.wait(), timeout=1)
        limited_result = await asyncio.wait_for(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "third"},
            ),
            timeout=0.1,
        )
        client.release_calls.set()
        completed_results = await asyncio.gather(first_call, second_call)
        return [*completed_results, limited_result]

    first_result, second_result, limited_result = await exercise_session_limit()

    assert client.maximum_active_calls == 2
    assert first_result.is_error is False
    assert second_result.is_error is False
    assert limited_result.is_error is True
    assert "usage limit reached" in call_tool_text(limited_result)


async def test_cancelled_call_releases_session_limit_reservation(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation returns reserved session quota for a later call."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    client = BlockingSerperClient(expected_active_calls=1)
    mcp_server = await server_factory.connect(client)

    async def cancel_and_retry() -> types.CallToolResult:
        cancelled_call = asyncio.create_task(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "cancelled"},
            )
        )
        await asyncio.wait_for(client.expected_calls_started.wait(), timeout=1)
        cancelled_call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled_call
        await asyncio.wait_for(client.call_finished.wait(), timeout=1)

        client.release_calls.set()
        return await call_tool_result_async(
            mcp_server,
            SerperTools.GOOGLE_SEARCH,
            {"q": "retry"},
        )

    retry_result = await cancel_and_retry()

    assert retry_result.is_error is False


async def test_global_concurrency_limit_replies_with_warning_without_queueing(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mixed tools share one fail-fast concurrent request limit."""

    monkeypatch.setenv(SERPER_MAX_CONCURRENT_REQUESTS_ENV_VAR, "2")
    client = BlockingSerperClient(expected_active_calls=2)
    mcp_server = await server_factory.connect(client)

    async def exercise_concurrency_limit() -> tuple[
        types.CallToolResult,
        tuple[types.CallToolResult, types.CallToolResult],
        types.CallToolResult,
    ]:
        search_call = asyncio.create_task(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "first"},
            )
        )
        scrape_call = asyncio.create_task(
            call_tool_result_async(
                mcp_server,
                SerperTools.WEBPAGE_SCRAPE,
                {"url": "https://example.com"},
            )
        )
        await asyncio.wait_for(client.expected_calls_started.wait(), timeout=1)
        rejected_result = await asyncio.wait_for(
            call_tool_result_async(
                mcp_server,
                SerperTools.GOOGLE_SEARCH,
                {"q": "rejected"},
            ),
            timeout=0.1,
        )
        client.release_calls.set()
        completed_results = await asyncio.gather(search_call, scrape_call)
        retry_result = await call_tool_result_async(
            mcp_server,
            SerperTools.GOOGLE_SEARCH,
            {"q": "retry"},
        )
        return rejected_result, completed_results, retry_result

    rejected_result, completed_results, retry_result = (
        await exercise_concurrency_limit()
    )

    assert client.maximum_active_calls == 2
    assert rejected_result.is_error is True
    warning_text = call_tool_text(rejected_result)
    assert "WARNING: The maximum of 2 simultaneous" in warning_text
    assert "not submitted or queued" in warning_text
    assert "Submit no more than 2" in warning_text
    assert all(result.is_error is False for result in completed_results)
    assert retry_result.is_error is False


async def test_webpage_scrape_session_limit_errors_after_success(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrape limits apply after the configured number of successful calls."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.WEBPAGE_SCRAPE),
        "1",
    )
    mcp_server = await server_factory.connect(FakeSerperClient())

    first_result = await call_tool_result_async(
        mcp_server,
        SerperTools.WEBPAGE_SCRAPE,
        {"url": "https://example.com"},
    )
    second_result = await call_tool_result_async(
        mcp_server,
        SerperTools.WEBPAGE_SCRAPE,
        {"url": "https://example.com"},
    )

    assert first_result.is_error is False
    assert second_result.is_error is True
    second_result_text = call_tool_text(second_result)
    assert "usage limit reached" in second_result_text
    assert "Do not call webpage_scrape again" in second_result_text


async def test_session_limits_are_independent_by_tool(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhausting one tool limit does not block another tool."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(
            SerperTools.GOOGLE_SEARCH_IMAGES
        ),
        "1",
    )
    mcp_server = await server_factory.connect(FakeSerperClient())

    search_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_search_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    image_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH_IMAGES,
        {"q": "openai"},
    )

    assert search_result.is_error is False
    assert limited_search_result.is_error is True
    assert image_result.is_error is False


async def test_validation_failures_do_not_consume_session_limit(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calls rejected by MCP validation do not count against the limit."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = await server_factory.connect(FakeSerperClient())

    invalid_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai", "num": 0},
    )
    successful_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert invalid_result.is_error is True
    assert successful_result.is_error is False
    assert limited_result.is_error is True
    assert "usage limit reached" in call_tool_text(limited_result)


async def test_serper_failures_do_not_consume_session_limit(
    server_factory: ServerFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serper client failures do not count against the limit."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = await server_factory.connect(IntermittentFailingSerperClient())

    failed_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    successful_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_result = await call_tool_result_async(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert failed_result.is_error is True
    assert "Serper API returned HTTP 500" in call_tool_text(failed_result)
    assert successful_result.is_error is False
    assert limited_result.is_error is True
    assert "usage limit reached" in call_tool_text(limited_result)


@pytest.mark.parametrize("limit_value", ["invalid", "0", "-1"])
def test_invalid_session_limit_fails_startup(
    monkeypatch: pytest.MonkeyPatch,
    limit_value: str,
) -> None:
    """Invalid configured session limits fail server creation clearly."""

    env_var_name = SerperMcpApplication.session_limit_env_var_name(
        SerperTools.GOOGLE_SEARCH_IMAGES
    )
    monkeypatch.setenv(env_var_name, limit_value)

    with pytest.raises(
        SerperConfigurationError,
        match=f"{env_var_name} must be a positive integer",
    ):
        create_mcp_server(FakeSerperClient())


async def test_metrics_disabled_uses_null_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled metrics leave the client with a null metrics recorder."""

    monkeypatch.setenv(METRICS_ENABLED_ENV_VAR, "false")
    application = SerperMcpApplication(FakeSerperClient())

    await application.start_metrics()

    assert isinstance(application.client.metrics, NullMetricsRecorder)
    assert application.metrics_service is None


async def test_metrics_service_starts_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application startup owns the metrics service lifecycle."""

    monkeypatch.setenv(METRICS_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(METRICS_HOST_ENV_VAR, "127.0.0.2")
    monkeypatch.setenv(METRICS_PORT_ENV_VAR, "3006")
    monkeypatch.setattr("serper_mcp_server.server.MetricsService", FakeMetricsService)
    application = SerperMcpApplication(FakeSerperClient())

    await application.start_metrics()

    service = application.metrics_service
    assert isinstance(service, FakeMetricsService)
    assert service.started_host == "127.0.0.2"
    assert service.started_port == 3006
    assert application.client.metrics is service

    await application.close_metrics()

    assert service.closed is True
    assert application.metrics_service is None
    assert isinstance(application.client.metrics, NullMetricsRecorder)


async def test_expected_tool_failure_sets_is_error(
    server_factory: ServerFactory,
) -> None:
    """Expected execution failures are exposed as MCP tool errors."""

    mcp_server = await server_factory.connect(FailingSerperClient())
    call_result = await mcp_server.call_tool(
        SerperTools.GOOGLE_SEARCH.value, {"q": "openai"}
    )

    assert call_result.is_error is True
    assert call_tool_text(call_result) == (
        "Error executing tool google_search: SERPER_API_KEY is empty"
    )


async def test_invalid_arguments_fail_validation(
    server_factory: ServerFactory,
) -> None:
    """Invalid tool arguments are rejected before handler execution."""

    mcp_server = await server_factory.connect(FakeSerperClient())
    call_result = await mcp_server.call_tool(
        SerperTools.GOOGLE_SEARCH.value, {"q": "openai", "num": 0}
    )
    assert call_result.is_error is True
    assert "validation error" in call_tool_text(call_result)


async def test_new_connections_have_independent_session_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new connection to the same server gets fresh quota."""

    monkeypatch.setenv("MCP_METRICS_ENABLED", "false")
    monkeypatch.setenv("SERPER_GOOGLE_SEARCH_SESSION_LIMIT", "1")
    application = SerperMcpApplication(FakeSerperClient())
    for mode in ("legacy", "auto", "legacy"):
        async with Client(application.mcp, mode=mode) as client:
            first = await client.call_tool("google_search", {"q": "first"})
            second = await client.call_tool("google_search", {"q": "second"})
            assert first.is_error is False
            assert second.is_error is True
            assert "usage limit reached" in call_tool_text(second)


async def test_forced_value_validation_is_a_tool_error(
    server_factory: ServerFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bad forced configuration remains a readable error and consumes no quota."""

    monkeypatch.setenv("SERPER_GOOGLE_SEARCH_SESSION_LIMIT", "1")
    monkeypatch.setenv("SERPER_FORCE_NUM", "invalid")
    client = await server_factory.connect(FakeSerperClient())
    failed = await client.call_tool("google_search", {"q": "test"})
    assert failed.is_error is True
    assert "validation error" in call_tool_text(failed)
    monkeypatch.delenv("SERPER_FORCE_NUM")
    successful = await client.call_tool("google_search", {"q": "test"})
    assert successful.is_error is False


async def test_metrics_and_client_follow_server_lifespan(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The MCP 2 lifespan owns metrics and closes both metrics and HTTP client."""

    monkeypatch.setenv("MCP_METRICS_ENABLED", "true")
    monkeypatch.setenv("MCP_METRICS_DB_PATH", str(tmp_path / "metrics.sqlite3"))
    monkeypatch.setenv("MCP_METRICS_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_METRICS_PORT", "3006")
    start_http = AsyncMock()
    monkeypatch.setattr(MetricsService, "start_http_server", start_http)
    backend = FakeSerperClient()
    close_client = AsyncMock(wraps=backend.close)
    monkeypatch.setattr(backend, "close", close_client)
    application = SerperMcpApplication(backend)
    async with Client(application.mcp, mode="legacy"):
        metrics = application.metrics_service
        assert metrics is not None
        assert backend.metrics is metrics
        start_http.assert_awaited_once_with("127.0.0.1", 3006)
        assert metrics.db.execute("SELECT 1").fetchone()[0] == 1
    close_client.assert_awaited_once()
    assert application.metrics_service is None
    assert isinstance(backend.metrics, NullMetricsRecorder)
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        metrics.db.execute("SELECT 1")
