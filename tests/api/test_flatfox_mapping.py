"""Tests for the flatfox API client mapping logic.

These tests use recorded fixtures (captured from live API) and do NOT hit
the network. To refresh fixtures, run::

    venv/bin/python scripts/refresh_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from apartment_finder_llm import Listing  # noqa: E402
from providers.flatfox_api import FlatfoxClient, mapping_to_listing  # noqa: E402

FIXTURES = ROOT / "tests" / "api" / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


def test_pin_response_shape_pks_present():
    data = load("flatfox_pin.json")
    assert isinstance(data, list)
    assert data, "fixture should not be empty"
    sample = data[0]
    for key in ("pk", "latitude", "longitude", "price_display"):
        assert key in sample, f"pin missing key {key}"


def test_listing_response_to_listing_dataclass():
    data = load("flatfox_listings.json")
    assert isinstance(data, list)
    assert data
    listing = mapping_to_listing(data[0], Listing)
    assert listing.provider == "flatfox"
    assert listing.listing_id.isdigit()
    assert listing.url.startswith("https://flatfox.ch/")
    assert listing.contact_url
    # rent + rooms should be populated for residential
    assert listing.lat is not None and listing.lon is not None
    assert listing.title
    # description/address pulled from API, no scraping needed
    assert isinstance(listing.description, str)
    assert isinstance(listing.address, str)


def test_extract_pk_from_url_variants():
    cases = {
        "/en/flat/some-slug-with-numbers/85947313/": 85947313,
        "https://flatfox.ch/it/flat/x/1750136/": 1750136,
        "/85947313/": 85947313,
        "85947313": 85947313,
        85947313: 85947313,
        "abc": None,
        "/it/search/": None,
    }
    for input_, expected in cases.items():
        assert FlatfoxClient._extract_pk(input_) == expected, input_


def test_mapping_handles_missing_optional_fields():
    minimal = {
        "pk": 1,
        "url": "/en/flat/x/1/",
        "short_url": "/1/",
        "submit_url": "/en/listing/1/submit/",
        "public_title": "Test",
        "rent_gross": None,
        "rent_net": None,
        "price_display": 2000,
        "number_of_rooms": None,
        "is_furnished": False,
        "is_temporary": None,
        "object_type": "FLAT",
        "object_category": "APAR",
        "description": "",
        "street": "",
        "zipcode": 0,
        "city": "",
        "public_address": "",
        "latitude": None,
        "longitude": None,
        "moving_date": None,
    }
    listing = mapping_to_listing(minimal, Listing)
    assert listing.price_chf == 2000.0
    assert listing.total_rooms is None
    assert listing.lat is None
