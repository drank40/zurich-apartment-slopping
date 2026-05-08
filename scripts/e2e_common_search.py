"""Cross-provider search: same SearchCriteria → flatfox + homegate.

Demonstrates the unified search API with keyword + positive/negative
filtering. Native filters (price/rooms/geo) are pushed to each provider's
own query language; keyword and feature filters run client-side on the
normalized ``Listing``.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers.common import SearchCriteria  # noqa: E402
from providers.flatfox_api import FlatfoxClient  # noqa: E402
from providers.homegate_api import HomegateClient  # noqa: E402


def _row(l, n: int = 1) -> str:
    return (
        f"  [{n}] {l.provider:<8} {l.listing_id:<12}  "
        f"CHF {str(l.price_chf or '-'):<6}  "
        f"{str(l.rooms or '-'):<4}rm  "
        f"{str(l.surface_living_m2 or '-'):<4}m²  "
        f"{(l.address.public or '')[:50]}"
    )


def main() -> int:
    crit = SearchCriteria(
        offer_type="RENT",
        categories=["APARTMENT"],
        cities=["Zurich"],          # homegate-friendly
        zipcodes=[],
        bbox=(47.32, 8.45, 47.42, 8.63),  # flatfox-friendly Zurich bbox
        min_rooms=3,
        max_price_chf=3600,
        keywords=["balcony"],          # title/description/attrs/address must include
        exclude_keywords=["mitbewohner", "wg", "shared"],   # WG-style sublets
        must_features=["balcony"],     # canonical feature
        must_not_features=["temporary"],
        must_be_furnished=True,
        limit=3,
        page_cap=80,
    )

    print("Criteria:")
    print(f"  bbox/cities: bbox={crit.bbox} cities={crit.cities}")
    print(f"  rooms>={crit.min_rooms}  price<={crit.max_price_chf}  furnished={crit.must_be_furnished}")
    print(f"  keywords={crit.keywords}  exclude={crit.exclude_keywords}")
    print(f"  must_features={crit.must_features}  must_not_features={crit.must_not_features}")
    print()

    print("[flatfox]")
    ff = FlatfoxClient()
    ff_listings = ff.search_listings(crit)
    for i, l in enumerate(ff_listings, 1):
        print(_row(l, i))
        print(f"      title : {l.title[:78]}")
        print(f"      attrs : {', '.join(l.canonical_attributes)}")
    if not ff_listings:
        print("  (no matches)")

    print()
    print("[homegate]")
    with HomegateClient() as hg:
        hg_listings = hg.search_listings(crit)
    for i, l in enumerate(hg_listings, 1):
        print(_row(l, i))
        print(f"      title : {l.title[:78]}")
        print(f"      attrs : {', '.join(l.canonical_attributes)}")
    if not hg_listings:
        print("  (no matches)")

    print()
    total = len(ff_listings) + len(hg_listings)
    print(f"Total across providers: {total}")
    return 0 if total else 1


if __name__ == "__main__":
    sys.exit(main())
