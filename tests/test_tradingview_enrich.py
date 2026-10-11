"""Free article confirmation and daily request budget for TradingView news."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from toki_finance_daily.collect import deduplicate
from toki_finance_daily.enrich import enrich_dataset, extract_article

URL = "https://cn.tradingview.com/news/panews:test-free-story:0/"
TEXT = "这是一段测试新闻正文，描述已公布的企业投资安排与尚待核实的交易条件。该内容仅用于离线解析测试，不代表真实财经消息。"


def page(free=True, part_free=True, *, url=URL, body=True, published="2026-10-10T14:55:01+00:00"):
    metadata = {"@type": "NewsArticle", "url": url, "isAccessibleForFree": free,
                "hasPart": {"@type": "WebPageElement", "isAccessibleForFree": part_free, "cssSelector": ".paywall"},
                "datePublished": published}
    content = '<div class="body-visible"><p>' + TEXT + '</p><a role="tab">688660</a><div class="symbolsContainer-tags">证券标签</div></div>' if body else ""
    return ('<html><script type="application/ld+json">' + json.dumps(metadata) + '</script>'
            '<main><nav>新闻首页</nav><div class="paywall">' + content + '</div>'
            '<p>推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯推荐资讯</p></main></html>').encode()


def dataset():
    now = datetime.fromisoformat("2026-10-11T06:00:00+08:00")
    record = {"id": "news_tradingview_fixture", "kind": "news", "source_id": "tradingview_news",
              "url": URL, "title": "测试新闻", "published_at": "2026-10-10T22:55:01+08:00",
              "observed_at": now.isoformat(), "summary": "",
              "data": {"aggregator": "TradingView", "paywall": False, "content_scope": "headline",
                       "original_source": "PANews", "original_source_url": "https://www.panewslab.com/zh/articles/test-story"}}
    return {"schema_version": 1, "date": now.date().isoformat(), "timezone": "Asia/Shanghai", "mode": "live",
            "cutoff": now.isoformat(), "window_start": (now - timedelta(hours=48)).isoformat(),
            "sources": [], "records": [record]}


class TradingViewEnrichTests(unittest.TestCase):
    def test_free_paywall_css_is_accepted_and_only_body_is_extracted(self):
        article = extract_article(page(), URL)
        self.assertEqual(article["text"], TEXT)
        self.assertEqual(article["published_at"], "2026-10-10T22:55:01+08:00")
        self.assertEqual(article["publication_field"], "NewsArticle.datePublished")

    def test_paid_unknown_mismatched_and_missing_bodies_are_rejected(self):
        cases = (page(free=False), page(part_free=False), page(free=None),
                 page(url=URL.replace("test-free-story", "other-story")), page(body=False))
        for body in cases:
            with self.subTest(body=body[:80]), self.assertRaises(ValueError):
                extract_article(body, URL)

    def test_article_daily_cache_preserves_original_observation_and_input(self):
        original = dataset()
        before = deepcopy(original)
        fetched_at = original["cutoff"]
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"TOKI_DAILY_CACHE_DIR": directory}), patch(
            "toki_finance_daily.enrich._fetch", return_value=(page(), {"transport": "http", "fetched_at": fetched_at})
        ) as fetch:
            first = enrich_dataset(original, ids=[original["records"][0]["id"]], out_dir=Path(directory)/"first")
            second = enrich_dataset(original, ids=[original["records"][0]["id"]], out_dir=Path(directory)/"second")
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1]["retries"], 0)
        self.assertEqual(first["records"][0]["summary"], TEXT)
        self.assertEqual(second["enrichment"][0]["fetched_at"], fetched_at)
        self.assertTrue(second["enrichment"][0]["cache_hit"])
        self.assertEqual(first["records"][0]["data"]["content_scope"], "article")
        self.assertEqual(original, before)

    def test_paid_or_unconfirmed_list_items_do_not_request_articles(self):
        for flags in ({"paywall": True}, {"paywall": None}, {"permission": "preview"}, {"permission": "headline"}, {"permission": None}):
            data = dataset()
            data["records"][0]["data"].update(flags)
            with self.subTest(flags=flags), patch("toki_finance_daily.enrich._fetch") as fetch:
                result = enrich_dataset(data, ids=[data["records"][0]["id"]])
                fetch.assert_not_called()
                self.assertEqual(result["enrichment"][0]["status"], "error")
                self.assertEqual(result["records"][0]["summary"], "")

    def test_missing_timezone_publication_or_cutoff_mismatch_never_replaces_title_evidence(self):
        for published in (None, "2026-10-10", "2026-10-10+00:00", "2026-10-10T22:55:01", "invalid"):
            with self.subTest(published=published), self.assertRaises(ValueError):
                extract_article(page(published=published), URL)
        cases = ("2026-10-11T06:00:01+08:00", "2026-10-09T05:59:59+08:00", "2026-10-10T22:55:02+08:00")
        for published in cases:
            original = dataset()
            before = deepcopy(original)
            with self.subTest(published=published), TemporaryDirectory() as directory, patch.dict(
                os.environ, {"TOKI_DAILY_CACHE_DIR": directory}
            ), patch("toki_finance_daily.enrich._fetch", return_value=(page(published=published), {})):
                result = enrich_dataset(original, ids=[original["records"][0]["id"]], out_dir=directory)
                status = result["enrichment"][0]
                record = result["records"][0]
                self.assertEqual(status["status"], "error")
                self.assertIn("publication", status["error"])
                self.assertEqual(record["summary"], "")
                self.assertEqual(record["published_at"], before["records"][0]["published_at"])
                self.assertEqual(record["data"]["content_scope"], "headline")
                self.assertNotIn("article_text_sha256", record["data"])
                self.assertFalse((Path(directory)/"raw"/(record["id"] + "-article.txt")).exists())
            self.assertEqual(original, before)

    def test_reprint_merges_by_original_article_url_without_losing_direct_text(self):
        reprint = dataset()["records"][0]
        direct = deepcopy(reprint)
        direct.update(id="news_direct", source_id="panews", title="同一文章的不同标题",
                      url=reprint["data"]["original_source_url"], summary="直接来源摘要")
        direct["data"] = {"original_source": "PANews"}
        combined = deduplicate([direct, reprint])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]["summary"], "直接来源摘要")
        self.assertEqual({item["source_id"] for item in combined[0]["data"]["provenance"]}, {"panews", "tradingview_news"})
        other = deepcopy(reprint)
        other.update(id="news_other", title="不同事件", url=URL.replace("test-free-story", "other-story"))
        other["data"]["original_source_url"] = "https://www.panewslab.com/"
        self.assertEqual(len(deduplicate([dataset()["records"][0], other])), 2)


if __name__ == "__main__":
    unittest.main()
