import sys
import types

from src.providers import multi
from src.providers.common import Listing, SearchCriteria


def _listing(listing_id: str) -> Listing:
    return Listing.from_dict({
        "provider": "flatfox",
        "listing_id": listing_id,
        "url": f"https://example.test/{listing_id}",
        "title": f"Listing {listing_id}",
        "description": "3 bedrooms",
        "price_chf": 3000,
        "rent_net_chf": None,
        "rent_charges_chf": None,
        "currency": "CHF",
        "rooms": 4.5,
    })


class _FakeFlatfoxClient:
    def search_listings(self, criteria, **kwargs):
        return [_listing("seen"), _listing("new")]


class _FakeHomegateClient:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def search_listings(self, criteria, **kwargs):
        return []


def test_search_all_iter_skips_seen_before_llm(monkeypatch):
    llm_calls = []
    skipped = []

    monkeypatch.setattr(multi, "FlatfoxClient", _FakeFlatfoxClient)
    monkeypatch.setattr(multi, "HomegateClient", _FakeHomegateClient)

    def fake_extract(description):
        llm_calls.append(description)
        return {"bedrooms": 3}

    fake_llm_module = types.ModuleType("src.providers.llm_extract")
    fake_llm_module.extract_listing_meta = fake_extract
    monkeypatch.setitem(sys.modules, "src.providers.llm_extract", fake_llm_module)

    result = list(multi.search_all_iter(
        SearchCriteria(limit=2),
        llm=True,
        commute=False,
        skip_seen={("flatfox", "seen")},
        on_skip_seen=skipped.append,
    ))

    assert [l.listing_id for l in result] == ["new"]
    assert [l.listing_id for l in skipped] == ["seen"]
    assert len(llm_calls) == 1
