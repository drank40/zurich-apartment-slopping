"""Flatfox.ch JSON API client.

The flatfox SPA uses a public REST API at https://flatfox.ch/api/v1/. No
authentication is required for the search/detail endpoints we need.

Two-step flow used by the SPA:
    1) GET /api/v1/pin/?north=&south=&east=&west=&min_rooms=&max_price=&...
       returns up to ``max_count`` "pins" (pk + lat/lon + price) matching the
       map bounds & filters.
    2) GET /api/v1/public-listing/?pk=N&pk=N&...&expand=cover_image&limit=0
       returns the full listing payload for those pks.

We do the same. Since `public-listing` accepts arbitrary repeated pk= params,
we can fetch all pins in chunks of ~50 to stay under URL length limits.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterable
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

API_ROOT = "https://flatfox.ch"
PIN_ENDPOINT = "/api/v1/pin/"
LISTING_ENDPOINT = "/api/v1/public-listing/"
DEFAULT_UA = (
    "Mozilla/5.0 (compatible; ZurichApartmentFinder/1.0; "
    "+https://github.com/zurich-apartment-finder)"
)


class FlatfoxClient:
    """Lightweight HTTP client around flatfox.ch's public JSON API."""

    def __init__(
        self,
        session: requests.Session | None = None,
        user_agent: str = DEFAULT_UA,
        timeout: float = 20.0,
        rate_delay: float = 0.5,
    ):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault(
            "Accept", "application/json, text/plain, */*"
        )
        self.session.headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        self.timeout = timeout
        self.rate_delay = rate_delay

    # -- low-level ----------------------------------------------------------

    def _get(self, path: str, params: Any = None) -> Any:
        url = urljoin(API_ROOT, path)
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # -- searches -----------------------------------------------------------

    def search_pins(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the raw list of pins matching the search bounds + filters.

        ``params`` mirrors the SPA's query string. Important keys:
            north, south, east, west, min_rooms, max_price, is_furnished,
            is_temporary, is_swap, ordering, max_count.
        """
        # Defaults that mirror the SPA's defaults
        merged: dict[str, Any] = {
            "max_count": 1000,
            "ordering": "price_display",
        }
        merged.update(params)
        # Flatfox accepts boolean params as lowercase "true"/"false" strings
        for k, v in list(merged.items()):
            if isinstance(v, bool):
                merged[k] = "true" if v else "false"
        data = self._get(PIN_ENDPOINT, params=merged)
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected pin response shape: {type(data)}")
        return data

    def fetch_listings(
        self,
        pks: Iterable[int | str],
        expand_cover_image: bool = True,
        chunk_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Fetch full ``public-listing`` records for the given pks.

        The endpoint accepts repeated ``pk=`` params. Chunked to keep URLs
        below ~2k chars.
        """
        pk_list = [str(p) for p in pks]
        out: list[dict[str, Any]] = []
        for i in range(0, len(pk_list), chunk_size):
            chunk = pk_list[i : i + chunk_size]
            params: list[tuple[str, str]] = [("pk", p) for p in chunk]
            params.append(("limit", "0"))  # disable pagination
            if expand_cover_image:
                params.append(("expand", "cover_image"))
            data = self._get(LISTING_ENDPOINT, params=params)
            # Endpoint sometimes returns a list (when pk provided) and
            # sometimes a paginated object. Handle both.
            if isinstance(data, list):
                out.extend(data)
            elif isinstance(data, dict) and "results" in data:
                out.extend(data["results"])
            if self.rate_delay:
                time.sleep(self.rate_delay)
        return out

    def search(
        self, criteria: dict[str, Any], limit: int | None = None
    ) -> list[dict[str, Any]]:
        """Combined helper: pins -> full listings.

        Returns the raw flatfox listing dicts. Conversion to ``Listing``
        happens in :func:`mapping_to_listing` so callers can decide.
        """
        pins = self.search_pins(criteria)
        # Filter pins to those that look like apartments before fetching
        # detail (cuts noise from PARK / GARAGE etc.)
        pks = [p["pk"] for p in pins if p.get("pk")]
        if limit:
            pks = pks[:limit]
        listings = self.fetch_listings(pks)
        return listings

    # -- detail -------------------------------------------------------------

    def fetch_detail(self, pk_or_url: int | str) -> dict[str, Any] | None:
        """Fetch a single listing by pk or full flatfox URL."""
        pk = self._extract_pk(pk_or_url)
        if pk is None:
            return None
        data = self.fetch_listings([pk])
        return data[0] if data else None

    @staticmethod
    def _extract_pk(value: int | str) -> int | None:
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if s.isdigit():
            return int(s)
        # Pull the last number group from common URL forms like
        # /en/flat/<slug>/<pk>/  or  /<pk>/
        import re

        m = re.search(r"/(\d{4,})/?(?:\?|$)", s)
        if m:
            return int(m.group(1))
        return None


# ----------------------------------------------------------------------------
# Mapping helpers
# ----------------------------------------------------------------------------

def mapping_to_listing(item: dict[str, Any], listing_cls):
    """Convert a flatfox public-listing dict into a Listing dataclass.

    ``listing_cls`` is passed in to avoid a circular import; pass the
    Listing class from ``apartment_finder_llm``.
    """
    from datetime import date as _date

    pk = item.get("pk")
    short_url = item.get("short_url") or f"/{pk}/"
    url = urljoin(API_ROOT, item.get("url") or short_url)
    title = (
        item.get("public_title")
        or item.get("description_title")
        or item.get("short_title")
        or f"Flatfox listing {pk}"
    )
    lat = item.get("latitude")
    lon = item.get("longitude")
    rooms = item.get("number_of_rooms")
    price = (
        item.get("rent_gross")
        or item.get("price_display")
        or item.get("rent_net")
    )
    furnished = item.get("is_furnished")
    is_temp = item.get("is_temporary")
    description = item.get("description") or ""
    address = item.get("public_address") or ", ".join(
        x for x in [item.get("street"), str(item.get("zipcode") or ""), item.get("city")] if x
    )

    moving_date = item.get("moving_date")
    avail_from: _date | None = None
    if moving_date:
        try:
            avail_from = _date.fromisoformat(str(moving_date)[:10])
        except Exception:
            avail_from = None

    contact_url = urljoin(API_ROOT, item.get("submit_url") or url)

    likely_shared = item.get("object_type", "").upper() in {"SHARED", "ROOM"} or (
        "object_category" in item
        and item.get("object_category", "").upper() == "SHARED"
    )

    return listing_cls(
        provider="flatfox",
        listing_id=str(pk),
        title=title,
        url=url,
        contact_url=contact_url,
        price_chf=float(price) if price is not None else None,
        bedrooms=None,
        total_rooms=float(rooms) if rooms is not None else None,
        available_from=avail_from,
        furnished=bool(furnished) if furnished is not None else None,
        likely_shared=likely_shared if likely_shared else None,
        is_temporary=bool(is_temp) if is_temp is not None else None,
        address=address,
        description=description,
        lat=lat,
        lon=lon,
        raw=item,
    )
