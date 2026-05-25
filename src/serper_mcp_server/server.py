"""FastMCP server exposing Serper search and scrape tools."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal, TypeVar

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel

from .core import SerperClient
from .enums import SerperTools
from .metrics import (
    MetricsConfigurationError,
    MetricsService,
    NullMetricsRecorder,
    get_metrics_host,
    get_metrics_port,
    metrics_enabled,
)
from .schemas import (
    AutocorrectRequest,
    LensRequest,
    MapsRequest,
    PatentsRequest,
    ReviewsRequest,
    SearchRequest,
    ShoppingRequest,
    WebpageRequest,
)

SERVER_INSTRUCTIONS = (
    "Search Google through Serper and scrape webpages. Tools call external "
    "Serper endpoints and return the raw Serper JSON response as structured "
    "content."
)

FORCE_ENV_PREFIX = "SERPER_FORCE_"

READ_ONLY_OPEN_WEB_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

logger = logging.getLogger(__name__)
RequestModelT = TypeVar("RequestModelT", bound=BaseModel)


class SerperMcpApplication:
    """Factory and registry for the Serper FastMCP server.

    :param client: Serper API client used by tool handlers.
    :type client: SerperClient | None
    """

    def __init__(self, client: SerperClient | None = None) -> None:
        self.client: SerperClient = client or SerperClient()
        self.metrics_service: MetricsService | None = None
        self.mcp: FastMCP = FastMCP(
            "Serper",
            instructions=SERVER_INSTRUCTIONS,
            lifespan=self.lifespan,
        )
        self.register_tools()

    @asynccontextmanager
    async def lifespan(self, _server: FastMCP) -> AsyncIterator[None]:
        """Manage reusable Serper client resources.

        :param _server: FastMCP server instance.
        :type _server: FastMCP
        :return: Async lifespan iterator.
        :rtype: AsyncIterator[None]
        """

        try:
            await self.start_metrics()
            yield
        finally:
            await self.client.close()
            await self.close_metrics()

    async def start_metrics(self) -> None:
        """Start the portable metrics service when enabled.

        :return: None.
        :rtype: None
        """

        if not metrics_enabled():
            self.client.metrics = NullMetricsRecorder()
            return

        try:
            self.metrics_service = MetricsService()
        except Exception as exc:
            logger.warning(
                "MCP metrics disabled after initialization failure: %s",
                exc,
            )
            self.client.metrics = NullMetricsRecorder()
            self.metrics_service = None
            return

        self.client.metrics = self.metrics_service
        try:
            await self.metrics_service.start_http_server(
                get_metrics_host(),
                get_metrics_port(),
            )
        except MetricsConfigurationError:
            await self.close_metrics()
            raise
        except Exception as exc:
            logger.warning(
                "MCP metrics HTTP sidecar disabled after failure: %s",
                exc,
            )

    async def close_metrics(self) -> None:
        """Close the metrics service when this process owns it.

        :return: None.
        :rtype: None
        """

        if self.metrics_service is not None:
            await self.metrics_service.close()
            self.metrics_service = None
            self.client.metrics = NullMetricsRecorder()

    def register_tools(self) -> None:
        """Register all public MCP tools.

        :return: None.
        :rtype: None
        """

        self._register_search_tools()
        self._register_autocorrect_tools()
        self._register_maps_tool()
        self._register_reviews_tool()
        self._register_shopping_tool()
        self._register_lens_tool()
        self._register_patents_tool()
        self._register_scrape_tool()

    async def execute_google_tool(
        self, tool: SerperTools, request: BaseModel
    ) -> dict[str, Any]:
        """Execute a Google-backed Serper tool.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated search request.
        :type request: SearchRequest | AutocorrectRequest
        :return: Raw Serper JSON response.
        :rtype: dict[str, Any]
        """

        logger.debug("Executing Serper tool %s", tool.value)
        return await self.client.google(tool, request)

    def build_request(
        self,
        model_type: type[RequestModelT],
        **values: Any,
    ) -> RequestModelT:
        """Build a request model after applying forced environment overrides.

        :param model_type: Pydantic request model type to instantiate.
        :type model_type: type[RequestModelT]
        :param values: Request field values from the MCP tool call.
        :type values: Any
        :return: Validated request model.
        :rtype: RequestModelT
        """

        forced_values = {
            name: self.resolve_forced_parameter(name, value)
            for name, value in values.items()
        }
        return model_type(**forced_values)

    def resolve_forced_parameter(self, parameter_name: str, value: Any) -> Any:
        """Resolve a parameter value from a forced env var or the tool argument.

        :param parameter_name: Tool parameter name.
        :type parameter_name: str
        :param value: Value supplied by the MCP tool caller.
        :type value: Any
        :return: Forced environment value when present, otherwise the caller value.
        :rtype: Any
        """

        env_var_name = self.force_env_var_name(parameter_name)
        if env_var_name in os.environ:
            logger.debug("Using forced Serper parameter from %s", env_var_name)
            return os.environ[env_var_name]
        return value

    @staticmethod
    def force_env_var_name(parameter_name: str) -> str:
        """Return the forced environment variable name for a parameter.

        :param parameter_name: Tool parameter name.
        :type parameter_name: str
        :return: Environment variable name with the ``SERPER_FORCE_`` prefix.
        :rtype: str
        """

        return f"{FORCE_ENV_PREFIX}{SerperMcpApplication.to_env_name(parameter_name)}"

    @staticmethod
    def to_env_name(parameter_name: str) -> str:
        """Convert a Python or Serper parameter name into env-var format.

        :param parameter_name: Parameter name such as ``includeMarkdown``.
        :type parameter_name: str
        :return: Upper snake-case parameter name.
        :rtype: str
        """

        env_name_parts: list[str] = []
        for index, character in enumerate(parameter_name):
            previous_character = parameter_name[index - 1] if index > 0 else ""
            next_character = (
                parameter_name[index + 1] if index + 1 < len(parameter_name) else ""
            )
            should_add_separator = (
                index > 0
                and character.isupper()
                and (
                    previous_character.islower()
                    or previous_character.isdigit()
                    or (previous_character.isupper() and next_character.islower())
                )
            )
            if should_add_separator:
                env_name_parts.append("_")
            if character in {"-", " "}:
                env_name_parts.append("_")
            else:
                env_name_parts.append(character.upper())
        return "".join(env_name_parts)

    def _register_search_tools(self) -> None:
        """Register general search-family tools.

        :return: None.
        :rtype: None
        """

        async def google_search(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            tbs: str | None = None,
            num: int = 10,
        ) -> dict[str, Any]:
            """Search Google web results through Serper."""

            request = self.build_request(
                SearchRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                tbs=tbs,
                num=num,
            )
            return await self.execute_google_tool(SerperTools.GOOGLE_SEARCH, request)

        async def google_search_images(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            tbs: str | None = None,
            num: int = 10,
        ) -> dict[str, Any]:
            """Search Google image results through Serper."""

            request = self.build_request(
                SearchRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                tbs=tbs,
                num=num,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_IMAGES, request
            )

        async def google_search_videos(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            tbs: str | None = None,
            num: int = 10,
        ) -> dict[str, Any]:
            """Search Google video results through Serper."""

            request = self.build_request(
                SearchRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                tbs=tbs,
                num=num,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_VIDEOS, request
            )

        async def google_search_news(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            tbs: str | None = None,
            num: int = 10,
        ) -> dict[str, Any]:
            """Search Google news results through Serper."""

            request = self.build_request(
                SearchRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                tbs=tbs,
                num=num,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_NEWS, request
            )

        self.mcp.add_tool(
            google_search,
            name=SerperTools.GOOGLE_SEARCH.value,
            title="Google Search",
            description="Search Google web results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )
        self.mcp.add_tool(
            google_search_images,
            name=SerperTools.GOOGLE_SEARCH_IMAGES.value,
            title="Google Image Search",
            description="Search Google image results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )
        self.mcp.add_tool(
            google_search_videos,
            name=SerperTools.GOOGLE_SEARCH_VIDEOS.value,
            title="Google Video Search",
            description="Search Google video results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )
        self.mcp.add_tool(
            google_search_news,
            name=SerperTools.GOOGLE_SEARCH_NEWS.value,
            title="Google News Search",
            description="Search Google news results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_autocorrect_tools(self) -> None:
        """Register search tools with autocorrect controls.

        :return: None.
        :rtype: None
        """

        async def google_search_places(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            autocorrect: bool = True,
        ) -> dict[str, Any]:
            """Search Google places results through Serper."""

            request = self.build_request(
                AutocorrectRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                autocorrect=autocorrect,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_PLACES, request
            )

        async def google_search_scholar(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            autocorrect: bool = True,
        ) -> dict[str, Any]:
            """Search Google Scholar results through Serper."""

            request = self.build_request(
                AutocorrectRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                autocorrect=autocorrect,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_SCHOLAR, request
            )

        async def google_search_autocomplete(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            autocorrect: bool = True,
        ) -> dict[str, Any]:
            """Fetch Google autocomplete suggestions through Serper."""

            request = self.build_request(
                AutocorrectRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                autocorrect=autocorrect,
            )
            return await self.execute_google_tool(
                SerperTools.GOOGLE_SEARCH_AUTOCOMPLETE, request
            )

        self.mcp.add_tool(
            google_search_places,
            name=SerperTools.GOOGLE_SEARCH_PLACES.value,
            title="Google Places Search",
            description="Search Google places results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )
        self.mcp.add_tool(
            google_search_scholar,
            name=SerperTools.GOOGLE_SEARCH_SCHOLAR.value,
            title="Google Scholar Search",
            description="Search Google Scholar results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )
        self.mcp.add_tool(
            google_search_autocomplete,
            name=SerperTools.GOOGLE_SEARCH_AUTOCOMPLETE.value,
            title="Google Autocomplete",
            description="Fetch Google autocomplete suggestions through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_maps_tool(self) -> None:
        """Register the maps search tool.

        :return: None.
        :rtype: None
        """

        async def google_search_maps(
            q: str,
            ll: str | None = None,
            placeId: str | None = None,
            cid: str | None = None,
            gl: str | None = None,
            hl: str | None = None,
            page: int = 1,
        ) -> dict[str, Any]:
            """Search Google Maps results through Serper."""

            request = self.build_request(
                MapsRequest,
                q=q,
                ll=ll,
                placeId=placeId,
                cid=cid,
                gl=gl,
                hl=hl,
                page=page,
            )
            return await self.client.google(SerperTools.GOOGLE_SEARCH_MAPS, request)

        self.mcp.add_tool(
            google_search_maps,
            name=SerperTools.GOOGLE_SEARCH_MAPS.value,
            title="Google Maps Search",
            description="Search Google Maps results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_reviews_tool(self) -> None:
        """Register the reviews search tool.

        :return: None.
        :rtype: None
        """

        async def google_search_reviews(
            fid: str,
            cid: str | None = None,
            placeId: str | None = None,
            sortBy: Literal[
                "mostRelevant", "newest", "highestRating", "lowestRating"
            ] = "mostRelevant",
            topicId: str | None = None,
            nextPageToken: str | None = None,
            gl: str | None = None,
            hl: str | None = None,
        ) -> dict[str, Any]:
            """Search Google review results through Serper."""

            request = self.build_request(
                ReviewsRequest,
                fid=fid,
                cid=cid,
                placeId=placeId,
                sortBy=sortBy,
                topicId=topicId,
                nextPageToken=nextPageToken,
                gl=gl,
                hl=hl,
            )
            return await self.client.google(SerperTools.GOOGLE_SEARCH_REVIEWS, request)

        self.mcp.add_tool(
            google_search_reviews,
            name=SerperTools.GOOGLE_SEARCH_REVIEWS.value,
            title="Google Reviews Search",
            description="Search Google review results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_shopping_tool(self) -> None:
        """Register the shopping search tool.

        :return: None.
        :rtype: None
        """

        async def google_search_shopping(
            q: str,
            gl: str | None = None,
            location: str | None = None,
            hl: str | None = None,
            page: int = 1,
            autocorrect: bool = True,
            num: int = 10,
        ) -> dict[str, Any]:
            """Search Google shopping results through Serper."""

            request = self.build_request(
                ShoppingRequest,
                q=q,
                gl=gl,
                location=location,
                hl=hl,
                page=page,
                autocorrect=autocorrect,
                num=num,
            )
            return await self.client.google(SerperTools.GOOGLE_SEARCH_SHOPPING, request)

        self.mcp.add_tool(
            google_search_shopping,
            name=SerperTools.GOOGLE_SEARCH_SHOPPING.value,
            title="Google Shopping Search",
            description="Search Google shopping results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_lens_tool(self) -> None:
        """Register the lens search tool.

        :return: None.
        :rtype: None
        """

        async def google_search_lens(
            url: str,
            gl: str | None = None,
            hl: str | None = None,
        ) -> dict[str, Any]:
            """Search Google Lens results through Serper."""

            request = self.build_request(LensRequest, url=url, gl=gl, hl=hl)
            return await self.client.google(SerperTools.GOOGLE_SEARCH_LENS, request)

        self.mcp.add_tool(
            google_search_lens,
            name=SerperTools.GOOGLE_SEARCH_LENS.value,
            title="Google Lens Search",
            description="Search Google Lens results from an image URL through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_patents_tool(self) -> None:
        """Register the patents search tool.

        :return: None.
        :rtype: None
        """

        async def google_search_patents(
            q: str,
            num: int = 10,
            page: int = 1,
        ) -> dict[str, Any]:
            """Search Google patents results through Serper."""

            request = self.build_request(PatentsRequest, q=q, num=num, page=page)
            return await self.client.google(SerperTools.GOOGLE_SEARCH_PATENTS, request)

        self.mcp.add_tool(
            google_search_patents,
            name=SerperTools.GOOGLE_SEARCH_PATENTS.value,
            title="Google Patents Search",
            description="Search Google patents results through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )

    def _register_scrape_tool(self) -> None:
        """Register the webpage scrape tool.

        :return: None.
        :rtype: None
        """

        async def webpage_scrape(
            url: str,
            includeMarkdown: bool = False,
        ) -> dict[str, Any]:
            """Scrape a webpage through Serper."""

            request = self.build_request(
                WebpageRequest,
                url=url,
                includeMarkdown=includeMarkdown,
            )
            return await self.client.scrape(request)

        self.mcp.add_tool(
            webpage_scrape,
            name=SerperTools.WEBPAGE_SCRAPE.value,
            title="Webpage Scrape",
            description="Scrape a webpage URL through Serper.",
            annotations=READ_ONLY_OPEN_WEB_ANNOTATIONS,
            structured_output=True,
        )


def create_mcp_server(client: SerperClient | None = None) -> FastMCP:
    """Create the Serper FastMCP server.

    :param client: Optional Serper client for tests or custom integrations.
    :type client: SerperClient | None
    :return: Configured FastMCP server.
    :rtype: FastMCP
    """

    load_dotenv()
    application = SerperMcpApplication(client=client)
    return application.mcp


server = create_mcp_server()


def main() -> None:
    """Run the Serper MCP server over stdio.

    :return: None.
    :rtype: None
    """

    server.run(transport="stdio")
