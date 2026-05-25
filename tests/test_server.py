"""Tests for the Serper FastMCP server."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from mcp import types
import pytest
from typing_extensions import override

from serper_mcp_server.core import (
    SerperClient,
    SerperClientError,
    SerperConfigurationError,
)
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


def run_async(awaitable: Any) -> Any:
    """Run an awaitable for tests.

    :param awaitable: Awaitable object.
    :type awaitable: Any
    :return: Awaitable result.
    :rtype: Any
    """

    return asyncio.run(awaitable)


def call_tool_result(
    mcp_server: Any,
    tool_name: SerperTools,
    arguments: dict[str, Any],
) -> types.CallToolResult:
    """Call a tool through the low-level MCP request handler.

    :param mcp_server: FastMCP server instance.
    :type mcp_server: Any
    :param tool_name: Tool to call.
    :type tool_name: SerperTools
    :param arguments: Tool arguments.
    :type arguments: dict[str, Any]
    :return: MCP call tool result.
    :rtype: types.CallToolResult
    """

    low_level_server = getattr(mcp_server, "_mcp_server")
    handler = low_level_server.request_handlers[types.CallToolRequest]
    result = run_async(
        handler(
            types.CallToolRequest(
                params=types.CallToolRequestParams(
                    name=tool_name.value,
                    arguments=arguments,
                )
            )
        )
    )
    return result.root


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


def test_tool_list_contains_expected_metadata() -> None:
    """All public tools expose useful metadata and annotations."""

    mcp_server = create_mcp_server(FakeSerperClient())
    tools = run_async(mcp_server.list_tools())
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
    assert search_tool.outputSchema is not None
    assert search_tool.annotations is not None
    assert search_tool.annotations.readOnlyHint is True
    assert search_tool.annotations.destructiveHint is False
    assert search_tool.annotations.idempotentHint is False
    assert search_tool.annotations.openWorldHint is True
    search_properties = search_tool.inputSchema["properties"]
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
    places_properties = places_tool.inputSchema["properties"]
    assert places_properties["autocorrect"]["type"] == "boolean"
    assert places_properties["autocorrect"]["default"] is True
    assert places_properties["autocorrect"]["description"] == (
        "Whether Serper should autocorrect the query."
    )

    maps_tool = tools_by_name[SerperTools.GOOGLE_SEARCH_MAPS.value]
    maps_properties = maps_tool.inputSchema["properties"]
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
    reviews_properties = reviews_tool.inputSchema["properties"]
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
    lens_properties = lens_tool.inputSchema["properties"]
    assert lens_properties["url"]["description"] == (
        "Absolute image URL to search with Google Lens."
    )

    scrape_tool = tools_by_name[SerperTools.WEBPAGE_SCRAPE.value]
    include_markdown_schema = scrape_tool.inputSchema["properties"]["includeMarkdown"]
    assert include_markdown_schema["type"] == "boolean"
    assert include_markdown_schema["default"] is False
    assert include_markdown_schema["description"] == (
        "Include Markdown in the scrape response."
    )
    assert scrape_tool.inputSchema["properties"]["url"]["description"] == (
        "Absolute URL to scrape."
    )


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


def test_google_search_session_limit_errors_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Search limits apply after the configured number of successful calls."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = create_mcp_server(FakeSerperClient())

    first_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    second_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert first_result.isError is False
    assert second_result.isError is True
    second_result_text = call_tool_text(second_result)
    assert "usage limit reached" in second_result_text
    assert "Do not call google_search again" in second_result_text


def test_webpage_scrape_session_limit_errors_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Scrape limits apply after the configured number of successful calls."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.WEBPAGE_SCRAPE),
        "1",
    )
    mcp_server = create_mcp_server(FakeSerperClient())

    first_result = call_tool_result(
        mcp_server,
        SerperTools.WEBPAGE_SCRAPE,
        {"url": "https://example.com"},
    )
    second_result = call_tool_result(
        mcp_server,
        SerperTools.WEBPAGE_SCRAPE,
        {"url": "https://example.com"},
    )

    assert first_result.isError is False
    assert second_result.isError is True
    second_result_text = call_tool_text(second_result)
    assert "usage limit reached" in second_result_text
    assert "Do not call webpage_scrape again" in second_result_text


def test_session_limits_are_independent_by_tool(
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
    mcp_server = create_mcp_server(FakeSerperClient())

    search_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_search_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    image_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH_IMAGES,
        {"q": "openai"},
    )

    assert search_result.isError is False
    assert limited_search_result.isError is True
    assert image_result.isError is False


def test_validation_failures_do_not_consume_session_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calls rejected by MCP validation do not count against the limit."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = create_mcp_server(FakeSerperClient())

    invalid_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai", "num": 0},
    )
    successful_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert invalid_result.isError is True
    assert successful_result.isError is False
    assert limited_result.isError is True
    assert "usage limit reached" in call_tool_text(limited_result)


def test_serper_failures_do_not_consume_session_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Serper client failures do not count against the limit."""

    monkeypatch.setenv(
        SerperMcpApplication.session_limit_env_var_name(SerperTools.GOOGLE_SEARCH),
        "1",
    )
    mcp_server = create_mcp_server(IntermittentFailingSerperClient())

    failed_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    successful_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )
    limited_result = call_tool_result(
        mcp_server,
        SerperTools.GOOGLE_SEARCH,
        {"q": "openai"},
    )

    assert failed_result.isError is True
    assert "Serper API returned HTTP 500" in call_tool_text(failed_result)
    assert successful_result.isError is False
    assert limited_result.isError is True
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
