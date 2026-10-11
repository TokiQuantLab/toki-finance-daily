"""Calendar history is bounded separately from news and never implies publication."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from toki_finance_daily.brief import make_packet
from toki_finance_daily.collect import collect, filter_records, parse_timestamp


def calendar(eid, at, zone=None):
    data = {"event_at": at, "actual": 0, "forecast": 0, "previous": None}
    if zone:
        data.update(provider_timezone=zone, time_precision="date", date_precision="day")
    return {"id": eid, "kind": "calendar", "source_id": "calendar", "title": eid,
            "url": "https://example.org/calendar", "published_at": None,
            "observed_at": "2025-10-11T00:30:00+08:00", "summary": "", "data": data}


class CalendarLookbackTests(TestCase):
    def test_exact_window_default_zero_scope_and_value_zero_preserved(self):
        cutoff = parse_timestamp("2025-10-11T08:00:00+08:00")
        values = [("too_old", cutoff - timedelta(days=7, seconds=1)),
                  ("lower", cutoff - timedelta(days=7)), ("past", cutoff - timedelta(seconds=1)),
                  ("now", cutoff), ("upper", cutoff + timedelta(days=7)),
                  ("too_future", cutoff + timedelta(days=7, seconds=1))]
        records = [calendar(eid, stamp.isoformat()) for eid, stamp in values]
        default = filter_records(deepcopy(records), cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([r["id"] for r in default], ["now", "upper"])
        result = filter_records(deepcopy(records), cutoff - timedelta(hours=48), cutoff, calendar_lookback_days=7)
        self.assertEqual([r["id"] for r in result], ["lower", "past", "now", "upper"])
        self.assertEqual([r["data"]["calendar_scope"] for r in result], ["past", "past", "upcoming", "upcoming"])
        self.assertTrue(all(r["data"]["actual"] == 0 and r["data"]["forecast"] == 0 for r in result))
        self.assertTrue(all(r["data"]["previous"] is None for r in result))

    def test_date_only_uses_provider_days_without_inventing_a_midnight(self):
        cutoff = parse_timestamp("2025-10-11T00:30:00+08:00")
        records = [calendar(day, day, "America/New_York") for day in
                   ("2025-10-02", "2025-10-03", "2025-10-09", "2025-10-10", "2025-10-17", "2025-10-18")]
        result = filter_records(deepcopy(records), cutoff - timedelta(hours=48), cutoff, calendar_lookback_days=7)
        self.assertEqual([r["id"] for r in result], ["2025-10-03", "2025-10-09", "2025-10-10", "2025-10-17"])
        self.assertEqual([r["data"]["calendar_scope"] for r in result], ["past", "past", "upcoming", "upcoming"])
        self.assertTrue(all(r["data"]["event_at"] == r["id"] and r["data"]["time_precision"] == "date" for r in result))
        legacy = deepcopy(records)
        for r in legacy:
            del r["data"]["provider_timezone"]
        result = filter_records(legacy, cutoff - timedelta(hours=48), cutoff, calendar_lookback_days=7)
        self.assertEqual([r["id"] for r in result], ["2025-10-09", "2025-10-10", "2025-10-17", "2025-10-18"])
        self.assertEqual(result[1]["data"]["calendar_scope"], "past")

    def test_config_default_override_and_invalid_values_are_checked_before_fetch(self):
        source = {"id": "calendar", "name": "Fixture", "adapter": "ics", "url": "https://example.org/calendar"}
        body = b"BEGIN:VCALENDAR\nBEGIN:VEVENT\nSUMMARY:Past\nDTSTART:20251010T000000Z\nEND:VEVENT\nBEGIN:VEVENT\nSUMMARY:Upcoming\nDTSTART:20251014T000000Z\nEND:VEVENT\nEND:VCALENDAR"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({"sources": [source], "calendar_lookback_days": 7}))
            with patch("toki_finance_daily.collect._fetch", return_value=(body, {"transport": "fixture"})):
                from_config = collect(path, cutoff="2025-10-11T08:00:00+08:00")
                explicit = collect(path, cutoff="2025-10-11T08:00:00+08:00", calendar_lookback_days=0)
            self.assertEqual(from_config["calendar_lookback_days"], 7)
            self.assertEqual({r["title"] for r in from_config["records"]}, {"Past", "Upcoming"})
            past = next(r for r in from_config["records"] if r["title"] == "Past")
            self.assertEqual(past["data"]["calendar_scope"], "past")
            self.assertIsNone(past["data"]["actual"])
            self.assertEqual(explicit["calendar_lookback_days"], 0)
            self.assertEqual([r["title"] for r in explicit["records"]], ["Upcoming"])
            path.write_text(json.dumps({"sources": [source]}))
            with patch("toki_finance_daily.collect._fetch", return_value=(body, {})):
                legacy = collect(path, cutoff="2025-10-11T08:00:00+08:00")
            self.assertEqual(legacy["calendar_lookback_days"], 0)
            for invalid in (-1, 32, True, 1.5, "7", None):
                path.write_text(json.dumps({"sources": [source], "calendar_lookback_days": invalid}))
                with self.subTest(invalid=invalid), patch("toki_finance_daily.collect._fetch") as fetch:
                    with self.assertRaisesRegex(ValueError, "calendar_lookback_days"):
                        collect(path, cutoff="2025-10-11T08:00:00+08:00")
                    fetch.assert_not_called()
            path.write_text(json.dumps({"sources": [source], "calendar_lookback_days": 7}))
            for invalid in (-1, 32, True, 1.5, "7"):
                with self.subTest(explicit=invalid), patch("toki_finance_daily.collect._fetch") as fetch:
                    with self.assertRaisesRegex(ValueError, "calendar_lookback_days"):
                        collect(path, cutoff="2025-10-11T08:00:00+08:00", calendar_lookback_days=invalid)
                    fetch.assert_not_called()

    def test_packet_preserves_future_calendars_ahead_of_history_and_other_reserves(self):
        root = Path(__file__).resolve().parents[1]
        dataset = json.loads((root / "examples/dataset.json").read_text())
        dataset.update(cutoff="2025-10-11T00:30:00+08:00", calendar_lookback_days=7)
        records = [calendar(f"past-{i}", f"2025-10-10T{12+i%10:02}:00:00+08:00") for i in range(50)]
        for record in records:
            record["source_id"] = f"past-source-{int(record['id'].split('-')[1])%15}"
        future = calendar("nbs-future", "2025-10-14T09:30:00+08:00")
        local_today = calendar("fed-local-today", "2025-10-10", "America/New_York")
        records += [future, local_today]
        records += [{"id": f"macro-{i}", "kind": "macro", "source_id": "official", "data": {"topic": "macro", "period": "2025-10-09", "series_id": str(i), "value": i}} for i in range(10)]
        records += [{"id": f"daily-{i}", "kind": "macro", "source_id": "technical", "data": {"topic": "technical", "period": "", "timeframe": "1D", "rating": "买入"}} for i in range(8)]
        dataset["records"] = records
        packet = make_packet(dataset)
        selected = {r["id"] for r in packet["records"]}
        self.assertEqual(len(selected), 30)
        self.assertEqual(packet["calendar_lookback_days"], 7)
        self.assertTrue({"nbs-future", "fed-local-today"} <= selected)
        selected_calendar = [r["id"] for r in packet["records"] if r["kind"] == "calendar"]
        self.assertEqual(set(selected_calendar[:2]), {"nbs-future", "fed-local-today"})
        self.assertTrue({f"macro-{i}" for i in range(10)} <= selected)
        self.assertTrue({f"daily-{i}" for i in range(8)} <= selected)
