# Contacting listings

Contacting is driven by the poll CSV. The CSV is the durable state store:

- `contact_status=sent` or a non-empty `contacted_at` means the row is skipped.
- Failed rows keep `contacted_at` empty and can be retried until `--max-attempts`.
- Dry runs never mark a row as contacted.

## Test safely

Use a test CSV or explicitly avoid rows that look like they match the current
target filters:

```bash
python scripts/contact_from_csv.py \
  --csv output/test_listings.csv \
  --only-non-matching-current-filters \
  --limit 3
```

The default is dry-run. Add `--send` only when you want to submit messages.

## Contact pending rows

Rows are processed highest `listing_score` first:

```bash
python scripts/contact_from_csv.py --provider flatfox --limit 5
python scripts/contact_from_csv.py --send --provider flatfox --limit 5
```

## Poll and contact new rows

`poll.py --contact` only considers rows first seen during that poll pass:

```bash
python scripts/poll.py --contact --contact-limit-per-pass 5
python scripts/poll.py --contact --contact-send --contact-limit-per-pass 5
```

## Credentials

`.creds` can contain:

```text
FLATFOX_EMAIL=...
FLATFOX_PASSWORD=...
FLATFOX_DEVICE_COOKIE=...
FLATFOX_OTP=...                # optional, only for one verification login

CONTACT_FIRST_NAME=...
CONTACT_LAST_NAME=...
CONTACT_EMAIL=...
CONTACT_PHONE=...
CONTACT_STREET=...
CONTACT_ZIP=...
CONTACT_CITY=...

HOMEGATE_DATADOME_COOKIE=...
HOMEGATE_COOKIE=...            # optional full Cookie header from www.homegate.ch
HOMEGATE_CF_CLEARANCE=...      # optional Cloudflare clearance cookie, if present
HOMEGATE_USER_AGENT=...        # exact navigator.userAgent from the browser that issued cf_clearance
HOMEGATE_PROFILE_DIR=.homegate-browser
HOMEGATE_CDP_URL=http://127.0.0.1:9222
```

Flatfox uses an authenticated HTTP submit endpoint. Homegate currently uses an
isolated browser-form sender because no stable JSON send endpoint has been
captured in this codebase yet. If `HOMEGATE_DATADOME_COOKIE` is present, the
browser sender preloads it before opening the contact page. For Homegate contact
pages, the DataDome cookie alone is usually not enough: listing search hits
`api.homegate.ch`, while the contact flow first has to load the
`www.homegate.ch` browser app and may require a normal trusted browser session.

## Capture Homegate contact endpoint

Use a harmless non-target listing and run the capture in headed mode:

```bash
python scripts/recon_homegate_contact.py \
  --url https://www.homegate.ch/mieten/4003120054 \
  --headed
```

The script does not bypass browser challenges. It reuses cookies from `.creds`,
then lets you solve/login/click manually in the browser. It writes redacted
request details to `/tmp/homegate_contact_api_calls.json` and the final page to
`/tmp/homegate_contact_final.html`.

If copied cookies do not pass the `www.homegate.ch` browser challenge, use a
persistent browser profile:

```bash
python scripts/recon_homegate_contact.py \
  --url https://www.homegate.ch/mieten/4003120054 \
  --headed \
  --profile-dir .homegate-browser
```

Solve/login once in that window, then keep `HOMEGATE_PROFILE_DIR=.homegate-browser`
in `.creds` so the Homegate sender reuses the same browser state.

Cloudflare clearance cookies can be tied to the browser fingerprint. If you
copy `cf_clearance` from another browser, also copy `navigator.userAgent` from
that same browser into `HOMEGATE_USER_AGENT`.

The capture script also has an optional Camoufox mode, matching the Homegate API
client's preferred low-block browser when available:

```bash
python scripts/recon_homegate_contact.py \
  --url https://www.homegate.ch/mieten/4003120054 \
  --browser camoufox
```

Camoufox is optional. Install it only in environments where you need it:
`pip install 'camoufox[geoip]'` and then `camoufox fetch`.

If copied cookies still fail, capture from a real browser that already passes
Homegate. Start Chrome with remote debugging, then run the capture against that
browser:

```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/homegate-debug-profile
```

Log into Homegate in that Chrome window, then in another terminal:

```bash
python scripts/recon_homegate_contact.py \
  --url https://www.homegate.ch/mieten/4003120054 \
  --cdp-url http://127.0.0.1:9222
```

This does not replay cookies into a synthetic browser; it observes the browser
session that you control.

The captured submit endpoint is:

```text
POST https://api.homegate.ch/inquiries/contact-request
```

It returned `201` in the live non-target test. The request requires a logged-in
browser `Authorization` header and a fresh `recaptchaToken`, so the production
sender uses the Homegate page form in a trusted browser context instead of
trying to replay a static server-side POST.
