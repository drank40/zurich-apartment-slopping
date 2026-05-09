"""LLM extraction for fields the listing APIs don't expose reliably.

Two entry points:

* ``extract_listing_meta(description)`` — single-shot, sync, ~5-20s on Haiku.
* ``enrich_listings(listings, max_concurrency=4)`` — batch, parallel,
  mutates the ``Listing`` objects in place. Use this when scoring search
  results.

Concurrency is capped to keep us under any API rate limits and avoid
trip-wires that flag bursty traffic. 4 is a conservative default for
Haiku; the SDK + the API itself can handle more, but listings come in
small batches so the launch overhead dominates anyway.


What we extract from the free-text description:

* ``bedrooms`` — actual sleeping rooms. The Swiss "rooms" count is total
  rooms (bedrooms + living + half-rooms), and the formula ``rooms - 1``
  often fails (open-plan layouts, studios, attic rooms, multi-level).
  Reading the description is the only reliable signal.
* ``is_temporary`` — sublet / fixed-term lease / "befristet". Often
  buried in the description even when the structured ``isTemporary``
  field is null.
* ``has_washing_machine`` — in-unit washing machine vs shared laundry.

Model: Haiku. Single message, JSON-only response. Robust regex parser
for the JSON in case the model adds prose around it.

Usage:
    from providers.llm_extract import extract_listing_meta
    meta = extract_listing_meta(listing.description)
    # {'bedrooms': 2, 'is_temporary': False, 'has_washing_machine': True}
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Iterable, Optional, TYPE_CHECKING, TypedDict

from claude_agent_sdk import query

from ._agent import Agent

if TYPE_CHECKING:
    from .common import Listing

SYSTEM_PROMPT = """You extract structured facts from Swiss apartment listing descriptions.

Output ONLY a single JSON object on one line, with these exact keys:
- "bedrooms":              integer | null   (number of separate sleeping rooms)
- "is_temporary":          boolean | null   (true if sublet / fixed-term / befristet / Zwischenmiete)
- "has_washing_machine":   boolean | null   (true if ANY laundry access exists,
                                             in-unit OR shared/laundry-service)
- "is_furnished":          boolean | null   (true if the unit comes with furniture)

Rules:
- Use null when the description does not say. Do NOT guess.
- A "studio" / "1-Zimmer" with a sleeping alcove counts as 0 bedrooms.
- Convention: total rooms = bedrooms + 1 living room. Half-rooms (e.g. 3.5)
  do NOT count as bedrooms unless explicitly described as a sleeping room.
- has_washing_machine is true for ANY of:
    * in-unit washer ("Waschmaschine", "WaMa", "Wäscheturm", "washer/dryer in unit")
    * shared laundry room ("Gemeinschaftswaschküche", "Waschküche im Keller", "shared laundry")
    * laundry service / coin laundry on premises
  Only false if the listing explicitly says no laundry is provided.
- is_furnished is true for ANY of:
    * "möbliert", "furnished", "fully-furnished", "voll möbliert", "meublé"
    * an explicit list of included furniture (bed, sofa, kitchen utensils, etc.)
  False when the listing says "unmöbliert", "unfurnished", "nicht möbliert".
  Null when the description doesn't address it.
- "befristet bis", "Zwischenmiete", "sublet", "fixed-term", "until <date>"
  → is_temporary: true.

No prose. No markdown. No explanation. JSON only."""


class ListingMeta(TypedDict, total=False):
    bedrooms: Optional[int]
    is_temporary: Optional[bool]
    has_washing_machine: Optional[bool]
    is_furnished: Optional[bool]


def _get_agent() -> Agent:
    """Build a fresh Agent per call.

    The Agent runs ``asyncio.run`` internally, which closes its event
    loop afterwards. Reusing the same Agent across calls makes the SDK
    raise a misleading ``Exception: Claude Code returned an error result:
    success`` because parts of its runtime are tied to the dead loop.
    Reconstructing per call is cheap (no network on construction).
    """
    return Agent(
        name="listing_meta",
        system_prompt=SYSTEM_PROMPT,
        model="haiku",
        max_turns=2,
    )


_JSON_OBJECT = re.compile(r"\{[^{}]*\}", re.S)


def _coerce_int(v: Any) -> Optional[int]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _coerce_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip().lower()
        if s in {"true", "yes", "1"}:
            return True
        if s in {"false", "no", "0"}:
            return False
    return None


def _parse(raw: str) -> ListingMeta:
    """Lenient parser: pull the first {...} blob, JSON-decode, coerce types."""
    none = {
        "bedrooms": None,
        "is_temporary": None,
        "has_washing_machine": None,
        "is_furnished": None,
    }
    if not raw:
        return none
    m = _JSON_OBJECT.search(raw)
    if not m:
        return none
    try:
        d = json.loads(m.group(0))
    except json.JSONDecodeError:
        return none
    return {
        "bedrooms": _coerce_int(d.get("bedrooms")),
        "is_temporary": _coerce_bool(d.get("is_temporary")),
        "has_washing_machine": _coerce_bool(d.get("has_washing_machine")),
        "is_furnished": _coerce_bool(d.get("is_furnished")),
    }


def extract_listing_meta(description: str) -> ListingMeta:
    """Run the Haiku extractor on a single description (sync).

    Returns ``{"bedrooms": int|None, "is_temporary": bool|None,
    "has_washing_machine": bool|None}``. Missing/unknown fields are
    ``None``. Never raises — on any failure returns all-None.
    """
    if not description or not description.strip():
        return {
            "bedrooms": None,
            "is_temporary": None,
            "has_washing_machine": None,
            "is_furnished": None,
        }
    try:
        raw = _get_agent()(description.strip())
    except Exception:
        return {"bedrooms": None, "is_temporary": None, "has_washing_machine": None}
    return _parse(raw)


# ---------------------------------------------------------------------------
# Async / batched
# ---------------------------------------------------------------------------

async def _extract_async(description: str) -> ListingMeta:
    """Async version of ``extract_listing_meta`` so callers can ``gather``.

    Builds a fresh ``Agent`` per call and consumes its async generator
    directly (no ``asyncio.run`` here — we're already in an event loop).
    """
    none = {
        "bedrooms": None,
        "is_temporary": None,
        "has_washing_machine": None,
        "is_furnished": None,
    }
    if not description or not description.strip():
        return none
    agent = _get_agent()
    out: list[str] = []
    try:
        async for msg in query(
            prompt=description.strip(),
            options=agent._build_options(),
        ):
            if hasattr(msg, "result") and msg.result:
                out.append(msg.result)
    except Exception:
        return none
    return _parse("\n".join(out))


async def _enrich_async(
    listings: Iterable["Listing"],
    max_concurrency: int = 4,
) -> list["Listing"]:
    sem = asyncio.Semaphore(max(1, int(max_concurrency)))
    listings = list(listings)

    async def one(l: "Listing") -> "Listing":
        async with sem:
            meta = await _extract_async(l.description)
        # Only fill fields the provider didn't populate. The provider's
        # structured ``is_temporary`` is more authoritative when present;
        # the LLM is the fallback for free-text-only signals.
        if l.bedrooms is None and meta.get("bedrooms") is not None:
            l.bedrooms = meta["bedrooms"]
        if l.is_temporary is None and meta.get("is_temporary") is not None:
            l.is_temporary = meta["is_temporary"]
        if l.is_furnished is None and meta.get("is_furnished") is not None:
            l.is_furnished = meta["is_furnished"]
        if meta.get("has_washing_machine") is not None:
            l.has_washing_machine = meta["has_washing_machine"]
        return l

    return await asyncio.gather(*(one(l) for l in listings))


def enrich_listings(
    listings: Iterable["Listing"],
    max_concurrency: int = 4,
) -> list["Listing"]:
    """Batch-enrich listings in parallel, mutating in place.

    ``max_concurrency`` caps in-flight LLM calls to avoid rate limits or
    bot-detection patterns. Default 4. Pass ``1`` for serial, higher for
    speed. Each call still costs ~5-20s, but ``len(listings)/concurrency``
    cycles instead of ``len(listings)``.

    Safe to call from inside a Playwright ``sync_api`` context (e.g.
    ``with HomegateClient() as hg``). Playwright runs its own event loop
    in the calling thread via greenlets, so a plain ``asyncio.run`` would
    fail with "cannot be called from a running event loop"; we detect
    that case and run the enrichment on a worker thread that owns a
    fresh loop.
    """
    listings = list(listings)
    if not listings:
        return listings
    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False
    coro_factory = lambda: _enrich_async(listings, max_concurrency)
    if not in_loop:
        return asyncio.run(coro_factory())
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro_factory())).result()
