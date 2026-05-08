# Flatfox.ch API

Flatfox exposes a public REST API at `https://flatfox.ch/api/v1/`. The site's
own SPA uses these endpoints with no special authentication. Both
authenticated and unauthenticated requests work; we use the unauthenticated
path.

## Endpoints we use

### `GET /api/v1/pin/`

Returns a list of "pin" descriptors (used by the SPA to render the map).
Filters by geographic bounding box and structured criteria.

**Query parameters (all optional, all provided as flat key=value):**

| Param | Type | Notes |
|-------|------|-------|
| `north` / `south` / `east` / `west` | float | Bounding box (decimal degrees) |
| `min_rooms` | float | Minimum room count, e.g. `3` |
| `max_rooms` | float | Maximum room count |
| `min_price` / `max_price` | int | CHF/month |
| `is_furnished` | "true"/"false" | Lowercase string, not boolean |
| `is_temporary` | "true"/"false" | Same |
| `is_swap` | "true"/"false" | Same |
| `ordering` | string | `price_display` for cheapest first, `-published` for newest |
| `max_count` | int | Cap on returned pins (default 400 in SPA, we use 1000) |

**Auth:** None. `User-Agent` and `Accept: application/json` headers are
sufficient. CORS-style headers (`Origin`, `Referer`) are not needed.

**Response (JSON list):**

```json
[
  {
    "pk": 85947313,
    "smg_id": "",
    "latitude": 47.4127917,
    "longitude": 8.6009386,
    "price_display": 2940,
    "price_display_type": "TOTAL",
    "price_unit": "monthly",
    "selling_price": null,
    "is_in_region": true,
    "is_liked": false,
    "is_disliked": false,
    "like_status": null,
    "flatfox_priority_exclusive_until": "2026-05-15T23:59:59+02:00"
  },
  ...
]
```

**Pagination:** None — single response capped by `max_count`. To page deeper,
adjust the bbox or filters.

### `GET /api/v1/public-listing/`

Full listing detail. Accepts repeated `pk=` params to batch many listings in
one call.

**Query parameters:**

| Param | Type | Notes |
|-------|------|-------|
| `pk` | int (repeatable) | Filter to specific listing pks |
| `limit` | int | `0` disables pagination (returns all matched) |
| `expand` | string (repeatable) | E.g. `cover_image` to include image objects |
| `include` | string (repeatable) | E.g. `is_liked` (requires session). Optional. |
| Various filters | | The endpoint also accepts `object_category`, `offer_type`, etc., but bounding-box / room / price filters do not appear to apply here — use `/pin/` for filtered discovery. |

**Auth:** None for public mode. With session cookies extra fields (`is_liked`, etc.) populate.

**Response:** When `pk=` provided, a JSON list of listing objects. Without
`pk`, a paginated object `{"count":N,"next":"...","previous":null,"results":[...]}`.

**Listing object key fields:**

```
pk, slug, url, short_url, submit_url
status, offer_type ("RENT"/"SELL"), object_category, object_type
public_title, description_title, short_title, description
rent_net, rent_charges, rent_gross, price_display, price_unit
surface_living, surface_property, surface_usable, number_of_rooms, floor
is_furnished, is_temporary, is_selling_furniture, is_swap
street, zipcode, city, public_address, latitude, longitude
moving_date_type, moving_date            // "agr"/"after"/null + ISO date
attributes (list of feature codes)
images (list of image ids), cover_image, documents
agency: {name, name_2, street, zipcode, city, country, logo:{url,...}}
```

## Quirks / limits

* `/pin/` accepts boolean params only as lowercase strings (`"true"`/`"false"`).
  Python booleans must be coerced.
* `/public-listing/` ignores most filter params unless `pk=` is provided.
  Use `/pin/` to discover pks first.
* `object_category` covers more than apartments — `PARK`, `GARAGE_SLOT`,
  `OFFICE`, etc. Filter by `object_category in {"APAR","HOUS"}` (or by
  presence of `number_of_rooms`) to keep only homes.
* `object_type=SHARED` indicates a shared flat / WG.
* Soft rate limit: behaves fine at ~2 req/s. We set a 0.5s delay between
  detail batches just to be polite.
* Cloudflare may inject a JS challenge in extreme cases (saw none during
  recon). If that happens, refresh `cf_clearance` via Playwright.

## Example replays

```bash
# Pins for Zurich bbox, furnished 3+ rooms <= 3600 CHF
curl -s 'https://flatfox.ch/api/v1/pin/?north=47.42&south=47.32&east=8.63&west=8.45&min_rooms=3&max_price=3600&is_furnished=true&is_swap=false&is_temporary=false&max_count=400&ordering=price_display' | jq 'length'

# Full data for two pks
curl -s 'https://flatfox.ch/api/v1/public-listing/?pk=85947313&pk=85988959&limit=0&expand=cover_image' | jq '.[0] | {pk,public_title,rent_gross,number_of_rooms}'
```
