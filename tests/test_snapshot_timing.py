"""Public snapshots use completion time, explicit cutoffs, and attempt cadence."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch
import json

from toki_finance_daily.collect import _record, collect
from toki_finance_daily.page_cache import fetch_source


SOURCE = {"id": "timing", "name": "Timing fixture", "adapter": "snapshot_fixture",
          "url": "https://example.org/public-technical", "enabled": True}


class SnapshotTimingTests(TestCase):
    def collect_between(self, start, completed, *, metadata_time=True, **options):
        class Clock(datetime):
            current = start

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

        def fetch(source, network):
            Clock.current = completed
            metadata = {"transport": "fixture"}
            if metadata_time:
                metadata["fetched_at"] = completed.isoformat()
            return b"visible public snapshot", metadata

        def parse(body, source, observed_at):
            return [_record(source, "macro", "Technical snapshot", source["url"], observed_at,
                            data={"topic": "technical", "label": "Fixture", "value": "买入", "unit": "",
                                  "period": "", "series_id": "TIMING", "provider_at": None,
                                  "availability_rule": "observed_snapshot"})]

        with TemporaryDirectory() as folder:
            config = Path(folder) / "sources.json"
            config.write_text(json.dumps({"sources": [SOURCE], "network": {"retries": 0, "max_workers": 1}}))
            with patch("toki_finance_daily.collect.datetime", Clock), \
                 patch("toki_finance_daily.collect._fetch", side_effect=fetch) as network, \
                 patch.dict("toki_finance_daily.collect._PARSERS", {"snapshot_fixture": parse}):
                dataset = collect(config, **options)
            self.assertEqual(network.call_count, 1)
        return dataset

    def test_automatic_live_cutoff_includes_completed_snapshot_but_fixed_cutoff_rejects_it(self):
        start = datetime.fromisoformat("2026-10-11T03:00:00+08:00")
        completed = start + timedelta(seconds=10)
        live = self.collect_between(start, completed)
        self.assertEqual(live["cutoff"], completed.isoformat())
        self.assertEqual(live["window_start"], (completed - timedelta(hours=48)).isoformat())
        self.assertEqual(len(live["records"]), 1)
        self.assertEqual(live["records"][0]["observed_at"], completed.isoformat())
        self.assertEqual(live["sources"][0]["fetched_at"], completed.isoformat())
        historical = self.collect_between(start, completed, cutoff=start.isoformat())
        self.assertEqual(historical["cutoff"], start.isoformat())
        self.assertEqual(historical["records"], [])
        self.assertEqual(historical["sources"][0]["count"], 0)
        self.assertEqual(historical["collected_at"], completed.isoformat())

    def test_midnight_updates_implicit_edition_and_caps_an_explicit_edition_date(self):
        start = datetime.fromisoformat("2026-10-10T23:59:58+08:00")
        completed = datetime.fromisoformat("2026-10-11T00:00:08+08:00")
        implicit = self.collect_between(start, completed)
        self.assertEqual((implicit["date"], implicit["cutoff"]), ("2026-10-11", completed.isoformat()))
        self.assertEqual(len(implicit["records"]), 1)
        explicit = self.collect_between(start, completed, date="2026-10-10")
        capped = "2026-10-10T23:59:59+08:00"
        self.assertEqual((explicit["date"], explicit["cutoff"]), ("2026-10-10", capped))
        self.assertEqual(explicit["records"], [])
        self.assertEqual(explicit["sources"][0]["fetched_at"], completed.isoformat())
        self.assertEqual(explicit["collected_at"], completed.isoformat())

    def test_non_cached_http_without_transport_timestamp_uses_completion_time(self):
        start = datetime.fromisoformat("2026-10-11T03:00:00+08:00")
        completed = start + timedelta(seconds=10)
        live = self.collect_between(start, completed, metadata_time=False)
        self.assertEqual(live["records"][0]["observed_at"], completed.isoformat())
        self.assertEqual(live["sources"][0]["fetched_at"], completed.isoformat())
        historical = self.collect_between(start, completed, metadata_time=False, cutoff=start.isoformat())
        self.assertEqual(historical["records"], [])
        self.assertEqual(historical["sources"][0]["fetched_at"], completed.isoformat())

    def test_cache_preserves_completion_time_and_refreshes_from_attempt_start(self):
        attempted = datetime(2026, 10, 11, tzinfo=timezone.utc)
        completed = attempted + timedelta(seconds=10)
        next_completed = attempted + timedelta(days=1, seconds=10)
        source = {**SOURCE, "minimum_interval_seconds": 86400}
        fetch = Mock(side_effect=[(b"first", {"fetched_at": completed.isoformat()}),
                                 (b"second", {"fetched_at": next_completed.isoformat()})])
        with TemporaryDirectory() as folder, patch.dict("os.environ", {"TOKI_DAILY_CACHE_DIR": folder}):
            first, metadata = fetch_source(source, {}, fetch, now=attempted)
            persisted = json.loads(next(Path(folder).glob("*.json")).read_text())
            self.assertEqual(persisted["attempted_at"], attempted.isoformat())
            self.assertEqual(persisted["fetched_at"], completed.isoformat())
            cached, reused = fetch_source(source, {}, fetch, now=attempted + timedelta(seconds=86399))
            self.assertEqual((first, cached), (b"first", b"first"))
            self.assertTrue(reused["cache_hit"])
            self.assertEqual(reused["fetched_at"], metadata["fetched_at"])
            self.assertEqual(reused["fetched_at"], completed.isoformat())
            self.assertEqual(fetch.call_count, 1)
            refreshed, latest = fetch_source(source, {}, fetch, now=attempted + timedelta(days=1))
            self.assertEqual(refreshed, b"second")
            self.assertFalse(latest["cache_hit"])
            self.assertEqual(latest["fetched_at"], next_completed.isoformat())
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(fetch.call_args.args[1]["retries"], 0)
