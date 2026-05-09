"""Minimal usage example — mirrors the original repo's `criteria` block.

Hard filters pushed to provider APIs (price, rooms, bbox, furnished,
not-temporary). Soft filters (min bedrooms, exclude WG/sublets) applied
on the streamed Listing objects.

    python scripts/quickstart.py            # all enrichers (LLM + tax + commute)
    python scripts/quickstart.py --no-llm   # skip Haiku enrichment
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds
from providers import SearchCriteria, search_all_iter

# ---------------------------------------------------------------------------
# Hard criteria — these go into the provider queries directly.
#
# Mirrors the original repo's `config.yaml`:
#   center      : Europaallee, 8004 Zurich (~6 km radius)
#   max_price   : 3600 CHF/month
#   min_rooms   : 3 (Swiss "rooms" — i.e. bedrooms + living room)
#   furnished   : required
#   temporary   : excluded
# ---------------------------------------------------------------------------
crit = SearchCriteria(
    offer_type="RENT",
    cities=[
        # Zurich + Zürichsee shore (Gold/Pfnüselküste) + close north belt.
        "Zurich",
        "Kilchberg", "Rüschlikon", "Thalwil", "Horgen",        # left bank
        "Zollikon", "Küsnacht", "Erlenbach", "Herrliberg", "Meilen",  # right bank
        "Zumikon", "Uetikon am See",
        "Adliswil",                                            # just south
        "Wallisellen", "Opfikon", "Dübendorf",                 # close north
    ],
    bbox=(47.25, 8.43, 47.46, 8.66),              # lake shore (Horgen/Meilen) → north (Opfikon)
    min_rooms=3,
    min_price_chf=2000,                           # filters parking spots / WG rooms
    max_price_chf=3600,
    must_be_furnished=True,
    must_be_temporary=False,
    # WG / sublet language to drop (original config used a separate flag,
    # we just keyword-exclude — covers both German and English postings).
    exclude_keywords=[
        "wg", "mitbewohner", "shared", "shared flat", "flat-share",
        "befristet", "zwischenmiete", "untermiete", "sublet",
        "blueground" # i hate blueground
    ],
    must_features=[],                             # no specific feature gate
    max_commute_min=30,                           # ≤30 min transit to Zurich HB (needs commute=True)
    limit=20,                                     # raw pull; we'll soft-filter below
)

# Soft criteria — applied on the streamed Listing (LLM-enriched fields).
# Original repo: min_bedrooms=2, "include_unknowns_to_avoid_false_negatives:
# true" → keep listings whose bedroom count we couldn't infer.
MIN_BEDROOMS = 2
KEEP_UNKNOWNS = True
AVAILABLE_BY = "2026-07-15"   # ISO; only used to print a flag, not to drop
OPTIONAL_FEATURES = {"washing_machine", "dishwasher"}

google_key = load_creds(Path(__file__).resolve().parent.parent / ".creds").get("GOOGLE_MAPS_KEY")
stream = search_all_iter(
    crit,
    llm="--no-llm" not in sys.argv,
    commute="--no-commute" not in sys.argv and bool(google_key),
    google_maps_key=google_key,
)

shown = 0
for l in stream:
    # Soft bedroom filter — match original `min_bedrooms` semantics.
    if l.bedrooms is not None and l.bedrooms < MIN_BEDROOMS:
        continue
    if l.bedrooms is None and not KEEP_UNKNOWNS:
        continue

    optional_hits = sorted(set(l.canonical_attributes) & OPTIONAL_FEATURES)
    if l.has_washing_machine and "washing_machine" not in optional_hits:
        optional_hits.append("washing_machine")

    print(f"\n{l.provider.upper()}  CHF {l.price_chf}  {l.rooms}rm  ({l.bedrooms} bed)  {l.surface_living_m2}m²")
    print(f"  {l.title}")
    print(f"  {l.address.public}  ({l.address.lat}, {l.address.lon})")
    print(f"  features      : {l.canonical_attributes}")
    print(f"  optional+     : {optional_hits or '-'}")
    print(f"  furnished     : {l.is_furnished}")
    print(f"  temporary     : {l.is_temporary}")
    print(f"  available     : {l.available_from}    (cutoff {AVAILABLE_BY})")
    print(f"  tax_rate (ZH) : {l.municipality_tax_rate}")
    if l.commute and l.commute.get("alternatives"):
        b = l.commute["alternatives"][0]
        print(f"  commute to HB : {b['travel_min']} min  ({' → '.join(b['modes'])})")
    print(f"  images ({len(l.images)}):")
    for u in l.images:
        print(f"     {u}")
    print(f"  url           : {l.url}", flush=True)
    shown += 1

if shown == 0:
    print("\n(no listings matched — try widening the bbox or relaxing min_bedrooms)")
