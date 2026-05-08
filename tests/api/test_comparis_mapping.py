"""Tests for comparis __NEXT_DATA__ extraction. Uses recorded fixtures."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from apartment_finder_llm import Listing  # noqa: E402
from providers.comparis_api import (  # noqa: E402
    ComparisClient,
    mapping_to_listing,
)

FIXTURES = ROOT / "tests" / "api" / "fixtures"


def test_next_data_extraction_from_html_template():
    # Build a minimal HTML wrapping our recorded payload
    payload = json.loads((FIXTURES / "comparis_next_data.json").read_text())
    html = (
        "<!doctype html><html><head></head><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'
        "</body></html>"
    )
    extracted = ComparisClient._extract_next_data(html)
    assert extracted is not None
    init = extracted["props"]["pageProps"]["initialResultData"]
    assert "resultItems" in init
    assert "adIds" in init


def test_mapping_to_listing_from_next_data_item():
    payload = json.loads((FIXTURES / "comparis_next_data.json").read_text())
    items = payload["props"]["pageProps"]["initialResultData"]["resultItems"]
    assert items
    listing = mapping_to_listing(items[0], Listing)
    assert listing.provider == "comparis"
    assert listing.listing_id
    assert listing.url.startswith("https://www.comparis.ch/")
    assert listing.title
    # Address is a list of strings in source; we join.
    assert listing.address


def test_challenge_detection_true_for_datadome_html():
    challenge = (
        '<html><head></head><body>'
        '<script>var dd={"cid":"X"}</script>'
        '<iframe src="https://geo.captcha-delivery.com/captcha/?x=1"></iframe>'
        '</body></html>'
    )
    assert ComparisClient._is_challenge(challenge) is True


def test_challenge_detection_false_for_real_page():
    real = '<html><body><script id="__NEXT_DATA__">{"x":1}</script></body></html>'
    assert ComparisClient._is_challenge(real) is False
