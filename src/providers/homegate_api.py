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
    """Homegate JSON API client.

    Default transport is **headless Playwright** (Chromium ``APIRequestContext``)
    because DataDome fingerprints the TLS handshake and revokes cookies the
    moment it sees a non-Chrome client. Playwright reuses a single Chromium
    process for the lifetime of the client; only API calls happen — no HTML
    pages are loaded after the initial cookie seed.

    Pass ``transport='requests'`` to force the legacy ``requests`` path
    (works only for very low traffic with a fresh cookie).
    """

    def __init__(
        self,
        datadome_cookie: str | None = None,
        cookies: dict[str, str] | None = None,
        session: requests.Session | None = None,  # legacy
        user_agent: str = DEFAULT_UA,
        timeout: float = 20.0,
        rate_delay: float = 0.5,
        transport: str = "playwright",
        headless: bool = True,
    ):
        self.user_agent = user_agent
        self.timeout = timeout
        self.rate_delay = rate_delay
        self.transport = transport
        self.headless = headless
        self._pw = None
        self._pw_browser = None
        self._pw_ctx = None

        merged_cookies = dict(cookies or {})
        if datadome_cookie and "datadome" not in merged_cookies:
            merged_cookies["datadome"] = datadome_cookie
        self._cookies = merged_cookies

        # Legacy requests session — kept for transport='requests'.
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Origin": WEB_ROOT,
            "Referer": WEB_ROOT + "/",
            "Content-Type": "application/json",
        })
        for k, v in self._cookies.items():
            self.session.cookies.set(k, v, domain=".homegate.ch")

    # -- transport helpers --------------------------------------------------

    def _t(self, label: str, t0: float) -> None:
        """Log elapsed time since t0. Cheap, safe to leave on by default."""
        import time as _t
        logger.info("Homegate timing: %s = %.2fs", label, _t.monotonic() - t0)

    def _ensure_pw(self):
        """Lazily launch a Camoufox browser context.

        Vanilla headless Chromium (incl. ``patchright``) cannot pass DataDome
        for homegate.ch from common networks. Camoufox (patched Firefox)
        does, with ``humanize=True`` and a Windows fingerprint.

        Strategy:

        1. If ``camoufox`` is importable, use it. If launch fails, raise a
           clear actionable error — do NOT fall back to playwright, because
           Camoufox typically leaves an asyncio loop running and a follow-up
           ``sync_playwright().start()`` would crash with a confusing
           "Sync API inside asyncio loop" error.
        2. If ``camoufox`` is not installed at all, fall back to vanilla
           playwright Chromium (will likely fail on DataDome but lets the
           caller proceed in low-block environments).
        """
        import time as _time
        if self._pw_ctx is not None:
            return
        t_total = _time.monotonic()
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
            camoufox_available = True
        except ImportError:
            camoufox_available = False

        if camoufox_available:
            try:
                t0 = _time.monotonic()
                self._cm = Camoufox(
                    headless=self.headless,
                    humanize=True,
                    geoip=True,
                    os=["windows"],
                )
                self._pw_browser = self._cm.__enter__()
                self._t("camoufox_launch", t0)
                t0 = _time.monotonic()
                self._pw_ctx = self._pw_browser.new_context()
                self._t("camoufox_new_context", t0)
                self._using_camoufox = True
            except Exception as exc:
                # Tear down whatever partially started.
                try:
                    if getattr(self, "_cm", None):
                        self._cm.__exit__(None, None, None)
                except Exception:
                    pass
                raise RuntimeError(
                    f"Camoufox failed to launch: {exc!r}.\n"
                    "Common causes: missing geoip extra (run "
                    "`pip install 'camoufox[geoip]'`), Camoufox binary not "
                    "fetched (`camoufox fetch`), or running inside an "
                    "asyncio event loop (Jupyter / pytest-asyncio). "
                    "If you're in a notebook, switch to the async API."
                ) from exc
        else:
            from playwright.sync_api import sync_playwright
            self._pw = sync_playwright().start()
            self._pw_browser = self._pw.chromium.launch(headless=self.headless)
            self._pw_ctx = self._pw_browser.new_context(
                user_agent=self.user_agent,
                viewport={"width": 1440, "height": 900},
            )
            self._pw_ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
                "window.chrome={runtime:{}};"
            )
            self._using_camoufox = False
        # Pre-load any caller-supplied cookies.
        cookies = [
            {"name": k, "value": v, "domain": ".homegate.ch", "path": "/"}
            for k, v in self._cookies.items()
        ]
        if cookies:
            self._pw_ctx.add_cookies(cookies)
        # Bootstrap once via root nav — mints a trusted datadome cookie.
        self._bootstrap_datadome()

    def _bootstrap_datadome(self) -> None:
        """Mint a fresh datadome cookie by visiting www.homegate.ch root.

        DataDome runs an async JS challenge that may finish *after*
        ``networkidle`` — without a small post-nav settle the page can land
        on the captcha shell with a low-trust cookie. We poll for a
        non-captcha body up to 2.5s, which is much faster than the old
        blanket 3s sleep but still robust.
        """
        import time as _time
        t0 = _time.monotonic()
        page = self._pw_ctx.new_page()
        try:
            page.goto(WEB_ROOT + "/", wait_until="networkidle", timeout=60000)
            # The captcha shell renders ``<title>homegate.ch</title>``; the
            # trusted page renders the marketing title. The trusted page
            # still contains a script tag pointing at captcha-delivery.com,
            # so we cannot match on the substring — title is the signal.
            deadline = _time.monotonic() + 5.0
            while _time.monotonic() < deadline:
                t = page.title()
                if t and t.lower() != "homegate.ch":
                    break
                page.wait_for_timeout(250)
            else:
                logger.warning("Homegate: root nav still gated by captcha after 5s.")
        finally:
            page.close()
        self._t("bootstrap_root_nav", t0)
        for c in self._pw_ctx.cookies():
            if c["name"] == "datadome" and "homegate.ch" in c["domain"]:
                self._cookies["datadome"] = c["value"]
                logger.info("Homegate: bootstrapped datadome cookie.")
                return
        logger.warning("Homegate: bootstrap finished but no datadome cookie present.")

    def _fetch_listing_via_page(self, listing_id: str) -> dict[str, Any] | None:
        """Navigate to /mieten/{id} and extract Pinia state.

        Homegate's detail pages are SSR'd — the entire listing object is
        embedded in ``window.__PINIA_INITIAL_STATE__``. We pluck it out and
        reuse the same field-mapping as the search API.
        """
        import re
        import json as _json
        self._ensure_pw()
        page = self._pw_ctx.new_page()
        try:
            page.goto(
                f"{WEB_ROOT}/mieten/{listing_id}",
                wait_until="networkidle",
                timeout=60000,
            )
            page.wait_for_timeout(2000)
            body = page.content()
        finally:
            page.close()
        m = re.search(
            r"window\.__PINIA_INITIAL_STATE__\s*=\s*(.+?)</script>",
            body, re.S,
        )
        if not m:
            logger.warning("Homegate: __PINIA_INITIAL_STATE__ not found.")
            return None
        try:
            state = _json.loads(m.group(1).rstrip().rstrip(";"))
        except Exception as exc:
            logger.warning("Homegate: pinia parse failed: %s", exc)
            return None
        listing_block = state.get("listing") or {}
        listing = listing_block.get("listing")
        if not listing:
            return None
        # Wrap to match the search-API result shape so fetch_full_detail
        # can reuse the same mapping logic.
        return {
            "id": str(listing.get("id") or listing_id),
            "listing": listing,
            "listingType": listing_block.get("listingType") or {},
            "listerBranding": state.get("listerBranding") or {},
            "agencyAgent": (listing.get("lister") or {}).get("contacts", {}).get("inquiry") or {},
        }

    def close(self):
        try:
            if self._pw_ctx:
                try:
                    self._pw_ctx.close()
                except Exception:
                    pass
            if getattr(self, "_using_camoufox", False) and getattr(self, "_cm", None):
                try:
                    self._cm.__exit__(None, None, None)
                except Exception:
                    pass
            else:
                if self._pw_browser:
                    try:
                        self._pw_browser.close()
                    except Exception:
                        pass
                if self._pw:
                    try:
                        self._pw.stop()
                    except Exception:
                        pass
        finally:
            self._pw = self._pw_browser = self._pw_ctx = None
            self._cm = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _post_json(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = urljoin(API_ROOT, path)
        if self.transport == "playwright":
            self._ensure_pw()
            import time as _time

            def _do() -> "tuple[int, str]":
                t0 = _time.monotonic()
                r = self._pw_ctx.request.post(
                    url,
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=self.timeout * 1000,
                )
                out = r.status, r.text()
                self._t(f"api_post {path}", t0)
                return out

            status, text = _do()
            if status >= 400 or "captcha-delivery.com" in text:
                # Cookie likely stale — re-bootstrap once and retry.
                logger.info("Homegate: stale cookie (%s); re-bootstrapping...", status)
                self._bootstrap_datadome()
                status, text = _do()
                if status >= 400 or "captcha-delivery.com" in text:
                    raise HomegateBotChallenge(
                        "DataDome blocked even after a fresh bootstrap. The "
                        "headless fingerprint may be flagged for this IP. "
                        f"Last status={status}."
                    )
            import json as _json
            return _json.loads(text)
        # requests fallback
        r = self.session.post(url, json=payload, timeout=self.timeout)
        if r.status_code != 200 or "captcha-delivery.com" in r.text:
            raise HomegateBotChallenge(
                "DataDome challenge triggered. Refresh the datadome cookie."
            )
        return r.json()

    # -- search -------------------------------------------------------------

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
        return self._post_json(SEARCH_ENDPOINT, payload)

    def search(self, criteria: dict[str, Any], limit: int | None = None,
               page_size: int = DEFAULT_PAGE_SIZE,
               sort_by: str = "monthlyRent",
               sort_direction: str = "asc") -> list[dict[str, Any]]:
        """Iterate the search endpoint until ``limit`` or ``maxFrom`` reached.

        ``criteria`` is the homegate-shaped query dict (see module docstring).
        """
        results: list[dict[str, Any]] = []
        from_ = 0
        while True:
            page = self.search_page(
                criteria, from_=from_, size=page_size,
                sort_by=sort_by, sort_direction=sort_direction,
            )
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
        """Fetch one listing by ID.

        Goes through the SSR detail page (``/mieten/{id}``) because the
        search endpoint silently ignores ``query.ids`` filters and the
        ``GET /listing/listings/{id}`` endpoint is gated to internal
        callers.
        """
        return self._fetch_listing_via_page(str(listing_id))

    @staticmethod
    def _extract_id(value: int | str) -> str | None:
        s = str(value).strip()
        if s.isdigit():
            return s
        import re
        m = re.search(r"/(\d{6,})(?:/|\?|$)", s)
        return m.group(1) if m else None

    def fetch_full_detail(self, url_or_id: int | str) -> dict[str, Any] | None:
        """Return a normalized detail dict matching the cross-provider shape."""
        listing_id = self._extract_id(url_or_id)
        if not listing_id:
            return None
        item = self.fetch_detail(listing_id)
        if not item:
            return None
        return _homegate_to_normalized(item, fallback_id=listing_id)

    def search_listings(
        self,
        criteria: "SearchCriteria",
        llm: bool = True,
        llm_concurrency: int = 4,
        tax: bool = True,
        commute: bool = False,
        google_maps_key: str | None = None,
    ) -> list["Listing"]:
        """Run a search using the cross-provider ``SearchCriteria``.

        ``llm=True`` (default) post-enriches matched listings via Haiku
        — fills ``bedrooms`` and ``has_washing_machine`` from descriptions
        in parallel (capped at ``llm_concurrency``). Pass ``llm=False`` to
        skip the LLM call entirely.
        """
        from .common import Listing, SearchCriteria, apply_common_filters
        assert isinstance(criteria, SearchCriteria)

        q: dict[str, Any] = {}
        if criteria.offer_type:
            q["offerType"] = criteria.offer_type
        if criteria.categories:
            q["categories"] = list(criteria.categories)
        # Geo: prefer zipcodes (precise), then cities, then nothing (whole CH).
        geo_tags: list[str] = []
        for z in criteria.zipcodes:
            geo_tags.append(f"geo-zipcode-{z}")
        for c in criteria.cities:
            slug = c.strip().lower().replace(" ", "-").replace("ü", "u").replace("ö", "o").replace("ä", "a")
            geo_tags.append(f"geo-city-{slug}")
        if geo_tags:
            q["location"] = {"geoTags": geo_tags}

        if criteria.min_rooms is not None or criteria.max_rooms is not None:
            r: dict[str, float] = {}
            if criteria.min_rooms is not None:
                r["from"] = criteria.min_rooms
            if criteria.max_rooms is not None:
                r["to"] = criteria.max_rooms
            q["numberOfRooms"] = r
        if criteria.min_price_chf is not None or criteria.max_price_chf is not None:
            p: dict[str, int] = {}
            if criteria.min_price_chf is not None:
                p["from"] = criteria.min_price_chf
            if criteria.max_price_chf is not None:
                p["to"] = criteria.max_price_chf
            q["monthlyRent" if criteria.offer_type == "RENT" else "purchasePrice"] = p
        if criteria.min_surface_m2 is not None or criteria.max_surface_m2 is not None:
            s: dict[str, int] = {}
            if criteria.min_surface_m2 is not None:
                s["from"] = criteria.min_surface_m2
            if criteria.max_surface_m2 is not None:
                s["to"] = criteria.max_surface_m2
            q["livingSpace"] = s
        # Push canonical features into homegate's native top-level booleans.
        # Anything not in this map falls through to the client-side filter.
        feature_to_native = {
            "balcony": "hasBalcony", "parking": "hasParking",
            "elevator": "hasElevator", "garage": "hasGarage",
            "garden": "hasGarden", "fireplace": "hasFireplace",
            "cellar": "hasCellar", "attic": "hasAttic",
            "view": "hasNiceView", "swimming_pool": "hasSwimmingPool",
            "wheelchair_accessible": "isWheelchairAccessible",
            "child_friendly": "isChildFriendly",
            "cats_allowed": "isCatsAllowed", "dogs_allowed": "isDogsAllowed",
            "minergie": "isMinergieGeneral",
            "new_building": "isNewBuilding", "old_building": "isOldBuilding",
        }
        for f in criteria.must_features:
            if f in feature_to_native:
                q[feature_to_native[f]] = True

        # Sort. Default = monthlyRent asc (cheapest first); polling
        # watchers want newest first (dateCreated desc) — pulls fresh
        # listings to the top.
        sort_by = "dateCreated" if criteria.sort_by_newest else "monthlyRent"
        sort_direction = "desc" if criteria.sort_by_newest else "asc"
        # Right-size the homegate page so we don't paginate when the caller
        # only wants a few results. Homegate caps ``size`` at ~100; we add
        # a buffer for client-side filtering attrition.
        if criteria.limit is not None:
            page_size = min(max(int(criteria.limit * 3), 10), 100, criteria.page_cap)
            # Also cap how many raw rows we fetch — no point paging deeper
            # than ~5x the limit. Common filters can drop a lot but if a
            # search yields nothing in 5x of the desired count, going
            # further usually doesn't help.
            raw_cap = min(criteria.limit * 5, criteria.page_cap)
        else:
            page_size = min(criteria.page_cap, 100)
            raw_cap = criteria.page_cap
        raw = self.search(
            q, limit=raw_cap, page_size=page_size,
            sort_by=sort_by, sort_direction=sort_direction,
        )
        listings = [
            Listing.from_dict(_homegate_to_normalized(item))
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


def _homegate_to_normalized(item: dict[str, Any], fallback_id: str | None = None) -> dict[str, Any]:
    """Map a homegate search-result / Pinia-detail row to the common shape."""
    listing = item.get("listing") or {}
    listing_id = str(listing.get("id") or fallback_id or item.get("id") or "")

    # Localized text — prefer primary, then de/en/fr/it.
    loc = listing.get("localization") or {}
    primary = loc.get("primary")
    title = description = situation = None
    attachments: list[dict[str, Any]] = []
    for lang in [primary, "de", "en", "fr", "it"]:
        if not lang:
            continue
        block = loc.get(lang) or {}
        text = block.get("text") or {}
        if title is None and text.get("title"):
            title = text.get("title")
        if description is None and text.get("description"):
            description = text.get("description")
        if situation is None and text.get("situation"):
            situation = text.get("situation")
        if not attachments:
            attachments = block.get("attachments") or []
    title = title or ""
    description = description or ""

    addr = listing.get("address") or {}
    coords = addr.get("geoCoordinates") or {}
    chars = listing.get("characteristics") or {}
    prices = listing.get("prices") or {}
    rent = prices.get("rent") or {}
    buy = prices.get("buy") or {}
    price = rent.get("gross") or rent.get("net") or buy.get("price")
    currency = prices.get("currency") or "CHF"

    images: list[str] = []
    for a in attachments:
        if a.get("type") == "IMAGE" and a.get("url"):
            images.append(a["url"])

    # Auto-collect every truthy boolean characteristic — homegate exposes
    # 55+ keys (hasBalcony, isQuiet, hasMountainView, …). Future-proof
    # against new ones; FEATURE_MAP covers their canonicalization.
    attributes = sorted(
        k for k, v in chars.items()
        if isinstance(v, bool) and v
    )

    agency_brand = item.get("listerBranding") or {}
    agent = item.get("agencyAgent") or {}
    agency = {
        "name": " ".join(x for x in [agent.get("firstName"), agent.get("lastName")] if x)
                or agent.get("email") or "",
        "phone": agent.get("phoneNumber") or agent.get("mobileNumber") or "",
        "email": agent.get("email") or "",
        "logo_url": agency_brand.get("logoUrl") or "",
        "subscription_type": agency_brand.get("subscriptionType") or "",
    }

    meta = listing.get("meta") or {}
    avail = meta.get("availableFrom") or meta.get("createdAt")

    return {
        "provider": "homegate",
        "listing_id": str(listing.get("id") or listing_id),
        "url": f"{WEB_ROOT}/mieten/{listing_id}",
        "title": title,
        "description": description,
        "situation": situation,
        "price_chf": float(price) if price is not None else None,
        "rent_net_chf": float(rent.get("net")) if rent.get("net") is not None else None,
        "rent_charges_chf": float(rent.get("expenses")) if rent.get("expenses") is not None else None,
        "currency": currency,
        "rooms": float(chars["numberOfRooms"]) if chars.get("numberOfRooms") is not None else None,
        "bedrooms": chars.get("numberOfBedrooms"),
        "bathrooms": chars.get("numberOfBathrooms"),
        "surface_living_m2": chars.get("livingSpace") or chars.get("totalFloorSpace"),
        "surface_usable_m2": chars.get("usableSpace") or chars.get("totalFloorSpace"),
        "surface_property_m2": chars.get("lotSize"),
        "floor": chars.get("floor"),
        "year_built": chars.get("yearBuilt"),
        "year_renovated": chars.get("yearRenovated"),
        "available_from": avail,
        "available_from_type": "dat" if (avail and "T" in str(avail)) else None,
        "is_furnished": chars.get("isFurnished"),
        "is_temporary": chars.get("isTemporary"),
        "object_category": (listing.get("categories") or [None])[0],
        "object_type": (listing.get("categories") or [None, None])[-1],
        "offer_type": listing.get("offerType"),
        "address": {
            "street": addr.get("street"),
            "zipcode": addr.get("postalCode"),
            "city": addr.get("locality"),
            "country": addr.get("country") or "CH",
            "public": ", ".join(x for x in [
                addr.get("street"),
                f"{addr.get('postalCode','')} {addr.get('locality','')}".strip(),
            ] if x and x.strip()),
            "lat": coords.get("latitude"),
            "lon": coords.get("longitude"),
        },
        "images": images,
        "attributes": attributes,
        "agency": agency,
        "raw": item,
    }


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
