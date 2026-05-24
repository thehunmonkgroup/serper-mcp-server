"""Request models for Serper MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BaseRequest(BaseModel):
    """Shared search request fields."""

    q: str = Field(..., description="The query to search for")
    gl: str | None = Field(
        None, description="The country to search in, e.g. us, uk, ca, au, etc."
    )
    location: str | None = Field(
        None, description="The location to search in, e.g. San Francisco, CA, USA"
    )
    hl: str | None = Field(
        None, description="The language to search in, e.g. en, es, fr, de, etc."
    )
    page: int = Field(
        1,
        ge=1,
        description="The page number to return, first page is 1",
    )


class SearchRequest(BaseRequest):
    """Request fields for general Serper search endpoints."""

    tbs: str | None = Field(
        None, description="The time period to search in, e.g. d, w, m, y"
    )
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="The number of results to return, max is 100",
    )


class AutocorrectRequest(BaseRequest):
    """Request fields for Serper endpoints with autocorrect support."""

    autocorrect: bool = Field(
        True,
        description="Automatically correct the query",
    )


class MapsRequest(BaseModel):
    """Request fields for Serper maps search."""

    q: str = Field(..., description="The query to search for")
    ll: str | None = Field(None, description="The GPS position & zoom level")
    placeId: str | None = Field(None, description="The place ID to search in")
    cid: str | None = Field(None, description="The CID to search in")
    gl: str | None = Field(
        None, description="The country to search in, e.g. us, uk, ca, au, etc."
    )
    hl: str | None = Field(
        None, description="The language to search in, e.g. en, es, fr, de, etc."
    )
    page: int = Field(
        1,
        ge=1,
        description="The page number to return, first page is 1",
    )


class ReviewsRequest(BaseModel):
    """Request fields for Serper reviews search."""

    fid: str = Field(..., description="The FID")
    cid: str | None = Field(None, description="The CID to search in")
    placeId: str | None = Field(None, description="The place ID to search in")
    sortBy: Literal["mostRelevant", "newest", "highestRating", "lowestRating"] = Field(
        "mostRelevant",
        description="The sort order to use",
    )
    topicId: str | None = Field(None, description="The topic ID to search in")
    nextPageToken: str | None = Field(None, description="The next page token to use")
    gl: str | None = Field(
        None, description="The country to search in, e.g. us, uk, ca, au, etc."
    )
    hl: str | None = Field(
        None, description="The language to search in, e.g. en, es, fr, de, etc."
    )


class ShoppingRequest(BaseRequest):
    """Request fields for Serper shopping search."""

    autocorrect: bool = Field(
        True,
        description="Automatically correct the query",
    )
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="The number of results to return, max is 100",
    )


class LensRequest(BaseModel):
    """Request fields for Serper lens search."""

    url: str = Field(..., description="The url to search")
    gl: str | None = Field(
        None, description="The country to search in, e.g. us, uk, ca, au, etc."
    )
    hl: str | None = Field(
        None, description="The language to search in, e.g. en, es, fr, de, etc."
    )


class PatentsRequest(BaseModel):
    """Request fields for Serper patents search."""

    q: str = Field(..., description="The query to search for")
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="The number of results to return, max is 100",
    )
    page: int = Field(
        1,
        ge=1,
        description="The page number to return, first page is 1",
    )


class WebpageRequest(BaseModel):
    """Request fields for Serper webpage scraping."""

    url: str = Field(..., description="The url to scrape")
    includeMarkdown: bool = Field(
        False,
        description="Include markdown in the response",
    )
