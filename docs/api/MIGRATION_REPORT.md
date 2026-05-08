# API Migration Report

Branch: `api-migration`
Date: 2026-05-08
Working copy: `/home/renny/doc/suisse/zurich-apartment-slopping-api/`
Live project (untouched): `/home/renny/doc/suisse/zurich-apartment-slopping/`

## Summary

| Provider | Before | After | Notes |
|----------|--------|-------|-------|
| **flatfox.ch** | Headed Playwright -> HTML scrape -> per-listing detail page fetch | Pure HTTP REST API (`/api/v1/pin/` + `/api/v1/public-listing/`) | Big win: no Playwright, no auth, full data in 2 calls. |
| **homegate.ch** | Headed Playwright -> Nuxt SSR HTML -> regex pull `__INITIAL_STATE__` | `POST https://api.homegate.ch/search/listings` with a one-shot bootstrapped `datadome` cookie | Medium win: Playwright still needed once at startup (or whenever cookie expires) but every subsequent call is httpx. |
| **comparis.ch** | Headed Playwright -> Next.js SSR HTML -> pull `__NEXT_DATA__` | Same Playwright transport, but cleanly wrapped as `ComparisClient`; treats `__NEXT_DATA__` as the JSON API surface | No transport-layer win — DataDome fingerprints the client and rejects every non-Playwright replay we tried, including ones with a freshly-issued cookie + matching User-Agent + full Sec-Ch-UA headers. |

## Code changes

### New
- `src/providers/__init__.py` — re-exports
- `src/providers/flatfox_api.py` — `FlatfoxClient`, `mapping_to_listing`
- `src/providers/homegate_api.py` — `HomegateClient`, `bootstrap_datadome_cookie`, `build_query_from_config`, `mapping_to_listing`
- `src/providers/comparis_api.py` — `ComparisClient` (Playwright-backed), `mapping_to_listing`
- `tests/api/test_flatfox_mapping.py` (4 tests)
- `tests/api/test_homegate_mapping.py` (4 tests)
- `tests/api/test_comparis_mapping.py` (4 tests)
- `tests/api/fixtures/{flatfox_pin,flatfox_listings,homegate_search,comparis_next_data}.json` — recorded responses for offline tests
- `scripts/recon_capture.py` and `scripts/recon_homegate_api.py` — Playwright-driven HAR/XHR recon utilities (kept for future spec changes)
- `docs/api/flatfox.md`, `docs/api/homegate.md`, `docs/api/comparis.md` — endpoint specs
- `docs/api/har/{flatfox.har, flatfox_xhr.json, homegate.har, homegate_api_calls.json, homegate_cookies.json, comparis.har, comparis_xhr.json, comparis_cookies.json, comparis_final.html, ...}` — captured network evidence

### Modified
- `src/apartment_finder_llm.py`:
  - Added `search_provider_via_api()` — tries the JSON API path first per provider.
  - `run()` calls the API path; on failure falls back to the legacy Playwright-HTML path. New `--legacy` flag forces the old path.
  - When the API path supplies descriptions + coordinates, `hydrate_details` skips spinning up Playwright entirely.
  - `requirements.txt` is unchanged (no new mandatory deps; flatfox uses `requests`, homegate uses `requests`, comparis uses `playwright` already in deps).

### Untouched
- `src/auto_contact.py` — out of scope.
- LLM extraction path, dashboard/HTML generation, filtering, commute calculation.

## What works (verified)

```
$ ./venv/bin/python -m pytest tests/api/ -v
========================= 12 passed in 0.26s =========================

$ ./venv/bin/python src/apartment_finder_llm.py --no-llm --providers flatfox --limit 5
[flatfox] Trying API path...
[flatfox] API search returned 5 listings
[flatfox] API supplied full data; skipping Playwright detail fetch

$ ./venv/bin/python src/apartment_finder_llm.py --no-llm --providers homegate --limit 3
[homegate] Trying API path...
[homegate] API search returned 3 listings
[homegate] API supplied full data; skipping Playwright detail fetch

$ ./venv/bin/python -c "with ComparisClient(...): client.search(...)"
# Returns 5 hydrated cards from __NEXT_DATA__ in ~7 seconds
```

End-to-end runs touch the live filtering / LLM / dashboard pipeline unchanged.

## What remains scrape-only

* **Comparis search & detail.** DataDome blocks every non-Playwright client.
  See `docs/api/comparis.md` for details and replay attempts. The
  `ComparisClient` we ship still uses Playwright as transport, but presents
  a clean API surface to callers.

## Endpoint reference (one-line each)

* **flatfox** `GET https://flatfox.ch/api/v1/pin/?north=&south=&east=&west=&min_rooms=&max_price=&is_furnished=&is_swap=&is_temporary=&max_count=&ordering=`
* **flatfox** `GET https://flatfox.ch/api/v1/public-listing/?pk=<n>&pk=<n>&...&limit=0&expand=cover_image`
* **homegate** `POST https://api.homegate.ch/search/listings` with body `{"query":{...},"sortBy":"monthlyRent","sortDirection":"asc","from":0,"size":20,"trackTotalHits":true,"fieldset":"srp-list"}` — needs `Cookie: datadome=...`
* **comparis** Playwright-rendered `https://www.comparis.ch/immobilien/result/list?sort=11&requestobject=<json>` then `JSON.parse(<script id="__NEXT_DATA__">.textContent).props.pageProps.initialResultData`

## Auth / cookies cheat-sheet

| Provider | Required | How obtained | Lifetime |
|----------|----------|--------------|----------|
| flatfox | None | n/a | n/a |
| homegate | `datadome` cookie only | `bootstrap_datadome_cookie()` (Playwright once) → cached to `cookies_homegate.txt` | Hours; auto-rebootstrap on 403 captcha |
| comparis | Full Playwright session (datadome + ASP.NET sessions + cf cookies + JS fingerprint) | The `ComparisClient` itself; cookies persisted via `cookies_comparis.txt` if provided | Per-process (Playwright context) |

## Methodology (how we verified)

1. Headed/headless Playwright recon in `scripts/recon_capture.py`. Captures
   HAR + XHR JSON to `docs/api/har/`. We saved the recorded responses as
   ground truth.
2. For each captured `/api/...` URL we replayed with `httpx` to isolate the
   minimum auth set (no cookies / UA only / Sec-Ch-UA / etc.).
3. Recorded fixtures are committed; tests don't touch the network.

## How to verify (commands)

```bash
# Activate env (nix shell sets LD_LIBRARY_PATH for chromium)
nix-shell        # OR: LD_PRELOAD=/usr/lib/libstdc++.so.6 source venv/bin/activate

# Unit tests (no network)
./venv/bin/python -m pytest tests/api/ -v

# Live smoke (flatfox - no auth)
./venv/bin/python src/apartment_finder_llm.py --no-llm --providers flatfox --limit 5

# Live smoke (homegate - first run will pop a Playwright window briefly)
./venv/bin/python src/apartment_finder_llm.py --no-llm --providers homegate --limit 5

# Force the old path
./venv/bin/python src/apartment_finder_llm.py --no-llm --providers flatfox --limit 5 --legacy

# Refresh API recon (creates HAR + XHR JSON in docs/api/har/)
./venv/bin/python scripts/recon_capture.py flatfox
./venv/bin/python scripts/recon_capture.py homegate
./venv/bin/python scripts/recon_capture.py comparis
```

## Judgment calls / trade-offs

* **Comparis kept Playwright-backed.** Tried hard to call the
  `_next/data/<buildId>/.../list.json` endpoint with copied cookies +
  full Sec-Ch-UA + Sec-Fetch headers; DataDome's TLS+JS fingerprint check
  rejected every replay. Worth retrying if/when comparis loosens DataDome
  config. The cleanly-encapsulated `ComparisClient` keeps that swap a
  single-class change.
* **Homegate cookie bootstrap is interactive on first run.** If DataDome
  shows a CAPTCHA, the user must solve it in the popped Playwright window.
  Subsequent runs reuse the cached cookie until it expires.
* **Flatfox `/pin/` `max_count`** capped at 1000 (server-side); for very
  broad searches you'd need to subdivide the bbox. Not a real concern for
  Zurich + 3 rooms + ≤3600 CHF (fewer than 100 hits).
* **No new mandatory deps.** `httpx` is installed in the dev venv but the
  shipped clients use `requests` to keep `requirements.txt` unchanged.
* **Did not modify `auto_contact.py`** per instructions.

## Branch / commit layout

The work is on branch `api-migration`. Commits are intentionally small and
reversible:
1. Add provider clients
2. Add API specs and HAR fixtures
3. Refactor runner
4. Add tests
