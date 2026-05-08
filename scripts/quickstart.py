"""Minimal usage example. ~30 lines.

Run from repo root:
    python scripts/quickstart.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers.common import SearchCriteria
from providers.flatfox_api import FlatfoxClient
from providers.homegate_api import HomegateClient


# 1) Define what you want.
crit = SearchCriteria(
    offer_type="RENT",
    cities=["Zurich"],                           # homegate
    bbox=(47.32, 8.45, 47.42, 8.63),             # flatfox
    min_rooms=3,
    max_price_chf=3600,
    keywords=["balcony"],                        # any of these in title+desc+attrs
    exclude_keywords=["wg", "mitbewohner",
                      "shared", "befristet"],    # disqualifies if present
    must_features=["balcony"],                   # canonical feature names
    limit=10,
)

# 2) Run on both providers — same criteria object.
flatfox = FlatfoxClient().search_listings(crit)
with HomegateClient() as hg:
    homegate = hg.search_listings(crit)

# 3) Use the unified Listing fields.
for listing in flatfox + homegate:
    print(f"\n{listing.provider.upper()}  CHF {listing.price_chf}  "
          f"{listing.rooms}rm  {listing.surface_living_m2}m²")
    print(f"  {listing.title}")
    print(f"  {listing.address.public}  ({listing.address.lat}, {listing.address.lon})")
    print(f"  features : {listing.canonical_attributes}")
    print(f"  cover    : {listing.images[0] if listing.images else '-'}")
    print(f"  url      : {listing.url}")
