"""Public edition coverage, privacy, portability and actual browser behavior."""
from copy import deepcopy
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, SkipTest
import base64
import importlib.util
import json

from toki_finance_daily.site import build_site


def inputs():
    def record(identifier, kind, data, **extra):
        return {"id": identifier, "kind": kind, "source_id": "source", "title": identifier,
                "url": "https://example.org/" + identifier, "published_at": "2026-10-10T20:00:00+08:00",
                "observed_at": "2026-10-11T03:00:00+08:00", "summary": "真实摘要 " * 50,
                "data": data, **extra}
    technical = {"topic": "technical", "provider": "提供者", "symbol": "TESTF", "symbol_name": "测试期货",
                 "contract": "TESTF", "timeframe": "1h", "subtype": "summary", "rating": "买入",
                 "value": "买入", "moving_average_rating": "买入", "indicator_rating": "中性",
                 "provider_at": None, "instrument_url": "https://example.org/test-technical"}
    records = [record("n"+str(i), "news", {"publisher": "媒体", "provenance": [{"source_id": "duplicate"}]}) for i in range(30)]
    records += [record("rating", "macro", technical),
                record("indicators", "macro", {**technical, "subtype": "indicators", "provider_at": "2026-10-10T00:01:00+00:00",
                    "details": [{"name": "RSI(14)", "value": 64.006, "signal": "买入"}]}),
                record("ma", "macro", {**technical, "subtype": "moving_averages", "details": [{"name": "MA5",
                    "simple": {"value": 100.25, "signal": "卖出"}, "exponential": {"value": 101.5, "signal": "买入"}}]}),
                record("pivot", "macro", {**technical, "subtype": "pivot_points", "details": [{"method": "经典", "levels": {"P": 102.5, "轴点": 102.5, "S1": None}, "raw_levels": {"P": "102.50", "轴点": "102.50", "S1": "—"}}]}),
                record("unknown", "macro", {**technical, "timeframe": None, "subtype": "indicators", "details": {"ATR(14)": "少波动", "last_price": 0, "counts": {"买入": 0}}}),
                record("daily", "macro", {**technical, "timeframe": "1D", "provider": "另一提供者"}),
                record("tv", "macro", {**technical, "timeframe": "1D", "provider": "TradingView", "oscillators": [{"name": "RSI", "value": 59.93, "action": "Neutral"}],
                    "moving_averages": [{"name": "EMA(10)", "value": 70, "action": "Buy"}],
                    "pivots": [{"name": "P", "classic": 72, "fibonacci": 73, "value_raw": {"classic": "72.00", "fibonacci": "73.00"}}]}),
                record("pattern", "macro", {**technical, "timeframe": "1D", "subtype": "candlestick_patterns", "details": [{"pattern": "Pattern", "stage": "Completed", "direction": "Bullish", "reliability": "高", "bars_ago": 0, "pattern_date": "2026-10-09"}]}),
                record("quote", "macro", {"topic": "market", "label": "现货", "value": 100.25, "unit": "美元", "ups_percent": 0, "provider_at": "2026-10-10T04:59:00+08:00"}),
                record("official", "macro", {"label": "官方数据", "value": 0, "unit": "%", "period": "调查期", "reference_only": True}),
                record("past", "calendar", {"event_at": "2026-10-10T10:00:00+08:00", "region": "中国", "star": 2, "previous": 1, "consensus": 2, "actual": 0, "revised": 0, "calendar_scope": "past"}),
                record("future", "calendar", {"event_at": "2026-10-14T09:30:00+08:00", "region": "中国", "actual": None, "forecast": None, "previous": None, "calendar_scope": "upcoming"})]
    dataset = {"schema_version": 1, "date": "2026-10-11", "timezone": "Asia/Shanghai", "cutoff": "2026-10-11T03:48:47+08:00", "mode": "live",
               "sources": [{"id": "source", "name": "正式来源", "status": "error", "error": "PRIVATE_ERROR", "count": 999}], "records": records}
    item = {"category": "财经", "title": "主线", "fact": "事实", "analysis": "解释", "watch": "观察", "evidence_ids": ["rating", "n1"]}
    brief = {"headline": "财经研究", "dek": "可验证的解读", "edition": "日报", "lead": item, "stories": [deepcopy(item), deepcopy(item)],
             "calendar": [{"title": "未来事件", "event_at": "2026-10-14T09:30:00+08:00", "region": "中国", "note": "初步日程", "evidence_ids": ["future"]}],
             "watchlist": [{"title": "情境", "detail": "触发与验证", "evidence_ids": ["n1"]}]}
    return dataset, brief


def focused_inputs():
    dataset, brief = inputs()
    dataset["focus_assets"] = [
        {"key": "gold", "label": "黄金", "source_id": "gold_source", "error": "PRIVATE_FOCUS"},
        {"key": "oil", "label": "原油", "source_id": "oil_source"},
        {"key": "bitcoin", "label": "比特币", "source_id": "bitcoin_source"},
        {"key": "fourth", "label": "NAS500", "source_id": None},
    ]
    for key, period, number, signal, stamp in (
        ("gold", "1h", 0, "超买", "2026-10-10T00:01:00+00:00"),
        ("oil", "5h", -9.25, "卖出", "2026-10-10T00:02:00+00:00"),
        ("bitcoin", "1D", 99, "买入", "2026-10-09T03:00:00+00:00"),
    ):
        base = {"topic": "technical", "provider": "实际提供者", "symbol": key.upper(),
                "contract": key.upper(), "instrument_name": key, "instrument_header": key + " 实际合约",
                "instrument_url": "https://example.org/" + key + "-technical", "provider_at": None}
        def record(identifier, data):
            return {"id": key + "_" + identifier, "kind": "macro", "source_id": key + "_source",
                    "title": identifier, "url": "https://example.org/" + key + "-technical",
                    "published_at": None, "observed_at": "2026-10-11T03:00:00+08:00", "summary": "",
                    "data": {**base, **data}}
        for timeframe in ("30m", "1h", "5h", "1D", "1W", "1M"):
            dataset["records"].append(record(timeframe, {"subtype": "summary", "timeframe": timeframe,
                "selected_timeframe": timeframe == period, "rating": "买入" if timeframe == period else "卖出"}))
        # Bitcoin's other-period table is deliberately present: it must not
        # fill the selected daily snapshot in the comparison.
        detail_period = "1h" if key == "bitcoin" else period
        dataset["records"] += [
            record("indicator", {"subtype": "indicators", "timeframe": detail_period, "provider_at": stamp,
                "details": [{"name": "RSI(14)", "value": number, "signal": signal},
                            {"name": "ROC", "value": -.75, "signal": "卖出"}]}),
            record("ma", {"subtype": "moving_averages", "timeframe": detail_period,
                "provider_at": "2026-10-08T04:00:00+00:00", "details": [{"name": "MA5",
                    "simple": {"value": 0, "signal": "买入"}, "exponential": {"value": -2, "signal": "卖出"}}]}),
            record("pivot", {"subtype": "pivot_points", "timeframe": detail_period, "details": [
                {"method": "经典", "levels": {"P": -3.5, "S1": None}}]}),
        ]
    return dataset, brief


def calendar_inputs():
    dataset, brief = inputs()
    for date_start, days, state in ((5, 6, "past"), (12, 3, "upcoming")):
        for date in range(date_start, date_start+days):
            for hour in range(6, 12):
                identifier = f"calendar_{date}_{hour}"
                dataset["records"].append({
                    "id": identifier, "kind": "calendar", "source_id": "source", "title": "真实日程 " + identifier,
                    "url": "https://example.org/" + identifier, "published_at": None,
                    "observed_at": "2026-10-11T03:00:00+08:00", "summary": "",
                    "data": {"event_at": f"2026-10-{date:02}T{hour:02}:00:00+08:00", "region": "中国",
                             "previous": 0, "consensus": None, "actual": 0 if state == "past" else None,
                             "calendar_scope": state},
                })
    return dataset, brief


class ScriptReader(HTMLParser):
    def __init__(self):
        super().__init__(); self.active = False; self.payload = ""; self.scripts = 0

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.scripts += 1
            self.active = dict(attrs).get("id") == "daily-data"

    def handle_endtag(self, tag):
        if tag == "script": self.active = False

    def handle_data(self, value):
        if self.active: self.payload += value


class SiteTests(TestCase):
    def build(self, dataset=None, brief=None, files=()):
        data, editorial = inputs()
        temp = TemporaryDirectory(); self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        for name in files: (folder / name).write_text("available", encoding="utf-8")
        result = build_site(dataset or data, brief or editorial, folder)
        return folder, result, json.loads(Path(result["public_data"]).read_text()), Path(result["html"]).read_text()

    def test_all_records_preserved_once_and_input_is_not_mutated(self):
        dataset, brief = inputs(); original = deepcopy((dataset, brief))
        _, _, public, _ = self.build(dataset, brief)
        self.assertEqual({r["id"] for r in public["records"]}, {r["id"] for r in dataset["records"]})
        self.assertEqual(len(public["records"]), len(dataset["records"]))
        self.assertTrue(all(len(r["summary"]) <= 180 for r in public["records"]))
        self.assertEqual((dataset, brief), original)
        self.assertEqual(public["default_technical_id"], "rating")

    def test_private_fields_are_excluded_at_every_table_depth(self):
        dataset, brief = inputs()
        dataset["enrichment"] = {"error": "PRIVATE_ENRICHMENT"}
        dataset["records"][0]["data"].update(token="PRIVATE_TOKEN", raw_path="PRIVATE_PATH", editorial_note="PRIVATE_DIAGNOSTIC")
        dataset["records"][31]["data"]["details"][0]["secret"] = "PRIVATE_NESTED"
        dataset["records"][31]["data"]["availability_note"] = "PRIVATE_AVAILABILITY"
        _, _, public, html = self.build(dataset, brief)
        for secret in ("PRIVATE_ERROR", "PRIVATE_ENRICHMENT", "PRIVATE_TOKEN", "PRIVATE_PATH", "PRIVATE_DIAGNOSTIC", "PRIVATE_NESTED", "PRIVATE_AVAILABILITY"):
            self.assertNotIn(secret, html)
            self.assertNotIn(secret, json.dumps(public))
        self.assertNotIn("sources", public)

    def test_script_closing_and_unsafe_urls_cannot_create_active_source_markup(self):
        dataset, brief = inputs()
        brief["headline"] = 'A <3 </script><script>window.attacked=true</script>'
        dataset["records"][0]["title"] = '<img src=x onerror=alert(1)>安全标题'
        dataset["records"][0]["url"] = 'javascript:alert(1)'
        dataset["records"][1]["url"] = 'https://user:password@example.org/private'
        dataset["records"][2]["url"] = 'https://example.org/\nattack'
        _, _, public, html = self.build(dataset, brief)
        reader = ScriptReader(); reader.feed(html)
        self.assertEqual(reader.scripts, 2)
        self.assertEqual(json.loads(reader.payload), public)
        self.assertNotIn('<img src=x', html)
        self.assertNotIn('window.attacked', html)
        self.assertIn('\\u003c3', reader.payload)
        self.assertTrue(all(r["url"] == "" for r in public["records"][:3]))

    def test_table_shapes_cycles_and_independent_times_are_kept(self):
        _, _, public, _ = self.build()
        by_id = {r["id"]: r for r in public["records"]}
        self.assertIsNone(by_id["rating"]["data"]["provider_at"])
        self.assertEqual(by_id["indicators"]["data"]["provider_at"], "2026-10-10T00:01:00+00:00")
        self.assertIsNone(by_id["unknown"]["data"]["timeframe"])
        self.assertEqual(by_id["ma"]["data"]["details"][0]["exponential"]["value"], 101.5)
        self.assertEqual(by_id["pivot"]["data"]["details"][0]["levels"]["P"], 102.5)
        self.assertEqual(by_id["pivot"]["data"]["details"][0]["levels"]["轴点"], 102.5)
        self.assertEqual(by_id["tv"]["data"]["pivots"][0]["classic"], 72)
        self.assertEqual(by_id["past"]["data"]["actual"], 0)
        self.assertEqual(by_id["past"]["data"]["revised"], 0)
        self.assertEqual(by_id["future"]["data"]["calendar_scope"], "upcoming")

    def test_portable_inline_data_and_only_existing_downloads(self):
        _, result, public, html = self.build(files=("poster.png", "technicals.csv", "technical.html"))
        self.assertEqual({item["path"] for item in public["assets"]["downloads"]}, {"poster.png", "technicals.csv"})
        self.assertTrue(public["assets"]["online"])
        self.assertNotIn('fetch(', html)
        self.assertNotIn('<script src=', html)
        self.assertNotIn('<link rel="stylesheet"', html)
        self.assertNotIn('dataset.json', html)
        js = (Path(__file__).resolve().parents[1] / "toki_finance_daily/assets/daily-site.js").read_text()
        digest = base64.b64encode(sha256(js.encode()).digest()).decode()
        self.assertIn('sha256-' + digest, html)
        self.assertTrue(Path(result["html"]).is_absolute())
        self.assertNotIn('innerHTML', js)

    def test_focus_bindings_preserve_order_and_empty_slot_without_exposing_source_configuration(self):
        dataset, brief = focused_inputs(); before = deepcopy(dataset)
        _, _, public, html = self.build(dataset, brief)
        focus = public["focus_assets"]
        self.assertEqual([asset["key"] for asset in focus], ["gold", "oil", "bitcoin", "fourth"])
        self.assertEqual(focus[3]["record_ids"], [])
        self.assertEqual(len(focus[0]["record_ids"]), 9)
        self.assertTrue(all(identifier.startswith("gold_") for identifier in focus[0]["record_ids"]))
        self.assertTrue(all(set(asset) == {"key", "label", "record_ids"} for asset in focus))
        self.assertNotIn('"source_id"', json.dumps(public))
        self.assertNotIn("PRIVATE_FOCUS", html)
        self.assertEqual(dataset, before)
        by_id = {record["id"]: record for record in public["records"]}
        self.assertTrue(by_id["gold_1h"]["data"]["selected_timeframe"])
        self.assertFalse(by_id["gold_1D"]["data"]["selected_timeframe"])
        self.assertEqual(by_id["gold_indicator"]["data"]["details"][0]["value"], 0)
        self.assertEqual(by_id["oil_indicator"]["data"]["details"][0]["value"], -9.25)
        self.assertEqual(by_id["gold_ma"]["data"]["provider_at"], "2026-10-08T04:00:00+00:00")

    def test_no_focus_configuration_creates_no_implied_portfolio(self):
        _, _, public, _ = self.build()
        self.assertEqual(public["focus_assets"], [])


class SiteBrowserTests(TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec("playwright") is None:
            raise SkipTest("Optional Playwright runtime is unavailable")
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        try: cls.browser = cls.runtime.chromium.launch(headless=True)
        except Exception as exc:
            cls.runtime.stop(); raise SkipTest("Optional Chromium unavailable: " + str(exc).splitlines()[0])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.runtime.stop()

    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        dataset, brief = inputs()
        self.result = build_site(dataset, brief, self.folder)
        self.page = self.browser.new_page(viewport={"width": 390, "height": 900})
        self.addCleanup(self.page.close)
        self.errors = []
        self.page.on("pageerror", lambda exc: self.errors.append(str(exc)))
        self.page.route("http://**/*", lambda route: route.abort())
        self.page.route("https://**/*", lambda route: route.abort())
        self.page.goto(Path(self.result["html"]).as_uri())

    def test_file_open_news_search_and_all_technical_shapes(self):
        self.assertEqual(self.page.locator(".news-card").count(), 6)
        self.page.locator("#news-all").click()
        self.assertEqual(self.page.locator(".news-card").count(), 30)
        self.page.locator("#news-search").fill("n29")
        self.assertEqual(self.page.locator(".news-card").count(), 1)
        self.assertEqual(self.page.locator("#technical-period").input_value(), "1h")
        self.assertIn("64.006", self.page.locator("#technical-list").inner_text())
        self.assertIn("101.5", self.page.locator("#technical-list").inner_text())
        self.assertIn("102.50", self.page.locator("#technical-list").inner_text())
        self.page.locator("#technical-period").select_option("1D")
        content = self.page.locator("#technical-list").inner_text()
        self.assertIn("59.93", content)
        self.assertIn("Pattern", content)
        self.assertNotIn("64.006", content)
        self.page.locator("#technical-period").select_option("unknown")
        self.assertIn("少波动", self.page.locator("#technical-list").inner_text())
        self.assertNotIn("[object Object]", self.page.locator("body").inner_text())
        self.assertFalse(self.errors)
        self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), 390)

    def test_news_reader_batches_all_records_and_collapses_from_inside_scroll_on_each_viewport(self):
        for width in (1440, 540, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(Path(self.result["html"]).as_uri())
                self.assertEqual(self.page.locator(".news-card").count(), 6)
                initial_height = self.page.evaluate("document.documentElement.scrollHeight")
                self.page.locator("#news-more").click()
                self.assertEqual(self.page.locator(".news-card").count(), 12)
                self.assertEqual(self.page.locator("#news-more").get_attribute("aria-expanded"), "true")
                self.page.locator("#news-all").focus()
                self.page.locator("#news-all").press("Enter")
                self.assertEqual(self.page.locator(".news-card").count(), 30)
                self.assertEqual(set(self.page.locator(".news-card").evaluate_all("cards => cards.map(card => card.dataset.recordId)")),
                                 {"n"+str(index) for index in range(30)})
                self.assertEqual(self.page.evaluate("document.activeElement.id"), "news-collapse")
                scroller = self.page.locator("#news-scroll")
                self.assertLessEqual(scroller.evaluate("node => node.clientHeight"), 720)
                self.assertGreater(scroller.evaluate("node => node.scrollHeight"), scroller.evaluate("node => node.clientHeight"))
                self.assertLess(self.page.evaluate("document.documentElement.scrollHeight")-initial_height, 150)
                collapse = self.page.locator("#news-collapse")
                before = collapse.bounding_box()
                scroller.evaluate("node => node.scrollTop = (node.scrollHeight-node.clientHeight)/2")
                after = collapse.bounding_box()
                self.assertAlmostEqual(before["y"], after["y"], delta=1)
                self.assertTrue(collapse.is_visible())
                self.assertGreaterEqual(after["y"], 0)
                self.assertLess(after["y"]+after["height"], 900)
                scroller.evaluate("node => node.scrollTop = node.scrollHeight")
                last = self.page.locator(".news-card").last.bounding_box()
                region = scroller.bounding_box()
                self.assertLessEqual(last["y"]+last["height"], region["y"]+region["height"]+1)
                self.assertGreater(last["y"]+last["height"], region["y"])
                scroller.evaluate("node => {node.scrollTop=0;node.focus({preventScroll:true})}")
                outer_y = self.page.evaluate("window.scrollY")
                scroller.press("PageDown")
                self.page.wait_for_timeout(250)
                self.assertGreater(scroller.evaluate("node => node.scrollTop"), 0)
                self.assertEqual(self.page.evaluate("window.scrollY"), outer_y)
                collapse.click()
                self.assertEqual(self.page.locator(".news-card").count(), 6)
                self.assertEqual(scroller.evaluate("node => node.scrollTop"), 0)
                self.assertEqual(self.page.locator("#news-more").get_attribute("aria-expanded"), "false")
                self.assertEqual(self.page.evaluate("document.activeElement.id"), "news-more")
                self.assertFalse(self.page.locator("#news-reader-bar").is_visible())
                self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.assertFalse(self.errors)

    def test_news_filter_resets_batch_scroll_and_preserves_all_matching_records(self):
        dataset, brief = inputs()
        dataset["sources"].append({"id": "other", "name": "另一个来源"})
        for index, record in enumerate(dataset["records"][:30]):
            record["title"] += " 黄金" if index % 2 == 0 else " 人工智能"
            if index >= 15: record["source_id"] = "other"
        result = build_site(dataset, brief, self.folder)
        self.page.goto(Path(result["html"]).as_uri())
        self.page.locator("#news-all").click()
        self.page.locator("#news-scroll").evaluate("node => node.scrollTop = node.scrollHeight")
        self.page.locator("#news-search").fill("n2")
        self.assertEqual(self.page.locator(".news-card").count(), 6)
        self.assertIn("11 则相关资讯", self.page.locator("#news-result").inner_text())
        self.assertEqual(self.page.locator("#news-scroll").evaluate("node => node.scrollTop"), 0)
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "news-search")
        self.page.locator("#news-all").click()
        self.assertEqual(self.page.locator(".news-card").count(), 11)
        self.assertTrue(all(identifier.startswith("n2") for identifier in self.page.locator(".news-card").evaluate_all("cards => cards.map(card => card.dataset.recordId)")))
        self.page.locator("#news-source").select_option("另一个来源")
        self.assertEqual(self.page.locator(".news-card").count(), 6)
        self.assertFalse(self.page.locator("#news-reader-bar").is_visible())
        self.page.locator("#news-search").fill("")
        self.page.locator("#news-topic").select_option("能源与商品")
        self.assertEqual(self.page.locator(".news-card").count(), 6)
        self.page.locator("#news-all").click()
        self.assertEqual(self.page.locator(".news-card").count(), 7)
        self.page.locator("#news-search").fill("没有这条资讯")
        self.assertEqual(self.page.locator(".news-card").count(), 0)
        self.assertEqual(self.page.locator(".empty-state").count(), 1)
        self.assertFalse(self.page.locator("#news-actions").is_visible())
        self.assertFalse(self.errors)

    def test_news_reset_cancels_native_scroll_and_keeps_immediate_reopening_position(self):
        self.page.locator("#news-all").click()
        self.page.locator("#news-scroll").evaluate("node => node.scrollTo({top:1800,behavior:'smooth'})")
        self.page.locator("#news-collapse").click()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#news-scroll").evaluate("node => node.scrollTop"), 0)
        self.assertEqual(self.page.locator(".news-card").count(), 6)
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "news-more")
        self.page.locator("#news-all").click()
        self.page.locator("#news-scroll").focus()
        self.page.locator("#news-scroll").press("PageDown")
        self.page.locator("#news-collapse").click()
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#news-scroll").evaluate("node => node.scrollTop"), 0)
        self.page.locator("#news-all").click()
        self.page.locator("#news-scroll").evaluate("node => node.scrollTo({top:1800,behavior:'smooth'})")
        self.page.locator("#news-search").fill("n2")
        self.page.wait_for_timeout(300)
        self.assertEqual(self.page.locator("#news-scroll").evaluate("node => node.scrollTop"), 0)
        self.assertEqual(self.page.evaluate("document.activeElement.id"), "news-search")
        self.page.locator("#news-search").fill("")
        # An immediate new request retains its intended reading position.
        self.page.evaluate("() => {document.getElementById('news-search').dispatchEvent(new Event('input'));document.getElementById('news-more').click();}")
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.locator(".news-card").count(), 12)
        self.assertGreater(self.page.locator("#news-scroll").evaluate("node => node.scrollTop"), 0)
        self.assertFalse(self.errors)

    def test_six_selected_snapshots_prefer_real_hourly_and_keep_other_cycle_values_separate(self):
        dataset, brief = focused_inputs()
        for record in dataset["records"]:
            if record.get("data", {}).get("subtype") == "summary" and record["source_id"].endswith("_source"):
                record["data"]["selected_timeframe"] = True
        result = build_site(dataset, brief, self.folder)
        self.page.goto(Path(result["html"]).as_uri())
        self.assertEqual(self.page.locator("#technical-period").input_value(), "1h")
        self.assertEqual(self.page.locator('.focus-card[data-focus-key=gold] .focus-card-period').inner_text(), "1 小时周期")
        self.assertEqual(self.page.locator('.focus-card').count(), 4)
        self.assertEqual(self.page.locator('#focus-ratings tbody tr').count(), 6)
        self.assertEqual(self.page.locator('#focus-indicators,#focus-ma,#focus-pivot').count(), 0)
        row = self.page.locator('.technical-record[data-record-id=gold_indicator] tbody tr').filter(has_text='RSI(14)')
        self.assertEqual(row.locator('td').nth(1).inner_text(), '0')
        self.assertEqual(row.locator('td').nth(2).inner_text(), '超买')
        self.assertEqual(self.page.locator('.technical-record[data-record-id=gold_indicator]').get_attribute('data-timeframe'), '1h')
        self.page.locator("#technical-asset").select_option(self.page.locator("#technical-asset option").filter(has_text="oil").get_attribute("value"))
        self.assertEqual(self.page.locator("#technical-period").input_value(), "1h")
        self.assertNotIn("-9.25", self.page.locator("#technical-list").inner_text())
        self.page.locator("#technical-period").select_option("5h")
        self.assertIn("-9.25", self.page.locator("#technical-list").inner_text())
        self.assertFalse(self.errors)

    def test_past_calendar_actual_zero_is_not_future_or_missing(self):
        self.page.locator("#calendar-status").select_option("past")
        rows = self.page.locator(".calendar-table tbody tr")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first.get_attribute("data-record-id"), "past")
        self.assertEqual(rows.first.locator("td").nth(6).inner_text(), "0\n修正 0")
        self.assertIn("已公布", rows.first.inner_text())
        self.page.locator("#calendar-status").select_option("future")
        rows = self.page.locator(".calendar-table tbody tr")
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first.get_attribute("data-record-id"), "future")
        self.assertIn("初步日程", rows.first.inner_text())
        self.assertEqual(rows.first.locator("td").nth(6).inner_text(), "—")
        self.assertFalse(self.errors)

    def test_calendar_reader_batches_all_rows_and_collapses_during_keyboard_scroll_on_each_viewport(self):
        dataset, brief = calendar_inputs()
        result = build_site(dataset, brief, self.folder)
        expected_ids = {record["id"] for record in dataset["records"] if record["kind"] == "calendar"}
        for width in (1440, 540, 390):
            with self.subTest(width=width):
                self.page.set_viewport_size({"width": width, "height": 900})
                self.page.goto(Path(result["html"]).as_uri())
                rows = self.page.locator('.calendar-table tbody tr')
                self.assertEqual(rows.count(), 12)
                self.assertEqual(self.page.locator('#calendar-result').inner_text(), '共 19 项日程 · 按日期阅读')
                self.page.locator('#calendar-status').select_option('all')
                self.assertEqual(rows.count(), 12)
                height = self.page.evaluate('document.documentElement.scrollHeight')
                self.page.locator('#calendar-more').click()
                self.assertEqual(rows.count(), 24)
                self.page.locator('#calendar-all').focus()
                self.page.locator('#calendar-all').press('Enter')
                self.assertEqual(rows.count(), 56)
                self.assertEqual(set(rows.evaluate_all('rows => rows.map(row => row.dataset.recordId)')), expected_ids)
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'calendar-collapse')
                scroller = self.page.locator('#calendar-scroll')
                self.assertLessEqual(scroller.evaluate('node => node.clientHeight'), 720)
                self.assertGreater(scroller.evaluate('node => node.scrollHeight'), scroller.evaluate('node => node.clientHeight'))
                self.assertLess(self.page.evaluate('document.documentElement.scrollHeight')-height, 150)
                collapse = self.page.locator('#calendar-collapse')
                top = collapse.bounding_box()
                scroller.evaluate('node => node.scrollTop=(node.scrollHeight-node.clientHeight)/2')
                self.assertAlmostEqual(collapse.bounding_box()['y'], top['y'], delta=1)
                scroller.evaluate('node => node.scrollTop=node.scrollHeight')
                final = rows.last.bounding_box(); region = scroller.bounding_box()
                self.assertLessEqual(final['y']+final['height'], region['y']+region['height']+1)
                self.assertGreater(final['y']+final['height'], region['y'])
                scroller.evaluate('node => {node.scrollTop=0;node.focus({preventScroll:true})}')
                scroller.press('PageDown')
                collapse.click()
                self.page.wait_for_timeout(300)
                self.assertEqual(rows.count(), 12)
                self.assertEqual(scroller.evaluate('node => node.scrollTop'), 0)
                self.assertEqual(self.page.evaluate('document.activeElement.id'), 'calendar-more')
                self.assertFalse(self.page.locator('#calendar-reader-bar').is_visible())
                self.assertEqual(self.page.locator('#calendar-more').get_attribute('aria-expanded'), 'false')
                self.assertEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
        self.assertFalse(self.errors)

    def test_calendar_filters_reset_batch_and_native_scroll_without_losing_date_range_or_zero_values(self):
        dataset, brief = calendar_inputs()
        result = build_site(dataset, brief, self.folder)
        self.page.goto(Path(result['html']).as_uri())
        rows = self.page.locator('.calendar-table tbody tr')
        self.page.locator('#calendar-status').select_option('all')
        self.page.locator('#calendar-all').click()
        self.page.locator('#calendar-scroll').evaluate("node => node.scrollTo({top:1800,behavior:'smooth'})")
        self.page.locator('#calendar-status').select_option('past')
        self.page.wait_for_timeout(300)
        self.assertEqual(rows.count(), 12)
        self.assertEqual(self.page.locator('#calendar-scroll').evaluate('node => node.scrollTop'), 0)
        self.page.locator('#calendar-all').click()
        self.assertEqual(rows.count(), 37)
        self.page.locator('#calendar-from').fill('2026-10-08')
        self.assertEqual(rows.count(), 12)
        self.assertEqual(self.page.evaluate('document.activeElement.id'), 'calendar-from')
        self.page.locator('#calendar-to').fill('2026-10-10')
        self.assertEqual(rows.count(), 12)
        self.page.locator('#calendar-all').click()
        expected = {record['id'] for record in dataset['records'] if record['kind'] == 'calendar'
                    and '2026-10-08' <= record['data']['event_at'][:10] <= '2026-10-10'}
        self.assertEqual(rows.count(), 19)
        self.assertEqual(set(rows.evaluate_all('rows => rows.map(row => row.dataset.recordId)')), expected)
        original = self.page.locator('.calendar-table tr[data-record-id=past]')
        self.assertEqual(original.locator('td').nth(6).inner_text(), '0\n修正 0')
        self.page.locator('#calendar-from').fill('2026-10-20')
        self.assertEqual(rows.count(), 0)
        self.assertEqual(self.page.locator('#calendar-list .empty-state').count(), 1)
        self.assertFalse(self.page.locator('#calendar-actions').is_visible())
        self.page.locator('#calendar-from').fill('')
        self.page.locator('#calendar-to').fill('')
        self.page.locator('#calendar-status').select_option('future')
        self.assertEqual(rows.count(), 12)
        self.page.locator('#calendar-all').click()
        self.assertEqual(rows.count(), 19)
        self.assertTrue(all(state == 'future' for state in rows.evaluate_all('rows => rows.map(row => row.dataset.eventState)')))
        self.assertFalse(self.errors)

    def test_focus_cards_and_ratings_keep_cycles_while_lower_research_keeps_values_and_independent_times(self):
        dataset, brief = focused_inputs()
        result = build_site(dataset, brief, self.folder)
        self.page.goto(Path(result["html"]).as_uri())
        self.assertEqual(self.page.locator(".focus-card").count(), 4)
        self.assertIn("本期技术标的", self.page.locator('.focus-card[data-focus-key=gold]').inner_text())
        self.assertIn("gold 实际合约", self.page.locator('.focus-card[data-focus-key=gold]').inner_text())
        self.assertIn("合约 GOLD", self.page.locator('.focus-card[data-focus-key=gold]').inner_text())
        self.assertEqual(self.page.locator("#technical-asset option").first.inner_text(), "gold")
        self.assertEqual(self.page.locator("#technical-period").input_value(), "1h")
        self.assertEqual(self.page.locator('.focus-card[data-focus-key=oil] .focus-card-period').inner_text(), '5 小时周期')
        self.assertEqual(self.page.locator('.focus-card[data-focus-key=bitcoin] .focus-card-period').inner_text(), '日线周期')
        self.assertEqual(self.page.locator('#focus-indicators,#focus-ma,#focus-pivot').count(), 0)
        self.assertEqual(self.page.locator('#focus-ratings tbody tr').count(), 6)
        self.assertEqual(self.page.locator('#focus-ratings thead th').count(), 5)
        self.assertEqual(self.page.locator('#focus-ratings td[data-focus-key=gold]').all_inner_texts(), ['卖出', '买入', '卖出', '卖出', '卖出', '卖出'])
        self.assertEqual(self.page.locator('#focus-ratings td[data-focus-key=oil]').all_inner_texts(), ['卖出', '卖出', '买入', '卖出', '卖出', '卖出'])
        self.assertIn("观察时点", self.page.locator("#focus-ratings th[data-focus-key=gold]").inner_text())
        self.assertIn("来源更新时间未确认", self.page.locator("#focus-ratings th[data-focus-key=gold]").inner_text())
        gold = self.page.locator('.technical-record[data-record-id=gold_indicator]')
        rsi = gold.locator('tbody tr').filter(has_text='RSI(14)')
        self.assertEqual(rsi.locator('td').nth(1).inner_text(), '0')
        self.assertEqual(rsi.locator('td').nth(2).inner_text(), '超买')
        self.assertEqual(rsi.locator('.signal-extreme').count(), 1)
        self.assertIn('1 小时', gold.locator('.technical-meta').inner_text())
        self.assertIn('08:01', gold.locator('.technical-time').inner_text())
        self.assertIn('来源更新', gold.locator('.technical-time').inner_text())
        averages = self.page.locator('.technical-record[data-record-id=gold_ma]')
        self.assertIn('12:00', averages.locator('.technical-time').inner_text())
        self.assertIn('2026/10/08', averages.locator('.technical-time').inner_text())
        self.assertEqual(averages.locator('tbody tr').first.locator('td').nth(2).inner_text(), '0')
        self.assertEqual(averages.locator('tbody tr').nth(1).locator('td').nth(2).inner_text(), '-2')
        pivots = self.page.locator('.technical-record[data-record-id=gold_pivot]')
        self.assertEqual(pivots.locator('tbody tr').first.locator('td').nth(2).inner_text(), '-3.5')
        self.assertEqual(pivots.locator('tbody tr').nth(1).locator('td').nth(2).inner_text(), '—')
        self.assertIn('观察时点', pivots.locator('.technical-time').inner_text())
        self.assertIn('来源更新时间未确认', pivots.locator('.technical-time').inner_text())
        self.assertNotIn('08:01', pivots.locator('.technical-time').inner_text())
        self.page.locator('#technical-asset').select_option(self.page.locator('#technical-asset option').filter(has_text='oil').get_attribute('value'))
        self.assertEqual(self.page.locator('#technical-period').input_value(), '5h')
        oil = self.page.locator('.technical-record[data-record-id=oil_indicator]')
        self.assertEqual(oil.locator('tbody tr').filter(has_text='RSI(14)').locator('td').nth(1).inner_text(), '-9.25')
        self.assertIn('08:02', oil.locator('.technical-time').inner_text())
        self.assertEqual(set(self.page.locator('#technical-list .technical-record').evaluate_all('cards => cards.map(card => card.dataset.timeframe)')), {'5h'})
        self.page.locator('#technical-period').select_option('1h')
        self.assertNotIn('-9.25', self.page.locator('#technical-list').inner_text())
        self.assertEqual(self.page.locator('.technical-record[data-record-id=oil_indicator]').count(), 0)
        self.page.locator('#technical-asset').select_option(self.page.locator('#technical-asset option').filter(has_text='bitcoin').get_attribute('value'))
        self.assertEqual(self.page.locator('#technical-period').input_value(), '1D')
        self.assertEqual(self.page.locator('.technical-record[data-record-id=bitcoin_indicator]').count(), 0)
        self.assertNotIn('99', self.page.locator('#technical-list').inner_text())
        self.page.locator('#technical-period').select_option('1h')
        self.assertEqual(self.page.locator('.technical-record[data-record-id=bitcoin_indicator] tbody tr').filter(has_text='RSI(14)').locator('td').nth(1).inner_text(), '99')
        for width in (1440, 540, 390):
            self.page.set_viewport_size({"width": width, "height": 900})
            cards = self.page.locator(".focus-card")
            bounds = [cards.nth(i).bounding_box() for i in range(4)]
            self.assertAlmostEqual(bounds[0]["y"], bounds[1]["y"])
            if width > 700: self.assertAlmostEqual(bounds[0]["y"], bounds[3]["y"])
            else: self.assertGreater(bounds[2]["y"], bounds[0]["y"])
            self.assertEqual(self.page.evaluate("document.documentElement.scrollWidth"), width)
        self.assertFalse(self.errors)
