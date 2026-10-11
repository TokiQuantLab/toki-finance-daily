"""Public source fixtures: SSR is data, subscription content is not collected."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

from toki_finance_daily.collect import (collect, deduplicate, filter_records, parse_cboe_vix, parse_fear_greed,
                                       parse_feed, parse_timestamp, parse_wallstreetcn, wallstreetcn_page_data)
from toki_finance_daily.enrich import extract_article

OBSERVED = "2026-10-11T08:00:00+08:00"
SOURCE = {"id": "wallstreetcn_live", "name": "华尔街见闻", "url": "https://wallstreetcn.com/live",
          "adapter": "wallstreetcn_live", "timezone": "Asia/Shanghai", "region": "全球",
          "source_type": "professional_media", "topic_scope": "general_finance",
          "editorial_note": "媒体线索需回查官方事实"}
ARTICLE_TEXT = "经济政策和利率变化影响企业融资与居民消费。" * 6


def ssr(data):
    payload = {"state": {"default": {"children": {"default": {"data": data}}}}}
    return '<script>window.__SSR__ = ' + json.dumps(payload, ensure_ascii=False) + '; window.untrustedScript();</script>'


def live_row(**overrides):
    return {"id": 123, "title": "", "content_text": "央行：最新货币政策发布。", "content": "<p>不选这个重复字段</p>",
            "uri": "https://wallstreetcn.com/livenews/123", "display_time": 1791641708,
            "author": {"display_name": "财经编辑"}, "source_name": "央行网站", "source_uri": "https://bank.example/release",
            "is_priced": False, **overrides}


class PublicSourceTests(TestCase):
    def test_ssr_data_not_javascript_execution_and_challenges_fail(self):
        self.assertEqual(wallstreetcn_page_data(ssr({"lives": []})), {"lives": []})
        for body in ('<html>Verify browser challenge</html>', '<script>__SSR__ = notJson</script>',
                     ssr([]), '<script>__SSR__ = {"unexpected": []}</script>'):
            with self.subTest(body=body), self.assertRaises(ValueError):
                wallstreetcn_page_data(body)

    def test_live_blank_title_fallback_date_origin_and_stable_id(self):
        body = ssr({"lives": [live_row()]})
        record = parse_wallstreetcn(body, SOURCE, OBSERVED)[0]
        self.assertEqual(record["title"], "央行：最新货币政策发布。")
        self.assertEqual(record["summary"], "央行：最新货币政策发布。")
        self.assertEqual(record["published_at"], "2026-10-10T22:15:08+08:00")
        self.assertEqual(record["data"]["original_source"], "央行网站")
        self.assertEqual(record["data"]["author"], "财经编辑")
        self.assertEqual(record["data"]["topic_scope"], "general_finance")
        self.assertEqual(record["id"], parse_wallstreetcn(body, SOURCE, "2026-10-12T08:00:00+08:00")[0]["id"])

    def test_subscription_and_nonarticle_routes_are_excluded(self):
        rows = [live_row(), live_row(id=2, is_priced=True), live_row(id=3, is_paid=1),
                live_row(id=4, is_in_vip_privilege="1"), live_row(id=5, vip_type="gold"),
                live_row(id=6, uri="https://evil.example/articles/6"),
                live_row(id=7, uri="https://wallstreetcn.com/calendar/7")]
        self.assertEqual(len(parse_wallstreetcn(ssr({"lives": rows}), SOURCE, OBSERVED)), 1)
        news_source = {**SOURCE, "adapter": "wallstreetcn_news"}
        items = [{"resource_type": "article", "resource": live_row(uri="https://wallstreetcn.com/articles/123", title="债券市场")},
                 {"resource_type": "article", "resource": live_row(is_priced=True)},
                 {"resource_type": "advertisement", "resource": live_row()}]
        self.assertEqual([x["title"] for x in parse_wallstreetcn(ssr({"items": items}), news_source, OBSERVED)], ["债券市场"])
        for data in ({"items": None}, {"lives": None}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_wallstreetcn(ssr(data), SOURCE if "lives" in data else news_source, OBSERVED)

    def test_rss_source_author_and_crypto_scope_survive(self):
        source = {**SOURCE, "id": "panews", "adapter": "rss", "url": "https://www.panewslab.com/rss.xml",
                  "name": "PANews", "topic_scope": "crypto_web3"}
        body = '''<rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item><title>稳定币监管报道</title>
        <link>https://www.panewslab.com/zh/articles/123</link><pubDate>Sat, 10 Oct 2026 15:31:00 GMT</pubDate>
        <description>&lt;p&gt;第三方机构观点&lt;/p&gt;</description><source>原始媒体</source><dc:creator>专栏作者</dc:creator>
        </item></channel></rss>'''
        record = parse_feed(body, source, OBSERVED)[0]
        self.assertEqual(record["published_at"], "2026-10-10T23:31:00+08:00")
        self.assertEqual(record["data"]["original_source"], "原始媒体")
        self.assertEqual(record["data"]["author"], "专栏作者")
        self.assertEqual(record["data"]["topic_scope"], "crypto_web3")

    def test_dedupe_keeps_reprint_origins_and_source_status_context(self):
        first = parse_wallstreetcn(ssr({"lives": [live_row()]}), SOURCE, OBSERVED)[0]
        second = deepcopy(first)
        second.update(source_id="other", url="https://other.example/reprint")
        second["data"].update(original_source="央行网站", author="转载编辑")
        record = deduplicate([first, second])[0]
        self.assertEqual(len(record["data"]["provenance"]), 2)
        self.assertEqual({x["original_source"] for x in record["data"]["provenance"]}, {"央行网站"})
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.json"
            config.write_text(json.dumps({"sources": [SOURCE]}), encoding="utf-8")
            with patch("toki_finance_daily.collect._fetch", return_value=(ssr({"lives": [live_row()]}).encode(), {})):
                dataset = collect(config, cutoff="2025-10-11T08:00:00+08:00")
            self.assertEqual(dataset["sources"][0]["source_type"], "professional_media")
            self.assertEqual(dataset["sources"][0]["editorial_note"], SOURCE["editorial_note"])

    def test_public_article_body_excludes_outer_header_and_related_content(self):
        page = '<main><header>外层标题和作者导航</header><section class="_articleBody_v7w04_1 article"><p>' + ARTICLE_TEXT + '</p></section><div class="related">推荐另一篇文章</div></main>'
        extracted = extract_article(page + ssr({"article": {"is_priced": False}}), "https://wallstreetcn.com/articles/123")
        self.assertEqual(extracted["text"], ARTICLE_TEXT)
        self.assertEqual(extracted["container"], "_articleBody_v7w04_1 article")
        self.assertIsNone(extracted["published_at"])
        with self.assertRaisesRegex(ValueError, "Subscription"):
            extract_article(page + ssr({"article": {"is_priced": True}}), "https://wallstreetcn.com/articles/123")
        panews = '<nav>热门导航</nav><article class="article-content prose"><p>' + ARTICLE_TEXT + '</p></article><aside>热门帖子</aside>'
        self.assertEqual(extract_article(panews, "https://www.panewslab.com/zh/articles/123")["text"], ARTICLE_TEXT)


class SentimentSourceTests(TestCase):
    SOURCE = {"id": "fng", "name": "Alternative.me", "adapter": "fear_greed",
              "url": "https://api.alternative.me/fng/?limit=8", "timezone": "UTC",
              "source_type": "market_indicator", "topic_scope": "crypto_sentiment"}

    @staticmethod
    def payload():
        day = datetime(2026, 10, 10, tzinfo=timezone.utc)
        return {"metadata": {"error": None}, "data": [
            {"value": str(value), "value_classification": "Greed",
             "timestamp": str(int((day - timedelta(days=index)).timestamp()))}
            for index, value in enumerate([64, 59, 64, 71, 73, 70, 65, 67])]}

    def test_latest_history_computed_deltas_and_zero_are_preserved(self):
        rows = parse_fear_greed(json.dumps(self.payload()), self.SOURCE, OBSERVED)
        cutoff = parse_timestamp(OBSERVED)
        record = filter_records(rows, cutoff - timedelta(hours=48), cutoff)[0]
        self.assertEqual(record["data"]["value"], 64)
        self.assertEqual(record["data"]["classification"], "贪婪")
        self.assertEqual(record["data"]["provider_at"], "2026-10-10T00:00:00+00:00")
        self.assertEqual(record["data"]["period"], "2026-10-10")
        self.assertEqual(record["data"]["change_1d"], 5)
        self.assertEqual(record["data"]["change_7d"], -3)
        self.assertEqual([point["value"] for point in record["data"]["history"]], [67, 65, 70, 73, 71, 64, 59, 64])
        self.assertEqual(record["data"]["topic"], "sentiment")
        payload = self.payload()
        payload["data"][0].update(value="0", value_classification="ProviderNewCategory")
        zero = parse_fear_greed(json.dumps(payload), self.SOURCE, OBSERVED)[-1]
        self.assertEqual(zero["data"]["value"], 0)
        self.assertEqual(zero["data"]["classification"], "ProviderNewCategory")
        self.assertEqual(zero["data"]["raw_classification"], "ProviderNewCategory")

    def test_precise_future_filter_and_history_do_not_leak_newer_points(self):
        records = parse_fear_greed(json.dumps(self.payload()), self.SOURCE, OBSERVED)
        cutoff = parse_timestamp("2026-10-10T07:59:59+08:00")
        kept = filter_records(records, cutoff - timedelta(hours=1), cutoff)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["data"]["period"], "2026-10-09")
        self.assertEqual(kept[0]["data"]["value"], 59)
        self.assertEqual(len(kept[0]["data"]["history"]), 7)
        self.assertTrue(all(parse_timestamp(point["provider_at"]) <= cutoff for point in kept[0]["data"]["history"]))

    def test_missing_day_deltas_are_null_and_invalid_api_fails(self):
        payload = self.payload()
        del payload["data"][1]
        latest = parse_fear_greed(json.dumps(payload), self.SOURCE, OBSERVED)[-1]
        self.assertIsNone(latest["data"]["change_1d"])
        self.assertEqual(latest["data"]["change_7d"], -3)
        for field, values in {"value": [None, True, "NaN", "-1", "101", "64.5", float("inf")],
                              "timestamp": [None, True, "2026-10-10", "bad", 1e100],
                              "value_classification": [None, ""]}.items():
            for value in values:
                invalid = self.payload()
                invalid["data"][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    parse_fear_greed(json.dumps(invalid), self.SOURCE, OBSERVED)
        for payload in ({"metadata": {"error": "unavailable"}, "data": []},
                        {"metadata": {"error": None}, "data": []},
                        {"metadata": {}, "data": self.payload()["data"]},
                        {"data": self.payload()["data"]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_fear_greed(json.dumps(payload), self.SOURCE, OBSERVED)


class VixSourceTests(TestCase):
    SOURCE = {"id": "vix", "name": "Cboe", "adapter": "cboe_vix",
              "url": "https://cdn-api.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv",
              "timezone": "America/New_York", "source_type": "market_indicator"}
    CSV = "DATE,OPEN,HIGH,LOW,CLOSE\n10/08/2026,15.68,16.46,15.15,15.41\n10/09/2026,15.34,15.34,14.77,14.84\n10/12/2026,15,16,14,15.20\n"

    def test_only_close_value_previous_trading_day_history_and_future_cutoff(self):
        records = parse_cboe_vix(self.CSV, self.SOURCE, OBSERVED)
        cutoff = parse_timestamp(OBSERVED)
        record = filter_records(records, cutoff - timedelta(hours=1), cutoff)[0]
        self.assertEqual(record["data"]["value"], 14.84)
        self.assertEqual(record["data"]["period"], "2026-10-09")
        self.assertEqual(record["data"]["change_previous"], -0.57)
        self.assertEqual(record["data"]["history"], [{"period": "2026-10-08", "value": 15.41}, {"period": "2026-10-09", "value": 14.84}])
        self.assertNotIn("classification", record["data"])
        self.assertNotIn("range_max", record["data"])
        self.assertNotIn("provider_at", record["data"])

    def test_current_new_york_trading_day_never_implies_close_already_published(self):
        records = parse_cboe_vix(self.CSV, self.SOURCE, OBSERVED)
        # 10/10 Shanghai 08:00 is still 10/09 20:00 in New York.
        cutoff = parse_timestamp("2026-10-10T08:00:00+08:00")
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual(kept[0]["data"]["period"], "2026-10-08")
        self.assertEqual(len(kept[0]["data"]["history"]), 1)
        self.assertIsNone(kept[0]["data"]["change_previous"])

    def test_previous_means_previous_observed_trading_day_not_calendar_day(self):
        csv = "DATE,CLOSE\n10/09/2026,14.84\n10/12/2026,15.20\n"
        latest = parse_cboe_vix(csv, self.SOURCE, OBSERVED)[-1]
        self.assertEqual(latest["data"]["change_previous"], 0.36)
        history_csv = "DATE,CLOSE\n" + "\n".join(f"10/{day:02d}/2026,{10 + day}" for day in range(1, 12))
        self.assertEqual(len(parse_cboe_vix(history_csv, self.SOURCE, OBSERVED)[-1]["data"]["history"]), 8)

    def test_malformed_and_nonpositive_closes_fail_and_empty_cutoff_is_explicit(self):
        for csv in ("DATE,OPEN\n10/09/2026,14\n", "DATE,CLOSE\n", "DATE,CLOSE\nbad,14\n",
                    "DATE,CLOSE\n10/09/2026,0\n", "DATE,CLOSE\n10/09/2026,NaN\n",
                    "DATE,CLOSE\n10/09/2026,inf\n", "DATE,CLOSE\n10/09/2026,14\n10/09/2026,15\n"):
            with self.subTest(csv=csv), self.assertRaises(ValueError):
                parse_cboe_vix(csv, self.SOURCE, OBSERVED)
        with TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.json"
            config.write_text(json.dumps({"sources": [self.SOURCE]}), encoding="utf-8")
            with patch("toki_finance_daily.collect._fetch", return_value=(self.CSV.encode(), {})):
                dataset = collect(config, cutoff="2025-10-11T08:00:00+08:00")
            self.assertEqual(dataset["sources"][0]["status"], "error")
            self.assertIn("No indicator observation", dataset["sources"][0]["error"])
            self.assertEqual(dataset["records"], [])
