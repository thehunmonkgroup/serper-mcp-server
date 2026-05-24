"""FastMCP server exposing Serper search and scrape tools."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .core import SerperClient
from .enums import SerperTools
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

READ_ONLY_OPEN_WEB_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)

logger = logging.getLogger(__name__)


class SerperMcpApplication:
    """Factory and registry for the Serper FastMCP server.

    :param client: Serper API client used by tool handlers.
    :type client: SerperClient | None
    """

    def __init__(self, client: SerperClient | None = None) -> None:
        self.client: SerperClient = client or SerperClient()
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
            yield
        finally:
            await self.client.close()

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
        self, tool: SerperTools, request: SearchRequest | AutocorrectRequest
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

            request = SearchRequest(
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

            request = SearchRequest(
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

            request = SearchRequest(
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

            request = SearchRequest(
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

            request = AutocorrectRequest(
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

            request = AutocorrectRequest(
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

            request = AutocorrectRequest(
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

            request = MapsRequest(
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

            request = ReviewsRequest(
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

            request = ShoppingRequest(
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

            request = LensRequest(url=url, gl=gl, hl=hl)
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

            request = PatentsRequest(q=q, num=num, page=page)
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

            request = WebpageRequest(url=url, includeMarkdown=includeMarkdown)
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
