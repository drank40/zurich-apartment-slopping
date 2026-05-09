"""Test LLM extraction on real listing descriptions from each provider."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers.flatfox_api import FlatfoxClient  # noqa: E402
from providers.homegate_api import HomegateClient  # noqa: E402
from providers.llm_extract import extract_listing_meta  # noqa: E402

FLATFOX_URL = "https://flatfox.ch/de/wohnung/aegertlistrasse-18-8800-thalwil/85920696/"
HOMEGATE_URL = "https://www.homegate.ch/mieten/4003130882"


def _show(label: str, listing) -> None:
    print(f"\n=== {label} ===")
    print(f"URL          : {listing['url']}")
    print(f"Title        : {listing['title']}")
    print(f"rooms (raw)  : {listing.get('rooms')}")
    desc = (listing.get("description") or "").strip()
    print(f"desc chars   : {len(desc)}")
    t0 = time.monotonic()
    meta = extract_listing_meta(desc)
    print(f"LLM extract  : {meta}    ({time.monotonic()-t0:.2f}s)")


def main() -> int:
    ff = FlatfoxClient().fetch_full_detail(FLATFOX_URL)
    _show("FLATFOX", ff)

    with HomegateClient() as hg:
        hg_d = hg.fetch_full_detail(HOMEGATE_URL)
    _show("HOMEGATE", hg_d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
