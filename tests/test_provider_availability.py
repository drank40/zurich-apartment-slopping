from src.providers.common import (
    Listing,
    filter_by_availability,
    infer_available_from_text,
    normalize_date,
)
from src.providers.homegate_api import _homegate_to_normalized


def _listing(available_from=None, description=""):
    return Listing.from_dict({
        "provider": "test",
        "listing_id": "1",
        "url": "https://example.test/1",
        "title": "Test",
        "description": description,
        "price_chf": 3000,
        "rent_net_chf": None,
        "rent_charges_chf": None,
        "currency": "CHF",
        "rooms": 3.5,
        "available_from": available_from,
    })


def test_infer_available_from_enter_date_phrase():
    assert infer_available_from_text("enter date 1.6.2026") == "2026-06-01"


def test_listing_from_dict_falls_back_to_description_availability():
    listing = _listing(
        available_from=None,
        description="viewing will announce privately\nenter date 1.6.2026",
    )

    assert listing.available_from == "2026-06-01"


def test_filter_by_availability_keeps_unknown_and_before_cutoff():
    listings = [
        _listing(description="no availability stated"),
        _listing(description="enter date 1.6.2026"),
    ]

    assert filter_by_availability(listings, "2026-07-15") == listings


def test_filter_by_availability_drops_known_late_date():
    listings = [
        _listing(description="enter date 1.8.2026"),
        _listing(description="enter date 1.6.2026"),
    ]

    kept = filter_by_availability(listings, "2026-07-15")

    assert [l.available_from for l in kept] == ["2026-06-01"]


def test_normalize_date_handles_provider_iso_date():
    assert normalize_date("2026-07-01") == "2026-07-01"


def test_homegate_normalizer_falls_back_to_description_availability():
    row = {
        "id": "4000000001",
        "listing": {
            "id": "4000000001",
            "localization": {
                "primary": "en",
                "en": {
                    "text": {
                        "title": "Homegate test",
                        "description": "Bright apartment. Available from 1.8.2026.",
                    },
                },
            },
            "address": {"geoCoordinates": {"latitude": 47.0, "longitude": 8.0}},
            "characteristics": {"numberOfRooms": 3.5},
            "prices": {"rent": {"gross": 3000}},
            "meta": {},
        },
    }

    listing = Listing.from_dict(_homegate_to_normalized(row))

    assert listing.available_from == "2026-08-01"
    assert filter_by_availability([listing], "2026-07-15") == []
