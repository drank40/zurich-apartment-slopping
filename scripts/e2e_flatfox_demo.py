"""End-to-end demo: log in to flatfox, fetch one listing's full detail.

Usage:
    python scripts/e2e_flatfox_demo.py [--no-login] [--headed]

Reads credentials from ``.creds`` at the repo root. The flatfox public API
does not require auth for read endpoints, so ``--no-login`` skips the
Playwright login step and still works.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# Allow running from repo root without installing.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds, redact  # noqa: E402
from providers.flatfox_api import FlatfoxClient  # noqa: E402

DEFAULT_URL = "https://flatfox.ch/de/wohnung/aegertlistrasse-18-8800-thalwil/85920696/"


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


def _print_report(detail: dict) -> None:
    addr = detail["address"]
    print("=" * 72)
    print(f"  {detail['title']}")
    print("=" * 72)
    print(f"URL              : {detail['url']}")
    print(f"Listing ID       : {detail['listing_id']}")
    print(
        f"Price            : CHF {detail['price_chf']} "
        f"(net {detail['rent_net_chf']} + charges {detail['rent_charges_chf']})"
    )
    print(f"Rooms            : {detail['rooms']}")
    print(f"Surface (living) : {detail['surface_living_m2']} m²")
    print(f"Surface (usable) : {detail['surface_usable_m2']} m²")
    print(f"Floor            : {detail['floor']}")
    print(f"Year built       : {detail['year_built']}")
    print(f"Available from   : {detail['available_from']} ({detail['available_from_type']})")
    print(f"Furnished        : {detail['is_furnished']}")
    print(f"Temporary        : {detail['is_temporary']}")
    print(
        f"Address          : {addr['public']} "
        f"(lat={addr['lat']}, lon={addr['lon']})"
    )
    print(f"Object           : {detail['object_category']} / {detail['object_type']}")
    print(f"Attributes       : {', '.join(detail['attributes']) or '-'}")
    print(f"Agency           : {detail['agency']['name'] or '-'}")
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
    parser.add_argument("--url", default=DEFAULT_URL, help="Flatfox listing URL or pk")
    parser.add_argument("--no-login", action="store_true", help="Skip login")
    parser.add_argument("--json", action="store_true", help="Dump full JSON instead of report")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    _setup_logging(args.verbose)

    creds = load_creds(Path(__file__).resolve().parent.parent / ".creds")
    client = FlatfoxClient()

    if not args.no_login:
        email = creds.get("FLATFOX_EMAIL")
        password = creds.get("FLATFOX_PASSWORD")
        device_cookie = creds.get("FLATFOX_DEVICE_COOKIE")
        if not email or not password:
            print("WARN: missing FLATFOX_EMAIL/PASSWORD in .creds — skipping login.")
        else:
            print(f"Logging in as {email} ({redact(password)})...")
            try:
                ok = client.login(email, password, device_cookie=device_cookie)
                print("OK: flatfox session established." if ok else "WARN: login probe failed.")
            except Exception as exc:  # DeviceNotVerifiedError / LoginFailedError
                print(f"\nLOGIN ERROR: {exc}\n")
                return 2

    print(f"\nFetching detail for: {args.url}\n")
    detail = client.fetch_full_detail(args.url)
    if not detail:
        print("ERROR: could not fetch listing detail.")
        return 1

    if args.json:
        # Don't dump ``raw`` by default — it's large.
        d = dict(detail)
        d.pop("raw", None)
        print(json.dumps(d, indent=2, ensure_ascii=False))
    else:
        _print_report(detail)

    # Quick assertions for an E2E-style sanity check.
    assert detail["title"], "title missing"
    assert detail["description"], "description missing"
    assert detail["price_chf"] and detail["price_chf"] > 0, "price missing"
    assert detail["rooms"] and detail["rooms"] > 0, "rooms missing"
    assert detail["surface_living_m2"] and detail["surface_living_m2"] > 0, "surface missing"
    assert len(detail["images"]) >= 1, "no images"
    assert detail["address"]["lat"] and detail["address"]["lon"], "no coords"
    print("All E2E assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
