"""Public browser routes are narrow, parsed and do not change Investing tabs."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from toki_finance_daily.browser_pages import _browser_kind, fetch_browser_page, investing_visible_capture_script, validate_browser_snapshot

GOLD = {"id": "investing_gold_technical", "name": "Gold", "adapter": "investing_instrument_technical",
        "url": "https://cn.investing.com/commodities/gold-technical", "instrument_name": "黄金期货",
        "minimum_interval_seconds": 86400}
OBSERVED = "2026-10-10T19:39:53.262745+00:00"
BODY = (Path(__file__).parent / "fixtures" / "investing-gold-technical.html").read_bytes()


class PublicBrowserPageTests(unittest.TestCase):
    def browser(self, status=200, body=BODY):
        page = MagicMock()
        page.goto.return_value = SimpleNamespace(status=status)
        page.content.return_value = body.decode()
        page.evaluate.return_value = body.decode()
        browser = MagicMock()
        browser.new_page.return_value = page
        manager = MagicMock()
        manager.__enter__.return_value.chromium.launch.return_value = browser
        return manager, browser, page

    def test_only_known_https_instrument_routes_and_matching_adapter_are_allowed(self):
        paths = ["/crypto/bitcoin/technical", "/commodities/gold-technical", "/commodities/crude-oil-technical",
                 "/commodities/brent-oil-technical", "/currencies/xau-usd-technical", "/indices/nq-100-technical"]
        for path in paths:
            self.assertNotEqual(_browser_kind({**GOLD,"url":"https://cn.investing.com"+path}),"tradingview")
        for url in ("http://cn.investing.com/commodities/gold-technical",
                    "https://cn.investing.com/commodities/silver-technical",
                    "https://cn.investing.com/pro/", "https://other.example/commodities/gold-technical",
                    "https://person:password@cn.investing.com/commodities/gold-technical",
                    GOLD["url"]+"?login=true", GOLD["url"]+"#paid"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                _browser_kind({**GOLD,"url":url})
        with self.assertRaises(ValueError):
            _browser_kind({**GOLD,"adapter":"html_news"})

    def test_visible_parser_rejects_instrument_mismatch_and_locked_selected_period(self):
        records = validate_browser_snapshot(GOLD,{},BODY,OBSERVED)
        self.assertEqual(len(records),9)
        with self.assertRaisesRegex(ValueError,"source URL"):
            validate_browser_snapshot({**GOLD,"url":"https://cn.investing.com/crypto/bitcoin/technical"},{},BODY,OBSERVED)
        locked = BODY.replace(b' class="!border-inv-blue-500"',b'').replace(b'data-test="1m"',b'data-test="1m" aria-selected="true"')
        with self.assertRaisesRegex(ValueError,"selected free timeframe"):
            validate_browser_snapshot(GOLD,{},locked,OBSERVED)
        with self.assertRaisesRegex(ValueError,"heading"):
            validate_browser_snapshot(GOLD,{},b'<h1>Verify you are human</h1>',OBSERVED)

    def test_investing_transport_waits_for_current_free_values_without_clicking_any_tab(self):
        manager,browser,page = self.browser()
        with patch("playwright.sync_api.sync_playwright",return_value=manager):
            body,metadata = fetch_browser_page({**GOLD,"timeframe":"1D"},{})
        self.assertEqual(body,BODY)
        self.assertEqual(metadata["selected_timeframe"],"1h")
        self.assertNotIn("requested_timeframe",metadata)
        self.assertEqual(metadata["http_status"],200)
        self.assertEqual(metadata["snapshot_format"],"visible-dom-projection")
        self.assertTrue(metadata["fetched_at"].endswith("+00:00"))
        page.locator.assert_called_once_with('[data-test="timeframe-selection"]')
        page.locator.return_value.click.assert_not_called()
        page.wait_for_function.assert_called_once()
        page.content.assert_not_called()
        self.assertIn("getClientRects",page.wait_for_function.call_args.args[0])
        browser.close.assert_called_once()

    def test_http403_is_not_retried_or_treated_as_browser_success(self):
        manager,browser,page = self.browser(status=403)
        with patch("playwright.sync_api.sync_playwright",return_value=manager), self.assertRaisesRegex(RuntimeError,"403"):
            fetch_browser_page(GOLD,{})
        page.goto.assert_called_once()
        page.locator.assert_not_called()
        page.content.assert_not_called()
        browser.close.assert_called_once()

    def test_invalid_route_or_budget_does_not_launch_a_browser(self):
        with patch("playwright.sync_api.sync_playwright") as playwright:
            for source,network in (({**GOLD,"url":"https://cn.investing.com/pro/"},{}),(GOLD,{"max_bytes":True}),
                                   (GOLD,{"max_bytes":1023})):
                with self.assertRaises(ValueError):fetch_browser_page(source,network)
            with self.assertRaises(ValueError):fetch_browser_page({**GOLD,"browser_timeout_seconds":0},{})
            playwright.assert_not_called()

    def test_tradingview_existing_public_tab_selection_is_retained(self):
        manager,browser,page = self.browser()
        page.locator.return_value.get_attribute.return_value="false"
        source={"url":"https://www.tradingview.com/symbols/SPX/technicals/","adapter":"tradingview_technical","timeframe":"1W"}
        with patch("playwright.sync_api.sync_playwright",return_value=manager):
            _,metadata=fetch_browser_page(source,{})
        page.locator.assert_called_once_with('button[role="tab"][id="1W"]')
        page.locator.return_value.click.assert_called_once()
        self.assertEqual(metadata["requested_timeframe"],"1W")

    def test_visible_browser_mode_uses_regular_chromium(self):
        manager, browser, page = self.browser()
        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            fetch_browser_page({**GOLD, "browser_headless": False}, {})
        launch = manager.__enter__.return_value.chromium.launch
        self.assertFalse(launch.call_args.kwargs["headless"])
        self.assertEqual(set(launch.call_args.kwargs), {"headless", "timeout"})
        with patch("playwright.sync_api.sync_playwright") as playwright:
            with self.assertRaisesRegex(ValueError, "browser_headless"):
                fetch_browser_page({**GOLD, "browser_headless": "false"}, {})
            playwright.assert_not_called()

    def test_real_offline_dom_projection_excludes_hidden_scripts_and_sidebars_with_own_table_times(self):
        from playwright.sync_api import sync_playwright
        fixture=(Path(__file__).parent/"fixtures"/"investing-bitcoin-visible.html").read_text()
        source={**GOLD,"url":"https://cn.investing.com/crypto/bitcoin/technical","instrument_name":"比特币"}
        contaminated=fixture.replace('</body>', '<aside><h1>Wrong asset</h1><table><tr><td>RSI</td><td>999</td><td>买入</td></tr></table></aside><div hidden><h1>Hidden asset</h1></div><script>window.secret="hidden-script-value"</script><p>FAQ irrelevant data</p></body>')
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                page=browser.new_page();page.route('**/*',lambda route:route.abort());page.set_content(contaminated)
                projected=page.evaluate(investing_visible_capture_script())
            finally:browser.close()
        self.assertNotIn('hidden-script-value',projected)
        self.assertNotIn('Wrong asset',projected)
        self.assertNotIn('Hidden asset',projected)
        self.assertNotIn('FAQ',projected)
        self.assertEqual(projected.count('<table>'),4)
        records=validate_browser_snapshot(source,{},projected.encode(),"2026-10-11T05:00:00+00:00")
        self.assertEqual(len(records),9)
        self.assertTrue(all(r['data']['symbol']=='BTC' and r['data']['contract'] is None for r in records))
        details={r['data']['subtype']:r['data'] for r in records if r['data']['subtype']!='summary'}
        self.assertEqual(details['indicators']['indicators'][0]['value'],60.189)
        self.assertEqual(details['moving_averages']['details'][0]['simple']['value'],83057.3)
        self.assertEqual(details['indicators']['provider_at'],"2026-10-11T04:11:00+00:00")
        self.assertIsNone(details['pivot_points']['provider_at'])

    def test_visible_snapshot_with_partial_shell_tables_cannot_be_admitted(self):
        # The summary can render before every detail table; an absent table
        # must not turn this loading shell into a successful daily capture.
        partial=BODY.replace(b'<span>S3</span>',b'<span>Loading S3</span>')
        with self.assertRaisesRegex(ValueError,'finite visible values'):
            validate_browser_snapshot(GOLD,{},partial,OBSERVED)

    def test_raw_hidden_state_or_script_is_not_a_visible_snapshot(self):
        for fragment in (b'<script>window.hiddenState={}</script>',b'<div hidden>paid value</div>',
                         b'<aside>sidebar</aside>',b'<div style="display:none">invisible</div>'):
            with self.subTest(fragment=fragment),self.assertRaisesRegex(ValueError,'visible projection'):
                validate_browser_snapshot(GOLD,{},BODY+fragment,OBSERVED)


if __name__=="__main__":unittest.main()
