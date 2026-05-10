"""Fetch one flatfox or homegate listing by URL, dump as JSON.

    python scripts/fetch_url.py <url> [--llm]

Tax + commute enrichers run by default (commute needs GOOGLE_MAPS_KEY in
.creds). Pass --llm to also run Haiku description extraction.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from creds import load_creds
from providers import fetch_listing

args = sys.argv[1:]
llm = "--llm" in args
urls = [a for a in args if not a.startswith("--")]
if len(urls) != 1:
    print(__doc__, file=sys.stderr)
    sys.exit(2)

google_key = (
    load_creds(Path(__file__).resolve().parent.parent / ".creds").get("GOOGLE_MAPS_KEY")
    or os.environ.get("GOOGLE_MAPS_KEY")
)
listing = fetch_listing(urls[0], llm=llm, google_maps_key=google_key)
if listing is None:
    print("ERROR: empty detail", file=sys.stderr)
    sys.exit(1)

print(json.dumps(listing.to_dict(), indent=2, ensure_ascii=False, default=str))
