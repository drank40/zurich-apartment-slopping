import csv

from src.contacting import (
    ContactResult,
    homegate_cookies_from_creds,
    parse_cookie_header,
    process_contact_csv,
    select_contact_targets,
)


class FakeSender:
    def __init__(self, status="sent"):
        self.status = status
        self.targets = []
        self.closed = False

    def send(self, target, message):
        self.targets.append((target.provider, target.listing_id, target.score, message))
        return ContactResult(status=self.status, method="fake", error="" if self.status == "sent" else "nope")

    def close(self):
        self.closed = True


def _write_csv(path, rows):
    fieldnames = [
        "seen_at",
        "listing_score",
        "provider",
        "listing_id",
        "url",
        "title",
        "price_chf",
        "rooms",
        "bedrooms",
        "is_furnished",
        "is_temporary",
        "commute_min",
        "contact_status",
        "contacted_at",
        "contact_attempts",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_select_contact_targets_skips_sent_and_sorts_by_score():
    rows = [
        {"provider": "flatfox", "listing_id": "low", "listing_score": "20"},
        {"provider": "flatfox", "listing_id": "sent", "listing_score": "99", "contact_status": "sent"},
        {"provider": "homegate", "listing_id": "high", "listing_score": "80"},
    ]

    targets = select_contact_targets(rows)

    assert [t.listing_id for t in targets] == ["high", "low"]


def test_process_contact_csv_marks_live_send_once(tmp_path):
    csv_path = tmp_path / "listings.csv"
    _write_csv(csv_path, [
        {
            "seen_at": "2026-05-10T12:00:00",
            "listing_score": "70",
            "provider": "flatfox",
            "listing_id": "new",
            "url": "https://flatfox.ch/en/flat/test/1/",
            "title": "New",
        },
    ])
    sender = FakeSender()

    summary = process_contact_csv(
        csv_path,
        "hello",
        dry_run=False,
        senders={"flatfox": sender},
    )

    rows = _read_csv(csv_path)
    assert summary.sent == 1
    assert sender.targets == [("flatfox", "new", 70.0, "hello")]
    assert rows[0]["contact_status"] == "sent"
    assert rows[0]["contacted_at"]
    assert rows[0]["contact_attempts"] == "1"

    again = process_contact_csv(
        csv_path,
        "hello",
        dry_run=False,
        senders={"flatfox": sender},
    )

    assert again.selected == 0
    assert len(sender.targets) == 1


def test_process_contact_csv_dry_run_does_not_mark_contacted(tmp_path):
    csv_path = tmp_path / "listings.csv"
    _write_csv(csv_path, [
        {
            "seen_at": "2026-05-10T12:00:00",
            "listing_score": "70",
            "provider": "flatfox",
            "listing_id": "dry",
            "url": "https://flatfox.ch/en/flat/test/1/",
            "title": "Dry",
        },
    ])

    summary = process_contact_csv(csv_path, "hello", dry_run=True)

    rows = _read_csv(csv_path)
    assert summary.dry_run == 1
    assert rows[0]["contact_status"] == ""
    assert rows[0]["contacted_at"] == ""


def test_failed_contact_increments_attempts_without_contacted_at(tmp_path):
    csv_path = tmp_path / "listings.csv"
    _write_csv(csv_path, [
        {
            "seen_at": "2026-05-10T12:00:00",
            "listing_score": "70",
            "provider": "flatfox",
            "listing_id": "bad",
            "url": "https://flatfox.ch/en/flat/test/1/",
            "title": "Bad",
        },
    ])
    sender = FakeSender(status="failed")

    summary = process_contact_csv(
        csv_path,
        "hello",
        dry_run=False,
        senders={"flatfox": sender},
    )

    rows = _read_csv(csv_path)
    assert summary.failed == 1
    assert rows[0]["contact_status"] == "failed"
    assert rows[0]["contact_attempts"] == "1"
    assert rows[0]["contacted_at"] == ""
    assert rows[0]["contact_last_error"] == "nope"


def test_parse_cookie_header_for_playwright():
    cookies = parse_cookie_header("datadome=abc; cf_clearance=xyz; empty")

    assert cookies == [
        {"name": "datadome", "value": "abc", "domain": ".homegate.ch", "path": "/"},
        {"name": "cf_clearance", "value": "xyz", "domain": ".homegate.ch", "path": "/"},
    ]


def test_homegate_cookies_from_creds_merges_without_duplicates():
    cookies = homegate_cookies_from_creds({
        "HOMEGATE_COOKIE": "datadome=from-header; sessionid=s1",
        "HOMEGATE_DATADOME_COOKIE": "ignored",
        "HOMEGATE_CF_CLEARANCE": "clear",
    })

    assert [cookie["name"] for cookie in cookies] == ["datadome", "sessionid", "cf_clearance"]
    assert cookies[0]["value"] == "from-header"
    assert cookies[2]["secure"] is True
