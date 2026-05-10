"""Contact uncontacted listings from the poll CSV, highest score first.

Dry-run is the default. Use ``--send`` only when you are ready to actually
submit DMs/contact forms.

Safe test example using rows that do not match the current poll filters:

    python scripts/contact_from_csv.py --csv output/test_listings.csv \
        --only-non-matching-current-filters --limit 3

Live VPS example:

    python scripts/contact_from_csv.py --send --provider flatfox --limit 5
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from contacting import load_message, process_contact_csv  # noqa: E402
from providers.common import is_available_after_cutoff  # noqa: E402

DEFAULT_CSV = Path("output/listings.csv")
DEFAULT_MESSAGE = Path("message_template.txt")


def _float_or_none(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: object) -> bool | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    return None


def _row_matches_current_poll_filters(row: dict[str, object]) -> bool:
    """Conservative row check mirroring the current hard/soft poll filters."""
    price = _float_or_none(row.get("price_chf"))
    rooms = _float_or_none(row.get("rooms"))
    bedrooms = _float_or_none(row.get("bedrooms"))
    commute = _float_or_none(row.get("commute_min"))
    furnished = _bool_or_none(row.get("is_furnished"))
    temporary = _bool_or_none(row.get("is_temporary"))

    if price is None or price < 2000 or price > 3600:
        return False
    if rooms is None or rooms < 3:
        return False
    if bedrooms is not None and bedrooms < 2:
        return False
    if furnished is False:
        return False
    if temporary is True:
        return False
    if commute is not None and commute > 30:
        return False
    if is_available_after_cutoff(SimpleNamespace(available_from=row.get("available_from")), "2026-07-15"):
        return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    parser.add_argument("--message-template", type=Path, default=DEFAULT_MESSAGE)
    parser.add_argument("--creds", type=Path, default=Path(".creds"))
    parser.add_argument("--provider", action="append", choices=["flatfox", "homegate"])
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--min-score", type=float, default=None)
    parser.add_argument("--max-score", type=float, default=None)
    parser.add_argument("--seen-since", type=str, default=None, help="ISO timestamp lower bound")
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--send", action="store_true", help="Actually send DMs/contact forms")
    parser.add_argument("--headed", action="store_true", help="Show browser for browser-based providers")
    parser.add_argument(
        "--only-non-matching-current-filters",
        action="store_true",
        help="Testing guard: skip rows that look like they match the current target filters",
    )
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")
    message = load_message(args.message_template)
    seen_since = datetime.fromisoformat(args.seen_since) if args.seen_since else None
    row_predicate = None
    if args.only_non_matching_current_filters:
        row_predicate = lambda row: not _row_matches_current_poll_filters(row)

    summary = process_contact_csv(
        args.csv,
        message,
        creds_path=args.creds,
        dry_run=not args.send,
        providers=set(args.provider or []) or None,
        limit=args.limit,
        min_score=args.min_score,
        max_score=args.max_score,
        seen_since=seen_since,
        max_attempts=args.max_attempts,
        headless=not args.headed,
        row_predicate=row_predicate,
    )
    mode = "LIVE SEND" if args.send else "dry-run"
    print(
        f"{mode}: selected={summary.selected} sent={summary.sent} "
        f"dry_run={summary.dry_run} failed={summary.failed} skipped={summary.skipped}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
