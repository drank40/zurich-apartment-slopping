"""Headed Playwright recon: navigate each provider's SPA, capture XHR/fetch.

Saves a HAR file under docs/api/har/<provider>.har and a JSON list of XHR/fetch
requests with method, url, post body, response content type, and status to
docs/api/har/<provider>_xhr.json.

Usage:
    venv/bin/python scripts/recon_capture.py flatfox
    venv/bin/python scripts/recon_capture.py homegate
    venv/bin/python scripts/recon_capture.py comparis

Run with HEADLESS=1 to suppress the browser window. Default is headed so you
can solve any Cloudflare/DataDome challenges manually. After the page is
"ready" (URL stable, listings rendered), press Enter in the terminal.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HAR_DIR = ROOT / "docs" / "api" / "har"
HAR_DIR.mkdir(parents=True, exist_ok=True)

PROVIDERS = {
    "flatfox": {
        "url": "https://flatfox.ch/it/search/?query=Europaallee&place_name=Europaallee%2C+8004+Zurich%2C+Switzerland&north=47.42&south=47.32&east=8.63&west=8.45&min_rooms=3&is_furnished=true&is_swap=false&ordering=price_display&take=40&max_price=3600&is_temporary=false",
        "cookies_file": ROOT / "cookies_flatfox.txt",
    },
    "homegate": {
        "url": "https://www.homegate.ch/rent/real-estate/city-zurich/matching-list?ac=3&ah=3600",
        "cookies_file": ROOT / "cookies_homegate.txt",
    },
    "comparis": {
        "url": (
            "https://www.comparis.ch/immobilien/result/list?sort=11&requestobject="
            + json.dumps({
                "DealType": 10, "SiteId": 0, "RootPropertyTypes": [],
                "PropertyTypes": [], "RoomsFrom": "3", "PriceTo": "3600",
                "Radius": "6", "LocationSearchString": "8004", "Sort": 11,
                "SwapProperty": 1, "MinAvailableDate": "1753-01-01T00:00:00",
            })
        ),
        "cookies_file": ROOT / "cookies_comparis.txt",
    },
}


def parse_cookie_string(cookie_str: str, domain: str):
    cookies = []
    for part in cookie_str.strip().split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        cookies.append({
            "name": name.strip(),
            "value": value.strip(),
            "domain": domain,
            "path": "/",
        })
    return cookies


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in PROVIDERS:
        print(__doc__)
        sys.exit(1)
    provider = sys.argv[1]
    cfg = PROVIDERS[provider]
    url = cfg["url"]
    cookies_file: Path = cfg["cookies_file"]
    headless = os.environ.get("HEADLESS") == "1"

    parsed = urlparse(url)
    base_domain = parsed.netloc
    cookie_domain = "." + base_domain.lstrip(".")

    har_path = HAR_DIR / f"{provider}.har"
    xhr_log = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            record_har_path=str(har_path),
            record_har_content="embed",
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        if cookies_file.exists():
            content = cookies_file.read_text(encoding="utf-8")
            ck = parse_cookie_string(content, cookie_domain)
            try:
                context.add_cookies(ck)
                print(f"Loaded {len(ck)} cookies for {cookie_domain}")
            except Exception as e:
                print(f"Cookie load warning: {e}")

        page = context.new_page()

        def on_request(req):
            if req.resource_type in ("xhr", "fetch"):
                try:
                    post = req.post_data
                except Exception:
                    post = None
                xhr_log.append({
                    "method": req.method,
                    "url": req.url,
                    "resource_type": req.resource_type,
                    "post_data": post,
                    "headers": dict(req.headers),
                })

        page.on("request", on_request)

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type in ("xhr", "fetch"):
                    matching = next(
                        (e for e in reversed(xhr_log) if e["url"] == req.url and "status" not in e),
                        None,
                    )
                    if matching is not None:
                        matching["status"] = resp.status
                        matching["resp_content_type"] = resp.headers.get("content-type", "")
                        try:
                            body = resp.body()
                            ct = matching["resp_content_type"].lower()
                            if "json" in ct and len(body) < 200_000:
                                try:
                                    matching["resp_json_preview"] = json.loads(
                                        body.decode("utf-8", errors="replace")
                                    )
                                except Exception:
                                    matching["resp_text_preview"] = body[:1500].decode(
                                        "utf-8", errors="replace"
                                    )
                            elif len(body) < 4000:
                                matching["resp_text_preview"] = body[:4000].decode(
                                    "utf-8", errors="replace"
                                )
                        except Exception as e:
                            matching["resp_error"] = str(e)
            except Exception as e:
                print(f"on_response error: {e}")

        page.on("response", on_response)

        print(f"Navigating to {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        # Wait for SPA to settle
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(8000)

        if not headless:
            print("Browser window open. Solve any challenges, scroll if needed.")
            print("Press Enter when ready to capture...")
            try:
                input()
            except EOFError:
                pass

        # Final wait + scroll to trigger lazy loads
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(2000)
        except Exception:
            pass

        # Save full HTML too
        html_path = HAR_DIR / f"{provider}_final.html"
        try:
            html_path.write_text(page.content(), encoding="utf-8")
        except Exception:
            pass

        context.close()
        browser.close()

    xhr_path = HAR_DIR / f"{provider}_xhr.json"
    xhr_path.write_text(json.dumps(xhr_log, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {har_path} ({har_path.stat().st_size} bytes)")
    print(f"Wrote {xhr_path} ({len(xhr_log)} XHR/fetch entries)")


if __name__ == "__main__":
    main()
