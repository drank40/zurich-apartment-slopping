"""Open homegate.ch in Playwright, wait for the SPA to make XHR calls,
and capture the URL + headers + body of the search API call(s).

We let DataDome run, then either succeed or get the cookie. The script
records ANY request hitting api.homegate.ch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HAR = ROOT / "docs" / "api" / "har"
HAR.mkdir(parents=True, exist_ok=True)

URL = "https://www.homegate.ch/rent/real-estate/city-zurich/matching-list?ac=3&ah=3600"


def main():
    api_calls = []
    with sync_playwright() as p:
        # Headed mode: chance to solve captcha if needed
        browser = p.chromium.launch(
            headless=os.environ.get("HEADLESS") == "1",
            args=["--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = context.new_page()

        def on_req(req):
            if "api.homegate.ch" in req.url or "/an/" in req.url or "/api/" in req.url:
                api_calls.append({
                    "method": req.method,
                    "url": req.url,
                    "headers": dict(req.headers),
                    "post_data": req.post_data,
                })

        def on_resp(resp):
            try:
                req = resp.request
                if "api.homegate.ch" in req.url or "/an/" in req.url:
                    matching = next(
                        (e for e in reversed(api_calls)
                         if e["url"] == req.url and "status" not in e),
                        None,
                    )
                    if matching is not None:
                        matching["status"] = resp.status
                        ct = resp.headers.get("content-type", "")
                        matching["resp_content_type"] = ct
                        try:
                            body = resp.body()
                            if "json" in ct.lower() and len(body) < 500_000:
                                try:
                                    matching["resp_json"] = json.loads(
                                        body.decode("utf-8", errors="replace")
                                    )
                                except Exception:
                                    matching["resp_text"] = body[:5000].decode(
                                        "utf-8", errors="replace"
                                    )
                            else:
                                matching["resp_text"] = body[:2000].decode(
                                    "utf-8", errors="replace"
                                )
                        except Exception as e:
                            matching["resp_err"] = str(e)
            except Exception:
                pass

        page.on("request", on_req)
        page.on("response", on_resp)

        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"goto warning: {e}")
        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        try:
            page.wait_for_timeout(8000)
        except Exception:
            pass
        # Try a scroll
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)
        except Exception:
            pass

        # Save final page + cookies
        try:
            (HAR / "homegate_final2.html").write_text(
                page.content(), encoding="utf-8"
            )
        except Exception:
            pass
        cookies = context.cookies()
        (HAR / "homegate_cookies.json").write_text(
            json.dumps(cookies, indent=2), encoding="utf-8"
        )

        context.close()
        browser.close()

    out = HAR / "homegate_api_calls.json"
    out.write_text(json.dumps(api_calls, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {out} with {len(api_calls)} entries")


if __name__ == "__main__":
    main()
