# Homegate.ch API

Homegate's SPA (Vue 3 / Nuxt) uses an internal JSON API hosted at
`https://api.homegate.ch`. The endpoints we use are:

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/search/listings` | POST | Run a structured listing search |
| `/geo/locations-by-id` | GET | Lookup geo tags by id (rarely needed) |
| `/points-of-interest-api/custom-points/default` | POST | POI distances (we do this ourselves) |

**The detail endpoint (`/listing/listings/<id>`) is NOT public** — it
returns 403 with `application/xml` even with valid cookies. Detail data is
sufficient inside the search response under `results[*].listing`.

## Auth: DataDome cookie

Every request to `api.homegate.ch` is gated by DataDome bot mitigation. A
valid `datadome` cookie is required. Without it the API returns 403 plus a
JSON body referencing `https://geo.captcha-delivery.com/captcha/...`.

The cookie is a JWT-like string of ~120 characters. It is set via:

* Visiting `https://www.homegate.ch/` in a real browser (or Playwright with
  webdriver/automation hints disabled).
* Solving the visual CAPTCHA when DataDome challenges. In our recon a
  headed-but-stealthed Chromium was issued the cookie automatically.

In testing the cookie is **portable** across plain HTTP clients: copying
the value into `httpx`/`requests` works and remains valid for at least
several hours, surviving a User-Agent mismatch in recon.

The bootstrap helper `homegate_api.bootstrap_datadome_cookie()` automates
this.

## `POST /search/listings`

### Request headers (sufficient set)

```
User-Agent: <any plausible Chromium UA>
Accept: application/json, text/plain, */*
Accept-Language: en-US,en;q=0.9
Origin: https://www.homegate.ch
Referer: https://www.homegate.ch/
Content-Type: application/json
Cookie: datadome=<value>
```

### Request body

```jsonc
{
  "query": {
    "offerType": "RENT",                          // RENT | BUY
    "categories": ["APARTMENT", "HOUSE"],         // see below for more
    "location": {
      "geoTags": ["geo-city-zurich"]              // multiple tags allowed
    },
    "monthlyRent": {"to": 3600},                   // {from, to}
    "numberOfRooms": {"from": 3},                  // {from, to}
    "livingSpace": {"from": 50, "to": 150}        // optional, m²
    // "ids": ["4002964983"]                         // single-listing fetch
  },
  "sortBy": "monthlyRent",                          // see allowed values
  "sortDirection": "asc",                           // asc | desc
  "from": 0,
  "size": 20,
  "trackTotalHits": true,
  "fieldset": "srp-list"                            // only "srp-list" works
}
```

**Allowed `sortBy` values** (server told us in a 400):
`place`, `purchasePrice`, `monthlyRent`, `numberOfRooms`, `dateCreated`,
`listingType`, `listingCompleteness`, `random`, `exclusive`.

**Common geo tags:** `geo-city-zurich`, `geo-canton-zurich`,
`geo-zipcode-8004`, `geo-cityregion-kreis-4`, etc. Each ZIP/Kreis is its
own tag. The geo lookup endpoint resolves these from a flat list.

### Response (`200 OK`)

```jsonc
{
  "from": 0,
  "size": 20,
  "total": 312,                                  // total hits
  "maxFrom": 1000,                               // server-side pagination cap
  "results": [
    {
      "listingType": {"type": "STANDARD"},
      "id": "4002964983",
      "listing": {
        "id": "4002964983",
        "offerType": "RENT",
        "categories": ["APARTMENT", "FLAT"],
        "address": {
          "street": "Bachmattstrasse 46",
          "postalCode": "8048",
          "locality": "Zürich",
          "geoCoordinates": {"latitude": 47.389..., "longitude": 8.483..., "accuracy": "HIGH"}
        },
        "characteristics": {"numberOfRooms": 3, "livingSpace": 65, ...},
        "prices": {"rent": {"gross": 2900, "interval": "MONTH"}},
        "localization": {
          "primary": "de",
          "de": {"text": {"title": "Wohnung in Zürich", "description": "..."}}
        },
        "meta": {"createdDate": "...", "updatedDate": "...", "availableFrom": "..."},
        "platforms": ["HOMEGATE"]
      },
      "listingCard": { /* CDN image URLs */ },
      "listingScores": { /* relevance scores */ },
      "listerBranding": { /* agency info */ }
    },
    ...
  ]
}
```

### Quirks / limits

* `maxFrom: 1000` — you can't page past the 1000th result; refine the query.
* `fieldset` only accepts `"srp-list"` (other guesses 422). It already
  contains title + description + characteristics + address — no separate
  detail call needed.
* DataDome cookie can be invalidated by a TLS fingerprint mismatch. If a
  long-running httpx loop suddenly starts getting 403 captcha responses,
  re-bootstrap via Playwright.
* Responses are gzipped; both `httpx` and `requests` handle this transparently.
* Soft rate limit: ~2 req/s observed safe. We pace at 1s/page.
* Single-listing fetch via `query.ids = ["..."]` works.

## Example replay

```bash
DATADOME='paste-value-here'
curl -sX POST 'https://api.homegate.ch/search/listings' \
  -H "Cookie: datadome=${DATADOME}" \
  -H "Origin: https://www.homegate.ch" \
  -H "Referer: https://www.homegate.ch/" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Mozilla/5.0" \
  --data '{
    "query":{"offerType":"RENT","categories":["APARTMENT","HOUSE"],
              "location":{"geoTags":["geo-city-zurich"]},
              "monthlyRent":{"to":3600},"numberOfRooms":{"from":3}},
    "sortBy":"monthlyRent","sortDirection":"asc","from":0,"size":3,
    "trackTotalHits":true,"fieldset":"srp-list"}' \
  | jq '.total, (.results[0] | {id, listing: {address, prices, characteristics}})'
```
