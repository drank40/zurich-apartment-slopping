"""End-to-end demo: fetch one homegate listing's full detail.

Usage:
    python scripts/e2e_homegate_demo.py

Reads HOMEGATE_DATADOME_COOKIE from .creds. Uses headless Playwright as a
TLS-correct transport against api.homegate.ch (DataDome rejects raw
``requests`` after a couple calls because of TLS fingerprint mismatch).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds  # noqa: E402
from providers.homegate_api import HomegateClient, HomegateBotChallenge  # noqa: E402

DEFAULT_URL = "https://www.homegate.ch/mieten/4003130882"


def _print_report(detail: dict) -> None:
    addr = detail["address"]
    print("=" * 72)
    print(f"  {detail['title']}")
    print("=" * 72)
    print(f"URL              : {detail['url']}")
    print(f"Listing ID       : {detail['listing_id']}")
    print(
        f"Price            : {detail['currency']} {detail['price_chf']} "
        f"(net {detail['rent_net_chf']} + charges {detail['rent_charges_chf']})"
    )
    print(f"Rooms            : {detail['rooms']}  | Bed: {detail.get('bedrooms')}  | Bath: {detail.get('bathrooms')}")
    print(f"Surface (living) : {detail['surface_living_m2']} m²")
    print(f"Surface (usable) : {detail['surface_usable_m2']} m²")
    print(f"Lot size         : {detail.get('surface_property_m2')} m²")
    print(f"Floor            : {detail['floor']}")
    print(f"Year built       : {detail['year_built']}")
    print(f"Available from   : {detail['available_from']} ({detail['available_from_type']})")
    print(f"Furnished        : {detail['is_furnished']}")
    print(f"Temporary        : {detail['is_temporary']}")
    print(f"Offer type       : {detail['offer_type']}")
    print(f"Categories       : {detail['object_category']} / {detail['object_type']}")
    print(
        f"Address          : {addr['public']} "
        f"(lat={addr['lat']}, lon={addr['lon']})"
    )
    print(f"Attributes       : {', '.join(detail['attributes']) or '-'}")
    ag = detail["agency"]
    print(f"Agency           : {ag['name']}  {ag['phone']}  {ag['email']}")
    print(f"Images           : {len(detail['images'])}")
    for i, u in enumerate(detail["images"][:5], 1):
        print(f"  [{i}] {u}")
    if len(detail["images"]) > 5:
        print(f"  ... ({len(detail['images']) - 5} more)")
    print()
    desc = (detail["description"] or "").strip()
    print("Description (first 500 chars):")
    print(desc[:500] + ("…" if len(desc) > 500 else ""))
    print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=DEFAULT_URL, help="Homegate listing URL or id")
    parser.add_argument("--transport", choices=["playwright", "requests"], default="playwright")
    parser.add_argument("--headed", action="store_true", help="Show the headless Chromium")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    creds = load_creds(Path(__file__).resolve().parent.parent / ".creds")
    cookie = creds.get("HOMEGATE_DATADOME_COOKIE") or None  # empty str → bootstrap

    print(f"Fetching detail for: {args.url}\n")
    with HomegateClient(
        datadome_cookie=cookie,
        transport=args.transport,
        headless=not args.headed,
    ) as client:
        try:
            detail = client.fetch_full_detail(args.url)
        except HomegateBotChallenge as exc:
            print(f"\nBLOCKED: {exc}\n")
            print("To refresh: open homegate.ch in a browser, copy the 'datadome'")
            print("cookie from DevTools → Application → Cookies, then update")
            print("HOMEGATE_DATADOME_COOKIE in .creds and re-run.")
            return 3

    if not detail:
        print("ERROR: listing not found.")
        return 1

    if args.json:
        d = dict(detail)
        d.pop("raw", None)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        _print_report(detail)

    assert detail["title"], "title missing"
    assert detail["description"], "description missing"
    assert detail["price_chf"] and detail["price_chf"] > 0, "price missing"
    assert detail["rooms"] and detail["rooms"] > 0, "rooms missing"
    assert detail["surface_living_m2"], "surface missing"
    assert len(detail["images"]) >= 1, "no images"
    assert detail["address"]["lat"] and detail["address"]["lon"], "no coords"
    print("All E2E assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
