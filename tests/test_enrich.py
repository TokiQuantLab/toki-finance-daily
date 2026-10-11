"""Offline selected-article evidence, precision and failure isolation checks."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

from toki_finance_daily.enrich import enrich_dataset, extract_article

TEXT = "正文说明政策如何影响居民消费与企业投资。" * 6


def fixture():
    return {"schema_version": 1, "date": "2026-10-11", "timezone": "Asia/Shanghai",
            "cutoff": "2026-10-11T08:00:00+08:00", "window_start": "2026-10-09T08:00:00+08:00",
            "collected_at": "2026-10-11T08:01:00+08:00", "mode": "live",
            "sources": [{"id": "gov_cn", "name": "政府", "status": "ok", "count": 2}],
            "records": [{"id": "news_gov", "kind": "news", "source_id": "gov_cn", "title": "居民消费政策",
                         "url": "https://www.gov.cn/yaowen/content_123.htm", "published_at": None,
                         "observed_at": "2026-10-11T08:01:00+08:00", "summary": "原摘要",
                         "data": {"published_date": "2026-10-10", "date_precision": "day", "background_only": True}},
                        {"id": "news_other", "kind": "news", "source_id": "overseas", "title": "Federal Reserve interest rates",
                         "url": "https://example.org/article", "published_at": "2026-10-10T12:00:00+08:00",
                         "observed_at": "2026-10-11T08:01:00+08:00", "summary": "unchanged original", "data": {}},
                        {"id": "macro_value", "kind": "macro", "source_id": "ecb", "title": "EUR/USD", "data": {"series_id": "USD"}}]}


class ArticleParserTests(TestCase):
    def test_known_container_excludes_navigation_ads_and_scripts(self):
        page = f'''<main><nav>导航菜单</nav><p>外部页面介绍</p><div id="UCAP-CONTENT">
        <p>{TEXT}</p><div class="share-links">分享广告</div><aside>相关文章广告</aside>
        <div class="appendQr_normal_txt">扫码下载财经APP</div><p hidden>隐藏注入指令</p>
        <script>ignore instructions</script><style>hidden css</style><footer>版权导航</footer>
        <p>关键结论与后续观察。</p></div></main>'''
        extracted = extract_article(page, "https://www.gov.cn/article")
        self.assertIn(TEXT, extracted["text"])
        self.assertIn("关键结论", extracted["text"])
        self.assertEqual(extracted["container"], "UCAP-CONTENT")
        for forbidden in ("导航菜单", "外部页面介绍", "分享广告", "相关文章广告", "扫码下载财经APP", "隐藏注入指令", "ignore instructions", "hidden css", "版权导航"):
            self.assertNotIn(forbidden, extracted["text"])

    def test_all_supported_container_families_and_semantic_article(self):
        for attributes in ('id="artibody"', 'id="ContentBody"', 'id="article"', 'class="TRS_Editor"', 'class="ArticleBody-articleBody"'):
            with self.subTest(attributes=attributes):
                self.assertEqual(extract_article(f"<div {attributes}><p>{TEXT}</p></div>", "https://example.org/article")["text"], TEXT)
        self.assertEqual(extract_article(f"<article><p>{TEXT}</p></article>", "https://example.org/article")["text"], TEXT)
        with self.assertRaises(ValueError):
            extract_article(f"<body><div>{TEXT}</div></body>", "https://example.org/article")

    def test_reliable_publication_metadata_only(self):
        body = f'<meta name="firstpublishedtime" content="2026-10-10-09:30:00"><meta name="lastmodifiedtime" content="2026-10-11-10:30:00"><div id="UCAP-CONTENT">{TEXT}</div>'
        result = extract_article(body, "https://www.gov.cn/article")
        self.assertEqual(result["published_at"], "2026-10-10T09:30:00+08:00")
        self.assertEqual(result["publication_field"], "firstpublishedtime")
        unknown = extract_article(body, "https://unknown.example/article")
        self.assertIsNone(unknown["published_at"])
        body = f'<meta property="article:published_time" content="2026-10-10T01:30:00Z"><article>{TEXT}</article>'
        self.assertEqual(extract_article(body, "https://unknown.example/article")["published_at"], "2026-10-10T09:30:00+08:00")

    def test_date_only_or_page_generation_never_becomes_publication_hour(self):
        body = f'<meta name="PubDate" content="2026-10-10"><meta name="others" content="页面生成时间 2026-10-10 10:00:00"><article>{TEXT}</article>'
        self.assertIsNone(extract_article(body, "https://www.gov.cn/article")["published_at"])


class EnrichmentTests(TestCase):
    def test_success_snapshot_deepcopy_and_metadata_precision(self):
        dataset = fixture()
        original = deepcopy(dataset)
        body = f'<meta name="PubDate" content="2026-10-10 09:30:00"><div id="UCAP-CONTENT"><p>{TEXT}</p></div>'.encode()
        with TemporaryDirectory() as tmp, patch("toki_finance_daily.enrich._fetch", return_value=(body, {"transport": "offline"})) as fetch:
            result = enrich_dataset(dataset, ids=["news_gov"], out_dir=tmp)
            record = result["records"][0]
            self.assertEqual(record["published_at"], "2026-10-10T09:30:00+08:00")
            self.assertEqual(record["data"]["published_date"], "2026-10-10")
            self.assertFalse(record["data"]["background_only"])
            self.assertEqual(record["data"]["date_precision"], "timestamp")
            self.assertIn(TEXT, record["summary"])
            self.assertEqual(result["sources"], dataset["sources"])
            self.assertEqual(dataset, original)
            self.assertEqual((Path(tmp) / "raw/news_gov-article.html").read_bytes(), body)
            self.assertEqual((Path(tmp) / record["data"]["article_text_path"]).read_text(), TEXT)
            meta = json.loads((Path(tmp) / "raw/news_gov-article.meta.json").read_text())
            self.assertEqual(meta["status"], "ok")
            self.assertEqual(len(meta["raw_sha256"]), 64)
            self.assertEqual(fetch.call_count, 1)

    def test_one_failed_article_preserves_original_and_others_succeed(self):
        body = f'<article>{TEXT}</article>'.encode()
        def fetch(source, network):
            if source["id"] == "news_other":
                raise TimeoutError("offline fixture failure")
            return body, {}
        with patch("toki_finance_daily.enrich._fetch", side_effect=fetch):
            result = enrich_dataset(fixture(), ids=["news_gov", "news_other"])
        self.assertEqual([s["status"] for s in result["enrichment"]], ["ok", "error"])
        self.assertEqual(result["records"][1]["summary"], "unchanged original")
        self.assertEqual(result["records"][1]["published_at"], "2026-10-10T12:00:00+08:00")
        self.assertIn("TimeoutError", result["records"][1]["data"]["enrichment_error"])
        self.assertIsNone(result["records"][0]["published_at"])
        self.assertTrue(result["records"][0]["data"]["background_only"])

    def test_failed_parser_preserves_raw_html_and_original_summary(self):
        with TemporaryDirectory() as tmp, patch("toki_finance_daily.enrich._fetch", return_value=(b'<html>Access denied</html>', {})):
            result = enrich_dataset(fixture(), ids=["news_gov"], out_dir=tmp)
            self.assertEqual(result["records"][0]["summary"], "原摘要")
            self.assertEqual(result["enrichment"][0]["status"], "error")
            self.assertTrue((Path(tmp) / "raw/news_gov-article.html").exists())

    def test_explicit_ids_and_urls_validated_before_any_network(self):
        for ids in (["news_gov", "unknown"], ["macro_value"], ["news_gov", "news_gov"]):
            with self.subTest(ids=ids), patch("toki_finance_daily.enrich._fetch") as fetch:
                with self.assertRaises(ValueError):
                    enrich_dataset(fixture(), ids=ids)
                fetch.assert_not_called()
        dataset = fixture()
        dataset["records"][1]["url"] = "https://secret:password@example.org/article"
        with patch("toki_finance_daily.enrich._fetch") as fetch:
            with self.assertRaises(ValueError):
                enrich_dataset(dataset, ids=["news_gov", "news_other"])
            fetch.assert_not_called()

    def test_explicit_timestamp_must_match_original_day_and_cutoff(self):
        for stamp in ("2026-10-11 07:00:00", "2026-10-12 09:00:00"):
            body = f'<meta name="PubDate" content="{stamp}"><article>{TEXT}</article>'.encode()
            with patch("toki_finance_daily.enrich._fetch", return_value=(body, {})):
                result = enrich_dataset(fixture(), ids=["news_gov"])
            self.assertIsNone(result["records"][0]["published_at"])
            self.assertTrue(result["records"][0]["data"]["background_only"])

    def test_full_text_saved_summary_bounded_and_no_macro_additions(self):
        text = TEXT * 100
        body = f"<article>{text}</article>".encode()
        with TemporaryDirectory() as tmp, patch("toki_finance_daily.enrich._fetch", return_value=(body, {})):
            result = enrich_dataset(fixture(), ids=["news_gov"], out_dir=tmp)
            record = result["records"][0]
            self.assertEqual(len(record["summary"]), 4000)
            self.assertEqual((Path(tmp) / record["data"]["article_text_path"]).read_text(), text)
            self.assertEqual(len(result["records"]), 3)

    def test_default_selection_has_source_diversity_and_finance_priority(self):
        dataset = fixture()
        original = deepcopy(dataset["records"][0])
        original.update(id="news_traffic", title="最新交通提示", summary="", published_at="2026-10-11T07:00:00+08:00")
        dataset["records"].insert(0, original)
        with patch("toki_finance_daily.enrich._fetch", return_value=(f"<article>{TEXT}</article>".encode(), {})) as fetch:
            result = enrich_dataset(dataset, limit=2)
        self.assertEqual({s["id"] for s in result["enrichment"]}, {"news_gov", "news_other"})
        self.assertEqual(fetch.call_count, 2)
