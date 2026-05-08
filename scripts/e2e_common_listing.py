"""Cross-provider E2E: fetch one flatfox + one homegate listing, normalize
both into the common ``Listing`` dataclass, print side-by-side.

Demonstrates that both providers produce structurally identical output
suitable for cross-platform ranking.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds  # noqa: E402
from providers.common import Listing  # noqa: E402
from providers.flatfox_api import FlatfoxClient  # noqa: E402
from providers.homegate_api import HomegateClient  # noqa: E402

FLATFOX_URL = "https://flatfox.ch/de/wohnung/aegertlistrasse-18-8800-thalwil/85920696/"
HOMEGATE_URL = "https://www.homegate.ch/mieten/4003130882"


def _row(label: str, *vals: object) -> None:
    print(f"  {label:<22}" + "".join(f"  {str(v):<46}" for v in vals))


def _short(s: object, n: int = 44) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    creds = load_creds(Path(__file__).resolve().parent.parent / ".creds")
    listings: list[Listing] = []

    # --- flatfox ---
    print("[flatfox] fetching...")
    ff = FlatfoxClient()
    if creds.get("FLATFOX_EMAIL"):
        try:
            ff.login(
                creds["FLATFOX_EMAIL"],
                creds["FLATFOX_PASSWORD"],
                device_cookie=creds.get("FLATFOX_DEVICE_COOKIE"),
            )
        except Exception as exc:
            print(f"  login warn: {exc}")
    ff_detail = ff.fetch_full_detail(FLATFOX_URL)
    if ff_detail:
        listings.append(Listing.from_dict(ff_detail))
        print(f"  ok: {listings[-1].title[:60]}")
    else:
        print("  ERROR: flatfox detail empty")

    # --- homegate ---
    print("[homegate] fetching (Camoufox + Pinia)...")
    with HomegateClient() as hg:
        hg_detail = hg.fetch_full_detail(HOMEGATE_URL)
    if hg_detail:
        listings.append(Listing.from_dict(hg_detail))
        print(f"  ok: {listings[-1].title[:60]}")
    else:
        print("  ERROR: homegate detail empty")

    if len(listings) < 2:
        print("\nMissing one provider's listing — aborting comparison.")
        return 1

    # --- side-by-side ---
    print()
    print("=" * 116)
    headers = [f"{l.provider.upper()} {l.listing_id}" for l in listings]
    _row("FIELD", *headers)
    print("-" * 116)
    _row("title", *(_short(l.title) for l in listings))
    _row("price_chf", *(l.price_chf for l in listings))
    _row("rent_net_chf", *(l.rent_net_chf for l in listings))
    _row("rent_charges_chf", *(l.rent_charges_chf for l in listings))
    _row("rooms", *(l.rooms for l in listings))
    _row("surface_living_m2", *(l.surface_living_m2 for l in listings))
    _row("price_per_m2", *(l.price_per_m2 for l in listings))
    _row("floor", *(l.floor for l in listings))
    _row("year_built", *(l.year_built for l in listings))
    _row("available_from", *(_short(l.available_from) for l in listings))
    _row("is_furnished", *(l.is_furnished for l in listings))
    _row("offer_type", *(l.offer_type for l in listings))
    _row("object_type", *(l.object_type for l in listings))
    _row("address.public", *(_short(l.address.public) for l in listings))
    _row("zip / city", *(f"{l.address.zipcode} / {l.address.city}" for l in listings))
    _row("lat / lon", *(f"{l.address.lat} / {l.address.lon}" for l in listings))
    _row("images", *(len(l.images) for l in listings))
    _row("attributes", *(_short(", ".join(l.attributes)) for l in listings))
    _row("agency", *(_short(l.agency.name) for l in listings))
    _row("description chars", *(len(l.description) for l in listings))
    print("=" * 116)

    # --- assertions: both must populate the common fields ---
    for l in listings:
        assert l.title, f"{l.provider}: title empty"
        assert l.description, f"{l.provider}: description empty"
        assert l.price_chf and l.price_chf > 0, f"{l.provider}: bad price"
        assert l.rooms and l.rooms > 0, f"{l.provider}: bad rooms"
        assert l.surface_living_m2 and l.surface_living_m2 > 0, f"{l.provider}: bad surface"
        assert l.has_coords, f"{l.provider}: no coords"
        assert l.images, f"{l.provider}: no images"
    print("\nAll cross-provider assertions passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
