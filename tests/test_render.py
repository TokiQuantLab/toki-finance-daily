import struct
from html.parser import HTMLParser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from toki_finance_daily.render import _safe_url, render_html, render_png


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"style", "script"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"style", "script"}:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def visible_text(value):
    parser = VisibleText()
    parser.feed(value)
    return "".join(parser.parts)


class RenderTests(unittest.TestCase):
    def setUp(self):
        self.dataset = {
            "schema_version": 1,
            "date": "2026-10-11",
            "timezone": "Asia/Shanghai",
            "cutoff": "2026-10-11T08:00:00+08:00",
            "collected_at": "2026-10-11T07:59:00+08:00",
            "window_start": "2026-10-10T08:00:00+08:00",
            "mode": "demo",
            "sources": [
                {"id": "official", "name": "官方来源", "status": "ok", "count": 2,
                 "fetched_at": "2026-10-11T07:59:00+08:00"},
                {"id": "broken", "name": "失败来源", "status": "error", "count": 0,
                 "error": "网络不可达", "fetched_at": "2026-10-11T07:58:00+08:00"},
            ],
            "records": [
                {"id": "news-1", "kind": "news", "title": "来源标题", "source_id": "official",
                 "url": "https://example.org/report", "published_at": "2026-10-11T00:00:00Z", "data": {}},
                {"id": "macro-1", "kind": "macro", "title": "GDP", "source_id": "official",
                 "url": "https://example.org/data", "published_at": "2026-10-10T12:00:00+08:00",
                 "data": {"label": "官方指标", "value": 0, "unit": "%", "period": "2026-Q3"}},
            ],
        }
        self.brief = {
            "date": "2026-10-11", "headline": "利率与增长的新坐标", "dek": "把事实放回时间线。",
            "lead": {"category": "宏观", "title": "事实与判断", "fact": "事实", "analysis": "分析",
                     "watch": "继续观察", "evidence_ids": ["news-1"]},
            "stories": [], "calendar": [], "watchlist": [],
        }

    def test_untrusted_text_is_escaped_and_unsafe_links_are_not_rendered(self):
        payload = '<img src=x onerror="alert(1)">'
        self.brief["headline"] = payload
        self.brief["lead"]["fact"] = payload
        self.dataset["records"][0]["title"] = payload
        self.dataset["records"][0]["url"] = "javascript:alert(1)"
        result = render_html(self.brief, self.dataset)
        self.assertNotIn("<img", result)
        self.assertNotIn('href="javascript:', result)
        self.assertIn("&lt;img", result)

    def test_inline_source_links_keep_times_without_appendix_or_public_diagnostics(self):
        result = render_html(self.brief, self.dataset)
        self.assertNotIn('href="#source-', result)
        self.assertNotIn('class="sources-section"', result)
        self.assertNotIn("来源与延伸阅读", result)
        self.assertNotIn("https://example.org/report", visible_text(result))
        self.assertIn("官方来源 · 10/11 08:00", visible_text(result))
        self.assertIn('href="https://example.org/report"', result)
        self.assertNotIn("网络不可达", result)
        self.assertNotIn("失败来源", result)
        self.assertNotIn("采集覆盖", result)
        self.assertNotIn("未启用", result)
        self.assertNotIn("source-status", result)
        self.assertNotIn("AI 解读", result)
        self.assertNotIn("Asia/Shanghai", result)
        self.assertNotIn("UTC+08:00", result)
        self.assertIn("北京时间", result)
        self.assertEqual(self.dataset["sources"][1]["error"], "网络不可达")
        self.assertIn("10/11 08:00", result)

    def test_unselected_dataset_data_does_not_add_panels_or_numbers(self):
        self.brief["macro"] = [{"value": "99999", "label": "虚构行情"}]
        result = render_html(self.brief, self.dataset)
        self.assertNotIn("99999", result)
        self.assertNotIn("虚构行情", result)
        self.assertNotIn("官方指标", result)
        self.assertNotIn("2026-Q3", result)
        self.assertNotIn('class="macro-card"', result)

    def test_demo_remains_honest_without_concept_diagram_or_remote_resources(self):
        result = render_html(self.brief, self.dataset)
        self.assertIn("非当日真实资讯", result)
        self.assertNotIn("概念示意", result)
        self.assertNotIn("非行情图", result)
        self.assertNotIn("<svg", result)
        self.assertNotIn("orbit-art", result)
        self.assertIn('class="brand-symbol">TQ<span>↗</span>', result)
        self.assertNotIn("<script", result)
        self.assertNotIn("@import", result)
        self.assertNotIn('<link rel="stylesheet"', result)

    def test_date_only_provenance_preserves_precision_without_fake_midnight(self):
        record = self.dataset["records"][0]
        record["published_at"] = None
        record["data"] = {"published_date": "2026-10-10", "date_precision": "day", "background_only": True}
        result = render_html(self.brief, self.dataset)
        self.assertEqual(result.count("2026-10-10 · 仅日期 · 背景资料"), 1)
        self.assertNotIn("2026-10-10 00:00", result)
        self.assertNotIn("10/10 00:00", result)

    def test_undated_macro_uses_observation_time_and_calendar_uses_event_time(self):
        self.dataset["records"][1]["published_at"] = None
        self.dataset["records"][1]["observed_at"] = "2026-10-11T07:59:00+08:00"
        self.dataset["records"].append({
            "id": "calendar-1", "kind": "calendar", "title": "官方发布日历", "source_id": "official",
            "published_at": None, "url": "https://example.org/calendar",
            "data": {"event_at": "2026-10-15"},
        })
        self.brief["calendar"] = [{"title": "后续发布", "event_at": "2026-10-15", "evidence_ids": ["calendar-1"]}]
        self.brief["lead"]["evidence_ids"].append("macro-1")
        result = render_html(self.brief, self.dataset)
        self.assertNotIn('class="macro-time"', result)
        self.assertIn("统计期 2026-Q3 · 截至 10/11 07:59", result)
        self.assertIn("事件 2026-10-15 · 时间待公布", result)
        self.assertNotIn("发布 未标注发布时间", result)
        self.assertNotIn("采集", result)

    def test_us_date_only_calendar_uses_source_local_date_without_midnight_conversion(self):
        self.dataset["records"].append({
            "id": "fed-calendar", "kind": "calendar", "source_id": "official", "title": "FOMC会议",
            "url": "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm",
            "published_at": None,
            "data": {"event_at": "2026-10-28", "time_precision": "date",
                     "provider_timezone": "America/New_York", "region": "美国"},
        })
        self.brief["calendar"] = [{"title": "FOMC会议", "event_at": "2026-10-28",
                                  "region": "美国", "evidence_ids": ["fed-calendar"]}]
        result = render_html(self.brief, self.dataset)
        self.assertIn('<div class="event-time">10月28日<br><small>美国当地日期</small></div>', result)
        self.assertIn("事件 10月28日 · 美国当地日期", result)
        self.assertIn("仅有日期的美国日程按当地日期显示", result)
        self.assertNotIn("America/New_York", result)
        self.assertNotIn("10/28 00:00", result)
        self.assertNotIn("10/28 12:00", result)
        self.assertNotIn("10/29", result)

    def test_exact_us_time_and_other_date_calendars_keep_existing_beijing_behavior(self):
        record = {"id": "calendar-1", "kind": "calendar", "source_id": "official",
                  "title": "官方日程", "published_at": None, "data": {}}
        self.dataset["records"].append(record)
        for event_at, data, expected in (
            ("2026-10-28T14:00:00-04:00", {"time_precision": "time", "provider_timezone": "America/New_York"}, "10/29 02:00"),
            ("2026-10-28", {"time_precision": "date", "provider_timezone": "Asia/Shanghai"}, "2026-10-28 · 时间待公布"),
            ("2026-10-28", {}, "2026-10-28 · 时间待公布"),
        ):
            with self.subTest(event_at=event_at, data=data):
                record["data"] = {"event_at": event_at, **data}
                self.brief["calendar"] = [{"title": "日程", "event_at": event_at,
                    "timezone": "America/New_York", "evidence_ids": ["calendar-1"]}]
                result = render_html(self.brief, self.dataset)
                self.assertIn(f'<div class="event-time">{expected}</div>', result)
                self.assertNotIn("美国当地日期", result)

    def test_calendar_locality_requires_evidence_matching_the_event_date(self):
        self.dataset["records"].append({
            "id": "fed-calendar", "kind": "calendar", "source_id": "official", "title": "FOMC会议",
            "published_at": None, "data": {"event_at": "2026-10-28", "time_precision": "date",
                     "provider_timezone": "America/New_York"},
        })
        # Validation rejects this editorial mismatch upstream; the renderer also
        # must not borrow one event's locality for another date from the brief.
        self.brief["calendar"] = [{"title": "日期不一致", "event_at": "2026-10-29",
            "timezone": "America/New_York", "evidence_ids": ["fed-calendar"]}]
        result = render_html(self.brief, self.dataset)
        self.assertIn('<div class="event-time">2026-10-29 · 时间待公布</div>', result)
        self.assertNotIn("10月29日 · 美国当地日期", result)

    def test_missing_citation_fails_before_publishing_diagnostic_text(self):
        self.brief["lead"]["evidence_ids"] = ["unknown-record"]
        with self.assertRaisesRegex(ValueError, "不存在的证据"):
            render_html(self.brief, self.dataset)

    def test_dataset_macro_quote_sentiment_and_technical_never_auto_expand_the_news_edition(self):
        baseline = render_html(self.brief, self.dataset)
        for topic in ("market", "sentiment", "technical", "macro"):
            with self.subTest(topic=topic):
                self.dataset["records"].append({
                    "id": "extra-" + topic, "kind": "macro", "source_id": "official", "title": "UNSELECTED_" + topic,
                    "url": "https://example.org/extra", "observed_at": "2026-10-11T07:58:00+08:00",
                    "data": {"topic": topic, "label": "UNSELECTED_" + topic, "value": 987654,
                             "rating": "强力买入", "timeframe": "1D", "range_min": 0, "range_max": 100},
                })
                self.assertEqual(render_html(self.brief, self.dataset), baseline)
        for section in ("macro", "market", "sentiment", "technical", "sources"):
            self.assertNotIn(f'class="{section}-section"', baseline)

    def test_story_calendar_and_watchlist_all_keep_concise_provenance(self):
        self.dataset["records"].append({"id": "event", "kind": "calendar", "title": "发布日期",
            "source_id": "official", "url": "https://example.org/calendar", "published_at": None,
            "data": {"event_at": "2026-10-15T09:30:00+08:00"}})
        self.brief["stories"] = [{"category": "黄金 · XAUUSD", "title": "黄金事件", "fact": "新闻事实",
            "analysis": "资产传导", "watch": "验证条件", "evidence_ids": ["news-1", "news-1"]}]
        self.brief["calendar"] = [{"title": "发布日期", "event_at": "2026-10-15T09:30:00+08:00",
            "note": "关注数据", "evidence_ids": ["event"]}]
        self.brief["watchlist"] = [{"title": "后续观察", "detail": "需要追踪的变量", "evidence_ids": ["news-1"]}]
        result = render_html(self.brief, self.dataset)
        self.assertEqual(result.count('class="provenance"'), 4)
        self.assertEqual(result.count('class="source-credit"'), 4)
        self.assertIn("黄金 · XAUUSD", result)
        self.assertIn("事件 10/15 09:30", result)
        self.assertNotIn('class="citation"', result)
        self.assertNotIn("https://example.org/", visible_text(result))
        self.assertIn("事实 / FACT", result)
        self.assertIn("解读 / VIEW", result)

    def test_scale_is_validated_before_browser_or_file_access(self):
        for value in (0, 0.5, 5, float("nan"), float("inf"), "bad"):
            with self.subTest(scale=value), self.assertRaises(ValueError):
                render_png("missing.html", "missing.png", width=540, scale=value)

    def test_pixel_density_is_independent_of_mobile_layout_width(self):
        # Verify the browser receives a narrow layout and a separate device ratio;
        # report actual PNG dimensions rather than inferring them from CSS height.
        png = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1080, 4010)
        page = MagicMock()
        page.evaluate.side_effect = [None, {"width": 540, "height": 2005,
            "horizontal_overflow": [], "text_overflow": [],
            "document_horizontal_overflow": False, "fonts_ready": True}]
        page.screenshot.return_value = png
        browser = MagicMock()
        browser.new_page.return_value = page
        playwright = MagicMock()
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        try:
            import playwright.sync_api
        except ImportError:
            self.skipTest("Optional Playwright dependency is not installed")
        with tempfile.TemporaryDirectory() as temporary:
            input_path = Path(temporary) / "input.html"
            input_path.write_text("<!DOCTYPE html><main>测试</main>")
            with patch("playwright.sync_api.sync_playwright", return_value=manager):
                result = render_png(input_path, Path(temporary) / "output.png", width=540)
        self.assertEqual(browser.new_page.call_args.kwargs["viewport"]["width"], 540)
        self.assertEqual(browser.new_page.call_args.kwargs["device_scale_factor"], 2)
        self.assertEqual(result["pixel_width"], 1080)
        self.assertEqual(result["pixel_height"], 4010)
        self.assertEqual(result["width"], 540)
        self.assertTrue(result["ok"])

    def test_safe_url_rejects_credentials_control_characters_and_nonweb_schemes(self):
        for value in ("javascript:alert(1)", "file:///etc/passwd", "https://a:b@example.org", "https://example.org\n/a"):
            self.assertEqual(_safe_url(value), "")
        self.assertEqual(_safe_url("https://example.org/a?b=1&c=2"), "https://example.org/a?b=1&c=2")


if __name__ == "__main__":
    unittest.main()
