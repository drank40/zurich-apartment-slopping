"""Capture Homegate contact-flow XHR/fetch calls.

This script does not bypass browser challenges. It reuses legitimate cookies
from .creds and, in headed mode, lets you solve/login manually before pressing
Enter. Captured files intentionally omit Cookie/Authorization headers.

Examples:
    python scripts/recon_homegate_contact.py --url https://www.homegate.ch/mieten/4003120054 --headed
    python scripts/recon_homegate_contact.py --url https://www.homegate.ch/mieten/4003120054 --headed --profile-dir .homegate-browser
    python scripts/recon_homegate_contact.py --url https://www.homegate.ch/mieten/4003120054 --cdp-url http://127.0.0.1:9222
    python scripts/recon_homegate_contact.py --url https://www.homegate.ch/rent/4003120054/contact --wait-seconds 45
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from contacting import homegate_cookies_from_creds  # noqa: E402
from creds import load_creds  # noqa: E402

SENSITIVE_HEADERS = {"authorization", "cookie", "proxy-authorization", "x-csrf-token", "x-xsrf-token"}
SENSITIVE_JSON_FIELDS = {
    "authorization",
    "email",
    "firstName",
    "lastName",
    "message",
    "phone",
    "recaptchaToken",
}


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in SENSITIVE_HEADERS:
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if key in SENSITIVE_JSON_FIELDS:
                redacted[key] = f"<redacted:{len(str(item))}>"
            else:
                redacted[key] = _redact_json(item)
        return redacted
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def _redacted_post_data(post_data: str | None) -> str | None:
    if not post_data:
        return post_data
    try:
        decoded = json.loads(post_data)
    except json.JSONDecodeError:
        return post_data
    return json.dumps(_redact_json(decoded), ensure_ascii=False)


def _looks_relevant(url: str) -> bool:
    lowered = url.lower()
    needles = (
        "api.homegate.ch",
        "/api/",
        "/contact",
        "/kontakt",
        "/inquiry",
        "/inquiries",
        "/lead",
        "/leads",
        "/message",
        "/messages",
        "/email",
        "/advertisement/",
    )
    return any(needle in lowered for needle in needles)


def _cookie_report(cookies: list[dict[str, object]]) -> list[dict[str, object]]:
    report = []
    for cookie in cookies:
        name = str(cookie.get("name", ""))
        if "homegate.ch" not in str(cookie.get("domain", "")):
            continue
        report.append({
            "name": name,
            "domain": cookie.get("domain"),
            "path": cookie.get("path"),
            "expires": cookie.get("expires"),
            "httpOnly": cookie.get("httpOnly"),
            "secure": cookie.get("secure"),
            "sameSite": cookie.get("sameSite"),
            "value_len": len(str(cookie.get("value", ""))),
        })
    return sorted(report, key=lambda item: str(item["name"]))


def _page_is_homegate(page) -> bool:
    try:
        return "homegate.ch" in page.url
    except Exception:
        return False


def _is_browser_challenge(title: str, html: str) -> bool:
    lowered_title = title.lower()
    lowered_html = html.lower()
    if "just a moment" in lowered_title:
        return True
    challenge_markers = ("cf-turnstile-response", "cf_challenge_response", "captcha-delivery.com")
    app_markers = ("pdp-contact-form", "hg-listing-details", "listingdetails_")
    return any(marker in lowered_html for marker in challenge_markers) and not any(
        marker in lowered_html for marker in app_markers
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Homegate listing/contact URL to open")
    parser.add_argument("--creds", type=Path, default=Path(".creds"))
    parser.add_argument("--out", type=Path, default=Path("/tmp/homegate_contact_api_calls.json"))
    parser.add_argument("--html-out", type=Path, default=Path("/tmp/homegate_contact_final.html"))
    parser.add_argument("--headed", action="store_true", help="Show browser for manual challenge/login/contact actions")
    parser.add_argument(
        "--profile-dir",
        type=Path,
        default=None,
        help="Persistent browser profile directory for solve/login once, then reuse",
    )
    parser.add_argument(
        "--browser",
        choices=["chromium", "camoufox"],
        default="chromium",
        help="Browser engine to use for the capture",
    )
    parser.add_argument(
        "--cdp-url",
        default=None,
        help="Connect to an already-running Chrome/Chromium over CDP instead of launching a browser",
    )
    parser.add_argument(
        "--use-current-page",
        action="store_true",
        help="In CDP mode, attach to an already-open Homegate tab instead of opening/navigating a new page",
    )
    parser.add_argument("--wait-seconds", type=int, default=20, help="Extra wait after first load in headless mode")
    args = parser.parse_args()

    creds = load_creds(args.creds)
    cookies = homegate_cookies_from_creds(creds)
    user_agent = creds.get("HOMEGATE_USER_AGENT") or (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    calls: list[dict[str, object]] = []

    browser_owner = None
    playwright_owner = None
    if args.cdp_url:
        from playwright.sync_api import sync_playwright
        playwright_owner = sync_playwright().start()
        browser_context = playwright_owner.chromium.connect_over_cdp(args.cdp_url)
    elif args.browser == "camoufox":
        try:
            from camoufox.sync_api import Camoufox  # type: ignore
        except ImportError as exc:
            raise SystemExit(
                "Camoufox is not installed. Install with "
                "`pip install 'camoufox[geoip]'` and fetch the browser with `camoufox fetch`."
            ) from exc
        browser_owner = Camoufox(headless=not args.headed, humanize=True, geoip=True, os=["windows"])
        browser_context = browser_owner.__enter__()
    else:
        from playwright.sync_api import sync_playwright
        playwright_owner = sync_playwright().start()
        browser_context = playwright_owner

    try:
        browser = None
        context_kwargs = {
            "viewport": {"width": 1440, "height": 1000},
            "locale": "en-US",
        }
        if args.browser != "camoufox" and not args.cdp_url and user_agent:
            context_kwargs["user_agent"] = user_agent
        if args.cdp_url:
            contexts = browser_context.contexts
            context = contexts[0] if contexts else browser_context.new_context()
        elif args.browser == "camoufox":
            if args.profile_dir:
                print("Ignoring --profile-dir for --browser camoufox; Camoufox manages its own context.")
            context = browser_context.new_context(**context_kwargs)
        elif args.profile_dir:
            context = browser_context.chromium.launch_persistent_context(
                str(args.profile_dir),
                headless=not args.headed,
                **context_kwargs,
            )
        else:
            browser = browser_context.chromium.launch(headless=not args.headed)
            context = browser.new_context(**context_kwargs)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = {runtime: {}};"
        )
        if cookies and not args.cdp_url:
            context.add_cookies(cookies)
            print(f"Loaded {len(cookies)} Homegate cookie(s) from {args.creds}")
        elif args.cdp_url:
            print(f"Connected to existing browser over CDP: {args.cdp_url}")
        else:
            print(f"No Homegate cookies found in {args.creds}")

        if args.cdp_url and args.use_current_page:
            existing_pages = [page for page in context.pages if _page_is_homegate(page)]
            if not existing_pages:
                existing_pages = list(context.pages)
            if not existing_pages:
                raise RuntimeError("No existing CDP pages found. Open Homegate in Chrome first.")
            page = existing_pages[-1]
            print(f"Using existing page: {page.url}")
        else:
            page = context.new_page()

        def on_request(req):
            if req.resource_type not in {"xhr", "fetch", "document"}:
                return
            if not _looks_relevant(req.url):
                return
            calls.append({
                "method": req.method,
                "url": req.url,
                "resource_type": req.resource_type,
                "post_data": _redacted_post_data(req.post_data),
                "headers": _redacted_headers(dict(req.headers)),
            })

        def on_response(resp):
            try:
                req = resp.request
                if not _looks_relevant(req.url):
                    return
                matching = next(
                    (
                        entry for entry in reversed(calls)
                        if entry["url"] == req.url and "status" not in entry
                    ),
                    None,
                )
                if matching is None:
                    matching = {
                        "method": req.method,
                        "url": req.url,
                        "resource_type": req.resource_type,
                        "post_data": _redacted_post_data(req.post_data),
                        "headers": _redacted_headers(dict(req.headers)),
                    }
                    calls.append(matching)
                matching["status"] = resp.status
                matching["resp_content_type"] = resp.headers.get("content-type", "")
                try:
                    body = resp.body()
                except BaseException as exc:
                    matching["resp_error"] = f"{type(exc).__name__}: {exc}"
                    return
                if len(body) <= 200_000:
                    text = body.decode("utf-8", errors="replace")
                    if "json" in str(matching["resp_content_type"]).lower():
                        try:
                            matching["resp_json"] = _redact_json(json.loads(text))
                        except json.JSONDecodeError:
                            matching["resp_text"] = text[:4000]
                    else:
                        matching["resp_text"] = text[:4000]
            except BaseException as exc:
                calls.append({"capture_error": repr(exc)})

        page.on("request", on_request)
        page.on("response", on_response)

        if args.cdp_url and args.use_current_page:
            print("Observing existing page; interact with that Chrome tab if needed.")
        else:
            print(f"Opening {args.url}")
            try:
                page.goto(args.url, wait_until="domcontentloaded", timeout=90000)
            except Exception as exc:
                print(f"Navigation warning: {exc}")
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except Exception:
                pass

        title = page.title()
        html = page.content()
        print(f"Final URL: {page.url}")
        print(f"Title: {title}")
        loaded_cookie_report = _cookie_report(context.cookies("https://www.homegate.ch/"))
        print("Homegate cookies in browser context:")
        for cookie in loaded_cookie_report:
            print(
                "  "
                f"{cookie['name']} domain={cookie['domain']} "
                f"secure={cookie['secure']} httpOnly={cookie['httpOnly']} "
                f"sameSite={cookie['sameSite']} value_len={cookie['value_len']}"
            )
        challenge = _is_browser_challenge(title, html)
        if challenge:
            print("Page is still at a browser challenge; no contact endpoint can be captured until the app loads.")

        if args.headed or (args.cdp_url and args.use_current_page):
            print("Use the browser normally: solve/login/open contact/send a harmless test message if intended.")
            print("Press Enter here when finished; the script will save captured XHR/fetch calls.")
            try:
                input()
            except EOFError:
                pass
        else:
            page.wait_for_timeout(max(args.wait_seconds, 0) * 1000)

        try:
            args.html_out.write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            print(f"HTML write warning: {exc}")

        if not args.cdp_url:
            context.close()
            if browser:
                browser.close()
    finally:
        if browser_owner:
            browser_owner.__exit__(None, None, None)
        if playwright_owner:
            playwright_owner.stop()

    args.out.write_text(json.dumps(calls, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {args.out} with {len(calls)} relevant request(s)")
    print(f"Wrote {args.html_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
