import csv

from scripts.poll import append_listing
from src.providers.common import Listing
from src.providers.scoring import SCORE_WEIGHTS, listing_score, score_csv_row, score_dict


def _listing(**overrides):
    data = {
        "provider": "flatfox",
        "listing_id": "1",
        "url": "https://example.test/1",
        "title": "Terrace apartment",
        "description": "Apartment with terrace.",
        "price_chf": 3000,
        "rent_net_chf": None,
        "rent_charges_chf": None,
        "currency": "CHF",
        "rooms": 3.5,
        "bedrooms": 2,
        "surface_living_m2": 90,
        "attributes": ["balconygarden", "dishwasher", "washingmachine"],
        "has_washing_machine": True,
        "address": {"city": "Zurich"},
    }
    data.update(overrides)
    listing = Listing.from_dict(data)
    listing.municipality_tax_rate = overrides.get("municipality_tax_rate")
    listing.commute = overrides.get("commute")
    return listing


def test_listing_score_prefers_current_priorities():
    strong = _listing(
        price_chf=2850,
        surface_living_m2=105,
        rooms=4.0,
        attributes=["terrace", "hasNiceView", "dishwasher", "washingmachine", "hasElevator"],
        municipality_tax_rate=75,
        commute={"walk_min": 16, "walk_km": 1.2, "alternatives": [{"modes": ["WALK"], "travel_min": 16}]},
    )
    weak = _listing(
        title="Basic apartment",
        description="Basic apartment.",
        price_chf=3500,
        surface_living_m2=62,
        rooms=3.0,
        attributes=[],
        has_washing_machine=False,
        municipality_tax_rate=119,
        commute={"walk_min": 55, "walk_km": 4.2, "alternatives": [{"modes": ["BUS", "WALK"], "travel_min": 30}]},
    )

    assert listing_score(strong) > listing_score(weak)


def test_score_dict_contains_total_and_confidence():
    score = score_dict(_listing(municipality_tax_rate=100))

    assert 0 <= score["listing_score"] <= 100
    assert 0 <= score["score_confidence"] <= 1
    assert 0 <= score["score_washing_machine"] <= 100


def test_score_weights_sum_to_100():
    assert sum(SCORE_WEIGHTS.values()) == 100


def test_washing_machine_is_explicit_score_factor():
    with_washer = _listing(has_washing_machine=True, attributes=[])
    without_washer = _listing(has_washing_machine=False, attributes=[])

    assert score_dict(with_washer)["score_washing_machine"] == 100
    assert score_dict(without_washer)["score_washing_machine"] == 0
    assert listing_score(with_washer) > listing_score(without_washer)


def test_score_csv_row_uses_stored_poll_fields():
    score = score_csv_row({
        "provider": "flatfox",
        "listing_id": "1",
        "url": "https://example.test/1",
        "title": "Balcony apartment",
        "price_chf": "3000",
        "rooms": "3.5",
        "bedrooms": "2",
        "surface_living_m2": "90",
        "tax_rate": "80",
        "commute_min": "18",
        "commute_modes": "WALK",
        "attributes": "balcony, view, dishwasher",
    })

    assert 0 <= score["listing_score"] <= 100
    assert score["score_confidence"] > 0.5


def test_append_listing_upgrades_existing_csv_with_score_columns(tmp_path):
    csv_path = tmp_path / "listings.csv"
    csv_path.write_text(
        "seen_at,provider,listing_id,url,title\n"
        "2026-05-01T12:00:00,flatfox,old,https://example.test/old,Old\n",
        encoding="utf-8",
    )

    append_listing(csv_path, _listing(listing_id="new"), "2026-05-02T12:00:00")

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert "listing_score" in (reader.fieldnames or [])
    assert "score_confidence" in (reader.fieldnames or [])
    assert rows[0]["listing_score"]
    assert rows[1]["listing_id"] == "new"
    assert rows[1]["listing_score"]
