import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock
from urllib.error import HTTPError

from toki_finance_daily.page_cache import admit_browser_snapshot, fetch_source
import json
import os
from unittest.mock import patch


class PublicPageCacheTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.source = {"id": "technical", "url": "https://example.org/technical/", "adapter": "technical",
                       "minimum_interval_seconds": 86400}
        self.network = {"cache_dir": self.folder.name, "retries": 2}
        self.now = datetime(2026, 10, 11, tzinfo=timezone.utc)

    def test_success_reused_across_editions_with_original_fetch_time_and_daily_refresh(self):
        fetch = Mock(side_effect=[(b"first", {"transport": "urllib"}), (b"second", {"transport": "urllib"})])
        first, metadata = fetch_source(self.source, self.network, fetch, now=self.now)
        cached, later = fetch_source(self.source, self.network, fetch, now=self.now + timedelta(hours=23))
        self.assertEqual((first, cached), (b"first", b"first"))
        self.assertTrue(later["cache_hit"])
        self.assertEqual(later["fetched_at"], metadata["fetched_at"])
        self.assertEqual(fetch.call_count, 1)
        second, _ = fetch_source(self.source, self.network, fetch, now=self.now + timedelta(hours=24))
        self.assertEqual(second, b"second")
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args.args[1]["retries"], 0)

    def test_access_failure_does_not_trigger_another_daily_attempt(self):
        fetch = Mock(side_effect=HTTPError(self.source["url"], 403, "Forbidden", {}, None))
        with self.assertRaises(HTTPError):
            fetch_source(self.source, self.network, fetch, now=self.now)
        with self.assertRaisesRegex(RuntimeError, "403"):
            fetch_source(self.source, self.network, fetch, now=self.now + timedelta(hours=2))
        self.assertEqual(fetch.call_count, 1)

    def test_corrupt_snapshot_cannot_silently_refresh_inside_interval(self):
        fetch = Mock(return_value=(b"first", {"transport": "urllib"}))
        fetch_source(self.source, self.network, fetch, now=self.now)
        next(Path(self.folder.name).glob("*.bin")).write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "checksum"):
            fetch_source(self.source, self.network, fetch, now=self.now + timedelta(hours=1))
        self.assertEqual(fetch.call_count, 1)

    def test_mcp_tools_and_cursors_do_not_share_the_same_response_cache(self):
        source = {**self.source, "url": "https://mcp.jin10.com/mcp", "adapter": "jin10_mcp",
                  "transport": "jin10-mcp", "mcp_tool": "list_flash"}
        fetch = Mock(side_effect=[(b"flashes", {}), (b"calendar", {}), (b"next page", {})])
        flashes, _ = fetch_source(source, self.network, fetch, now=self.now)
        calendar, _ = fetch_source({**source, "mcp_tool": "list_calendar"}, self.network, fetch, now=self.now)
        next_page, _ = fetch_source({**source, "mcp_arguments": {"cursor": "next"}}, self.network, fetch, now=self.now)
        reused, metadata = fetch_source(source, self.network, fetch, now=self.now + timedelta(hours=1))
        self.assertEqual((flashes, calendar, next_page, reused), (b"flashes", b"calendar", b"next page", b"flashes"))
        self.assertTrue(metadata["cache_hit"])
        self.assertEqual(fetch.call_count, 3)


class BrowserSnapshotAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.source={"id":"gold", "name":"Gold", "adapter":"investing_instrument_technical",
                     "url":"https://cn.investing.com/commodities/gold-technical", "instrument_name":"黄金期货",
                     "minimum_interval_seconds":86400}
        self.body=(Path(__file__).parent/"fixtures"/"investing-gold-technical.html").read_bytes()
        self.now=datetime.fromisoformat("2026-10-10T20:00:00+00:00")
        self.observed="2026-10-10T19:39:53.262745+00:00"
        self.network={"cache_dir":self.folder.name}
        self.env=patch.dict(os.environ,{"TOKI_DAILY_CACHE_DIR":self.folder.name});self.env.start();self.addCleanup(self.env.stop)

    def test_valid_import_supersedes403_explicitly_and_replays_original_capture_without_network(self):
        failed=Mock(side_effect=HTTPError(self.source["url"],403,"Forbidden",{},None))
        with self.assertRaises(HTTPError):fetch_source(self.source,self.network,failed,now=self.now-timedelta(minutes=30))
        before=json.loads(next(Path(self.folder.name).glob("*.json")).read_text())
        body,metadata=admit_browser_snapshot(self.source,self.network,self.body,self.observed,now=self.now)
        self.assertEqual(body,self.body)
        self.assertEqual(metadata["fetched_at"],self.observed)
        self.assertEqual(metadata["imported_at"],self.now.isoformat())
        self.assertEqual(metadata["selected_timeframe"],"1h")
        self.assertEqual(metadata["validated_records"],9)
        self.assertNotIn("http_status",metadata)
        self.assertEqual(metadata["superseded_attempt"],before)
        self.assertEqual(metadata["superseded_attempt"]["state"],"error")
        fetch=Mock(side_effect=AssertionError("A replay must not access the network"))
        cached,replayed=fetch_source(self.source,self.network,fetch,now=self.now+timedelta(hours=23))
        self.assertEqual(cached,self.body);self.assertTrue(replayed["cache_hit"])
        self.assertEqual(replayed["fetched_at"],self.observed)
        fetch.assert_not_called()
        persisted=json.loads(next(Path(self.folder.name).glob("*.json")).read_text())
        self.assertEqual(persisted["state"],"ok")
        self.assertEqual(persisted["superseded_attempt"]["state"],"error")

    def test_import_expires_then_normal_fetch_failure_is_retained_for_another_daily_interval(self):
        admit_browser_snapshot(self.source,self.network,self.body,self.observed,now=self.now)
        failed=Mock(side_effect=HTTPError(self.source["url"],403,"Forbidden",{},None))
        cached,_=fetch_source(self.source,self.network,failed,now=self.now+timedelta(seconds=86399))
        self.assertEqual(cached,self.body);failed.assert_not_called()
        with self.assertRaises(HTTPError):fetch_source(self.source,self.network,failed,now=self.now+timedelta(days=1))
        with self.assertRaisesRegex(RuntimeError,"403"):
            fetch_source(self.source,self.network,failed,now=self.now+timedelta(days=1,hours=2))
        self.assertEqual(failed.call_count,1);self.assertEqual(failed.call_args.args[1]["retries"],0)

    def test_invalid_source_time_locked_values_or_budget_is_rejected_before_cache_write(self):
        locked=self.body.replace(b' class="!border-inv-blue-500"',b'').replace(b'data-test="1m"',b'data-test="1m" aria-selected="true"')
        cases=[({**self.source,"url":"https://cn.investing.com/crypto/bitcoin/technical"},self.network,self.body,self.observed),
               (self.source,self.network,self.body,(self.now+timedelta(seconds=1)).isoformat()),
               (self.source,self.network,self.body,"2026-10-10T19:39:53"),
               (self.source,{**self.network,"max_bytes":1024},self.body,self.observed),
               (self.source,self.network,locked,self.observed),
               (self.source,self.network,self.body+b'<script>hidden state</script>',self.observed),
               (self.source,self.network,b'<h1>Challenge</h1>',self.observed),
               ({**self.source,"minimum_interval_seconds":0},self.network,self.body,self.observed)]
        for source,network,body,observed in cases:
            with self.subTest(source=source["url"],observed=observed),self.assertRaises(ValueError):
                admit_browser_snapshot(source,network,body,observed,now=self.now)
            self.assertEqual(list(Path(self.folder.name).iterdir()),[])

    def test_collection_lock_and_newer_capture_cannot_be_overwritten(self):
        admit_browser_snapshot(self.source,self.network,self.body,self.observed,now=self.now)
        meta=next(Path(self.folder.name).glob("*.json"));original=meta.read_bytes();body=next(Path(self.folder.name).glob("*.bin"));original_body=body.read_bytes()
        lock=meta.with_suffix(".lock");lock.write_text("existing collection")
        with self.assertRaisesRegex(RuntimeError,"already being collected"):
            admit_browser_snapshot(self.source,self.network,self.body,self.observed,now=self.now+timedelta(minutes=1))
        self.assertEqual(meta.read_bytes(),original);self.assertEqual(body.read_bytes(),original_body);self.assertTrue(lock.exists())
        lock.unlink()
        with self.assertRaisesRegex(ValueError,"newer successful observation"):
            admit_browser_snapshot(self.source,self.network,self.body,"2026-10-10T19:00:00+00:00",now=self.now+timedelta(minutes=1))
        self.assertEqual(meta.read_bytes(),original);self.assertEqual(body.read_bytes(),original_body)


if __name__ == "__main__":
    unittest.main()
