"""Offline contracts for independent free-period captures and daily replay."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch
import json
import os
import re
import tempfile
import unittest

from toki_finance_daily.browser_pages import (
    FREE_INVESTING_TIMEFRAMES, _INVESTING_PERIOD_READY, _INVESTING_SELECTED_TAB,
    _INVESTING_TABLE_SIGNATURE, fetch_browser_page, investing_visible_capture_script,
    validate_browser_snapshot,
)
from toki_finance_daily.investing_instrument import parse_investing_instrument_technical
from toki_finance_daily.page_cache import admit_browser_snapshot, fetch_source

BASE = (Path(__file__).parent / "fixtures" / "investing-gold-technical.html").read_text()
SOURCE = {"id": "gold", "name": "Gold", "adapter": "investing_instrument_technical",
          "url": "https://cn.investing.com/commodities/gold-technical", "instrument_name": "黄金期货",
          "minimum_interval_seconds": 86400, "browser_timeframes": list(FREE_INVESTING_TIMEFRAMES)}
START = datetime(2026, 10, 10, 20, tzinfo=timezone.utc)
CODES = {"30m": "30m", "1h": "1h", "5h": "5h", "1D": "1d", "1W": "1w", "1M": "1mo"}


def period_html(frame):
    """Synthetic, explicitly varied offline periods; never used as live data."""
    index = list(FREE_INVESTING_TIMEFRAMES).index(frame)
    html = BASE.replace(' class="!border-inv-blue-500"', '')
    html = html.replace('aria-selected="false" data-test="' + CODES[frame] + '"',
                        'aria-selected="true" data-test="' + CODES[frame] + '"')
    return html.replace('64.006', str(60 + index)).replace('00:01 GMT', f'00:{index + 1:02d} GMT')


def make_bundle(captured=None, requested=None):
    frames = list(captured or FREE_INVESTING_TIMEFRAMES)
    requested = list(requested or FREE_INVESTING_TIMEFRAMES)
    snapshots = []
    for index, frame in enumerate(frames):
        html = period_html(frame)
        snapshots.append({"timeframe": frame, "observed_at": (START + timedelta(seconds=index)).isoformat(),
                          "html": html, "sha256": sha256(html.encode()).hexdigest()})
    return {"format": "investing-visible-timeframes", "version": 1, "source_url": SOURCE["url"],
            "requested_timeframes": requested, "observed_at": snapshots[-1]["observed_at"], "snapshots": snapshots,
            "errors": [{"timeframe": frame, "error": "Visible table did not refresh"} for frame in requested if frame not in frames]}


def encoded(bundle):
    return json.dumps(bundle, ensure_ascii=False).encode()


class InvestingTimeframeTests(unittest.TestCase):
    def test_all_six_periods_keep_their_own_numeric_details_clocks_and_observations(self):
        bundle = make_bundle()
        records = parse_investing_instrument_technical(encoded(bundle), SOURCE, bundle["observed_at"])
        self.assertEqual(len(records), 24)
        self.assertEqual(len({record["id"] for record in records}), 24)
        for index, frame in enumerate(FREE_INVESTING_TIMEFRAMES):
            group = {r["data"]["subtype"]: r for r in records if r["data"]["timeframe"] == frame}
            self.assertEqual(set(group), {"summary", "indicators", "moving_averages", "pivot_points"})
            self.assertEqual(group["indicators"]["data"]["indicators"][0]["value"], 60 + index)
            self.assertEqual(group["indicators"]["data"]["provider_at"], f"2026-10-10T00:{index + 1:02d}:00+00:00")
            self.assertEqual(group["indicators"]["observed_at"], (START + timedelta(seconds=index)).isoformat())
            self.assertTrue(group["summary"]["data"]["selected_timeframe"])
            self.assertIsNone(group["summary"]["data"]["provider_at"])
            self.assertIsNone(group["pivot_points"]["data"]["provider_at"])

    def test_partial_capture_retains_summaries_but_does_not_invent_missing_period_details(self):
        bundle = make_bundle(["1h", "1W"])
        records = validate_browser_snapshot(SOURCE, {}, encoded(bundle), bundle["observed_at"])
        self.assertEqual(len(records), 12)
        self.assertEqual({r["data"]["timeframe"] for r in records if r["data"]["subtype"] != "summary"}, {"1h", "1W"})
        daily = next(r["data"] for r in records if r["data"]["timeframe"] == "1D")
        self.assertFalse(daily["selected_timeframe"])
        self.assertIsNone(daily["moving_average_rating"])
        self.assertIsNone(daily["indicator_rating"])

    def test_wrong_period_checksum_source_future_capture_and_unreported_gaps_are_rejected(self):
        correct = make_bundle(["1h"], ["1h", "1D"])
        cases = []
        wrong = json.loads(json.dumps(correct)); wrong["snapshots"][0]["timeframe"] = "1D"; wrong["errors"][0]["timeframe"] = "1h"; cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["snapshots"][0]["sha256"] = "0" * 64; cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["source_url"] = "https://cn.investing.com/crypto/bitcoin/technical"; cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["observed_at"] = (START + timedelta(days=1)).isoformat(); cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["errors"] = []; cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["snapshots"].append(wrong["snapshots"][0]); cases.append(wrong)
        wrong = json.loads(json.dumps(correct)); wrong["requested_timeframes"].append("1m"); cases.append(wrong)
        wrong = make_bundle(["1h", "1D"], ["1h", "1D"])
        wrong["snapshots"][1]["html"] = wrong["snapshots"][1]["html"].replace("GCZ6", "GCG7")
        wrong["snapshots"][1]["sha256"] = sha256(wrong["snapshots"][1]["html"].encode()).hexdigest(); cases.append(wrong)
        source = {**SOURCE, "browser_timeframes": ["1h", "1D"]}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"TOKI_DAILY_CACHE_DIR": folder}):
            for bundle in cases:
                with self.subTest(bundle=bundle.keys()), self.assertRaises(ValueError):
                    admit_browser_snapshot(source, {}, encoded(bundle), (START + timedelta(minutes=1)).isoformat(), now=START + timedelta(hours=1))
                self.assertEqual(list(Path(folder).iterdir()), [])

    def test_multi_cache_is_separate_from_old_single_period_and_replays_without_network(self):
        bundle = make_bundle(); body = encoded(bundle)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"TOKI_DAILY_CACHE_DIR": folder}):
            single = {k: v for k, v in SOURCE.items() if k != "browser_timeframes"}
            admit_browser_snapshot(single, {}, BASE.encode(), START.isoformat(), now=START + timedelta(minutes=1))
            _, metadata = admit_browser_snapshot(SOURCE, {}, body, (START + timedelta(minutes=1)).isoformat(), now=START + timedelta(minutes=2))
            self.assertEqual(metadata["fetched_at"], bundle["observed_at"])
            self.assertEqual(len(list(Path(folder).glob("*.bin"))), 2)
            self.assertEqual(set(metadata["captured_timeframes"]), set(FREE_INVESTING_TIMEFRAMES))
            fetch = Mock(side_effect=AssertionError("No network on cache replay"))
            replay, info = fetch_source(SOURCE, {}, fetch, now=START + timedelta(hours=23))
            self.assertEqual(replay, body); self.assertEqual(info["fetched_at"], bundle["observed_at"]); fetch.assert_not_called()
            legacy, _ = fetch_source(single, {}, fetch, now=START + timedelta(hours=23)); self.assertEqual(legacy, BASE.encode())
            refreshed = make_bundle(); refreshed["snapshots"][0]["html"] = refreshed["snapshots"][0]["html"].replace("60</span>", "59</span>")
            refreshed["snapshots"][0]["sha256"] = sha256(refreshed["snapshots"][0]["html"].encode()).hexdigest()
            _, replaced = admit_browser_snapshot(SOURCE, {}, encoded(refreshed), refreshed["observed_at"], now=START + timedelta(minutes=3))
            self.assertEqual(Path(replaced["superseded_snapshot"]["body_path"]).read_bytes(), body)
            failing = Mock(side_effect=RuntimeError("HTTP403"))
            with self.assertRaisesRegex(RuntimeError, "403"): fetch_source(SOURCE, {}, failing, now=START + timedelta(days=1, minutes=3))
            with self.assertRaisesRegex(RuntimeError, "retained"): fetch_source(SOURCE, {}, failing, now=START + timedelta(days=1, hours=2))
            self.assertEqual(failing.call_count, 1)

    def test_browser_visits_once_and_records_failed_tab_without_retry_or_hourly_copy(self):
        source = {**SOURCE, "browser_timeframes": ["1h", "1D"]}
        page, browser, manager = MagicMock(), MagicMock(), MagicMock()
        manager.__enter__.return_value.chromium.launch.return_value = browser
        browser.new_page.return_value = page; page.goto.return_value = SimpleNamespace(status=200)
        state = {"frame": "1h"}; activated = []
        def locator(selector):
            tab = MagicMock()
            match = re.search(r'\[data-test="(1d|1h)"\]$', selector)
            if match:
                code = match.group(1)
                def activate(*args, **kwargs): activated.append(code); state["frame"] = "1D" if code == "1d" else "1h"
                tab.press.side_effect = activate
            return tab
        page.locator.side_effect = locator
        def evaluate(script):
            if script == _INVESTING_SELECTED_TAB: return "1h"
            if script == _INVESTING_TABLE_SIGNATURE: return "hourly-real-values"
            return period_html(state["frame"])
        page.evaluate.side_effect = evaluate
        def ready(script, *, arg, timeout):
            if arg["code"] == "1d": raise TimeoutError("Visible detail tables unchanged")
        page.wait_for_function.side_effect = ready
        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            body, metadata = fetch_browser_page(source, {})
        bundle = json.loads(body)
        self.assertEqual([sample["timeframe"] for sample in bundle["snapshots"]], ["1h"])
        self.assertEqual(metadata["missing_timeframes"], ["1D"])
        self.assertEqual(activated, ["1d"]); self.assertEqual(metadata["validated_records"], 9)
        page.goto.assert_called_once(); page.reload.assert_not_called(); page.content.assert_not_called(); browser.close.assert_called_once()

    def test_selected_label_and_summary_changes_do_not_prove_detail_refresh(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                clean = (Path(__file__).parent / "fixtures" / "investing-bitcoin-visible.html").read_text()
                page = browser.new_page(); page.route("**/*", lambda route: route.abort()); page.set_content(clean)
                previous = page.evaluate(_INVESTING_TABLE_SIGNATURE)
                page.evaluate("""() => {
                    document.querySelector('[data-test="1h"]').classList.remove('!border-inv-blue-500');
                    document.querySelector('[data-test="1d"]').setAttribute('aria-selected','true');
                    document.querySelector('table td:nth-child(2)').innerText='卖出';
                }""")
                self.assertFalse(page.evaluate(_INVESTING_PERIOD_READY, {"code": "1d", "previous": previous}))
                page.evaluate("() => document.querySelectorAll('table')[1].rows[1].cells[1].innerText='45.123'")
                self.assertFalse(page.evaluate(_INVESTING_PERIOD_READY, {"code": "1d", "previous": previous, "stableMillis": 0}))
                self.assertTrue(page.evaluate(_INVESTING_PERIOD_READY, {"code": "1d", "previous": previous, "stableMillis": 0}))
            finally: browser.close()


if __name__ == "__main__":
    unittest.main()
