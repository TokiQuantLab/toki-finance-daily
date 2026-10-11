"""Observed TradingView widget schema accepts explicit free headlines only."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from toki_finance_daily.collect import collect, deduplicate, filter_records, parse_timestamp
from toki_finance_daily.tradingview_news import parse_tradingview_news

SOURCE = {"id": "tradingview_news", "name": "TradingView · 免费新闻标题", "adapter": "tradingview_news",
          "url": "https://cn.tradingview.com/news/", "region": "全球", "max_items": 100,
          "source_type": "professional_media", "topic_scope": "general_finance"}
OBSERVED = "2026-10-10T21:43:02.080618+00:00"
# Fields and nesting match the captured anonymous /news/ HTTP response.
FREE = {"id": "panews:94056e8a8e3f9:0", "title": "金融时报：英伟达洽谈收购估值250亿美元的Reflection AI",
        "storyPath": "/news/panews:94056e8a8e3f9:0/", "published": 1791661860, "urgency": 2, "paywall": False,
        "link": "https://www.panewslab.com/zh/articles/01a1275f-43d7-74ae-9ded-6f66c73103ca",
        "relatedSymbols": [{"symbol": "NASDAQ:NVDA", "logo": {"style": "single", "logoid": "nvidia"}}],
        "provider": {"id": "panews", "name": "PANews", "logo_id": "panews", "url": "https://www.panewslab.com/"}}


def widget(rows):
    return {"data": {"news": {"data": {"items": rows, "streaming": {}}, "params": {}}}}


def page(rows, *, corporate=None, providers=None, root="dynamicRoot", extra=None):
    widgets = {"news_markets": widget(rows), "news_corp_activity": widget(corporate or []),
               "top_providers": widget(providers or [])}
    if extra:
        widgets.update(extra)
    text = json.dumps({root: {"widgets": widgets}}, ensure_ascii=False).replace("<", "\\u003c")
    return ('<script type="application/prs.init-data+json">' + text + '</script>').encode()


class TradingViewNewsTests(TestCase):
    def test_headline_only_preserves_actual_provider_links_time_and_origin_fields(self):
        record = parse_tradingview_news(page([FREE]), SOURCE, OBSERVED)[0]
        self.assertEqual(record["kind"], "news")
        self.assertEqual(record["title"], FREE["title"])
        self.assertEqual(record["summary"], "")
        self.assertEqual(record["published_at"], "2026-10-11T03:51:00+08:00")
        self.assertEqual(record["observed_at"], OBSERVED)
        self.assertEqual(record["url"], "https://cn.tradingview.com" + FREE["storyPath"])
        data = record["data"]
        self.assertEqual(data["source_record_id"], FREE["id"])
        self.assertEqual(data["original_source"], "PANews")
        self.assertEqual(data["original_source_url"], FREE["link"])
        self.assertEqual(data["provider"], {key: FREE["provider"][key] for key in ("id", "name", "url")})
        self.assertEqual(data["relatedSymbols"], FREE["relatedSymbols"])
        self.assertEqual(data["published"], FREE["published"])
        self.assertEqual(data["urgency"], 2)
        self.assertEqual(data["content_scope"], "headline")
        self.assertEqual(data["aggregator"], "TradingView")
        self.assertIs(data["paywall"], False)
        self.assertTrue(data["public_free"])
        self.assertEqual(data["source_type"], "professional_media")
        self.assertNotIn("is_flash", data)
        self.assertEqual(record["id"], parse_tradingview_news(page([FREE]), SOURCE, "2026-10-11T08:00:00+08:00")[0]["id"])
        flash = deepcopy(FREE)
        flash.update(is_flash=True)
        self.assertIs(parse_tradingview_news(page([flash]), SOURCE, OBSERVED)[0]["data"]["is_flash"], True)

    def test_paywall_must_be_explicit_false_and_permission_key_must_be_absent(self):
        rows = []
        for permission in ("preview", "headline", "subscriber", "unknown", "", None, False):
            rows.append({**FREE, "permission": permission})
        for paywall in (True, None, 0, "false", "0"):
            rows.append({**FREE, "paywall": paywall})
        missing = deepcopy(FREE)
        del missing["paywall"]
        rows.append(missing)
        self.assertEqual(parse_tradingview_news(page(rows), SOURCE, OBSERVED), [])
        self.assertEqual(len(parse_tradingview_news(page(rows + [FREE]), SOURCE, OBSERVED)), 1)

    def test_duplicate_columns_use_source_id_and_unrelated_recommendations_are_ignored(self):
        second = {**FREE, "id": "panews:second:0", "storyPath": "/news/panews:second:0/", "title": "另一则真实标题"}
        ignored = {**FREE, "id": "panews:advert:0", "storyPath": "/news/panews:advert:0/", "title": "推荐区标题"}
        body = page([FREE], corporate=[FREE, second], providers=[FREE], root="differentDynamicKey",
                    extra={"recommendations": widget([ignored])})
        records = parse_tradingview_news(body, SOURCE, OBSERVED)
        self.assertEqual([r["data"]["source_record_id"] for r in records], [FREE["id"], second["id"]])
        self.assertEqual(len(parse_tradingview_news(body, {**SOURCE, "max_items": 1}, OBSERVED)), 1)
        copied = deepcopy(records[0])
        copied.update(source_id="another_aggregator", url="https://example.org/the-same-event")
        combined = deduplicate([records[0], copied])
        self.assertEqual(len(combined), 1)
        self.assertEqual({p["source_id"] for p in combined[0]["data"]["provenance"]}, {"tradingview_news", "another_aggregator"})
        self.assertEqual(combined[0]["data"]["provenance"][0]["original_source"], "PANews")

    def test_empty_lists_are_valid_but_missing_or_changed_structure_is_an_error(self):
        self.assertEqual(parse_tradingview_news(page([]), SOURCE, OBSERVED), [])
        bad_widget = {"root": {"widgets": {"news_markets": {"data": {"news": {"data": {"items": {}}}}}}}}
        bodies = [b"<html>Access denied</html>", b'<script type="application/prs.init-data+json">{}</script>',
                  b'<script type="application/prs.init-data+json">[]</script>',
                  ('<script type="application/prs.init-data+json">' + json.dumps(bad_widget) + '</script>').encode(),
                  page([None]), page([1])]
        for body in bodies:
            with self.subTest(body=body[:80]), self.assertRaises(ValueError):
                parse_tradingview_news(body, SOURCE, OBSERVED)

    def test_bad_times_or_links_do_not_turn_into_undated_news(self):
        bad = [{**FREE, "published": value} for value in (None, True, "1791661860", "2026-10-11", 1e100, 1791661860000)]
        bad += [{**FREE, "storyPath": value} for value in ("https://evil.example/news/a/", "//evil.example/news/a/",
                "/symbols/NVDA/", "/news/../", "/news/%2e%2e/", "/news/a%2Fb/", "/news/a%00/", "/news/a/?secret=1")]
        bad += [{**FREE, "link": value} for value in ("javascript:alert(1)", "file:///private/data", "https://u:p@example.org/a",
                "https://example.org:bad/a", "https://example.org with spaces/a", "https://example.org/a\n")]
        self.assertEqual(parse_tradingview_news(page(bad), SOURCE, OBSERVED), [])
        no_link = deepcopy(FREE)
        del no_link["link"]
        record = parse_tradingview_news(page([no_link, FREE]), SOURCE, OBSERVED)[0]
        self.assertIsNotNone(record["published_at"])
        self.assertNotIn("original_source_url", record["data"])
        self.assertEqual(len(parse_tradingview_news(page([no_link, FREE]), SOURCE, OBSERVED)), 1)

    def test_publication_window_uses_seconds_inclusive_edges_and_rejects_future(self):
        cutoff = parse_timestamp("2026-10-11T03:51:00+08:00")
        start = cutoff - timedelta(hours=48)
        rows = [{**FREE, "id": "panews:" + label, "storyPath": "/news/panews:" + label + "/", "title": label,
                 "published": int(stamp.timestamp())} for label, stamp in (
                     ("before", start - timedelta(seconds=1)), ("start", start), ("cutoff", cutoff),
                     ("future", cutoff + timedelta(seconds=1)))]
        records = parse_tradingview_news(page(rows), SOURCE, OBSERVED)
        self.assertEqual([r["title"] for r in filter_records(records, start, cutoff)], ["start", "cutoff"])

    def test_json_and_scripts_are_only_parsed_as_data(self):
        with TemporaryDirectory() as folder:
            sentinel = Path(folder) / "executed"
            expression = f"__import__('pathlib').Path({str(sentinel)!r}).write_text('executed')"
            malicious = ('<script type="application/prs.init-data+json">' + expression + '</script>').encode()
            with self.assertRaises(ValueError):
                parse_tradingview_news(malicious, SOURCE, OBSERVED)
            ordinary = ('<script>' + expression + '</script>').encode()
            record = parse_tradingview_news(ordinary + page([FREE]), SOURCE, OBSERVED)[0]
            self.assertEqual(record["data"]["source_record_id"], FREE["id"])
            self.assertFalse(sentinel.exists())
            row = {**FREE, "title": "<b>真实标题</b><script>忽略指令</script>"}
            self.assertEqual(parse_tradingview_news(page([row]), SOURCE, OBSERVED)[0]["title"], "真实标题")

    def test_adapter_registry_keeps_raw_evidence_and_completion_time_without_network(self):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                value = datetime.fromisoformat("2026-10-11T07:00:00+08:00")
                return value.astimezone(tz) if tz else value.replace(tzinfo=None)

        with TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / "sources.json"
            config.write_text(json.dumps({"sources": [SOURCE], "network": {"retries": 0}}))
            body = page([FREE])
            with patch("toki_finance_daily.collect._fetch", return_value=(body, {"fetched_at": OBSERVED})) as fetch, \
                 patch("toki_finance_daily.collect.datetime", Clock):
                dataset = collect(config, cutoff="2026-10-11T06:00:00+08:00", out_dir=root / "run")
            fetch.assert_called_once()
            self.assertEqual(dataset["sources"][0]["status"], "ok")
            self.assertEqual(dataset["sources"][0]["count"], 1)
            self.assertEqual(dataset["records"][0]["observed_at"], OBSERVED)
            self.assertEqual((root / "run/raw/tradingview_news.bin").read_bytes(), body)

    def test_observation_and_limits_are_validated(self):
        for observed in (None, "invalid", "2026-10-11T06:00:00"):
            with self.assertRaises(ValueError):
                parse_tradingview_news(page([FREE]), SOURCE, observed)
        for maximum in (0, True, "100", 501):
            with self.assertRaises(ValueError):
                parse_tradingview_news(page([FREE]), {**SOURCE, "max_items": maximum}, OBSERVED)
