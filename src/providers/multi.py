"""Cross-provider search.

Two entry points:

* :func:`search_all_iter` — generator yielding ``Listing`` objects as
  they arrive. Both providers run concurrently; the HomegateClient
  Playwright/datadome session stays alive for the whole iteration so
  pagination and follow-up enrichers reuse it. LLM enrichment, when
  enabled, runs in parallel worker threads and listings are yielded
  in arrival order (not provider order).

* :func:`search_all` — convenience wrapper that drains the iterator
  into a list. Use this when you don't need streaming output.

Both split ``criteria.limit`` 50/50 across flatfox + homegate (flatfox
gets +1 on odd totals so we saturate when one side is sparse).
"""
from __future__ import annotations

import logging
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Callable, Iterator, TYPE_CHECKING

from .common import Listing, filter_by_commute
from .flatfox_api import FlatfoxClient
from .homegate_api import HomegateClient

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .common import SearchCriteria


def fetch_listing(
    url: str,
    *,
    tax: bool = True,
    commute: bool = True,
    llm: bool = False,
    google_maps_key: str | None = None,
) -> Listing | None:
    """Fetch one listing by URL and normalize to ``Listing``.

    Provider is picked from the host (flatfox.ch / homegate.ch).
    Tax + commute enrichers run by default (commute is a no-op without
    ``google_maps_key``); LLM enrichment is opt-in.
    """
    u = url.lower()
    if "flatfox.ch" in u:
        detail = FlatfoxClient().fetch_full_detail(url)
    elif "homegate.ch" in u:
        with HomegateClient() as hg:
            detail = hg.fetch_full_detail(url)
    else:
        raise ValueError(f"unsupported provider: {url}")
    if not detail:
        return None
    listing = Listing.from_dict(detail)
    if tax:
        from .enrich import enrich_with_tax
        enrich_with_tax([listing])
    if commute and google_maps_key:
        from .enrich import enrich_with_commute
        enrich_with_commute([listing], google_maps_key)
    if llm:
        from .llm_extract import enrich_listings
        enrich_listings([listing])
    return listing

_SENTINEL = object()


def _split_limit(total: int) -> tuple[int, int]:
    """Split a total count 50/50; flatfox gets the extra on odd totals."""
    return (total + 1) // 2, total // 2


def search_all_iter(
    criteria: "SearchCriteria",
    *,
    llm: bool = True,
    llm_concurrency: int = 4,
    tax: bool = True,
    commute: bool = False,
    google_maps_key: str | None = None,
    skip_seen: set[tuple[str, str]] | None = None,
    on_skip_seen: Callable[["Listing"], None] | None = None,
) -> Iterator["Listing"]:
    """Yield ``Listing`` objects as they arrive from either provider.

    Real-time: each listing is yielded once after the enabled enrichers
    and filters finish. When ``skip_seen`` is provided, already-known
    listings are dropped before expensive LLM and commute work.

    Concretely the pipeline is:

      1. Both providers fetch in parallel; tax is applied per row.
      2. Each filtered row is pushed onto a queue.
      3. If ``llm=True``: a dispatcher pulls from that queue and spawns
         a worker per unseen row (capped at ``llm_concurrency``); workers
         run the LLM/date filters, then commute runs only for survivors.
      4. If ``llm=False``: unseen rows pass through after optional commute.
      5. The generator yields from the final queue until both providers
         and all workers are done.

    HomegateClient is held inside the homegate producer thread, so its
    Playwright + datadome session lives until iteration ends.
    """
    total = criteria.limit or 0
    half_a, half_b = _split_limit(total)
    crit_ff = replace(criteria, limit=half_a)
    crit_hg = replace(criteria, limit=half_b)

    provider_kw = dict(
        llm=False,                    # we run LLM ourselves below
        tax=tax,
        commute=False,                 # commute runs after LLM/date filters below
        google_maps_key=google_maps_key,
    )

    def _commute_one(listing: "Listing") -> "Listing" | None:
        if commute and google_maps_key:
            from .enrich import enrich_with_commute
            enrich_with_commute([listing], google_maps_key)
        if criteria.max_commute_min is not None:
            filtered = filter_by_commute([listing], criteria.max_commute_min)
            if not filtered:
                return None
        return listing

    raw_q: "queue.Queue[object]" = queue.Queue()
    skip_seen = skip_seen or set()

    def _is_seen(listing: "Listing") -> bool:
        if (listing.provider, str(listing.listing_id)) not in skip_seen:
            return False
        if on_skip_seen:
            on_skip_seen(listing)
        return True

    def _produce_flatfox() -> None:
        try:
            for l in FlatfoxClient().search_listings(crit_ff, **provider_kw):
                raw_q.put(l)
        except Exception as exc:
            logger.error("flatfox producer failed: %s", exc, exc_info=True)
        finally:
            raw_q.put(_SENTINEL)

    def _produce_homegate() -> None:
        try:
            with HomegateClient() as hg:
                for l in hg.search_listings(crit_hg, **provider_kw):
                    raw_q.put(l)
        except Exception as exc:
            logger.error("homegate producer failed: %s", exc, exc_info=True)
        finally:
            raw_q.put(_SENTINEL)

    threading.Thread(target=_produce_flatfox, daemon=True, name="flatfox-fetch").start()
    threading.Thread(target=_produce_homegate, daemon=True, name="homegate-fetch").start()

    if not llm:
        done = 0
        while done < 2:
            item = raw_q.get()
            if item is _SENTINEL:
                done += 1
                continue
            if _is_seen(item):  # type: ignore[arg-type]
                continue
            enriched = _commute_one(item)  # type: ignore[arg-type]
            if enriched is not None:
                yield enriched
        return

    # ---- LLM-enriched stream ------------------------------------------
    from .common import is_available_after_cutoff
    from .llm_extract import extract_listing_meta

    enriched_q: "queue.Queue[object]" = queue.Queue()

    def _llm_one(listing: "Listing") -> None:
        try:
            meta = extract_listing_meta(listing.description)
        except Exception as exc:
            logger.warning("LLM extract failed for %s: %s", listing.listing_id, exc)
            meta = {}
        if listing.bedrooms is None and meta.get("bedrooms") is not None:
            listing.bedrooms = meta["bedrooms"]
        if listing.is_temporary is None and meta.get("is_temporary") is not None:
            listing.is_temporary = meta["is_temporary"]
        if listing.is_furnished is None and meta.get("is_furnished") is not None:
            listing.is_furnished = meta["is_furnished"]
        if listing.available_from is None and meta.get("available_from") is not None:
            listing.available_from = meta["available_from"]
        if meta.get("has_washing_machine") is not None:
            listing.has_washing_machine = meta["has_washing_machine"]
        if is_available_after_cutoff(listing, criteria.available_on_or_before):
            return
        enriched = _commute_one(listing)
        if enriched is not None:
            enriched_q.put(enriched)

    def _dispatch() -> None:
        # Workers run on a cap-ed thread pool; raw rows are routed in
        # arrival order. Submit blocks once ``llm_concurrency`` workers
        # are busy, naturally back-pressuring the producers.
        with ThreadPoolExecutor(max_workers=max(1, llm_concurrency)) as pool:
            done = 0
            futures = []
            while done < 2:
                item = raw_q.get()
                if item is _SENTINEL:
                    done += 1
                    continue
                if _is_seen(item):  # type: ignore[arg-type]
                    continue
                futures.append(pool.submit(_llm_one, item))  # type: ignore[arg-type]
            for f in futures:
                f.result()
        enriched_q.put(_SENTINEL)

    threading.Thread(target=_dispatch, daemon=True, name="llm-dispatch").start()

    while True:
        item = enriched_q.get()
        if item is _SENTINEL:
            return
        yield item  # type: ignore[misc]


def search_all(
    criteria: "SearchCriteria",
    *,
    llm: bool = True,
    llm_concurrency: int = 4,
    tax: bool = True,
    commute: bool = False,
    google_maps_key: str | None = None,
    skip_seen: set[tuple[str, str]] | None = None,
) -> list["Listing"]:
    """Drain :func:`search_all_iter` into a list.

    Use the iterator form when you want to log or process listings as
    they arrive; this wrapper is just sugar for callers that want the
    full result in one shot.
    """
    return list(search_all_iter(
        criteria,
        llm=llm,
        llm_concurrency=llm_concurrency,
        tax=tax,
        commute=commute,
        google_maps_key=google_maps_key,
        skip_seen=skip_seen,
    ))
