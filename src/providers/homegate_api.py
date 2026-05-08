"""Homegate.ch JSON API client.

Homegate's SPA uses an internal JSON API at https://api.homegate.ch. The
search endpoint is ``POST /search/listings`` accepting a JSON body of the
shape::

    {
      "query": {
        "offerType": "RENT",
        "categories": ["APARTMENT","HOUSE"],
        "location": {"geoTags": ["geo-city-zurich"]},
        "monthlyRent": {"to": 3600},
        "numberOfRooms": {"from": 3}
      },
      "sortBy": "monthlyRent",
      "sortDirection": "asc",
      "from": 0,
      "size": 20,
      "trackTotalHits": true,
      "fieldset": "srp-list"
    }

Response::

    {"from":0,"size":20,"total":312,"results":[ ... ], "maxFrom": ...}

Each result has::

    {"id": "4002964983",
     "listing": {"address": {...}, "categories": [...], "characteristics": {...},
                 "id": "...", "localization": {"de": {"text": {"title","description"}}},
                 "meta": {...}, "offerType": "RENT", "platforms": [...],
                 "prices": {"rent": {"gross"|"net": int, "interval": "MONTH"}, ...},
                 "valueAddedServices": {...}},
     "listingCard": {...},
     "listingScores": {...}}

Auth quirk: the endpoint is gated by DataDome bot mitigation. A valid
``datadome`` cookie issued after passing the JS challenge is required.
We expect callers to pass it in via ``cookies={"datadome": "..."}``.
A helper :func:`bootstrap_datadome_cookie` uses Playwright once to fetch it.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)

API_ROOT = "https://api.homegate.ch"
SEARCH_ENDPOINT = "/search/listings"
WEB_ROOT = "https://www.homegate.ch"

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_FIELDSET = "srp-list"  # only valid fieldset besides "srp-list"
DEFAULT_PAGE_SIZE = 20
MAX_FROM = 1000  # homegate caps deep pagination


class HomegateClient:
    def __init__(
        self,
        datadome_cookie: str | None = None,
        cookies: dict[str, str] | None = None,
        session: requests.Session | None = None,
        user_agent: str = DEFAULT_UA,
        timeout: float = 20.0,
        rate_delay: float = 1.0,
    ):
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": WEB_ROOT,
            "Referer": WEB_ROOT + "/",
            "Content-Type": "application/json",
        })
        self.timeout = timeout
        self.rate_delay = rate_delay
        merged = dict(cookies or {})
        if datadome_cookie and "datadome" not in merged:
            merged["datadome"] = datadome_cookie
        for k, v in merged.items():
            self.session.cookies.set(k, v, domain=".homegate.ch")

    def search_page(self, query: dict[str, Any], from_: int = 0, size: int = DEFAULT_PAGE_SIZE,
                    sort_by: str = "monthlyRent", sort_direction: str = "asc",
                    fieldset: str = DEFAULT_FIELDSET) -> dict[str, Any]:
        payload = {
            "query": query,
            "sortBy": sort_by,
            "sortDirection": sort_direction,
            "from": from_,
            "size": size,
            "trackTotalHits": True,
            "fieldset": fieldset,
        }
        url = urljoin(API_ROOT, SEARCH_ENDPOINT)
        r = self.session.post(url, json=payload, timeout=self.timeout)
        if r.status_code == 403 and "captcha" in r.text:
            raise HomegateBotChallenge(
                "DataDome challenge triggered. Refresh the datadome cookie."
            )
        r.raise_for_status()
        return r.json()

    def search(self, criteria: dict[str, Any], limit: int | None = None,
               page_size: int = DEFAULT_PAGE_SIZE) -> list[dict[str, Any]]:
        """Iterate the search endpoint until ``limit`` or ``maxFrom`` reached.

        ``criteria`` is the homegate-shaped query dict (see module docstring).
        """
        results: list[dict[str, Any]] = []
        from_ = 0
        while True:
            page = self.search_page(criteria, from_=from_, size=page_size)
            page_results = page.get("results", [])
            if not page_results:
                break
            results.extend(page_results)
            total = page.get("total", 0)
            max_from = page.get("maxFrom", MAX_FROM)
            from_ += len(page_results)
            if limit and len(results) >= limit:
                results = results[:limit]
                break
            if from_ >= total or from_ >= max_from:
                break
            if self.rate_delay:
                time.sleep(self.rate_delay)
        return results

    def fetch_detail(self, listing_id: str) -> dict[str, Any] | None:
        """Search for a single id. Homegate's public detail endpoint is
        gated; the search endpoint with an id filter returns the same data.
        """
        try:
            page = self.search_page(
                {"ids": [str(listing_id)]}, from_=0, size=1
            )
        except Exception:
            return None
        items = page.get("results") or []
        return items[0] if items else None


class HomegateBotChallenge(RuntimeError):
    pass


# ----------------------------------------------------------------------------
# Bootstrap helpers
# ----------------------------------------------------------------------------

def bootstrap_datadome_cookie(
    headless: bool = False,
    referer_url: str = "https://www.homegate.ch/rent/real-estate/city-zurich/matching-list",
    user_agent: str = DEFAULT_UA,
) -> str | None:
    """Run a one-shot Playwright session to obtain a ``datadome`` cookie.

    Use ``headless=False`` if a CAPTCHA appears so the user can solve it.
    Returns the cookie value or ``None`` on failure.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("Playwright not installed; cannot bootstrap cookie")
        return None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=user_agent, viewport={"width": 1440, "height": 900}
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = context.new_page()
        try:
            page.goto(referer_url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            logger.warning(f"goto warning: {e}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(8000)
        except Exception:
            pass
        ck = context.cookies()
        context.close()
        browser.close()

    for c in ck:
        if c["name"] == "datadome" and ".homegate.ch" in c["domain"]:
            return c["value"]
    return None


# ----------------------------------------------------------------------------
# Mapping helpers
# ----------------------------------------------------------------------------

def build_query_from_config(p_cfg: dict[str, Any]) -> dict[str, Any]:
    """Build the homegate query dict from the legacy ``params`` config.

    Legacy config used ``ac`` (min rooms) and ``ah`` (max price). We keep
    those for backwards-compat but also accept structured fields if present.
    """
    params = p_cfg.get("params", {}) or {}
    query: dict[str, Any] = {
        "offerType": "RENT",
        "categories": ["APARTMENT", "HOUSE"],
        "location": {"geoTags": ["geo-city-zurich"]},
    }
    if "ah" in params or "max_price" in params:
        query["monthlyRent"] = {"to": int(params.get("ah") or params["max_price"])}
    if "ac" in params or "min_rooms" in params:
        query["numberOfRooms"] = {"from": float(params.get("ac") or params["min_rooms"])}
    # Allow direct overrides
    for k in ("offerType", "categories", "location", "monthlyRent",
              "numberOfRooms", "livingSpace"):
        if k in params:
            query[k] = params[k]
    return query


def mapping_to_listing(item: dict[str, Any], listing_cls):
    """Convert a homegate /search/listings result into a Listing dataclass."""
    listing = item.get("listing") or {}
    item_id = item.get("id") or listing.get("id")
    addr = listing.get("address") or {}
    geo = addr.get("geoCoordinates") or {}
    chars = listing.get("characteristics") or {}
    prices = listing.get("prices") or {}
    rent = prices.get("rent") or {}
    price = rent.get("gross") or rent.get("net") or listing.get("price")

    loc = (
        listing.get("localization", {})
        .get(listing.get("localization", {}).get("primary", "de"), {})
        .get("text", {})
    )
    if not loc:
        loc_root = listing.get("localization") or {}
        for lang in ("de", "en", "fr", "it"):
            if lang in loc_root and "text" in loc_root[lang]:
                loc = loc_root[lang]["text"]
                break

    title = loc.get("title") or "Homegate Listing"
    description = loc.get("description") or ""

    address_str = ", ".join(
        x for x in [
            addr.get("street"),
            f"{addr.get('postalCode','')} {addr.get('locality','')}".strip(),
        ] if x
    )
    rooms = chars.get("numberOfRooms") or chars.get("totalRooms")
    surface = chars.get("livingSpace")

    url = f"{WEB_ROOT}/rent/{item_id}"

    avail = listing.get("meta", {}).get("availableFrom") or addr.get("availableFrom")
    avail_from = None
    if avail:
        try:
            from datetime import date as _date
            avail_from = _date.fromisoformat(str(avail)[:10])
        except Exception:
            pass

    return listing_cls(
        provider="homegate",
        listing_id=str(item_id),
        title=title,
        url=url,
        contact_url=url,
        price_chf=float(price) if price is not None else None,
        total_rooms=float(rooms) if rooms is not None else None,
        bedrooms=None,
        available_from=avail_from,
        address=address_str,
        description=description,
        lat=geo.get("latitude"),
        lon=geo.get("longitude"),
        raw=item,
    )
