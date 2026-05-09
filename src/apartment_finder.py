import argparse
import json
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urljoin

import requests
import yaml
from bs4 import BeautifulSoup
from dateutil import parser as dt_parser
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("apartment_finder.log", encoding="utf-8")],
)
logger = logging.getLogger(__name__)

MONEY_REGEX = re.compile(r"(\d[\d'’‘,. ]*)")
DATE_REGEX = re.compile(r"(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})")
BEDROOM_REGEX = re.compile(
    r"(?:(?<!\.)(\d+)\s*(?:bedroom|bedrooms|schlafzimmer|chambre|camera|camere|stanze da letto))",
    re.IGNORECASE,
)
TOTAL_ROOMS_REGEX = re.compile(
    r"(?:(\d+(?:[.,]\d+)?)\s*(?:rooms|zimmer|pi[eè]ces|locali|stanze))",
    re.IGNORECASE,
)

NEGATIVE_SHARED = [
    "wg", "wohngemeinschaft", "shared", "roommate", "colocation",
    "stanza in appartamento", "sublet room", "single room", "chambre dans",
    "condiviso", "coabitazione", "mitbewohner",
]

KEYWORDS = {
    "furnished": ["furnished", "möbliert", "mobilato", "ameubl", "arredato", "completo di mobili"],
    "kitchen": ["kitchen", "küche", "cucina", "cuisine", "angolo cottura", "wohnküche"],
    "bathroom": ["bathroom", "badzimmer", "bad", "bagno", "salle de bain", "wc", "doccia"],
    "living": ["living room", "wohnzimmer", "salotto", "soggiorno", "séjour", "area giorno"],
    "sofa": ["sofa", "couch", "canape", "divano", "poltrona"],
    "washing_machine": ["washing machine", "waschmaschine", "lavatrice", "lave-linge", "waschturm"],
    "dishwasher": ["dishwasher", "geschirrspüler", "lavastoviglie", "lave-vaisselle", "spülmaschine"],
}

OFFICE_LAT, OFFICE_LON = 47.3781, 8.5342


@dataclass
class Listing:
    provider: str
    listing_id: str
    title: str
    url: str
    contact_url: str
    price_chf: Optional[float] = None
    bedrooms: Optional[float] = None
    total_rooms: Optional[float] = None
    available_from: Optional[date] = None
    furnished: Optional[bool] = None
    has_kitchen: Optional[bool] = None
    has_bathroom: Optional[bool] = None
    has_living_room: Optional[bool] = None
    has_sofa: Optional[bool] = None
    has_washing_machine: Optional[bool] = None
    has_dishwasher: Optional[bool] = None
    likely_shared: Optional[bool] = None
    address: Optional[str] = None
    description: str = ""
    municipal_tax: int = 119
    lat: Optional[float] = None
    lon: Optional[float] = None
    distance_km: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    exclusion_reasons: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.exclusion_reasons

    def to_string(self) -> str:
        price = f"CHF {self.price_chf:,.0f}".replace(",", "'") if self.price_chf else "Unknown"
        dist = f"{self.distance_km:.2f} km" if self.distance_km is not None else "Unknown"
        lines = [f"## {self.title}"]
        if self.exclusion_reasons:
            lines.append(f"- **EXCLUDED**: **{', '.join(self.exclusion_reasons)}**")
        lines += [
            f"- **Price**: {price}",
            f"- **Distance to Office**: {dist}",
            f"- **Bedrooms**: {self.bedrooms or 'n/a'} (Total rooms: {self.total_rooms or 'n/a'})",
            f"- **Available**: {self.available_from or 'Unknown'}",
            f"- **Furnished**: {self.furnished}",
            f"- **Address**: {self.address or 'See listing'}",
            f"- **Features**: Kitchen: {self.has_kitchen}, Living: {self.has_living_room}, Sofa: {self.has_sofa}",
            f"- **Optional**: Wash: {self.has_washing_machine}, Dish: {self.has_dishwasher}",
            f"- [View Listing]({self.url})",
            f"- [**Contact with 1 Click**]({self.contact_url})",
            "",
        ]
        return "\n".join(lines)


def haversine(lat1, lon1, lat2, lon2) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def extract_coords(html: str) -> Tuple[Optional[float], Optional[float]]:
    m = re.search(r'north=([\d.]+)&amp;east=([\d.]+)&amp;south=([\d.]+)&amp;west=([\d.]+)', html)
    if m:
        n, e, s, w = map(float, m.groups())
        return (n + s) / 2, (e + w) / 2
    m = re.search(r'"latitude":\s*([\d.]+),"longitude":\s*([\d.]+)', html)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r'lat[:=]\s*([\d.]+),?\s*lon[:=]\s*([\d.]+)', html, re.I)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def load_config(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def normalize_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_price(raw: Any) -> Optional[float]:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw)
    context_found = False
    if len(text) > 50:
        m = re.search(r"(?:CHF|affitto|rent|prezzo|preis|gross|net)\s*[:\-\s]*([\d'’‘,. ]{3,})", text, re.I)
        if m:
            text = m.group(1)
            context_found = True
    elif "CHF" in text.upper():
        context_found = True

    m = MONEY_REGEX.search(text)
    if not m:
        return None
    raw_val = m.group(1).strip()
    if not raw_val:
        return None
    cleaned = re.sub(r"['’‘\s]", "", raw_val)

    if "." in cleaned and "," in cleaned:
        if cleaned.find(".") < cleaned.find(","):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", "." if len(cleaned.split(",")[-1]) == 2 else "")
    elif "." in cleaned and len(cleaned.split(".")[-1]) != 2:
        cleaned = cleaned.replace(".", "")

    try:
        val = float(cleaned)
        if 8000 <= val <= 8999 and not context_found:
            return None
        return val
    except ValueError:
        return None


def parse_date(raw: Any) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, date):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return dt_parser.parse(text, dayfirst=True, fuzzy=True).date()
    except Exception:
        m = DATE_REGEX.search(text)
        if not m:
            return None
        try:
            return dt_parser.parse(m.group(1), dayfirst=True).date()
        except Exception:
            return None


def infer_bool_from_text(text: str, category: str) -> Optional[bool]:
    return True if any(k in text.lower() for k in KEYWORDS.get(category, [])) else None


def infer_likely_shared(text: str) -> Optional[bool]:
    return True if any(k in text.lower() for k in NEGATIVE_SHARED) else None


def infer_bedrooms(text: str) -> Tuple[Optional[float], Optional[float]]:
    m = BEDROOM_REGEX.search(text)
    bedrooms = float(m.group(1).replace(",", ".")) if m else None
    m2 = TOTAL_ROOMS_REGEX.search(text)
    total_rooms = float(m2.group(1).replace(",", ".")) if m2 else None
    return bedrooms, total_rooms


def to_absolute(base_url: str, maybe_url: Optional[str]) -> str:
    return urljoin(base_url, maybe_url) if maybe_url else base_url


def is_challenge_html(html: str) -> bool:
    s = html.lower()
    indicators = ["cloudflare", "cf-chl", "captcha", "hcaptcha", "recaptcha",
                  "turnstile", "verify you are human", "attention required",
                  "access denied", "security check"]
    if not any(k in s for k in indicators):
        return False
    cues = ["price_display", "number_of_rooms", "is_furnished", "chf", "rooms"]
    return not any(c in s for c in cues)


def fetch_with_playwright(search_url: str, cfg: Dict[str, Any]) -> Optional[str]:
    if not cfg.get("enabled", False) or sync_playwright is None:
        return None
    headless = bool(cfg.get("headless", True))
    wait_after = float(cfg.get("wait_after_load_seconds", 6))
    challenge_wait = float(cfg.get("challenge_wait_seconds", 20))
    manual = bool(cfg.get("manual_continue", False))
    cookies = cfg.get("cookies") or []
    dump = cfg.get("dump_html_path")

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            ctx = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
            if cookies:
                ctx.add_cookies(cookies)
            page = ctx.new_page()
            page.goto(search_url, wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(int(wait_after * 1000))
            html = page.content()
            if is_challenge_html(html):
                logger.warning("Bot challenge detected, waiting...")
                page.wait_for_timeout(int(challenge_wait * 1000))
                if manual and not headless:
                    input("Solve challenge, then press Enter...")
                html = page.content()
            if dump:
                Path(dump).parent.mkdir(parents=True, exist_ok=True)
                Path(dump).write_text(html, encoding="utf-8")
            browser.close()
            return html
    except Exception as e:
        logger.error(f"Playwright error: {e}")
        return None


def parse_listings_from_html(base_url: str, html: str) -> List[Listing]:
    listings: List[Listing] = []
    soup = BeautifulSoup(html, "html.parser")

    for script in soup.select('script[type="application/ld+json"]'):
        try:
            doc = json.loads(script.get_text(strip=True))
            for node in (doc if isinstance(doc, list) else [doc]):
                if isinstance(node, dict) and ("Product" in str(node.get("@type")) or "Accommodation" in str(node.get("@type"))):
                    l = build_listing_from_ld_json("flatfox", base_url, node)
                    if l:
                        listings.append(l)
        except Exception:
            pass

    for thumb in soup.select(".listing-thumb"):
        link = thumb.select_one("a.listing-thumb__image, a.listing-thumb-title")
        if not link or not link.get("href"):
            continue
        url = to_absolute(base_url, link.get("href"))
        title_tag = thumb.select_one(".listing-thumb-title")
        title = normalize_spaces(title_tag.get_text()) if title_tag else "Untitled"
        price_tag = thumb.select_one(".price")
        price = parse_price(price_tag.get_text()) if price_tag else None
        loc_tag = thumb.select_one(".listing-thumb-title__location")
        address = normalize_spaces(loc_tag.get_text()) if loc_tag else ""
        attrs_tag = thumb.select_one(".attributes")
        attrs_text = normalize_spaces(attrs_tag.get_text(" ")) if attrs_tag else ""

        listings.append(Listing(
            provider="flatfox",
            listing_id=str(abs(hash(url))),
            title=title,
            url=url,
            contact_url=url,
            price_chf=price,
            furnished=infer_bool_from_text(attrs_text + " " + title, "furnished"),
            likely_shared=infer_likely_shared(attrs_text + " " + title),
            address=address,
            description=attrs_text,
        ))

    if not listings:
        for a in soup.select('a[href*="/flat/"], a[href*="/listing/"], a[href*="/rent/"]'):
            href = a.get("href")
            if not href or len(href) < 10:
                continue
            url = to_absolute(base_url, href)
            listings.append(Listing(
                provider="flatfox", listing_id=str(abs(hash(url))),
                title=normalize_spaces(a.get_text()) or "Listing", url=url, contact_url=url,
            ))

    return dedupe_listings(listings)


def build_listing_from_ld_json(provider: str, base_url: str, node: Dict[str, Any]) -> Optional[Listing]:
    url = to_absolute(base_url, node.get("url"))
    if url == base_url:
        return None
    title = normalize_spaces(node.get("name", "Untitled"))
    desc = normalize_spaces(node.get("description", ""))
    full = f"{title} {desc}"
    price = None
    offers = node.get("offers")
    if isinstance(offers, dict):
        price = parse_price(offers.get("price"))
    bed, tot = infer_bedrooms(full)

    return Listing(
        provider=provider,
        listing_id=str(node.get("@id") or abs(hash(url))),
        title=title, url=url, contact_url=url,
        price_chf=price, bedrooms=bed, total_rooms=tot,
        available_from=parse_date(full),
        furnished=infer_bool_from_text(full, "furnished"),
        has_kitchen=infer_bool_from_text(full, "kitchen"),
        has_bathroom=infer_bool_from_text(full, "bathroom"),
        has_living_room=infer_bool_from_text(full, "living"),
        has_sofa=infer_bool_from_text(full, "sofa"),
        has_washing_machine=infer_bool_from_text(full, "washing_machine"),
        has_dishwasher=infer_bool_from_text(full, "dishwasher"),
        likely_shared=infer_likely_shared(full),
        address=normalize_spaces(str(node.get("address", ""))),
        description=desc, raw=node,
    )


def dedupe_listings(listings: List[Listing]) -> List[Listing]:
    seen: Dict[str, Listing] = {}
    for l in listings:
        seen.setdefault(l.url, l)
    return list(seen.values())


def _find_homegate_results(node: Any) -> List[Dict[str, Any]]:
    if isinstance(node, list):
        if node and all(isinstance(x, dict) for x in node):
            keys = set().union(*(set(x.keys()) for x in node if isinstance(x, dict)))
            if any(k in keys for k in ("id", "listingId", "slug")):
                return node
        for item in node:
            found = _find_homegate_results(item)
            if found:
                return found
    elif isinstance(node, dict):
        for key in ("results", "items", "listings", "hits"):
            v = node.get(key)
            if isinstance(v, list):
                found = _find_homegate_results(v)
                if found:
                    return found
        for v in node.values():
            found = _find_homegate_results(v)
            if found:
                return found
    return []


FIELD_MAP = {
    "furnished": "furnished", "sofa": "has_sofa", "living": "has_living_room",
    "kitchen": "has_kitchen", "bathroom": "has_bathroom",
    "washing_machine": "has_washing_machine", "dishwasher": "has_dishwasher",
}


def hydrate_one(l: Listing, timeout: int) -> None:
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    r = requests.get(l.url, headers=headers, timeout=timeout)
    if r.status_code >= 400:
        l.warnings.append(f"http_{r.status_code}")
        return
    soup = BeautifulSoup(r.text, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    text = normalize_spaces(soup.get_text(" ", strip=True))
    l.description = text[:5000]
    l.price_chf = l.price_chf or parse_price(text)
    l.available_from = l.available_from or parse_date(text)
    b, t = infer_bedrooms(text)
    l.bedrooms = l.bedrooms or b
    l.total_rooms = l.total_rooms or t
    l.lat, l.lon = extract_coords(r.text)
    if l.lat and l.lon:
        l.distance_km = haversine(l.lat, l.lon, OFFICE_LAT, OFFICE_LON)
    for cat, fname in FIELD_MAP.items():
        if getattr(l, fname) is None:
            setattr(l, fname, infer_bool_from_text(text, cat))
    if l.likely_shared is None:
        l.likely_shared = infer_likely_shared(text)
    btn = soup.select_one('a[href*="/contact"], a[href*="mailto:"], button[data-href*="contact"]')
    if btn:
        l.contact_url = to_absolute(l.url, btn.get("href") or btn.get("data-href"))


def evaluate_filters(l: Listing, criteria: Dict[str, Any]) -> None:
    include_unknown = bool(criteria.get("include_unknowns_to_avoid_false_negatives", True))
    reasons: List[str] = []

    max_price = criteria.get("max_price")
    if max_price and l.price_chf and l.price_chf > float(max_price):
        reasons.append(f"Price CHF {l.price_chf} > {max_price}")
    elif max_price and not include_unknown and l.price_chf is None:
        reasons.append("Price unknown")

    target = parse_date(criteria.get("available_on_or_before"))
    if target and l.available_from and l.available_from > target:
        reasons.append(f"Available {l.available_from} > {target}")
    elif target and not include_unknown and not l.available_from:
        reasons.append("Available date unknown")

    min_bed = float(criteria.get("min_bedrooms", 2))
    bed = l.bedrooms if l.bedrooms is not None else (max(1.0, l.total_rooms - 1.0) if l.total_rooms else None)
    if bed is not None and bed < min_bed:
        reasons.append(f"Bedrooms {bed} < {min_bed}")
    elif bed is None and not include_unknown:
        reasons.append("Bedrooms unknown")

    flag_filters = [
        ("must_be_furnished", "furnished", "Not furnished", False),
        ("must_have_private_entire_place", "likely_shared", "Likely shared/WG", True),
        ("must_have_kitchen", "has_kitchen", "No kitchen", False),
        ("must_have_bathroom", "has_bathroom", "No bathroom", False),
        ("must_have_living_room", "has_living_room", "No living room", False),
        ("must_have_sofa", "has_sofa", "No sofa", False),
    ]
    for key, fname, msg, negate in flag_filters:
        if not criteria.get(key, False):
            continue
        val = getattr(l, fname)
        actual = (not val) if negate and val is not None else val
        if actual is False:
            reasons.append(msg)
        elif val is None and not include_unknown:
            reasons.append(f"{msg} (unknown)")

    l.exclusion_reasons = reasons


def parse_listings_from_html_homegate(base_url: str, html: str) -> List[Listing]:
    listings: List[Listing] = []
    soup = BeautifulSoup(html, "html.parser")
    nd = soup.select_one('script#__NEXT_DATA__')
    if nd:
        try:
            data = json.loads(nd.get_text())
            items = data.get("props", {}).get("pageProps", {}).get("initialState", {}).get("search", {}).get("results", [])
            if not items:
                items = _find_homegate_results(data)
            for item in items:
                id_ = item.get("id") or item.get("listingId")
                if not id_:
                    continue
                detail = item.get("detailUrl") or item.get("url")
                url = to_absolute(base_url, detail) if detail else urljoin(base_url, f"/rent/{id_}")
                title = item.get("title", "Homegate Listing")
                listings.append(Listing(
                    provider="homegate", listing_id=str(id_),
                    title=title, url=url, contact_url=url,
                    price_chf=parse_price(item.get("price")),
                    total_rooms=float(item["rooms"]) if item.get("rooms") else None,
                    address=f"{item.get('street', '')}, {item.get('zip', '')} {item.get('city', '')}".strip(", "),
                    description=title,
                ))
            if listings:
                return dedupe_listings(listings)
        except Exception as e:
            logger.debug(f"NEXT_DATA parse failed: {e}")

    for a in soup.select('a[href*="/rent/"], a[href*="/mieten/"]'):
        href = a.get("href")
        if not href or len(href) < 15:
            continue
        if any(x in href for x in ["/rent/real-estate", "/mieten/immobilien", "city-zurich", "/matching-list"]):
            continue
        url = to_absolute(base_url, href)
        title = normalize_spaces(a.get_text(" ", strip=True)) or "Homegate Listing"
        listings.append(Listing(
            provider="homegate", listing_id=str(abs(hash(url))),
            title=title, url=url, contact_url=url, description=title,
        ))
    return dedupe_listings(listings)


PARSERS = {
    "flatfox": parse_listings_from_html,
    "homegate": parse_listings_from_html_homegate,
}


def fetch_search_html(search_url: str, p_cfg: Dict[str, Any], headers: Dict[str, str]) -> Optional[str]:
    try:
        r = requests.get(
            search_url,
            timeout=p_cfg.get("request_timeout_seconds", 20),
            headers=headers,
            cookies=p_cfg.get("cookies", {}),
        )
        if not is_challenge_html(r.text):
            return r.text
        logger.info("Direct blocked, trying Playwright...")
    except Exception as e:
        logger.warning(f"Direct request failed: {e}")
    if p_cfg.get("use_playwright_fallback"):
        return fetch_with_playwright(search_url, p_cfg.get("playwright", {}))
    return None


def run(config_path: Path, providers_override: Optional[List[str]] = None):
    cfg = load_config(config_path)
    search_cfg = cfg.get("search", {})
    criteria = cfg.get("criteria", {})
    output_dir = Path(search_cfg.get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    filt_path = output_dir / "listings_filtered.md"
    excl_path = output_dir / "listings_excluded.md"
    today = date.today()
    n_pass = n_excl = 0

    with filt_path.open("w", encoding="utf-8") as f_filt, excl_path.open("w", encoding="utf-8") as f_excl:
        f_filt.write(f"# Zurich Apartment Search Results\nGenerated on {today}\n\n")
        f_excl.write(f"# Excluded Listings (Audit Log)\nGenerated on {today}\n\n")

        providers = providers_override or search_cfg.get("providers", [])
        for provider in providers:
            if provider not in search_cfg:
                logger.warning(f"Provider {provider} missing config section.")
                continue
            p_cfg = search_cfg[provider]
            base = p_cfg.get("base_url")
            search_url = f"{base}?{urlencode(p_cfg.get('params', {}))}"
            headers = {"User-Agent": "Mozilla/5.0", **p_cfg.get("request_headers", {})}

            logger.info(f"[{provider}] {search_url}")
            html = fetch_search_html(search_url, p_cfg, headers)
            if not html:
                logger.error(f"[{provider}] no HTML.")
                continue

            parser_fn = PARSERS.get(provider)
            if not parser_fn:
                continue
            found = parser_fn(urljoin(search_url, "/"), html)
            logger.info(f"[{provider}] {len(found)} listings.")

            timeout = p_cfg.get("request_timeout_seconds", 20)
            delay = p_cfg.get("detail_request_delay_seconds", 0.5)
            for idx, l in enumerate(found, 1):
                try:
                    hydrate_one(l, timeout)
                except Exception as e:
                    l.warnings.append(f"hydration_error: {e}")
                evaluate_filters(l, criteria)
                target = f_filt if l.passed else f_excl
                target.write(l.to_string())
                print( l.passed, "\n\n",  l.to_string())
                target.flush()
                if l.passed:
                    n_pass += 1
                else:
                    n_excl += 1
                if idx % 10 == 0:
                    logger.info(f"[{provider}] {idx}/{len(found)} processed.")
                time.sleep(delay)

    logger.info(f"Done. Filtered: {n_pass}, Excluded: {n_excl}. -> {filt_path}, {excl_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Apartment finder")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--providers", help="Comma-separated providers")
    args = parser.parse_args()
    sel = [p.strip().lower() for p in args.providers.split(",") if p.strip()] if args.providers else None
    run(Path(args.config), providers_override=sel)
