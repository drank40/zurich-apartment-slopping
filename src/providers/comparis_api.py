"""Comparis.ch JSON-from-SSR client.

Comparis runs Next.js with server-side rendering. The list page embeds the
full search response in ``<script id="__NEXT_DATA__">``. The Next.js runtime
also exposes a JSON endpoint at ``_next/data/<buildId>/...`` that returns
the same payload. Both are gated by DataDome which fingerprints the client;
plain ``httpx`` requests are blocked even with cookies copied from a
Playwright session, so we keep Playwright as the transport layer and treat
``__NEXT_DATA__`` as our "API".

The detail page (``/immobilien/marktplatz/details/show/<id>``) is also a
Next.js SSR page; its ``__NEXT_DATA__`` contains the full ad detail.

Usage::

    client = ComparisClient(headless=False, cookies_file="cookies_comparis.txt")
    client.start()  # spins up Playwright
    listings = client.search(criteria)
    for l in listings:
        client.fetch_detail(l['AdId'])
    client.close()
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlencode

logger = logging.getLogger(__name__)

WEB_ROOT = "https://www.comparis.ch"
SEARCH_PATH = "/immobilien/result/list"
DETAIL_PATH = "/immobilien/marktplatz/details/show/"
DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def _parse_cookie_string(cookie_str: str, domain: str = ".comparis.ch") -> list[dict[str, Any]]:
    out = []
    for part in cookie_str.strip().split(";"):
        if "=" not in part:
            continue
        name, val = part.strip().split("=", 1)
        out.append({"name": name.strip(), "value": val.strip(), "domain": domain, "path": "/"})
    return out


class ComparisClient:
    """Playwright-backed client that extracts JSON payloads from Next.js."""

    def __init__(
        self,
        headless: bool = False,
        cookies_file: str | None = None,
        cookies: list[dict[str, Any]] | None = None,
        user_agent: str = DEFAULT_UA,
        manual_continue: bool = False,
        challenge_wait_s: float = 30.0,
        rate_delay: float = 1.0,
        page_load_timeout_ms: int = 60000,
    ):
        self.headless = headless
        self.cookies_file = cookies_file
        self.cookies = list(cookies or [])
        self.user_agent = user_agent
        self.manual_continue = manual_continue
        self.challenge_wait_s = challenge_wait_s
        self.rate_delay = rate_delay
        self.page_load_timeout_ms = page_load_timeout_ms

        self._pw = None
        self._browser = None
        self._context = None

    # -- lifecycle ----------------------------------------------------------

    def start(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=self.headless)
        self._context = self._browser.new_context(
            user_agent=self.user_agent,
            viewport={"width": 1440, "height": 900},
            locale="de-CH",
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        cookies = list(self.cookies)
        if self.cookies_file and Path(self.cookies_file).exists():
            content = Path(self.cookies_file).read_text(encoding="utf-8")
            cookies.extend(_parse_cookie_string(content))
        if cookies:
            try:
                self._context.add_cookies(cookies)
            except Exception as e:
                logger.warning(f"Cookie load warning: {e}")
        return self

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self._context = self._browser = self._pw = None

    # -- low-level page fetch ----------------------------------------------

    def _fetch_html(self, url: str) -> str:
        if not self._context:
            raise RuntimeError("ComparisClient.start() must be called first")
        page = self._context.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.page_load_timeout_ms)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(2000)
            html = page.content()
            if self._is_challenge(html):
                logger.warning(f"Bot challenge on {url}; waiting...")
                page.wait_for_timeout(int(self.challenge_wait_s * 1000))
                if self.manual_continue and not self.headless:
                    try:
                        input("Solve the challenge in the browser, then press Enter here...")
                    except EOFError:
                        pass
                html = page.content()
            return html
        finally:
            page.close()

    @staticmethod
    def _is_challenge(html: str) -> bool:
        s = html.lower()
        if "captcha-delivery" in s or "datadome" in s and "captcha" in s:
            return "__next_data__" not in s
        return False

    # -- API ----------------------------------------------------------------

    @staticmethod
    def _extract_next_data(html: str) -> dict[str, Any] | None:
        m = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL
        )
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None

    def search(self, criteria: dict[str, Any], limit: int | None = None) -> list[dict[str, Any]]:
        """Run a search, return the list of result item dicts (raw comparis shape).

        ``criteria`` is the legacy ``request_object`` dict (see config.yaml).
        """
        params = {"sort": criteria.get("Sort", 11), "requestobject": json.dumps(criteria)}
        url = f"{WEB_ROOT}{SEARCH_PATH}?{urlencode(params)}"
        html = self._fetch_html(url)
        data = self._extract_next_data(html)
        if not data:
            logger.warning("Comparis: no __NEXT_DATA__ in HTML")
            return []
        res = (
            data.get("props", {})
            .get("pageProps", {})
            .get("initialResultData", {})
        )
        items = res.get("resultItems", []) or []
        # Some IDs only appear in adIds without a full item — expose those too
        ad_ids = res.get("adIds", []) or []
        seen = {str(it.get("AdId")) for it in items if it.get("AdId")}
        for ad_id in ad_ids:
            if str(ad_id) not in seen:
                items.append({"AdId": ad_id, "_partial": True})
        if limit:
            items = items[:limit]
        return items

    def fetch_detail(self, ad_id: str | int) -> dict[str, Any] | None:
        """Fetch the detail page for a single ad and return its NEXT_DATA props."""
        url = f"{WEB_ROOT}{DETAIL_PATH}{ad_id}"
        html = self._fetch_html(url)
        data = self._extract_next_data(html)
        if not data:
            return None
        page_props = data.get("props", {}).get("pageProps", {})
        # Common shapes
        for k in ("ad", "advertisement", "details", "initialDetailData", "result"):
            if k in page_props and page_props[k]:
                return page_props[k]
        return page_props or None


# ----------------------------------------------------------------------------
# Mapping helpers
# ----------------------------------------------------------------------------

def _parse_price(raw: Any) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^0-9.,]", "", str(raw))
    if not s:
        return None
    s = s.replace("'", "")
    if "," in s and "." in s:
        if s.find(",") < s.find("."):
            s = s.replace(",", "")
        else:
            s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".") if len(s.split(",")[-1]) == 2 else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def mapping_to_listing(item: dict[str, Any], listing_cls):
    ad_id = item.get("AdId")
    url = urljoin(WEB_ROOT, f"{DETAIL_PATH}{ad_id}")
    title = item.get("Title") or item.get("Header") or f"Comparis Listing {ad_id}"
    addr_parts = item.get("Address", [])
    if isinstance(addr_parts, list):
        address = ", ".join(str(x) for x in addr_parts if x)
    else:
        address = str(addr_parts or "")

    price = _parse_price(item.get("PriceValue") or item.get("Price"))
    rooms = None
    for info in item.get("EssentialInformation", []) or []:
        m = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:Zimmer|rooms|stanze|pi[eè]ces)", str(info), re.I)
        if m:
            rooms = float(m.group(1).replace(",", "."))
            break
    if rooms is None and "Rooms" in item:
        try:
            rooms = float(item["Rooms"])
        except Exception:
            pass

    description = " ".join(str(x) for x in item.get("EssentialInformation", []) or []) or title
    lat = item.get("Latitude") or item.get("Lat")
    lon = item.get("Longitude") or item.get("Lng") or item.get("Lon")

    return listing_cls(
        provider="comparis",
        listing_id=str(ad_id),
        title=title,
        url=url,
        contact_url=url,
        price_chf=price,
        total_rooms=rooms,
        bedrooms=None,
        address=address,
        description=description,
        lat=float(lat) if lat else None,
        lon=float(lon) if lon else None,
        raw=item,
    )
