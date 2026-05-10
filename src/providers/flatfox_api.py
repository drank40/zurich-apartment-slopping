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


class FlatfoxAuthError(Exception):
    """Base for flatfox auth errors."""


class DeviceNotVerifiedError(FlatfoxAuthError):
    """Raised when ``flatfoxDevice`` cookie is missing or stale."""


class LoginFailedError(FlatfoxAuthError):
    """Raised on any other non-2xx login response."""


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
        self.session.headers.setdefault("Origin", API_ROOT)
        self.session.headers.setdefault("Referer", API_ROOT + "/")
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
        expand_images: bool = True,
        expand_agency: bool = True,
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
            if expand_images:
                params.append(("expand", "images"))
            if expand_agency:
                params.append(("expand", "agency"))
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

    # -- common-API search --------------------------------------------------

    def search_listings(
        self,
        criteria: "SearchCriteria",
        llm: bool = True,
        llm_concurrency: int = 4,
        tax: bool = True,
        commute: bool = False,
        google_maps_key: str | None = None,
    ) -> list["Listing"]:
        """Run a search using the cross-provider ``SearchCriteria`` and
        return normalized ``Listing`` objects.

        Native filters (price, rooms, bbox, furnished/temporary, surface)
        are pushed into the ``/api/v1/pin/`` query. Keyword and feature
        filters are applied post-fetch on the normalized listings.

        ``llm=True`` (default) post-enriches matched listings via Haiku
        — fills ``bedrooms`` and ``has_washing_machine`` from descriptions
        in parallel (capped at ``llm_concurrency``). Pass ``llm=False`` to
        skip the LLM call entirely.
        """
        from .common import Listing, SearchCriteria, apply_common_filters  # local import: avoid cycles
        assert isinstance(criteria, SearchCriteria)
        # Translate criteria -> flatfox-native pin params.
        ff: dict[str, Any] = {
            "max_count": min(criteria.page_cap, 1000),
            "ordering": "-published" if criteria.sort_by_newest else "price_display",
        }
        if criteria.bbox:
            s, w, n, e = criteria.bbox
            ff.update({"south": s, "west": w, "north": n, "east": e})
        if criteria.min_rooms is not None:
            ff["min_rooms"] = criteria.min_rooms
        if criteria.max_rooms is not None:
            ff["max_rooms"] = criteria.max_rooms
        if criteria.min_price_chf is not None:
            ff["min_price"] = criteria.min_price_chf
        if criteria.max_price_chf is not None:
            ff["max_price"] = criteria.max_price_chf
        # Boolean flags: only push when explicitly demanded. Flatfox's
        # ``is_furnished=true`` filters TO furnished, ``=false`` excludes
        # furnished — so the field is unsuitable for "don't care".
        if criteria.must_be_furnished is True:
            ff["is_furnished"] = True
        elif criteria.must_be_furnished is False:
            ff["is_furnished"] = False
        if criteria.must_be_temporary is True:
            ff["is_temporary"] = True
        elif criteria.must_be_temporary is False:
            ff["is_temporary"] = False

        raw = self.search(ff, limit=criteria.page_cap)
        # Filter to apartments/houses (flatfox's pin can include PARK etc.)
        wanted_categories = {c.upper() for c in criteria.categories}
        if wanted_categories:
            raw = [
                r for r in raw
                if (r.get("object_category") or "").upper() in wanted_categories
            ]
        # ZIP filter (flatfox returns the postcode in each listing)
        if criteria.zipcodes:
            zips = set(str(z) for z in criteria.zipcodes)
            raw = [r for r in raw if str(r.get("zipcode") or "") in zips]
        # Map to Listing through fetch_full_detail's normalizer logic by
        # post-processing the ``raw`` rows directly (they already have
        # everything the normalizer needs).
        listings = [
            Listing.from_dict(_flatfox_to_normalized(item))
            for item in raw
        ]
        result = apply_common_filters(listings, criteria)
        if tax and result:
            from .enrich import enrich_with_tax
            enrich_with_tax(result)
        if commute and result and google_maps_key:
            from .enrich import enrich_with_commute
            enrich_with_commute(result, google_maps_key)
        if criteria.max_commute_min is not None:
            from .common import filter_by_commute
            result = filter_by_commute(result, criteria.max_commute_min)
        if llm and result:
            from .llm_extract import enrich_listings
            result = enrich_listings(result, max_concurrency=llm_concurrency)
            from .common import filter_by_availability
            result = filter_by_availability(result, criteria.available_on_or_before)
        return result

    # -- detail -------------------------------------------------------------

    def fetch_detail(self, pk_or_url: int | str) -> dict[str, Any] | None:
        """Fetch a single listing by pk or full flatfox URL."""
        pk = self._extract_pk(pk_or_url)
        if pk is None:
            return None
        data = self.fetch_listings([pk])
        return data[0] if data else None

    # -- login (pure requests, JSON DRF endpoint) ---------------------------

    LOGIN_ENDPOINT = "/api/v1/auth/login/"
    ACCOUNT_ENDPOINT = "/api/v1/account/"

    def login(
        self,
        email: str,
        password: str,
        device_cookie: str | None = None,
        otp: str | None = None,
    ) -> bool:
        """Log in via ``POST /api/v1/auth/login/`` (dj-rest-auth-style).

        Flatfox uses per-device verification: on a cold device the API
        returns 403 ``device-not-verified`` and emails an OTP. Once the
        device is verified once, the ``flatfoxDevice`` cookie acts as a
        long-lived trust token — pass it via ``device_cookie`` to skip the
        OTP step on every run. Raises :class:`DeviceNotVerifiedError` when
        the supplied cookie is missing/stale, with an explanation of what
        to do.
        """
        # Seed the device cookie BEFORE any GET so the bootstrap doesn't
        # mint a fresh (untrusted) one.
        if device_cookie:
            self.session.cookies.set(
                "flatfoxDevice", device_cookie, domain="flatfox.ch", path="/",
            )

        # Bootstrap CF cookies (__cf_bm etc). 403 is fine — Set-Cookie still applies.
        self.session.get(
            urljoin(API_ROOT, self.ACCOUNT_ENDPOINT),
            timeout=self.timeout,
            allow_redirects=False,
        )

        body: dict[str, Any] = {"email": email, "password": password}
        if otp:
            body["otp"] = otp
        r = self.session.post(
            urljoin(API_ROOT, self.LOGIN_ENDPOINT),
            json=body,
            timeout=self.timeout,
        )
        if r.status_code == 403 and "device-not-verified" in r.text:
            raise DeviceNotVerifiedError(
                "Flatfox refused login: 'Device not verified'. Either the "
                "FLATFOX_DEVICE_COOKIE in .creds is missing/stale or this is "
                "a fresh device. To refresh: log into flatfox.ch in a browser "
                "(complete the email OTP), then copy the value of the "
                "'flatfoxDevice' cookie from DevTools and update "
                "FLATFOX_DEVICE_COOKIE in .creds."
            )
        if not r.ok:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:300]
            raise LoginFailedError(
                f"Flatfox login failed (HTTP {r.status_code}): {detail}"
            )
        ok = self._probe_authed()
        logger.info("Flatfox login %s.", "OK" if ok else "FAILED (probe)")
        return ok

    def _probe_authed(self) -> bool:
        try:
            r = self.session.get(
                urljoin(API_ROOT, self.ACCOUNT_ENDPOINT),
                timeout=self.timeout,
                allow_redirects=False,
            )
            return r.status_code == 200
        except Exception:
            return False

    # -- normalized detail --------------------------------------------------

    def fetch_full_detail(self, pk_or_url: int | str) -> dict[str, Any] | None:
        """Return a normalized detail dict (description/price/images/address/meta).

        See ``docs/api/flatfox.md`` for the source schema. This wrapper picks
        the largest image URL and exposes a stable shape for downstream tools.
        """
        pk = self._extract_pk(pk_or_url)
        if pk is None:
            return None
        params: list[tuple[str, str]] = [
            ("pk", str(pk)),
            ("limit", "0"),
            ("expand", "cover_image"),
            ("expand", "images"),
            ("expand", "agency"),
        ]
        data = self._get(LISTING_ENDPOINT, params=params)
        items = data if isinstance(data, list) else (data.get("results") or [])
        if not items:
            return None
        return _flatfox_to_normalized(items[0])

    @staticmethod
    def _extract_pk(value: int | str) -> int | None:
        if isinstance(value, int):
            return value
        s = str(value).strip()
        if s.isdigit():
            return int(s)
        import re
        m = re.search(r"/(\d{4,})/?(?:\?|$)", s)
        return int(m.group(1)) if m else None


def _flatfox_to_normalized(item: dict[str, Any]) -> dict[str, Any]:
    """Map a flatfox public-listing dict (as returned by either /pin/ +
    /public-listing/ or the /pin/ endpoint when listing fields are
    embedded) to the cross-provider normalized shape.
    """

    def _img_url(img: Any) -> str:
        # Skip unexpanded references (bare ints when ?expand=images was omitted).
        if not isinstance(img, dict):
            return ""
        raw = img.get("url") or img.get("url_thumb_m") or img.get("url_listing_search")
        return urljoin(API_ROOT, raw) if raw else ""

    pk = item.get("pk")
    images = [u for u in (_img_url(i) for i in (item.get("images") or [])) if u]
    cover = item.get("cover_image") or {}
    if cover and _img_url(cover) and _img_url(cover) not in images:
        images.insert(0, _img_url(cover))

    price = item.get("rent_gross") or item.get("price_display") or item.get("rent_net")
    rent_net = item.get("rent_net")
    rent_charges = item.get("rent_charges")

    attributes = [
        a.get("name") if isinstance(a, dict) else str(a)
        for a in (item.get("attributes") or [])
    ]

    agency = item.get("agency") or {}
    agency_logo = (agency.get("logo") or {}).get("url") if agency.get("logo") else None

    return {
            "provider": "flatfox",
            "listing_id": str(pk),
            "url": urljoin(API_ROOT, item.get("url") or item.get("short_url") or ""),
            "submit_url": urljoin(API_ROOT, item.get("submit_url") or ""),
            "title": (
                item.get("public_title")
                or item.get("description_title")
                or item.get("short_title")
            ),
            "description": item.get("description") or "",
            "price_chf": float(price) if price is not None else None,
            "rent_net_chf": float(rent_net) if rent_net is not None else None,
            "rent_charges_chf": float(rent_charges) if rent_charges is not None else None,
            "currency": "CHF",
            "rooms": float(item["number_of_rooms"]) if item.get("number_of_rooms") is not None else None,
            "surface_living_m2": item.get("surface_living"),
            "surface_usable_m2": item.get("surface_usable"),
            "floor": item.get("floor"),
            "year_built": item.get("year_built"),
            "year_renovated": item.get("year_renovated"),
            "available_from": item.get("moving_date"),
            "available_from_type": item.get("moving_date_type"),
            "is_furnished": item.get("is_furnished"),
            "is_temporary": item.get("is_temporary"),
            "object_category": item.get("object_category"),
            "object_type": item.get("object_type"),
            "address": {
                "street": item.get("street"),
                "zipcode": item.get("zipcode"),
                "city": item.get("city"),
                "country": item.get("country"),
                "public": item.get("public_address"),
                "lat": item.get("latitude"),
                "lon": item.get("longitude"),
            },
            "images": images,
            "attributes": attributes,
            "agency": {
                "name": agency.get("name") or "",
                "name_2": agency.get("name_2") or "",
                "street": agency.get("street") or "",
                "zipcode": agency.get("zipcode") or "",
                "city": agency.get("city") or "",
                "country": agency.get("country") or "",
                "logo_url": urljoin(API_ROOT, agency_logo) if agency_logo else None,
            },
            "raw": item,
        }



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
