"""Contact listings from the poll CSV.

The CSV is the durable state store: a row is contacted at most once when
``contact_status=sent`` or ``contacted_at`` is present. Dry runs never mark a
row as contacted.
"""
from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Protocol
from urllib.parse import urljoin

try:  # package import, e.g. ``from src.contacting import ...``
    from .creds import load_creds
    from .providers.flatfox_api import API_ROOT as FLATFOX_ROOT
    from .providers.flatfox_api import DeviceNotVerifiedError, FlatfoxClient
    from .providers.scoring import score_csv_row
except ImportError:  # script import after adding ``src`` to sys.path
    from creds import load_creds  # type: ignore
    from providers.flatfox_api import API_ROOT as FLATFOX_ROOT  # type: ignore
    from providers.flatfox_api import DeviceNotVerifiedError, FlatfoxClient  # type: ignore
    from providers.scoring import score_csv_row  # type: ignore

logger = logging.getLogger(__name__)

CONTACT_FIELDS = [
    "contact_status",
    "contacted_at",
    "contact_attempts",
    "contact_method",
    "contact_last_error",
]
SCORE_FIELDS = ["listing_score", "score_confidence"]
SENT_STATUSES = {"sent"}
HOMEGATE_COOKIE_DOMAIN = ".homegate.ch"


@dataclass(frozen=True)
class ContactTarget:
    provider: str
    listing_id: str
    url: str
    title: str
    score: float
    row_index: int
    row: dict[str, Any]


@dataclass(frozen=True)
class ContactResult:
    status: str
    method: str
    error: str = ""

    @property
    def sent(self) -> bool:
        return self.status == "sent"


@dataclass(frozen=True)
class ContactSummary:
    selected: int
    sent: int
    dry_run: int
    failed: int
    skipped: int


class ContactSender(Protocol):
    def send(self, target: ContactTarget, message: str) -> ContactResult:
        ...

    def close(self) -> None:
        ...


class DryRunSender:
    def __init__(self, method: str):
        self.method = method

    def send(self, target: ContactTarget, message: str) -> ContactResult:
        logger.info(
            "[dry-run] would contact %s/%s score=%s via %s",
            target.provider,
            target.listing_id,
            target.score,
            self.method,
        )
        return ContactResult(status="dry_run", method=self.method)

    def close(self) -> None:
        return None


class UnsupportedContactSender:
    def __init__(self, provider: str):
        self.provider = provider

    def send(self, target: ContactTarget, message: str) -> ContactResult:
        return ContactResult(
            status="failed",
            method="unsupported",
            error=f"contact sender not implemented for provider={self.provider}",
        )

    def close(self) -> None:
        return None


def parse_cookie_header(cookie_header: str, *, domain: str = HOMEGATE_COOKIE_DOMAIN) -> list[dict[str, Any]]:
    """Convert a browser ``Cookie:`` header value into Playwright cookies."""
    cookies: list[dict[str, Any]] = []
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        name = name.strip()
        if not name:
            continue
        cookies.append({
            "name": name,
            "value": value.strip(),
            "domain": domain,
            "path": "/",
        })
    return cookies


def homegate_cookies_from_creds(creds: dict[str, str]) -> list[dict[str, Any]]:
    """Build Homegate browser cookies from .creds values."""
    cookies: list[dict[str, Any]] = []
    names_seen: set[str] = set()

    raw_cookie_header = creds.get("HOMEGATE_COOKIE") or creds.get("HOMEGATE_COOKIES")
    if raw_cookie_header:
        for cookie in parse_cookie_header(raw_cookie_header):
            cookies.append(cookie)
            names_seen.add(cookie["name"])

    datadome = creds.get("HOMEGATE_DATADOME_COOKIE")
    if datadome and "datadome" not in names_seen:
        cookies.append({
            "name": "datadome",
            "value": datadome,
            "domain": HOMEGATE_COOKIE_DOMAIN,
            "path": "/",
            "secure": True,
            "sameSite": "None",
        })
        names_seen.add("datadome")

    cf_clearance = creds.get("HOMEGATE_CF_CLEARANCE")
    if cf_clearance and "cf_clearance" not in names_seen:
        cookies.append({
            "name": "cf_clearance",
            "value": cf_clearance,
            "domain": HOMEGATE_COOKIE_DOMAIN,
            "path": "/",
            "secure": True,
            "sameSite": "Lax",
        })

    return cookies


class FlatfoxContactSender:
    """Authenticated Flatfox contact sender using the listing submit endpoint."""

    method = "flatfox_submit_form"

    def __init__(self, creds: dict[str, str], *, dry_run: bool = True):
        self.creds = creds
        self.dry_run = dry_run
        self.client = FlatfoxClient()
        self._logged_in = False

    def _login(self) -> None:
        if self._logged_in:
            return
        email = self.creds.get("FLATFOX_EMAIL")
        password = self.creds.get("FLATFOX_PASSWORD")
        if not email or not password:
            raise RuntimeError("FLATFOX_EMAIL and FLATFOX_PASSWORD are required for live Flatfox contact")
        self.client.login(
            email,
            password,
            device_cookie=self.creds.get("FLATFOX_DEVICE_COOKIE"),
            otp=self.creds.get("FLATFOX_OTP"),
        )
        self._logged_in = True

    def _submit_url(self, target: ContactTarget) -> str:
        detail = self.client.fetch_full_detail(target.url or target.listing_id)
        if detail and detail.get("submit_url"):
            return str(detail["submit_url"])
        return urljoin(FLATFOX_ROOT, f"/en/listing/{target.listing_id}/submit/")

    def send(self, target: ContactTarget, message: str) -> ContactResult:
        if self.dry_run:
            return ContactResult(status="dry_run", method=self.method)
        try:
            self._login()
            submit_url = self._submit_url(target)
            session = self.client.session
            session.get(submit_url, timeout=self.client.timeout, allow_redirects=True)
            csrftoken = session.cookies.get("csrftoken") or session.cookies.get("csrf")
            headers = {"Referer": target.url or submit_url}
            if csrftoken:
                headers["X-CSRFToken"] = csrftoken
            data = {"text": message, "contact-advertiser": "1"}
            response = session.post(
                submit_url,
                data=data,
                headers=headers,
                timeout=self.client.timeout,
                allow_redirects=False,
            )
            if response.status_code in {200, 201, 202, 204, 302, 303}:
                return ContactResult(status="sent", method=self.method)
            return ContactResult(
                status="failed",
                method=self.method,
                error=f"HTTP {response.status_code}: {response.text[:300]}",
            )
        except DeviceNotVerifiedError as exc:
            return ContactResult(status="failed", method=self.method, error=str(exc))
        except Exception as exc:
            return ContactResult(status="failed", method=self.method, error=repr(exc))

    def close(self) -> None:
        return None


class HomegateBrowserContactSender:
    """Homegate contact-page sender.

    No stable JSON send endpoint is documented in the current recon. This keeps
    Homegate isolated behind a provider sender so it can be replaced with an API
    implementation when the endpoint is captured.
    """

    method = "homegate_browser_form"

    def __init__(
        self,
        creds: dict[str, str],
        *,
        dry_run: bool = True,
        headless: bool = True,
    ):
        self.creds = creds
        self.dry_run = dry_run
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp_browser = False

    @staticmethod
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

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        cdp_url = self.creds.get("HOMEGATE_CDP_URL")
        if cdp_url:
            self._browser = self._pw.chromium.connect_over_cdp(cdp_url)
            self._context = self._browser.contexts[0] if self._browser.contexts else self._browser.new_context()
            self._cdp_browser = True
            self._page = self._context.new_page()
            return self._page

        profile_dir = self.creds.get("HOMEGATE_PROFILE_DIR")
        user_agent = self.creds.get("HOMEGATE_USER_AGENT") or (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        context_kwargs = {
            "user_agent": user_agent,
            "viewport": {"width": 1280, "height": 1024},
        }
        if profile_dir:
            self._context = self._pw.chromium.launch_persistent_context(
                profile_dir,
                headless=self.headless,
                **context_kwargs,
            )
        else:
            self._browser = self._pw.chromium.launch(headless=self.headless)
            self._context = self._browser.new_context(**context_kwargs)
        cookies = homegate_cookies_from_creds(self.creds)
        if cookies:
            self._context.add_cookies(cookies)
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            "window.chrome = { runtime: {} };"
        )
        self._page = self._context.new_page()
        return self._page

    def _contact_details(self) -> dict[str, str]:
        details = {
            "firstName": self.creds.get("CONTACT_FIRST_NAME", ""),
            "lastName": self.creds.get("CONTACT_LAST_NAME", ""),
            "email": self.creds.get("CONTACT_EMAIL", ""),
            "phone": self.creds.get("CONTACT_PHONE", ""),
            "street": self.creds.get("CONTACT_STREET", ""),
            "zip": self.creds.get("CONTACT_ZIP", ""),
            "city": self.creds.get("CONTACT_CITY", ""),
        }
        required = {
            "firstName": "CONTACT_FIRST_NAME",
            "lastName": "CONTACT_LAST_NAME",
            "email": "CONTACT_EMAIL",
        }
        missing = [env_name for detail_name, env_name in required.items() if not details[detail_name]]
        if missing:
            raise RuntimeError(
                "missing Homegate contact detail(s): "
                + ", ".join(missing)
            )
        return details

    def send(self, target: ContactTarget, message: str) -> ContactResult:
        if self.dry_run:
            return ContactResult(status="dry_run", method=self.method)
        try:
            details = self._contact_details()
            page = self._ensure_page()
            listing_id = target.listing_id or target.url.rstrip("/").split("/")[-1]
            listing_url = target.url or f"https://www.homegate.ch/mieten/{listing_id}"
            page.goto(listing_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            body = page.content().lower()
            title = page.title().lower()
            if self._is_browser_challenge(title, body):
                return ContactResult(
                    status="failed",
                    method=self.method,
                    error=(
                        "Homegate browser challenge before contact form. "
                        "HOMEGATE_DATADOME_COOKIE is enough for api.homegate.ch lookups, "
                        "but the www.homegate.ch contact page also requires a trusted browser session."
                    ),
                )

            for name, value in details.items():
                field = page.locator(f'input[name="{name}"]').first
                if field.count() > 0:
                    current = field.evaluate("el => el.value")
                    if not current:
                        field.fill(value)

            textarea = page.locator('textarea[name="message"], textarea[name="text"], textarea').first
            if textarea.count() == 0:
                return ContactResult(status="failed", method=self.method, error="message textarea not found")
            textarea.fill(message)

            send_btn = page.locator(
                'button[type="submit"]:has-text("invia"), '
                'button[type="submit"]:has-text("Send"), '
                'button[type="submit"]:has-text("Anfrage"), '
                'button[type="submit"]'
            ).first
            if send_btn.count() == 0:
                return ContactResult(status="failed", method=self.method, error="submit button not found")
            send_btn.click()
            page.wait_for_timeout(4000)
            return ContactResult(status="sent", method=self.method)
        except Exception as exc:
            return ContactResult(status="failed", method=self.method, error=repr(exc))

    def close(self) -> None:
        try:
            if self._context and not self._cdp_browser:
                self._context.close()
            if self._browser and not self._cdp_browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            self._pw = self._browser = self._context = self._page = None


def load_message(path: Path) -> str:
    message = path.read_text(encoding="utf-8").strip()
    if not message:
        raise ValueError(f"empty contact message template: {path}")
    return message


def ensure_contact_schema(csv_path: Path) -> list[str]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        existing = reader.fieldnames or []
        rows = list(reader)

    missing = [f for f in SCORE_FIELDS + CONTACT_FIELDS if f not in existing]
    if not missing:
        return existing

    fieldnames = existing + missing
    tmp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            if not row.get("listing_score") or not row.get("score_confidence"):
                score = score_csv_row(row)
                if not row.get("listing_score"):
                    row["listing_score"] = score["listing_score"]
                if not row.get("score_confidence"):
                    row["score_confidence"] = score["score_confidence"]
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    tmp_path.replace(csv_path)
    return fieldnames


def read_contact_rows(csv_path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    fieldnames = ensure_contact_schema(csv_path)
    with csv_path.open(newline="", encoding="utf-8") as f:
        return fieldnames, list(csv.DictReader(f))


def write_contact_rows(csv_path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    tmp_path = csv_path.with_suffix(csv_path.suffix + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp_path.replace(csv_path)


def already_contacted(row: dict[str, Any]) -> bool:
    return (row.get("contact_status") or "").strip().lower() in SENT_STATUSES or bool(row.get("contacted_at"))


def row_score(row: dict[str, Any]) -> float:
    if not row.get("listing_score"):
        score = score_csv_row(row)
        row["listing_score"] = score["listing_score"]
        row["score_confidence"] = score["score_confidence"]
    try:
        return float(row.get("listing_score") or 0)
    except (TypeError, ValueError):
        return 0.0


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def select_contact_targets(
    rows: list[dict[str, Any]],
    *,
    providers: set[str] | None = None,
    limit: int | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    seen_since: datetime | None = None,
    max_attempts: int = 3,
    row_predicate: Any | None = None,
) -> list[ContactTarget]:
    targets: list[ContactTarget] = []
    for idx, row in enumerate(rows):
        provider = (row.get("provider") or "").strip().lower()
        if providers and provider not in providers:
            continue
        if already_contacted(row):
            continue
        try:
            attempts = int(row.get("contact_attempts") or 0)
        except ValueError:
            attempts = 0
        if attempts >= max_attempts:
            continue
        seen_at = _parse_dt(row.get("seen_at"))
        if seen_since and (seen_at is None or seen_at < seen_since):
            continue
        score = row_score(row)
        if min_score is not None and score < min_score:
            continue
        if max_score is not None and score > max_score:
            continue
        if row_predicate and not row_predicate(row):
            continue
        targets.append(ContactTarget(
            provider=provider,
            listing_id=str(row.get("listing_id") or ""),
            url=str(row.get("url") or ""),
            title=str(row.get("title") or ""),
            score=score,
            row_index=idx,
            row=row,
        ))
    targets.sort(key=lambda t: t.score, reverse=True)
    return targets[:limit] if limit else targets


def _sender_for_provider(
    provider: str,
    creds: dict[str, str],
    *,
    dry_run: bool,
    headless: bool,
) -> ContactSender:
    if dry_run:
        method = "flatfox_submit_form" if provider == "flatfox" else "homegate_browser_form"
        return DryRunSender(method)
    if provider == "flatfox":
        return FlatfoxContactSender(creds, dry_run=False)
    if provider == "homegate":
        return HomegateBrowserContactSender(creds, dry_run=False, headless=headless)
    return UnsupportedContactSender(provider)


def process_contact_csv(
    csv_path: Path,
    message: str,
    *,
    creds_path: Path = Path(".creds"),
    dry_run: bool = True,
    providers: set[str] | None = None,
    limit: int | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    seen_since: datetime | None = None,
    max_attempts: int = 3,
    headless: bool = True,
    row_predicate: Any | None = None,
    senders: dict[str, ContactSender] | None = None,
) -> ContactSummary:
    fieldnames, rows = read_contact_rows(csv_path)
    targets = select_contact_targets(
        rows,
        providers=providers,
        limit=limit,
        min_score=min_score,
        max_score=max_score,
        seen_since=seen_since,
        max_attempts=max_attempts,
        row_predicate=row_predicate,
    )
    if not targets:
        return ContactSummary(selected=0, sent=0, dry_run=0, failed=0, skipped=0)

    creds = load_creds(creds_path)
    owned_senders: dict[str, ContactSender] = {}
    sent = dry = failed = skipped = 0
    now = datetime.now().isoformat(timespec="seconds")
    try:
        for target in targets:
            row = rows[target.row_index]
            if already_contacted(row):
                skipped += 1
                continue
            sender = (senders or {}).get(target.provider)
            if sender is None:
                sender = owned_senders.get(target.provider)
            if sender is None:
                sender = _sender_for_provider(
                    target.provider,
                    creds,
                    dry_run=dry_run,
                    headless=headless,
                )
                owned_senders[target.provider] = sender
            result = sender.send(target, message)
            if result.status == "dry_run":
                dry += 1
                logger.info("[dry-run] %s/%s %s", target.provider, target.listing_id, target.url)
                continue

            attempts = int(row.get("contact_attempts") or 0) + 1
            row["contact_attempts"] = attempts
            row["contact_status"] = result.status
            row["contact_method"] = result.method
            row["contact_last_error"] = result.error
            if result.sent:
                row["contacted_at"] = now
                sent += 1
            else:
                failed += 1
            write_contact_rows(csv_path, fieldnames, rows)
    finally:
        for sender in owned_senders.values():
            sender.close()

    return ContactSummary(
        selected=len(targets),
        sent=sent,
        dry_run=dry,
        failed=failed,
        skipped=skipped,
    )
