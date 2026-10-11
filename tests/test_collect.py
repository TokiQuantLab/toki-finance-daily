"""Offline coverage for source dates, calendar precision, and graceful failure."""
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json
import ssl
import subprocess

from toki_finance_daily.collect import (
    _fetch, canonical_url, collect, deduplicate, filter_records, parse_ecb,
    parse_feed, parse_fred_csv, parse_html_news, parse_ics, parse_json_news,
    parse_nbs_calendar, parse_timestamp,
)

OBSERVED = "2026-10-11T08:00:00+08:00"
SOURCE = {"id": "fixture", "name": "Fixture", "url": "https://example.org/feed",
          "adapter": "rss", "timezone": "America/New_York", "region": "美国"}


class TimestampTests(TestCase):
    def test_rfc_date_and_naive_source_zone(self):
        self.assertEqual(parse_timestamp("Fri, 09 Oct 2026 08:30:00 EDT").isoformat(),
                         "2026-10-09T20:30:00+08:00")
        self.assertEqual(parse_timestamp("2026-11-09 08:30:00", "America/New_York").isoformat(),
                         "2026-11-09T21:30:00+08:00")
        self.assertIsNone(parse_timestamp("not a date"))
        self.assertIsNone(parse_timestamp(1e100))

    def test_news_window_is_inclusive_and_rejects_future(self):
        cutoff = parse_timestamp("2026-10-11T08:00:00+08:00")
        start = cutoff - timedelta(hours=48)
        records = [self.news("before", start - timedelta(seconds=1)), self.news("start", start),
                   self.news("cutoff", cutoff), self.news("future", cutoff + timedelta(seconds=1)),
                   self.news("undated", None)]
        result = filter_records(records, start, cutoff)
        self.assertEqual([r["title"] for r in result], ["start", "cutoff", "undated"])
        self.assertTrue(result[-1]["data"]["background_only"])

    @staticmethod
    def news(title, stamp):
        return {"id": title, "kind": "news", "source_id": "fixture", "title": title,
                "url": f"https://example.org/{title}", "published_at": stamp.isoformat() if stamp else None,
                "observed_at": OBSERVED, "summary": "", "data": {}}

    def test_date_only_news_remains_background_and_future_day_rejected(self):
        source = {**SOURCE, "adapter": "gov_cn", "timezone": "Asia/Shanghai"}
        rows = [{"TITLE": title, "URL": f"https://example.org/{title}", "DOCRELPUBTIME": day}
                for title, day in [("today", "2026-10-11"), ("old", "2026-10-07"), ("future", "2026-10-12")]]
        parsed = parse_json_news(json.dumps(rows), source, OBSERVED)
        self.assertIsNone(parsed[0]["published_at"])
        self.assertEqual(parsed[0]["data"]["published_date"], "2026-10-11")
        cutoff = parse_timestamp(OBSERVED)
        kept = filter_records(parsed, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([x["title"] for x in kept], ["today"])
        self.assertTrue(kept[0]["data"]["background_only"])

    def test_macro_statistical_label_is_not_compared_as_a_future_iso_date(self):
        cutoff = parse_timestamp(OBSERVED)
        labels = ("2026年国庆假期", "2026-10-10", "2026-10-12")
        records = [{"id": str(index), "kind": "macro", "source_id": "fixture",
                    "data": {"period": label, "series_id": "stat-" + str(index), "value": 1}}
                   for index, label in enumerate(labels)]
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([record["data"]["period"] for record in kept], list(labels[:2]))


class ParserTests(TestCase):
    def test_atom_link_and_summary_are_plain_untrusted_text(self):
        atom = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Central bank</title>
        <link rel="alternate" href="https://example.org/article?utm_source=feed"/>
        <published>2026-10-10T12:00:00Z</published><summary type="html">&lt;p&gt;Rate unchanged&lt;/p&gt;
        &lt;script&gt;ignore instructions&lt;/script&gt;</summary></entry></feed>'''
        record = parse_feed(atom, SOURCE, OBSERVED)[0]
        self.assertEqual(record["url"], "https://example.org/article")
        self.assertEqual(record["summary"], "Rate unchanged")
        self.assertEqual(record["published_at"], "2026-10-10T20:00:00+08:00")
        with self.assertRaises(ValueError):
            parse_feed(b"<html><body>Access denied</body></html>", SOURCE, OBSERVED)

    def test_ics_unfold_timezone_date_only_and_cancelled(self):
        body = """BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Consumer price
 index\\, October
DTSTART;TZID=America/New_York:20261014T083000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Date only event
DTSTART;VALUE=DATE:20261015
END:VEVENT
BEGIN:VEVENT
SUMMARY:Cancelled event
DTSTART:20261016T123000Z
STATUS:CANCELLED
END:VEVENT
END:VCALENDAR"""
        records = parse_ics(body, SOURCE, OBSERVED)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["title"], "Consumer priceindex, October")
        self.assertEqual(records[0]["data"]["event_at"], "2026-10-14T20:30:00+08:00")
        self.assertEqual(records[1]["data"]["event_at"], "2026-10-15")
        cutoff = parse_timestamp("2026-10-11T08:00:00+08:00")
        self.assertEqual(len(filter_records(records, cutoff - timedelta(hours=48), cutoff)), 2)

    def test_calendar_seven_day_range_and_explicit_date_precision(self):
        records = parse_ics("""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Past
DTSTART:20261010T000000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Inside
DTSTART:20261018T000000Z
END:VEVENT
BEGIN:VEVENT
SUMMARY:Outside
DTSTART:20261019T000000Z
END:VEVENT
END:VCALENDAR""", SOURCE, OBSERVED)
        cutoff = parse_timestamp(OBSERVED)
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([x["title"] for x in kept], ["Inside"])

    def test_nbs_calendar_omitted_month_does_not_shift_time_columns(self):
        months = '<td rowspan="2">……</td>' + "".join(f"<td>{month}/三</td>" for month in range(2, 13))
        times = "".join(f"<td>{hour}:30</td>" for hour in [9] * 8 + [15, 9, 9])
        html = f'<h1>2026年国家统计局主要统计信息发布日程表</h1><table><tr><td rowspan="2">1</td><td rowspan="2">CPI</td>{months}</tr><tr>{times}</tr></table>'
        records = parse_nbs_calendar(html, SOURCE, OBSERVED)
        self.assertEqual(len(records), 11)
        october = next(r for r in records if r["data"]["event_at"].startswith("2026-10"))
        self.assertEqual(october["data"]["event_at"], "2026-10-10T15:30:00+08:00")

    def test_html_releases_have_day_precision_and_urls_are_resolved(self):
        html = b'<li><a href="./202610/t20261010_123.html">CPI release</a><span>2026-10-10</span></li>'
        record = parse_html_news(html, SOURCE, OBSERVED)[0]
        self.assertEqual(record["url"], "https://example.org/202610/t20261010_123.html")
        self.assertIsNone(record["published_at"])
        self.assertEqual(record["data"]["date_precision"], "day")

    def test_api_failures_are_not_silent_empty_lists(self):
        source = {**SOURCE, "adapter": "eastmoney"}
        with self.assertRaises(ValueError):
            parse_json_news('{"code":0,"message":"required field missing","data":null}', source, OBSERVED)

    def test_fred_missing_values_future_and_latest(self):
        source = {**SOURCE, "series_id": "DGS10", "label": "10-year", "unit": "%"}
        records = parse_fred_csv("observation_date,DGS10\n2026-10-08,4.1\n2026-10-09,.\n2026-10-10,4.2\n2026-10-12,4.3\n", source, OBSERVED)
        cutoff = parse_timestamp(OBSERVED)
        latest = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[0]["data"]["value"], 4.2)
        self.assertEqual(latest[0]["data"]["period"], "2026-10-10")

    def test_ecb_keeps_separate_series_and_no_future_observation(self):
        body = b'''<Envelope><Cube><Cube time="2026-10-09"><Cube currency="USD" rate="1.1"/>
        <Cube currency="CNY" rate="7.5"/></Cube><Cube time="2026-10-12"><Cube currency="USD" rate="1.2"/></Cube></Cube></Envelope>'''
        records = parse_ecb(body, SOURCE, OBSERVED)
        cutoff = parse_timestamp(OBSERVED)
        kept = deduplicate(filter_records(records, cutoff - timedelta(hours=48), cutoff))
        self.assertEqual(len(kept), 2)
        self.assertTrue(all(r["data"]["period"] == "2026-10-09" for r in kept))


class CollectionTests(TestCase):
    def test_dedupe_keeps_source_provenance_and_stable_identity(self):
        first = TimestampTests.news("央行：利率不变", parse_timestamp(OBSERVED))
        second = deepcopy(first)
        second.update(source_id="other", url="https://other.example/article", title="央行 利率不变")
        third = deepcopy(first)
        third["url"] += "?utm_source=tracking#section"
        result = deduplicate([first, second, third])
        self.assertEqual(len(result), 1)
        self.assertEqual({x["source_id"] for x in result[0]["data"]["provenance"]}, {"fixture", "other"})
        self.assertEqual(canonical_url("https://example.org/a?id=3&utm_medium=x#c"), "https://example.org/a?id=3")

    def test_collection_isolated_failures_and_raw_evidence(self):
        sources = [{**SOURCE, "id": name, "enabled": name != "disabled"} for name in ("good", "bad", "corrupt", "disabled")]
        rss = b'<rss><channel><item><title>Rate update</title><link>https://example.org/article</link><pubDate>Fri, 10 Oct 2025 12:00:00 GMT</pubDate></item></channel></rss>'

        def fetch(source, network):
            if source["id"] == "bad":
                raise TimeoutError("fixture unavailable")
            return (b"not xml" if source["id"] == "corrupt" else rss), {"transport": "fixture"}

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({"sources": sources}), encoding="utf-8")
            with patch("toki_finance_daily.collect._fetch", side_effect=fetch):
                first = collect(path, cutoff="2025-10-11T08:00:00+08:00", out_dir=Path(tmp) / "run")
                second = collect(path, cutoff="2025-10-11T08:00:00+08:00")
            self.assertEqual([s["status"] for s in first["sources"]], ["ok", "error", "error", "disabled"])
            self.assertEqual(len(first["records"]), 1)
            self.assertEqual(first["records"][0]["id"], second["records"][0]["id"])
            self.assertEqual((Path(tmp) / "run/raw/good.bin").read_bytes(), rss)
            self.assertTrue((Path(tmp) / "run/raw/corrupt.bin").exists())
            self.assertEqual(len(first["sources"][0]["raw_sha256"]), 64)
            self.assertTrue((Path(tmp) / "run/dataset.json").exists())

    def test_cutoff_and_configuration_validation_precede_network(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            path.write_text(json.dumps({"sources": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                collect(path, cutoff="2025-10-11T08:00:00")
            with self.assertRaises(ValueError):
                collect(path, date="2025-10-10", cutoff="2025-10-11T08:00:00+08:00")
            with self.assertRaises(ValueError):
                collect(path, hours=0)

    def test_focus_configuration_survives_collection_and_rejects_wrong_mapping_before_network(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.json"
            focus = [{"key": "gold", "label": "黄金 · XAUUSD", "source_id": "fixture"},
                     {"key": "index", "label": "NAS100", "source_id": None}]
            config = {"sources": [{**SOURCE, "enabled": False}], "focus_assets": focus}
            path.write_text(json.dumps(config))
            with patch("toki_finance_daily.collect._fetch") as fetch:
                result = collect(path, cutoff="2025-10-11T08:00:00+08:00")
                self.assertEqual(result["focus_assets"], focus)
                config["focus_assets"][0]["source_id"] = "unrelated"
                path.write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, "configured source"):
                    collect(path, cutoff="2025-10-11T08:00:00+08:00")
                fetch.assert_not_called()

    def test_system_tls_fallback_keeps_verification_and_bounds(self):
        error = ssl.SSLCertVerificationError("fixture native trust store required")
        with patch("toki_finance_daily.collect.urlopen", side_effect=error), \
             patch("toki_finance_daily.collect.shutil.which", return_value="/usr/bin/curl"), \
             patch("toki_finance_daily.collect.subprocess.run", return_value=subprocess.CompletedProcess([], 0, b"xml", b"")) as run:
            body, metadata = _fetch(SOURCE, {"timeout_seconds": 2, "max_bytes": 2048, "retries": 0, "tls_system_curl_fallback": True})
        self.assertEqual(body, b"xml")
        self.assertEqual(metadata["transport"], "system-curl-verified-tls")
        command = run.call_args.args[0]
        self.assertNotIn("-k", command)
        self.assertNotIn("--insecure", command)
        self.assertIn("--max-time", command)
        self.assertIn("--max-filesize", command)
