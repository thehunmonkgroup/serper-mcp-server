"""Request models for Serper MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class BaseRequest(BaseModel):
    """Shared search request fields."""

    q: str = Field(..., description="Google search query.")
    gl: str | None = Field(
        None, description="Two-letter country code, such as us, uk, or ca."
    )
    location: str | None = Field(
        None, description="Search origin location, such as San Francisco, CA, USA."
    )
    hl: str | None = Field(None, description="Language code, such as en, es, or fr.")
    page: int = Field(
        1,
        ge=1,
        description="One-based results page; 1 is the first page.",
    )


class SearchRequest(BaseRequest):
    """Request fields for general Serper search endpoints."""

    tbs: str | None = Field(
        None,
        description=(
            "Google time/search filter, such as qdr:d, qdr:w, qdr:m, or qdr:y."
        ),
    )
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="Number of results to request.",
    )


class AutocorrectRequest(BaseRequest):
    """Request fields for Serper endpoints with autocorrect support."""

    autocorrect: bool = Field(
        True,
        description="Whether Serper should autocorrect the query.",
    )


class MapsRequest(BaseModel):
    """Request fields for Serper maps search."""

    q: str = Field(..., description="Google search query.")
    ll: str | None = Field(
        None,
        description=(
            "Google Maps latitude, longitude, and zoom string, such as "
            "@40.7504178,-73.9824837,14z."
        ),
    )
    placeId: str | None = Field(
        None, description="Google place ID used to target a place."
    )
    cid: str | None = Field(
        None, description="Google customer ID used to target a place."
    )
    gl: str | None = Field(
        None, description="Two-letter country code, such as us, uk, or ca."
    )
    hl: str | None = Field(None, description="Language code, such as en, es, or fr.")
    page: int = Field(
        1,
        ge=1,
        description="One-based results page; 1 is the first page.",
    )


class ReviewsRequest(BaseModel):
    """Request fields for Serper reviews search."""

    fid: str = Field(..., description="Google reviews feature ID for the place.")
    cid: str | None = Field(
        None, description="Google customer ID used to target a place."
    )
    placeId: str | None = Field(
        None, description="Google place ID used to target a place."
    )
    sortBy: Literal["mostRelevant", "newest", "highestRating", "lowestRating"] = Field(
        "mostRelevant",
        description=(
            "Review sort order: mostRelevant, newest, highestRating, or "
            "lowestRating."
        ),
    )
    topicId: str | None = Field(
        None, description="Review topic ID used to filter reviews."
    )
    nextPageToken: str | None = Field(
        None, description="Token for the next page of reviews."
    )
    gl: str | None = Field(
        None, description="Two-letter country code, such as us, uk, or ca."
    )
    hl: str | None = Field(None, description="Language code, such as en, es, or fr.")


class ShoppingRequest(BaseRequest):
    """Request fields for Serper shopping search."""

    autocorrect: bool = Field(
        True,
        description="Whether Serper should autocorrect the query.",
    )
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="Number of results to request.",
    )


class LensRequest(BaseModel):
    """Request fields for Serper lens search."""

    url: str = Field(..., description="Absolute image URL to search with Google Lens.")
    gl: str | None = Field(
        None, description="Two-letter country code, such as us, uk, or ca."
    )
    hl: str | None = Field(None, description="Language code, such as en, es, or fr.")


class PatentsRequest(BaseModel):
    """Request fields for Serper patents search."""

    q: str = Field(..., description="Google search query.")
    num: int = Field(
        10,
        ge=1,
        le=100,
        description="Number of results to request.",
    )
    page: int = Field(
        1,
        ge=1,
        description="One-based results page; 1 is the first page.",
    )


class WebpageRequest(BaseModel):
    """Request fields for Serper webpage scraping."""

    url: str = Field(..., description="Absolute URL to scrape.")
    includeMarkdown: bool = Field(
        False,
        description="Include Markdown in the scrape response.",
    )
