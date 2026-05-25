"""Serper HTTP client used by the MCP server tools."""

from __future__ import annotations

import logging
import os
import ssl
from collections.abc import Mapping
from time import perf_counter
from typing import Any

import aiohttp
import certifi
from pydantic import BaseModel

from .enums import SerperTools
from .metrics import MetricEvent, MetricsRecorder, NullMetricsRecorder
from .schemas import WebpageRequest

DEFAULT_AIOHTTP_TIMEOUT_SECONDS = 15
GOOGLE_SERPER_BASE_URL = "https://google.serper.dev"
SCRAPE_SERPER_URL = "https://scrape.serper.dev"
SERPER_API_KEY_ENV_VAR = "SERPER_API_KEY"
AIOHTTP_TIMEOUT_ENV_VAR = "AIOHTTP_TIMEOUT"

logger = logging.getLogger(__name__)


class SerperClientError(Exception):
    """Error raised for expected Serper client failures."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        """Initialize a Serper client error.

        :param message: Error message.
        :type message: str
        :param status_code: Optional HTTP status code.
        :type status_code: int | None
        """

        super().__init__(message)
        self.status_code: int | None = status_code


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
    :param metrics: Optional metrics recorder.
    :type metrics: MetricsRecorder | None
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: int | None = None,
        session: aiohttp.ClientSession | None = None,
        metrics: MetricsRecorder | None = None,
    ) -> None:
        self._api_key: str | None = api_key
        self._timeout_seconds: int | None = timeout_seconds
        self._session: aiohttp.ClientSession | None = session
        self._owns_session: bool = session is None
        self.metrics: MetricsRecorder = metrics or NullMetricsRecorder()

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
        started_at = perf_counter()
        try:
            response, status_code = await self.fetch_json_response(
                f"{GOOGLE_SERPER_BASE_URL}/{endpoint}",
                request,
            )
        except SerperClientError as exc:
            await self.record_search_metric(
                tool=tool,
                request=request,
                started_at=started_at,
                succeeded=False,
                status_code=exc.status_code,
                error=str(exc),
            )
            raise
        await self.record_search_metric(
            tool=tool,
            request=request,
            started_at=started_at,
            succeeded=True,
            status_code=status_code,
            response=response,
        )
        return response

    async def scrape(self, request: WebpageRequest) -> dict[str, Any]:
        """Scrape a webpage through Serper.

        :param request: Validated webpage scrape request.
        :type request: WebpageRequest
        :return: Serper JSON response.
        :rtype: dict[str, Any]
        :raises SerperClientError: If the API call fails.
        """

        started_at = perf_counter()
        try:
            response, status_code = await self.fetch_json_response(
                SCRAPE_SERPER_URL,
                request,
            )
        except SerperClientError as exc:
            await self.record_scrape_metric(
                request=request,
                started_at=started_at,
                succeeded=False,
                status_code=exc.status_code,
                error=str(exc),
            )
            raise
        await self.record_scrape_metric(
            request=request,
            started_at=started_at,
            succeeded=True,
            status_code=status_code,
            response=response,
        )
        return response

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

        json_body, _status_code = await self.fetch_json_response(url, request)
        return json_body

    async def fetch_json_response(
        self,
        url: str,
        request: BaseModel,
    ) -> tuple[dict[str, Any], int]:
        """Post a request model to Serper and return JSON body with status.

        :param url: Serper endpoint URL.
        :type url: str
        :param request: Validated request model.
        :type request: BaseModel
        :return: Serper JSON response and HTTP status.
        :rtype: tuple[dict[str, Any], int]
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
                        (
                            f"Serper API returned HTTP {response.status}: "
                            f"{response_text[:500]}"
                        ),
                        response.status,
                    )
                try:
                    json_body = await response.json(content_type=None)
                except aiohttp.ContentTypeError as exc:
                    raise SerperClientError(
                        "Serper API returned a non-JSON response",
                        response.status,
                    ) from exc
                status_code = response.status
                if not isinstance(json_body, dict):
                    raise SerperClientError(
                        "Serper API returned an unexpected JSON shape",
                        status_code,
                    )
                return json_body, status_code
        except TimeoutError as exc:
            logger.warning("Serper request timed out: %s", url)
            raise SerperClientError("Serper API request timed out") from exc
        except aiohttp.ClientError as exc:
            logger.warning("Serper request failed: %s", url)
            raise SerperClientError(f"Serper API request failed: {exc}") from exc

    async def record_search_metric(
        self,
        *,
        tool: SerperTools,
        request: BaseModel,
        started_at: float,
        succeeded: bool,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Record a Serper search-family metric event.

        :param tool: Serper tool enum value.
        :type tool: SerperTools
        :param request: Search request model.
        :type request: BaseModel
        :param started_at: Monotonic start time.
        :type started_at: float
        :param succeeded: Whether the request succeeded.
        :type succeeded: bool
        :param status_code: Optional HTTP status code.
        :type status_code: int | None
        :param response: Optional response body.
        :type response: dict[str, Any] | None
        :param error: Optional error detail.
        :type error: str | None
        :return: None.
        :rtype: None
        """

        request_values = request.model_dump(exclude_none=True)
        await self.metrics.record_request(
            MetricEvent(
                tool=tool.value,
                request_type="search",
                succeeded=succeeded,
                latency_ms=elapsed_ms(started_at),
                status_code=status_code,
                query=string_value(request_values.get("q")),
                url=string_value(request_values.get("url")),
                result_count=count_serper_results(response),
                returned_bytes=count_json_bytes(response),
                error=error,
            )
        )

    async def record_scrape_metric(
        self,
        *,
        request: WebpageRequest,
        started_at: float,
        succeeded: bool,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        """Record a Serper scrape metric event.

        :param request: Webpage scrape request.
        :type request: WebpageRequest
        :param started_at: Monotonic start time.
        :type started_at: float
        :param succeeded: Whether the request succeeded.
        :type succeeded: bool
        :param status_code: Optional HTTP status code.
        :type status_code: int | None
        :param response: Optional response body.
        :type response: dict[str, Any] | None
        :param error: Optional error detail.
        :type error: str | None
        :return: None.
        :rtype: None
        """

        await self.metrics.record_request(
            MetricEvent(
                tool=SerperTools.WEBPAGE_SCRAPE.value,
                request_type="scrape",
                succeeded=succeeded,
                latency_ms=elapsed_ms(started_at),
                status_code=status_code,
                url=request.url,
                returned_bytes=count_json_bytes(response),
                response_format="markdown" if request.includeMarkdown else "text",
                error=error,
            )
        )

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


def elapsed_ms(started_at: float) -> float:
    """Return elapsed milliseconds since a monotonic start time.

    :param started_at: Monotonic start time.
    :type started_at: float
    :return: Elapsed milliseconds.
    :rtype: float
    """

    return (perf_counter() - started_at) * 1000


def count_serper_results(response: dict[str, Any] | None) -> int | None:
    """Count common Serper result-list fields.

    :param response: Serper response.
    :type response: dict[str, Any] | None
    :return: Result count when a known list field is available.
    :rtype: int | None
    """

    if response is None:
        return None
    for field_name in (
        "organic",
        "images",
        "videos",
        "places",
        "reviews",
        "news",
        "shopping",
        "patents",
        "suggestions",
    ):
        field_value = response.get(field_name)
        if isinstance(field_value, list):
            return len(field_value)
    return None


def count_json_bytes(response: dict[str, Any] | None) -> int | None:
    """Count serialized JSON response bytes.

    :param response: JSON response.
    :type response: dict[str, Any] | None
    :return: Response byte count.
    :rtype: int | None
    """

    if response is None:
        return None
    return len(str(response).encode("utf-8"))


def string_value(value: Any) -> str | None:
    """Return a value when it is a string.

    :param value: Candidate value.
    :type value: Any
    :return: String value when present.
    :rtype: str | None
    """

    return value if isinstance(value, str) else None
