"""Preference score for ranking listings before contacting them.

The score is intentionally deterministic and provider-agnostic. It uses only
fields already present on ``Listing`` after the usual tax/commute/LLM enrichers
have run, so a future contact script can sort the CSV by this value without
re-fetching listing details.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .common import Listing, best_commute_min


# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------
#
# All component scores are normalized to 0.0-1.0, then multiplied by these
# weights. Keep the values summing to 100 so ``ScoreBreakdown.total`` remains
# directly readable as a 0-100 ranking score.
SCORE_WEIGHTS: dict[str, float] = {
    "commute": 32.0,
    "tax": 24.0,
    "outdoor_view": 4.0,
    "size": 14.0,
    "washing_machine": 8.0,
    "quality": 8.0,
    "value": 10.0,
}

# Unknown values receive neutral-ish credit instead of becoming a hard penalty.
UNKNOWN_COMMUTE_SCORE = 0.45
UNKNOWN_TAX_SCORE = 0.50
UNKNOWN_SIZE_SCORE = 0.45
UNKNOWN_BEDROOM_SCORE = 0.50
UNKNOWN_VALUE_SCORE = 0.50
UNKNOWN_WASHING_MACHINE_SCORE = 0.50

# Linear scoring ranges. For ``*_LOW_GOOD`` ranges, the low end scores best.
COMMUTE_MIN_LOW_GOOD = (8.0, 32.0)
WALK_MIN_LOW_GOOD = (5.0, 35.0)
TAX_RATE_LOW_GOOD = (72.0, 122.0)
SURFACE_M2_HIGH_GOOD = (55.0, 110.0)
ROOMS_HIGH_GOOD = (3.0, 4.5)
BEDROOMS_HIGH_GOOD = (2.0, 3.0)
PRICE_CHF_LOW_GOOD = (2400.0, 3600.0)
PRICE_PER_M2_LOW_GOOD = (30.0, 55.0)

# Commute details.
WALK_ONLY_MIN_COMMUTE_SCORE = 0.95
COMMUTE_TRAVEL_SCORE_WEIGHT = 0.80
COMMUTE_WALK_SCORE_WEIGHT = 0.20
MISSING_TRAVEL_MIN_SORT_KEY = 10_000

# Outdoor / view component. These add up to 1.0.
OUTDOOR_VIEW_WEIGHTS: dict[str, float] = {
    "outdoor": 0.80,
    "view": 0.20,
}
OUTDOOR_FEATURES = {"terrace", "balcony", "garden", "roof_terrace"}
OUTDOOR_TEXT_TERMS = (
    "terrace",
    "terrasse",
    "sitzplatz",
    "balcony",
    "balkon",
    "garden",
    "garten",
)
VIEW_FEATURES = {"view", "lake_view", "mountain_view"}

# Size component. These add up to 1.0.
SIZE_WEIGHTS: dict[str, float] = {
    "surface": 0.60,
    "rooms": 0.25,
    "bedrooms": 0.15,
}

# Washing machine is explicit because it matters independently from general
# apartment quality. Attribute names are canonical provider attributes.
WASHING_MACHINE_FEATURES = {"washing_machine"}
WASHING_MACHINE_TEXT_TERMS = (
    "washing machine",
    "waschmaschine",
    "private laundry",
    "lavatrice",
    "lave-linge",
)

# Remaining practical quality signals. Washing machine is intentionally not
# listed here because it has its own component above.
QUALITY_FEATURES = {
    "dishwasher",
    "tumble_dryer",
    "elevator",
    "parking",
    "garage",
    "parquet",
    "minergie",
    "modernized",
    "refurbished",
    "new_building",
}
QUALITY_HITS_FOR_FULL_SCORE = 5

# Value component. These add up to 1.0.
VALUE_WEIGHTS: dict[str, float] = {
    "price": 0.45,
    "price_per_m2": 0.55,
}


@dataclass(frozen=True)
class ScoreBreakdown:
    total: float
    commute: float
    tax: float
    outdoor_view: float
    size: float
    washing_machine: float
    quality: float
    value: float
    confidence: float


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _linear_high_good(value: float | None, low: float, high: float, *, unknown: float = 0.5) -> float:
    if value is None:
        return unknown
    return _clamp((value - low) / (high - low))


def _linear_low_good(value: float | None, low: float, high: float, *, unknown: float = 0.5) -> float:
    if value is None:
        return unknown
    return 1.0 - _clamp((value - low) / (high - low))


def _best_modes(listing: Listing) -> list[str]:
    commute = listing.commute or {}
    alts = commute.get("alternatives") or []
    if not alts:
        return []
    best = min(
        alts,
        key=lambda a: a.get("travel_min")
        if a.get("travel_min") is not None
        else MISSING_TRAVEL_MIN_SORT_KEY,
    )
    return [str(m) for m in best.get("modes") or []]


def _best_walk_min(listing: Listing) -> int | None:
    commute = listing.commute or {}
    walk_min = commute.get("walk_min")
    if walk_min is not None:
        return int(walk_min)
    best_modes = _best_modes(listing)
    best_commute = best_commute_min(listing)
    if best_modes == ["WALK"] and best_commute is not None:
        return best_commute
    return None


def _has_any(listing: Listing, features: set[str], text_terms: tuple[str, ...] = ()) -> bool:
    attrs = set(listing.canonical_attributes)
    if attrs & features:
        return True
    haystack = listing.haystack
    return any(term in haystack for term in text_terms)


def _has_washing_machine(listing: Listing) -> bool | None:
    if listing.has_washing_machine is not None:
        return listing.has_washing_machine
    if _has_any(listing, WASHING_MACHINE_FEATURES, WASHING_MACHINE_TEXT_TERMS):
        return True
    if listing.canonical_attributes:
        return False
    return None


def score_listing(listing: Listing) -> ScoreBreakdown:
    """Return a 0-100 ranking score for contact prioritization.

    Tuning knobs live in the ``SCORE_*`` constants at the top of this module.
    Unknown values get neutral partial credit, so incomplete provider data does
    not bury an otherwise promising listing.
    """
    commute_min = best_commute_min(listing)
    walk_min = _best_walk_min(listing)
    modes = _best_modes(listing)

    commute_score = _linear_low_good(
        commute_min,
        *COMMUTE_MIN_LOW_GOOD,
        unknown=UNKNOWN_COMMUTE_SCORE,
    )
    if modes == ["WALK"]:
        commute_score = max(commute_score, WALK_ONLY_MIN_COMMUTE_SCORE)
    elif walk_min is not None:
        commute_score = (
            COMMUTE_TRAVEL_SCORE_WEIGHT * commute_score
            + COMMUTE_WALK_SCORE_WEIGHT
            * _linear_low_good(walk_min, *WALK_MIN_LOW_GOOD, unknown=UNKNOWN_COMMUTE_SCORE)
        )

    tax_score = _linear_low_good(
        listing.municipality_tax_rate,
        *TAX_RATE_LOW_GOOD,
        unknown=UNKNOWN_TAX_SCORE,
    )

    has_outdoor = _has_any(listing, OUTDOOR_FEATURES, OUTDOOR_TEXT_TERMS)
    has_view = _has_any(listing, VIEW_FEATURES)
    outdoor_view_score = (
        (OUTDOOR_VIEW_WEIGHTS["outdoor"] if has_outdoor else 0.0)
        + (OUTDOOR_VIEW_WEIGHTS["view"] if has_view else 0.0)
    )

    size_score = (
        SIZE_WEIGHTS["surface"]
        * _linear_high_good(listing.surface_living_m2, *SURFACE_M2_HIGH_GOOD, unknown=UNKNOWN_SIZE_SCORE)
        + SIZE_WEIGHTS["rooms"]
        * _linear_high_good(listing.rooms, *ROOMS_HIGH_GOOD, unknown=UNKNOWN_SIZE_SCORE)
        + SIZE_WEIGHTS["bedrooms"]
        * _linear_high_good(
            float(listing.bedrooms) if listing.bedrooms is not None else None,
            *BEDROOMS_HIGH_GOOD,
            unknown=UNKNOWN_BEDROOM_SCORE,
        )
    )

    washing_machine = _has_washing_machine(listing)
    washing_machine_score = (
        UNKNOWN_WASHING_MACHINE_SCORE if washing_machine is None else float(washing_machine)
    )

    attrs = set(listing.canonical_attributes)
    quality_score = _clamp(len(attrs & QUALITY_FEATURES) / QUALITY_HITS_FOR_FULL_SCORE)

    ppm = listing.price_per_m2
    price_score = _linear_low_good(listing.price_chf, *PRICE_CHF_LOW_GOOD, unknown=UNKNOWN_VALUE_SCORE)
    ppm_score = _linear_low_good(ppm, *PRICE_PER_M2_LOW_GOOD, unknown=UNKNOWN_VALUE_SCORE)
    value_score = VALUE_WEIGHTS["price"] * price_score + VALUE_WEIGHTS["price_per_m2"] * ppm_score

    known_parts = [
        commute_min is not None,
        listing.municipality_tax_rate is not None,
        bool(listing.canonical_attributes) or bool(listing.description),
        listing.surface_living_m2 is not None or listing.rooms is not None,
        washing_machine is not None,
        listing.price_chf is not None,
    ]
    confidence = sum(1 for known in known_parts if known) / len(known_parts)

    total = (
        SCORE_WEIGHTS["commute"] * commute_score
        + SCORE_WEIGHTS["tax"] * tax_score
        + SCORE_WEIGHTS["outdoor_view"] * outdoor_view_score
        + SCORE_WEIGHTS["size"] * size_score
        + SCORE_WEIGHTS["washing_machine"] * washing_machine_score
        + SCORE_WEIGHTS["quality"] * quality_score
        + SCORE_WEIGHTS["value"] * value_score
    )
    return ScoreBreakdown(
        total=round(total, 1),
        commute=round(100 * commute_score, 1),
        tax=round(100 * tax_score, 1),
        outdoor_view=round(100 * outdoor_view_score, 1),
        size=round(100 * size_score, 1),
        washing_machine=round(100 * washing_machine_score, 1),
        quality=round(100 * quality_score, 1),
        value=round(100 * value_score, 1),
        confidence=round(confidence, 2),
    )


def listing_score(listing: Listing) -> float:
    """Convenience wrapper returning only the total score."""
    return score_listing(listing).total


def score_dict(listing: Listing) -> dict[str, Any]:
    """CSV/debug friendly representation."""
    breakdown = score_listing(listing)
    return {
        "listing_score": breakdown.total,
        "score_confidence": breakdown.confidence,
        "score_commute": breakdown.commute,
        "score_tax": breakdown.tax,
        "score_outdoor_view": breakdown.outdoor_view,
        "score_size": breakdown.size,
        "score_washing_machine": breakdown.washing_machine,
        "score_quality": breakdown.quality,
        "score_value": breakdown.value,
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    parsed = _float_or_none(value)
    return int(parsed) if parsed is not None else None


def _bool_or_none(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def listing_from_csv_row(row: dict[str, Any]) -> Listing:
    """Rebuild enough of a ``Listing`` from poll CSV fields to score it."""
    attrs = [a.strip() for a in str(row.get("attributes") or "").split(",") if a.strip()]
    image_text = str(row.get("images") or "")
    listing = Listing.from_dict({
        "provider": row.get("provider") or "csv",
        "listing_id": row.get("listing_id") or "",
        "url": row.get("url") or "",
        "title": row.get("title") or "",
        "description": "",
        "price_chf": _float_or_none(row.get("price_chf")),
        "rent_net_chf": _float_or_none(row.get("rent_net_chf")),
        "rent_charges_chf": _float_or_none(row.get("rent_charges_chf")),
        "currency": "CHF",
        "rooms": _float_or_none(row.get("rooms")),
        "bedrooms": _int_or_none(row.get("bedrooms")),
        "surface_living_m2": _float_or_none(row.get("surface_living_m2")),
        "floor": _int_or_none(row.get("floor")),
        "year_built": _int_or_none(row.get("year_built")),
        "available_from": row.get("available_from") or None,
        "is_furnished": _bool_or_none(row.get("is_furnished")),
        "is_temporary": _bool_or_none(row.get("is_temporary")),
        "has_washing_machine": _bool_or_none(row.get("has_washing_machine")),
        "address": {
            "public": row.get("address") or None,
            "city": row.get("city") or None,
            "zipcode": row.get("zipcode") or None,
            "lat": _float_or_none(row.get("lat")),
            "lon": _float_or_none(row.get("lon")),
        },
        "attributes": attrs,
        "agency": {"name": row.get("agency") or None},
        "images": [u for u in image_text.split("|") if u],
    })
    listing.municipality_tax_rate = _float_or_none(row.get("tax_rate"))
    commute_min = _int_or_none(row.get("commute_min"))
    if commute_min is not None:
        modes = [m.strip() for m in str(row.get("commute_modes") or "").split("→") if m.strip()]
        listing.commute = {"alternatives": [{"modes": modes, "travel_min": commute_min}]}
    return listing


def score_csv_row(row: dict[str, Any]) -> dict[str, Any]:
    """Score a row that was previously written by ``scripts/poll.py``."""
    return score_dict(listing_from_csv_row(row))
