"""Official FOMC date ranges retain source years and never invent an hour."""
from datetime import timedelta
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

from toki_finance_daily.collect import collect, filter_records, parse_fed_fomc_calendar, parse_timestamp

OBSERVED = "2026-10-11T02:43:04+08:00"
SOURCE = {"id": "fed_fomc_calendar", "name": "美联储 FOMC", "adapter": "fed_fomc_calendar",
          "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
          "timezone": "America/New_York", "region": "美国", "source_type": "official"}
LEGEND = '<div class="panel-footer">* Meeting associated with a Summary of Economic Projections.</div>'
NOTE = '<p>Note: Each meeting date is tentative until confirmed at the meeting immediately preceding it.</p>'


def heading(year):
    return f'<h4><a id="{year}">{year} FOMC Meetings</a></h4>'


def meeting(month, days):
    return f'<div class="row fomc-meeting"><div class="fomc-meeting__month col-xs-5"><strong>{month}</strong></div><div class="fomc-meeting__date">{days}</div><div>Statement and minutes links, not dates</div></div>'


class FomcCalendarTests(TestCase):
    def test_date_precision_range_sep_and_unknown_release_hour(self):
        body = heading(2026) + meeting("March", "17-18*") + LEGEND + NOTE
        record = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)[0]
        data = record["data"]
        self.assertEqual(data["event_at"], "2026-03-18")
        self.assertEqual(data["meeting_start"], "2026-03-17")
        self.assertEqual(data["meeting_end"], "2026-03-18")
        self.assertEqual(data["time_precision"], "date")
        self.assertEqual(data["date_precision"], "day")
        self.assertEqual(data["provider_timezone"], "America/New_York")
        self.assertTrue(data["has_sep"])
        self.assertIsNone(record["published_at"])
        self.assertIsNone(data["actual"])
        self.assertIsNone(data["forecast"])
        self.assertIsNone(data["previous"])
        self.assertIn("tentative", data["schedule_note_source"])
        self.assertIn("可能调整", data["schedule_note"])
        self.assertNotIn("14:00", json.dumps(record))

    def test_cross_month_and_cross_year_end_date(self):
        body = (heading(2024) + meeting("Apr/May", "30-1")
                + heading(2023) + meeting("Jan/Feb", "31-1")
                + heading(2026) + meeting("Dec/Jan", "31-1") + LEGEND)
        records = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)
        self.assertEqual([(r["data"]["meeting_start"], r["data"]["event_at"]) for r in records],
                         [("2024-04-30", "2024-05-01"), ("2023-01-31", "2023-02-01"), ("2026-12-31", "2027-01-01")])
        self.assertFalse(records[0]["data"]["has_sep"])

    def test_header_year_is_used_not_nav_year_or_current_year(self):
        body = '<nav><a>2099</a></nav>' + heading(2025) + meeting("January", "28-29") + heading(2027) + meeting("January", "26-27")
        records = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)
        self.assertEqual([r["data"]["event_at"] for r in records], ["2025-01-29", "2027-01-27"])
        self.assertNotEqual(records[0]["id"], records[1]["id"])
        self.assertEqual(records[0]["id"], parse_fed_fomc_calendar(body, SOURCE, "2028-01-01T00:00:00+08:00")[0]["id"])

    def test_explicit_footnote_year_no_inferred_sep_and_skip_notation_vote(self):
        body = heading(2025) + meeting("August", "22 (notation vote)") + meeting("September", "16-17*") + LEGEND
        body += '<p>Note: A two-day meeting is scheduled for January 25-26, 2028. Each meeting date is tentative until confirmed at the meeting immediately preceding it.</p>'
        records = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[-1]["data"]["event_at"], "2028-01-26")
        self.assertTrue(records[-1]["data"]["source_footnote"])
        self.assertIsNone(records[-1]["data"]["has_sep"])
        no_legend = parse_fed_fomc_calendar(heading(2026) + meeting("March", "17-18*"), SOURCE, OBSERVED)[0]
        self.assertIsNone(no_legend["data"]["has_sep"])

    def test_invalid_layout_year_month_and_dates_report_failure(self):
        for body in ('<html>Browser challenge</html>', meeting("March", "17-18"),
                     heading("20XX") + meeting("March", "17-18"),
                     heading(2026) + meeting("Unknown", "17-18"),
                     heading(2026) + meeting("February", "28-29"),
                     heading(2026) + meeting("March", "18-17"),
                     heading(2026) + meeting("Apr/June", "30-1")):
            with self.subTest(body=body), self.assertRaises(ValueError):
                parse_fed_fomc_calendar(body, SOURCE, OBSERVED)

    def test_existing_calendar_window_uses_end_day_and_empty_is_success(self):
        body = heading(2025) + meeting("October", "14-15") + meeting("October", "21-22") + LEGEND + NOTE
        records = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)
        cutoff = parse_timestamp("2025-10-11T08:00:00+08:00")
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([r["data"]["event_at"] for r in kept], ["2025-10-15"])
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.json"
            config.write_text(json.dumps({"sources": [SOURCE]}), encoding="utf-8")
            with patch("toki_finance_daily.collect._fetch", return_value=(body.encode(), {})):
                dataset = collect(config, cutoff="2025-09-01T08:00:00+08:00", out_dir=Path(tmp) / "run")
            self.assertEqual(dataset["sources"][0]["status"], "ok")
            self.assertEqual(dataset["sources"][0]["parsed_count"], 2)
            self.assertEqual(dataset["sources"][0]["count"], 0)
            self.assertEqual(dataset["records"], [])
            self.assertEqual((Path(tmp) / "run/raw/fed_fomc_calendar.bin").read_bytes(), body.encode())

    def test_provider_date_window_at_shanghai_midnight_keeps_new_york_previous_day(self):
        body = heading(2025) + meeting("October", "10") + meeting("October", "17") + meeting("October", "18")
        records = parse_fed_fomc_calendar(body, SOURCE, OBSERVED)
        cutoff = parse_timestamp("2025-10-11T00:30:00+08:00")
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([r["data"]["event_at"] for r in kept], ["2025-10-10", "2025-10-17"])
        self.assertTrue(all(r["data"]["time_precision"] == "date" for r in kept))
        self.assertTrue(all(r["published_at"] is None for r in kept))
        # Other date-only calendars retain their previous display-zone rule.
        legacy = deepcopy(records)
        for record in legacy:
            del record["data"]["provider_timezone"]
        kept_legacy = filter_records(legacy, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([r["data"]["event_at"] for r in kept_legacy], ["2025-10-17", "2025-10-18"])
