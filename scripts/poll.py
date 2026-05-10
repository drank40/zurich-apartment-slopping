"""Poll flatfox + homegate every N minutes; append new listings to a CSV.

Already-seen listings (keyed by ``(provider, listing_id)``) are skipped so
each iteration only logs and stores new finds. Each row is timestamped at
``seen_at`` (the moment it first appeared).

    python scripts/poll.py                            # default: 5 min interval, output/listings.csv
    python scripts/poll.py --interval 10              # 10 min
    python scripts/poll.py --csv output/picks.csv     # custom path
    python scripts/poll.py --once                     # single pass then exit
    python scripts/poll.py --no-llm --no-commute      # cheap mode
    python scripts/poll.py --contact --contact-send   # contact new rows after each pass
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds
from providers import Listing, SearchCriteria, search_all_iter
from providers.scoring import score_csv_row, score_dict

DEFAULT_CSV = Path("output/listings.csv")

CSV_FIELDS = [
    "seen_at",
    "listing_score", "score_confidence",
    "provider", "listing_id", "url", "title",
    "price_chf", "rent_net_chf", "rent_charges_chf",
    "rooms", "bedrooms", "surface_living_m2", "floor", "year_built",
    "available_from", "is_furnished", "is_temporary", "has_washing_machine",
    "address", "city", "zipcode", "lat", "lon",
    "tax_rate", "commute_min", "commute_modes",
    "attributes", "agency",
    "images_count", "images",
]


def _row(l: Listing, seen_at: str) -> dict[str, object]:
    c = l.commute or {}
    alt = (c.get("alternatives") or [{}])[0] if c.get("alternatives") else {}
    score = score_dict(l)
    return {
        "seen_at": seen_at,
        "listing_score": score["listing_score"],
        "score_confidence": score["score_confidence"],
        "provider": l.provider,
        "listing_id": l.listing_id,
        "url": l.url,
        "title": l.title,
        "price_chf": l.price_chf,
        "rent_net_chf": l.rent_net_chf,
        "rent_charges_chf": l.rent_charges_chf,
        "rooms": l.rooms,
        "bedrooms": l.bedrooms,
        "surface_living_m2": l.surface_living_m2,
        "floor": l.floor,
        "year_built": l.year_built,
        "available_from": l.available_from,
        "is_furnished": l.is_furnished,
        "is_temporary": l.is_temporary,
        "has_washing_machine": l.has_washing_machine,
        "address": l.address.public,
        "city": l.address.city,
        "zipcode": l.address.zipcode,
        "lat": l.address.lat,
        "lon": l.address.lon,
        "tax_rate": l.municipality_tax_rate,
        "commute_min": alt.get("travel_min"),
        "commute_modes": " → ".join(alt.get("modes", []) or []),
        "attributes": ", ".join(l.canonical_attributes),
        "agency": l.agency.name,
        "images_count": len(l.images),
        # Pipe-joined so the cell is single-line + grep-friendly. Split with
        # ``row["images"].split("|")`` downstream.
        "images": "|".join(l.images),
    }


def load_seen(csv_path: Path) -> set[tuple[str, str]]:
    """Return ``{(provider, listing_id), ...}`` already in the CSV."""
    if not csv_path.exists():
        return set()
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {(r["provider"], r["listing_id"]) for r in csv.DictReader(f)}


def append_listing(csv_path: Path, listing: Listing, seen_at: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not csv_path.exists()
    fieldnames = CSV_FIELDS
    if not new_file:
        fieldnames = ensure_csv_schema(csv_path)
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            w.writeheader()
        w.writerow(_row(listing, seen_at))
        f.flush()


def ensure_csv_schema(csv_path: Path) -> list[str]:
    """Add missing known columns to an existing CSV header.

    Existing unknown columns are preserved. This keeps old poll CSVs readable
    after adding ``listing_score`` and is future-friendly for contact-tracking
    columns that may be added by another script.
    """
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing_fields = reader.fieldnames or []
        if all(field in existing_fields for field in CSV_FIELDS):
            return existing_fields
        rows = list(reader)

    upgraded_fields = existing_fields + [field for field in CSV_FIELDS if field not in existing_fields]
    tmp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=upgraded_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if not row.get("listing_score") or not row.get("score_confidence"):
                score = score_csv_row(row)
                if not row.get("listing_score"):
                    row["listing_score"] = score["listing_score"]
                if not row.get("score_confidence"):
                    row["score_confidence"] = score["score_confidence"]
            writer.writerow({field: row.get(field, "") for field in upgraded_fields})
    tmp_path.replace(csv_path)
    return upgraded_fields


def build_criteria() -> SearchCriteria:
    """Same criteria as quickstart — Zurich + lake shore + close north belt."""
    return SearchCriteria(
        offer_type="RENT",
        cities=[
            "Zurich",
            "Kilchberg", "Rüschlikon", "Thalwil", "Horgen",
            "Zollikon", "Küsnacht", "Erlenbach", "Herrliberg", "Meilen",
            "Zumikon", "Uetikon am See",
            "Adliswil",
            "Wallisellen", "Opfikon", "Dübendorf",
        ],
        bbox=(47.25, 8.43, 47.46, 8.66),
        min_rooms=3,
        min_price_chf=2000,
        max_price_chf=3600,
        must_be_furnished=True,
        must_be_temporary=False,
        exclude_keywords=[
            "wg", "mitbewohner", "shared", "shared flat", "flat-share",
            "befristet", "zwischenmiete", "untermiete", "sublet",
            "blueground",
        ],
        # Scan deep so dedup-after-first-pass doesn't hand us 0 new each
        # time. With sort_by_newest=True the newest listings are first,
        # so most passes will hit fresh rows quickly and break early.
        limit=200,
        page_cap=200,
        sort_by_newest=True,
        available_on_or_before="2026-07-15",
        max_commute_min=30,                       # ≤30 min transit to Zurich HB
    )


def run_once(
    crit: SearchCriteria,
    csv_path: Path,
    *,
    llm: bool,
    commute: bool,
    google_key: str | None,
    min_bedrooms: int = 2,
    target_new: int = 20,
) -> int:
    """Stream until we've added ``target_new`` rows or the iterator drains.

    The iterator yields up to ``crit.limit`` rows total. With
    ``sort_by_newest=True`` (the recommended polling setting), the
    earliest yielded rows are the freshest — most passes will hit
    ``target_new`` long before the iterator finishes.
    """
    seen = load_seen(csv_path)
    n_seen_early = 0

    def _mark_seen(_listing: Listing) -> None:
        nonlocal n_seen_early
        n_seen_early += 1

    n_new = n_seen = n_filtered = 0
    for l in search_all_iter(
        crit,
        llm=llm,
        commute=commute,
        google_maps_key=google_key,
        skip_seen=seen,
        on_skip_seen=_mark_seen,
    ):
        if l.bedrooms is not None and l.bedrooms < min_bedrooms:
            n_filtered += 1
            continue
        key = (l.provider, str(l.listing_id))
        if key in seen:
            n_seen += 1
            continue
        seen_at = datetime.now().isoformat(timespec="seconds")
        append_listing(csv_path, l, seen_at)
        seen.add(key)
        n_new += 1
        print(
            f"[{seen_at}] NEW  {l.provider:<8} {l.listing_id}  "
            f"CHF {l.price_chf}  {l.rooms}rm  ({l.bedrooms} bed)  "
            f"tax={l.municipality_tax_rate}  "
            f"{l.address.city or '-'}",
            flush=True,
        )
        print(f"          {l.title[:90]}", flush=True)
        print(f"          {l.url}", flush=True)
        if n_new >= target_new:
            break
    print(
        f"  scanned: {n_new + n_seen + n_seen_early + n_filtered}  "
        f"({n_new} new, {n_seen + n_seen_early} dup, {n_filtered} filtered)",
        flush=True,
    )
    return n_new


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--interval", type=float, default=5.0, help="Minutes between polls")
    p.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    p.add_argument("--once", action="store_true", help="Single pass then exit")
    p.add_argument("--no-llm", action="store_true")
    p.add_argument("--no-commute", action="store_true")
    p.add_argument("--min-bedrooms", type=int, default=2)
    p.add_argument(
        "--max-commute-min", type=int, default=None,
        help="Drop listings whose best transit commute to Zurich HB exceeds this. "
             "Requires commute=True (Google Maps key).",
    )
    p.add_argument(
        "--target-new", type=int, default=20,
        help="Stop scanning per pass once this many new listings have been added",
    )
    p.add_argument(
        "--contact", action="store_true",
        help="After each pass, contact uncontacted rows first seen in that pass. Dry-run unless --contact-send is set.",
    )
    p.add_argument(
        "--contact-send", action="store_true",
        help="Actually send DMs/contact forms. Without this, --contact only logs dry-run actions.",
    )
    p.add_argument("--contact-provider", action="append", choices=["flatfox", "homegate"])
    p.add_argument("--contact-limit-per-pass", type=int, default=5)
    p.add_argument("--contact-min-score", type=float, default=None)
    p.add_argument("--contact-message-template", type=Path, default=Path("message_template.txt"))
    p.add_argument("--contact-headed", action="store_true", help="Show browser for browser-based contact providers")
    args = p.parse_args()

    creds_path = Path(__file__).resolve().parent.parent / ".creds"
    creds = load_creds(creds_path)
    google_key = (
        creds.get("GOOGLE_MAPS_KEY")
        or os.environ.get("GOOGLE_MAPS_KEY")
    )
    crit = build_criteria()
    if args.max_commute_min is not None:
        from dataclasses import replace
        crit = replace(crit, max_commute_min=args.max_commute_min)
    kw = dict(
        llm=not args.no_llm,
        commute=not args.no_commute and bool(google_key),
        google_key=google_key,
        min_bedrooms=args.min_bedrooms,
        target_new=args.target_new,
    )

    print(
        f"Polling every {args.interval} min into {args.csv}\n"
        f"  llm={kw['llm']}  commute={kw['commute']}  min_bedrooms={kw['min_bedrooms']}\n"
        f"  contact={args.contact}  contact_send={args.contact_send}\n"
        f"  total previously seen: {len(load_seen(args.csv))}",
        flush=True,
    )
    contact_message = None
    if args.contact:
        from contacting import load_message
        contact_message = load_message(args.contact_message_template)
    while True:
        poll_started = datetime.now()
        print(f"\n=== poll @ {poll_started.isoformat(timespec='seconds')} ===", flush=True)
        try:
            n = run_once(crit, args.csv, **kw)
        except Exception as exc:
            print(f"  poll error: {exc}", flush=True)
            n = 0
        total = len(load_seen(args.csv))
        print(f"  {n} new this pass (CSV total: {total})", flush=True)
        if args.contact and contact_message:
            try:
                from contacting import process_contact_csv
                summary = process_contact_csv(
                    args.csv,
                    contact_message,
                    creds_path=creds_path,
                    dry_run=not args.contact_send,
                    providers=set(args.contact_provider or []) or None,
                    limit=args.contact_limit_per_pass,
                    min_score=args.contact_min_score,
                    seen_since=poll_started,
                    headless=not args.contact_headed,
                )
                mode = "LIVE SEND" if args.contact_send else "dry-run"
                print(
                    f"  contact {mode}: selected={summary.selected} sent={summary.sent} "
                    f"dry_run={summary.dry_run} failed={summary.failed} skipped={summary.skipped}",
                    flush=True,
                )
            except Exception as exc:
                print(f"  contact error: {exc}", flush=True)
        if args.once:
            return 0
        try:
            time.sleep(args.interval * 60)
        except KeyboardInterrupt:
            print("\nstopped.", flush=True)
            return 0


if __name__ == "__main__":
    sys.exit(main())
