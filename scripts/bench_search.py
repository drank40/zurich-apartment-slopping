"""Time the cross-provider search to see where the seconds go.

Same criteria as quickstart.py but with logging + per-stage timings on
the homegate path (Camoufox launch, root bootstrap, API POSTs).
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s.%(msecs)03d %(message)s",
    datefmt="%H:%M:%S",
)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from providers.common import SearchCriteria  # noqa: E402
from providers.flatfox_api import FlatfoxClient  # noqa: E402
from providers.homegate_api import HomegateClient  # noqa: E402


def main() -> int:
    crit = SearchCriteria(
        offer_type="RENT",
        cities=["Zurich"],
        bbox=(47.32, 8.45, 47.42, 8.63),
        min_rooms=3,
        max_price_chf=3600,
        keywords=["balcony"],
        exclude_keywords=["wg", "mitbewohner", "shared", "befristet"],
        must_features=["balcony"],
        limit=10,
    )

    print("\n=== flatfox ===")
    t0 = time.monotonic()
    ff = FlatfoxClient().search_listings(crit)
    print(f"flatfox total: {len(ff)} listings in {time.monotonic()-t0:.2f}s")

    print("\n=== homegate ===")
    t0 = time.monotonic()
    with HomegateClient() as hg:
        t_first_call = time.monotonic()
        results = hg.search_listings(crit)
    print(
        f"homegate total: {len(results)} listings in "
        f"{time.monotonic()-t0:.2f}s "
        f"(client construction overhead {t_first_call-t0:.2f}s)"
    )

    print("\n=== homegate (warm: second search reuses browser) ===")
    with HomegateClient() as hg:
        # First call cold, second call warm.
        t0 = time.monotonic()
        hg.search_listings(crit)
        cold = time.monotonic() - t0
        t0 = time.monotonic()
        hg.search_listings(crit)
        warm = time.monotonic() - t0
        print(f"cold call: {cold:.2f}s   warm call: {warm:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
