"""Tax + commute enrichers for cross-provider Listings.

Both functions mutate the listings in place and return the same list so
they compose:

    enrich_with_tax(listings)          # free, instant
    enrich_with_commute(listings, key) # Google Routes API, ~1-2 req/listing

Wired into ``FlatfoxClient.search_listings`` / ``HomegateClient.search_listings``
via the ``tax`` and ``commute`` kwargs.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Optional, TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .common import Listing

# Default tax CSV — same path the original repo uses. Override via the
# ``tax_csv`` argument or by symlinking.
DEFAULT_TAX_CSV = Path("./datasets/tax.csv")


def enrich_with_tax(
    listings: Iterable["Listing"],
    tax_csv: str | Path | None = None,
) -> list["Listing"]:
    """Populate ``Listing.municipality_tax_rate`` from the ZH tax CSV.

    Skips silently if the CSV is missing — the original CSV ships only ZH
    municipalities, so listings outside the canton come back as ``None``.
    """
    from municipality_tax import MunicipalityTax  # top-level src/ module
    path = Path(tax_csv or DEFAULT_TAX_CSV)
    listings = list(listings)
    if not path.exists():
        logger.info("Tax CSV not found at %s; skipping tax enrichment.", path)
        return listings
    try:
        tax = MunicipalityTax(path)
    except Exception as exc:
        logger.warning("Failed to load tax CSV (%s); skipping.", exc)
        return listings
    # Original ``lookup`` is exact-name only, so abbreviated forms like
    # "Langnau a.A." in the CSV miss when listings say "Langnau am Albis",
    # and "Effretikon" misses "Illnau-Effretikon". A cheap fallback: try
    # to find any CSV municipality whose normalized name is a substring
    # of the listing's normalized address (or vice versa).
    from municipality_tax import _norm  # private but stable enough
    by_name = tax.by_name

    def _lenient(name_or_addr: str) -> Optional[float]:
        if not name_or_addr:
            return None
        target = _norm(name_or_addr)
        if not target:
            return None
        # Prefer the longest CSV name that fits — avoids short overlaps
        # like "horgen" matching "horgenberg" type ambiguities.
        best: Optional[str] = None
        for key in by_name:
            if key in target or target in key:
                if best is None or len(key) > len(best):
                    best = key
        return by_name[best].tax_rate if best else None

    for l in listings:
        if l.municipality_tax_rate is not None:
            continue  # don't overwrite
        rate: Optional[float] = None
        if l.address.public:
            rate = tax.lookup_from_address(l.address.public)
        if rate is None and l.address.city:
            rate = tax.lookup(l.address.city)
        if rate is None:
            rate = _lenient(l.address.city or l.address.public or "")
        l.municipality_tax_rate = rate
    return listings


def enrich_with_commute(
    listings: Iterable["Listing"],
    google_maps_key: str,
    dest: tuple[float, float] | None = None,
) -> list["Listing"]:
    """Populate ``Listing.commute`` via Google Routes API.

    Each listing costs ~1-2 paid requests (one walk, one transit).
    Listings without coordinates are skipped. Failures are caught
    per-listing so one bad geocode doesn't kill the batch.
    """
    from maps import Commuter, ZURICH_HB  # top-level src/ module
    listings = list(listings)
    if not google_maps_key:
        logger.info("No GOOGLE_MAPS_KEY; skipping commute enrichment.")
        return listings
    c = Commuter(google_maps_key, dest=dest or ZURICH_HB)
    for l in listings:
        if l.commute is not None:
            continue
        if l.address.lat is None or l.address.lon is None:
            continue
        try:
            l.commute = c.commute((l.address.lat, l.address.lon))
        except Exception as exc:
            logger.warning(
                "commute fetch failed for %s/%s: %s",
                l.provider, l.listing_id, exc,
            )
            l.commute = None
    return listings
