"""Serper HTTP client used by the MCP server tools."""

from __future__ import annotations

import logging
import os
import ssl
from collections.abc import Mapping
from typing import Any

import certifi
import aiohttp
from pydantic import BaseModel

from .enums import SerperTools
from .schemas import WebpageRequest

DEFAULT_AIOHTTP_TIMEOUT_SECONDS = 15
GOOGLE_SERPER_BASE_URL = "https://google.serper.dev"
SCRAPE_SERPER_URL = "https://scrape.serper.dev"
SERPER_API_KEY_ENV_VAR = "SERPER_API_KEY"
AIOHTTP_TIMEOUT_ENV_VAR = "AIOHTTP_TIMEOUT"

logger = logging.getLogger(__name__)


class SerperClientError(Exception):
    """Error raised for expected Serper client failures."""


class SerperConfigurationError(SerperClientError):
    """Error raised when server configuration is invalid or incomplete."""


class SerperClient:
    """Reusable asynchronous Serper API client.

    :param api_key: Serper API key. When omitted, it is read lazily from the
        environment.
    :type api_key: str | None
    :param timeout_seconds: Request timeout in seconds. When omitted, it is
        read from the environment.
    :type timeout_seconds: int | None
    :param session: Optional injected aiohttp session for tests.
    :type session: aiohttp.ClientSession | None
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._api_key: str | None = api_key
        self._timeout_seconds: int | None = timeout_seconds
        self._session: aiohttp.ClientSession | None = session
        self._owns_session: bool = session is None

    async def google(self, tool: SerperTools, request: BaseModel) -> dict[str, Any]:
        """Search a Google-backed Serper endpoint.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Validated request model.
        :type request: BaseModel
        :return: Serper JSON response.
        :rtype: dict[str, Any]
        :raises SerperClientError: If the API call fails.
        """

        endpoint = tool.value.removeprefix("google_search_")
        endpoint = "search" if endpoint == "google_search" else endpoint
        return await self.fetch_json(f"{GOOGLE_SERPER_BASE_URL}/{endpoint}", request)

    async def scrape(self, request: WebpageRequest) -> dict[str, Any]:
        """Scrape a webpage through Serper.

        :param request: Validated webpage scrape request.
        :type request: WebpageRequest
        :return: Serper JSON response.
        :rtype: dict[str, Any]
        :raises SerperClientError: If the API call fails.
        """

        return await self.fetch_json(SCRAPE_SERPER_URL, request)

    async def fetch_json(self, url: str, request: BaseModel) -> dict[str, Any]:
        """Post a request model to Serper and return its JSON body.

        :param url: Serper endpoint URL.
        :type url: str
        :param request: Validated request model.
        :type request: BaseModel
        :return: Serper JSON response.
        :rtype: dict[str, Any]
        :raises SerperClientError: If the API call fails.
        """

        session = await self.get_session()
        payload = request.model_dump(exclude_none=True)
        logger.debug("Posting Serper request to %s", url)

        try:
            async with session.post(
                url,
                headers=self.headers,
                json=payload,
            ) as response:
                response_text = await response.text()
                if response.status >= 400:
                    raise SerperClientError(
                        f"Serper API returned HTTP {response.status}: {response_text[:500]}"
                    )
                try:
                    json_body = await response.json(content_type=None)
                except aiohttp.ContentTypeError as exc:
                    raise SerperClientError(
                        "Serper API returned a non-JSON response"
                    ) from exc
                if not isinstance(json_body, dict):
                    raise SerperClientError(
                        "Serper API returned an unexpected JSON shape"
                    )
                return json_body
        except TimeoutError as exc:
            logger.warning("Serper request timed out: %s", url)
            raise SerperClientError("Serper API request timed out") from exc
        except aiohttp.ClientError as exc:
            logger.warning("Serper request failed: %s", url)
            raise SerperClientError(f"Serper API request failed: {exc}") from exc

    async def get_session(self) -> aiohttp.ClientSession:
        """Return the reusable aiohttp session.

        :return: Active aiohttp client session.
        :rtype: aiohttp.ClientSession
        :raises SerperConfigurationError: If required configuration is missing
            or invalid.
        """

        if not self.api_key:
            raise SerperConfigurationError(f"{SERPER_API_KEY_ENV_VAR} is empty")

        if self._session is None or self._session.closed:
            ssl_context = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            self._session = aiohttp.ClientSession(
                connector=connector,
                timeout=timeout,
            )
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        """Close the owned aiohttp session.

        :return: None.
        :rtype: None
        """

        if self._owns_session and self._session is not None:
            await self._session.close()

    @property
    def api_key(self) -> str:
        """Return the configured Serper API key.

        :return: Serper API key, or an empty string if unset.
        :rtype: str
        """

        return (self._api_key or os.getenv(SERPER_API_KEY_ENV_VAR, "")).strip()

    @property
    def timeout_seconds(self) -> int:
        """Return the configured timeout.

        :return: Request timeout in seconds.
        :rtype: int
        :raises SerperConfigurationError: If timeout configuration is invalid.
        """

        if self._timeout_seconds is not None:
            return self._timeout_seconds

        value = os.getenv(
            AIOHTTP_TIMEOUT_ENV_VAR,
            str(DEFAULT_AIOHTTP_TIMEOUT_SECONDS),
        ).strip()
        try:
            timeout_seconds = int(value)
        except ValueError as exc:
            raise SerperConfigurationError(
                f"{AIOHTTP_TIMEOUT_ENV_VAR} must be an integer"
            ) from exc
        if timeout_seconds <= 0:
            raise SerperConfigurationError(
                f"{AIOHTTP_TIMEOUT_ENV_VAR} must be greater than 0"
            )
        return timeout_seconds

    @property
    def headers(self) -> Mapping[str, str]:
        """Return HTTP headers for Serper requests.

        :return: Serper request headers.
        :rtype: Mapping[str, str]
        """

        return {
            "X-API-KEY": self.api_key,
            "Content-Type": "application/json",
        }
