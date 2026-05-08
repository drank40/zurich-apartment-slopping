"""Tests for homegate API mapping. Uses recorded fixtures."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from apartment_finder_llm import Listing  # noqa: E402
from providers.homegate_api import (  # noqa: E402
    build_query_from_config,
    mapping_to_listing,
)

FIXTURES = ROOT / "tests" / "api" / "fixtures"


def test_search_response_shape():
    data = json.loads((FIXTURES / "homegate_search.json").read_text())
    assert {"from", "size", "total", "results"}.issubset(data)
    assert data["total"] >= 1
    assert isinstance(data["results"], list)


def test_mapping_to_listing_has_essentials():
    data = json.loads((FIXTURES / "homegate_search.json").read_text())
    item = data["results"][0]
    listing = mapping_to_listing(item, Listing)
    assert listing.provider == "homegate"
    assert listing.listing_id == item["id"]
    assert listing.url.endswith(item["id"])
    assert listing.title
    assert listing.address
    assert isinstance(listing.description, str)
    assert listing.lat is not None
    assert listing.lon is not None
    assert listing.total_rooms is not None
    # price_chf may be None for ON_REQUEST listings; not all do
    if "prices" in item.get("listing", {}):
        prices = item["listing"]["prices"]
        if prices.get("rent", {}).get("gross") or prices.get("rent", {}).get("net"):
            assert listing.price_chf is not None


def test_build_query_from_config_legacy_keys():
    p_cfg = {"params": {"ac": 3, "ah": 3600}}
    q = build_query_from_config(p_cfg)
    assert q["offerType"] == "RENT"
    assert q["monthlyRent"] == {"to": 3600}
    assert q["numberOfRooms"] == {"from": 3.0}
    assert q["location"] == {"geoTags": ["geo-city-zurich"]}


def test_build_query_from_config_overrides():
    p_cfg = {"params": {
        "ac": 3, "ah": 3600,
        "categories": ["APARTMENT"],
        "location": {"geoTags": ["geo-zipcode-8004"]},
    }}
    q = build_query_from_config(p_cfg)
    assert q["categories"] == ["APARTMENT"]
    assert q["location"] == {"geoTags": ["geo-zipcode-8004"]}
