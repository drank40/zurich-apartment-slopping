"""E2E for tax + commute enrichers.

Tax is free (CSV lookup) — always tested.
Commute uses Google Routes API — tested only when GOOGLE_MAPS_KEY is set
in .creds or the environment.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds  # noqa: E402
from providers.common import SearchCriteria  # noqa: E402
from providers.flatfox_api import FlatfoxClient  # noqa: E402


def main() -> int:
    creds = load_creds(Path(__file__).resolve().parent.parent / ".creds")
    google_key = creds.get("GOOGLE_MAPS_KEY") or os.environ.get("GOOGLE_MAPS_KEY")

    crit = SearchCriteria(
        offer_type="RENT",
        bbox=(47.20, 8.40, 47.50, 8.70),  # ZH canton-ish
        min_rooms=3,
        max_price_chf=3500,
        limit=4,
        page_cap=20,
    )

    print("=== Tax enrichment only (free) ===")
    t0 = time.monotonic()
    listings = FlatfoxClient().search_listings(
        crit, llm=False, tax=True, commute=False,
    )
    print(f"  fetched + taxed: {time.monotonic() - t0:.2f}s, {len(listings)} listings")
    for l in listings:
        print(
            f"  {l.listing_id:<10}  CHF {l.price_chf}  "
            f"{l.rooms}rm  {l.address.city or '-':<20}"
            f"  tax={l.municipality_tax_rate}"
        )

    if not google_key:
        print()
        print("(skipping commute test — set GOOGLE_MAPS_KEY in .creds to enable)")
        return 0

    print()
    print("=== Tax + commute (Google Routes API) ===")
    t0 = time.monotonic()
    listings = FlatfoxClient().search_listings(
        crit, llm=False, tax=True, commute=True, google_maps_key=google_key,
    )
    print(f"  fetched + enriched: {time.monotonic() - t0:.2f}s")
    for l in listings:
        c = l.commute or {}
        alts = c.get("alternatives") or []
        print(
            f"\n  {l.listing_id} {l.address.public}\n"
            f"    tax              : {l.municipality_tax_rate}\n"
            f"    walk to dest     : {c.get('walk_min')} min "
            f"({c.get('walk_km')} km)"
        )
        for a in alts[:3]:
            print(f"    alt: {a['travel_min']:>3} min  {' → '.join(a['modes'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
