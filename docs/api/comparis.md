# Comparis.ch API

**TL;DR — Comparis has no callable JSON backend for unauthenticated clients.**
Their listing UI is a Next.js app with **server-side rendering**: the
search response is embedded in `<script id="__NEXT_DATA__">` of the HTML.
The Next.js `_next/data/<buildId>/...json` route exists, but DataDome
fingerprints the client and rejects every replay we made — even when the
exact `datadome` cookie issued during a successful Playwright session was
copied into `httpx`/`requests` with full Sec-Ch-UA and Sec-Fetch headers.

So our "API client" is Playwright-driven: we render the SSR page, parse
`__NEXT_DATA__`, and treat the embedded JSON as the response. This is
strictly better than DOM scraping — the JSON is the source of truth the
page renders from.

## URLs

| URL | Purpose |
|-----|---------|
| `https://www.comparis.ch/immobilien/result/list?sort=11&requestobject=...` | Search results page |
| `https://www.comparis.ch/immobilien/marktplatz/details/show/<adId>` | Detail page |

## Search query

The single user-controlled query param is `requestobject`, a URL-encoded
JSON object:

```json
{
  "DealType": 10,                    // 10 = rent
  "SiteId": 0,
  "RootPropertyTypes": [],
  "PropertyTypes": [],               // ["APARTMENT","HOUSE"] etc. or [] for any
  "RoomsFrom": "3",
  "RoomsTo": null,
  "PriceTo": "3600",
  "PriceFrom": null,
  "Radius": "6",
  "LocationSearchString": "8004",   // ZIP, city, address; geocoded server-side
  "Sort": 11,                        // 11 = price asc, 12 = price desc, etc.
  "SwapProperty": 1,
  "MinAvailableDate": "1753-01-01T00:00:00"
}
```

## Embedded JSON: `props.pageProps.initialResultData`

```jsonc
{
  "props": {
    "pageProps": {
      "initialResultData": {
        "adIds": [37251774, 37251745, ...],         // all matched ids (paginated)
        "resultItems": [                              // hydrated cards for current page
          {
            "AdId": 37251774,
            "Title": "Schöne 3.5-Zimmer Wohnung",
            "Address": ["Musterstrasse 42", "8004 Zürich"],
            "Price": "CHF 3'200.-",
            "PriceValue": 3200,
            "Rooms": 3.5,
            "Latitude": 47.376,
            "Longitude": 8.534,
            "EssentialInformation": ["3.5 Zimmer", "85 m²", "Verfügbar ab ..."],
            "ProviderName": "...",
            "Images": [...]
          },
          ...
        ],
        "totalCount": 412,
        "currentPage": 1,
        "totalPages": 17
      },
      "buildId": "QUbDbIka5yrofxolYvUu4"
    }
  }
}
```

## Detail page

`window.__NEXT_DATA__.props.pageProps` keys vary by ad type but commonly
contain `ad`, `advertisement`, or `initialDetailData`, with full
description, contact info, all images, and structured features. We surface
whichever is present.

## Auth & quirks

* **DataDome.** Cookies needed: `datadome`, `__Secure-Gw-Session`,
  `ASP.NET_SessionId`, `__cf_bm`, all issued during the Playwright load.
  Cookies cannot be replayed in plain HTTP clients (TLS+JS fingerprint
  binding). Playwright is mandatory.
* **Cookie consent.** The site sometimes requires a click on the cookie
  banner before the SSR populates `resultItems`. In headed mode with
  `manual_continue=True` the user can dismiss it; in headless we let the
  banner sit and ignore it (the SSR data is already present, the banner
  doesn't gate it).
* **Pagination.** `resultItems` only covers the current SSR page (~25
  items). The `adIds` array contains every matched id; we use those to
  drive subsequent detail fetches. To paginate the cards, append `&page=2`
  to the URL.
* **Build id rotation.** `buildId` rotates on every comparis deploy (~once
  a week). Don't hardcode it; extract from the page if you need the
  `_next/data` URL pattern.
* **Rate limits.** No documented limit. We pace detail fetches at 1s and
  reuse the same Playwright context to amortize TLS / JS warmup.

## Example replay (Python with Playwright)

```python
from src.providers.comparis_api import ComparisClient
from src.apartment_finder_llm import Listing

with ComparisClient(headless=False, manual_continue=True) as c:
    items = c.search({
        "DealType": 10, "RoomsFrom": "3", "PriceTo": "3600",
        "LocationSearchString": "8004", "Radius": "6", "Sort": 11,
        "SwapProperty": 1, "MinAvailableDate": "1753-01-01T00:00:00",
    }, limit=20)
    for item in items[:3]:
        if item.get("_partial"):
            detail = c.fetch_detail(item["AdId"])
        print(item.get("Title"), item.get("PriceValue"))
```
