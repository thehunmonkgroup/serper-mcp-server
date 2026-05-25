"""Tests for the Serper FastMCP server."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from mcp import types
import pytest
from typing_extensions import override

from serper_mcp_server.core import SerperClient, SerperConfigurationError
from serper_mcp_server.enums import SerperTools
from serper_mcp_server.metrics import (
    METRICS_ENABLED_ENV_VAR,
    METRICS_HOST_ENV_VAR,
    METRICS_PORT_ENV_VAR,
    NullMetricsRecorder,
)
from serper_mcp_server.schemas import WebpageRequest
from serper_mcp_server.server import SerperMcpApplication, create_mcp_server


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


def run_async(awaitable: Any) -> Any:
    """Run an awaitable for tests.

    :param awaitable: Awaitable object.
    :type awaitable: Any
    :return: Awaitable result.
    :rtype: Any
    """

    return asyncio.run(awaitable)


def test_tool_list_contains_expected_metadata() -> None:
    """All public tools expose useful metadata and annotations."""

    mcp_server = create_mcp_server(FakeSerperClient())
    tools = run_async(mcp_server.list_tools())
    tools_by_name = {tool.name: tool for tool in tools}

    assert set(tools_by_name) == {tool.value for tool in SerperTools}

    search_tool = tools_by_name[SerperTools.GOOGLE_SEARCH.value]
    assert search_tool.title == "Google Search"
    assert search_tool.description == "Search Google web results through Serper."
    assert search_tool.outputSchema is not None
    assert search_tool.annotations is not None
    assert search_tool.annotations.readOnlyHint is True
    assert search_tool.annotations.destructiveHint is False
    assert search_tool.annotations.idempotentHint is False
    assert search_tool.annotations.openWorldHint is True
    assert search_tool.inputSchema["properties"]["page"]["type"] == "integer"
    assert search_tool.inputSchema["properties"]["num"]["type"] == "integer"

    scrape_tool = tools_by_name[SerperTools.WEBPAGE_SCRAPE.value]
    include_markdown_schema = scrape_tool.inputSchema["properties"]["includeMarkdown"]
    assert include_markdown_schema["type"] == "boolean"


def test_google_search_returns_structured_content() -> None:
    """Successful search calls return structured content."""

    client = FakeSerperClient()
    mcp_server = create_mcp_server(client)

    content, structured_content = run_async(
        mcp_server.call_tool(
            SerperTools.GOOGLE_SEARCH.value,
            {"q": "openai", "num": 5, "page": 1},
        )
    )

    assert structured_content["credits"] == 1
    assert structured_content["organic"][0]["title"] == "Example"
    assert content[0].type == "text"
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


def test_google_search_uses_forced_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search calls prefer forced environment values over caller arguments."""

    monkeypatch.setenv("SERPER_FORCE_GL", "us")
    monkeypatch.setenv("SERPER_FORCE_HL", "en")
    monkeypatch.setenv("SERPER_FORCE_NUM", "7")
    client = FakeSerperClient()
    mcp_server = create_mcp_server(client)

    run_async(
        mcp_server.call_tool(
            SerperTools.GOOGLE_SEARCH.value,
            {"q": "openai", "gl": "ca", "hl": "fr", "num": 5},
        )
    )

    assert client.last_payload is not None
    assert client.last_payload["gl"] == "us"
    assert client.last_payload["hl"] == "en"
    assert client.last_payload["num"] == 7


def test_webpage_scrape_returns_structured_content() -> None:
    """Successful scrape calls return structured content."""

    client = FakeSerperClient()
    mcp_server = create_mcp_server(client)

    content, structured_content = run_async(
        mcp_server.call_tool(
            SerperTools.WEBPAGE_SCRAPE.value,
            {"url": "https://example.com", "includeMarkdown": True},
        )
    )

    assert structured_content["credits"] == 2
    assert structured_content["metadata"]["title"] == "Example Domain"
    assert content[0].type == "text"
    assert client.last_payload is not None
    assert client.last_payload["includeMarkdown"] is True


def test_webpage_scrape_uses_forced_environment_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrape calls prefer forced environment values over caller arguments."""

    monkeypatch.setenv("SERPER_FORCE_INCLUDE_MARKDOWN", "true")
    client = FakeSerperClient()
    mcp_server = create_mcp_server(client)

    run_async(
        mcp_server.call_tool(
            SerperTools.WEBPAGE_SCRAPE.value,
            {"url": "https://example.com", "includeMarkdown": False},
        )
    )

    assert client.last_payload is not None
    assert client.last_payload["includeMarkdown"] is True


def test_metrics_disabled_uses_null_recorder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabled metrics leave the client with a null metrics recorder."""

    monkeypatch.setenv(METRICS_ENABLED_ENV_VAR, "false")
    application = SerperMcpApplication(FakeSerperClient())

    run_async(application.start_metrics())

    assert isinstance(application.client.metrics, NullMetricsRecorder)
    assert application.metrics_service is None


def test_metrics_service_starts_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Application startup owns the metrics service lifecycle."""

    monkeypatch.setenv(METRICS_ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(METRICS_HOST_ENV_VAR, "127.0.0.2")
    monkeypatch.setenv(METRICS_PORT_ENV_VAR, "3006")
    monkeypatch.setattr("serper_mcp_server.server.MetricsService", FakeMetricsService)
    application = SerperMcpApplication(FakeSerperClient())

    run_async(application.start_metrics())

    service = application.metrics_service
    assert isinstance(service, FakeMetricsService)
    assert service.started_host == "127.0.0.2"
    assert service.started_port == 3006
    assert application.client.metrics is service

    run_async(application.close_metrics())

    assert service.closed is True
    assert application.metrics_service is None
    assert isinstance(application.client.metrics, NullMetricsRecorder)


def test_expected_tool_failure_sets_is_error() -> None:
    """Expected execution failures are exposed as MCP tool errors."""

    mcp_server = create_mcp_server(FailingSerperClient())
    low_level_server = getattr(mcp_server, "_mcp_server")
    handler = low_level_server.request_handlers[types.CallToolRequest]

    result = run_async(
        handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name=SerperTools.GOOGLE_SEARCH.value,
                    arguments={"q": "openai"},
                )
            )
        )
    )

    call_result = result.root
    assert call_result.isError is True
    assert "SERPER_API_KEY is empty" in call_result.content[0].text


def test_invalid_arguments_fail_validation() -> None:
    """Invalid tool arguments are rejected before handler execution."""

    mcp_server = create_mcp_server(FakeSerperClient())
    low_level_server = getattr(mcp_server, "_mcp_server")
    handler = low_level_server.request_handlers[types.CallToolRequest]

    result = run_async(
        handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name=SerperTools.GOOGLE_SEARCH.value,
                    arguments={"q": "openai", "num": 0},
                )
            )
        )
    )

    call_result = result.root
    assert call_result.isError is True
    assert "validation error" in call_result.content[0].text
