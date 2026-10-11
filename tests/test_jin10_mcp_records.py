"""Offline contracts for authenticated official MCP records; no token needed."""
from copy import deepcopy
from datetime import datetime, timedelta
from unittest import TestCase
import json

from toki_finance_daily.collect import filter_records, parse_timestamp
from toki_finance_daily.jin10_mcp_records import parse_jin10_mcp

OBSERVED = "2026-10-11T03:15:00+08:00"
SOURCE = {"id": "jin10_mcp", "name": "金十数据", "url": "https://mcp.jin10.com/mcp"}


def body(tool, rows):
    data = rows if tool == "list_calendar" else {"items": rows, "next_cursor": None, "has_more": False}
    return json.dumps({"tool": tool, "result": {"data": data, "status": 200, "message": "OK"}}, ensure_ascii=False).encode()


class Jin10MCPRecordsTests(TestCase):
    def parse(self, tool, rows, source=None, observed=OBSERVED):
        return parse_jin10_mcp(body(tool, rows), source or SOURCE, observed)

    def test_flash_keeps_real_title_time_id_and_bounded_text(self):
        row = {"id": "20261011030732711800", "time": "2026-10-11 03:07:32", "title": "官方快讯标题",
               "content": "<b>快讯正文</b><script>private()</script>", "remark": "付费注解"}
        record = self.parse("list_flash", [row])[0]
        self.assertEqual(record["published_at"], "2026-10-11T03:07:32+08:00")
        self.assertEqual(record["url"], "https://flash.jin10.com/detail/20261011030732711800")
        self.assertEqual(record["title"], row["title"])
        self.assertEqual(record["summary"], "快讯正文")
        self.assertEqual(record["data"]["provider_id"], row["id"])
        self.assertNotIn("region", record["data"])
        self.assertNotIn("remark", record["data"])
        self.assertNotIn("public_free", record["data"])

    def test_actual_flash_schema_uses_content_excerpt_without_inventing_id(self):
        row = {"content": "美国能源部：最新一轮战略石油储备释放旨在缓解供应中断。后续正文。",
               "time": "2026-10-11T03:07:32+08:00",
               "url": "https://flash.jin10.com/detail/20261011030732711800"}
        record = self.parse("list_flash", [row])[0]
        self.assertEqual(record["title"], "美国能源部：最新一轮战略石油储备释放旨在缓解供应中断")
        self.assertEqual(record["data"]["title_origin"], "content_excerpt")
        self.assertNotIn("provider_id", record["data"])
        row["content"] = "【来源原标题】后续正文。"
        self.assertEqual(self.parse("list_flash", [row])[0]["title"], "来源原标题")

    def test_actual_calendar_schema_keeps_source_minute_and_string_values(self):
        row = {"actual": "51.9", "affect_txt": "利多", "consensus": None, "previous": "51.4",
               "pub_time": "2026-10-05 06:00", "revised": None, "star": 2,
               "title": "澳大利亚9月标普全球服务业PMI终值"}
        record = self.parse("list_calendar", [row])[0]
        self.assertEqual(record["data"]["event_at"], "2026-10-05T06:00:00+08:00")
        self.assertEqual(record["data"]["time_precision"], "minute")
        self.assertEqual(record["data"]["actual"], "51.9")
        cutoff = parse_timestamp(OBSERVED)
        self.assertEqual(filter_records([record], cutoff - timedelta(hours=48), cutoff), [])

    def test_news_uses_real_url_and_introduction_without_copying_whole_object(self):
        row = {"id": "article-a", "time": "2026-10-10T19:07:00Z", "title": "真实新闻",
               "introduction": "供应商摘要", "content": "很长正文" * 1000,
               "url": "https://news.jin10.com/example?utm_source=mcp", "next_cursor": "secret"}
        record = self.parse("list_news", [row])[0]
        self.assertEqual(record["published_at"], "2026-10-11T03:07:00+08:00")
        self.assertEqual(record["summary"], "供应商摘要")
        self.assertEqual(record["url"], "https://news.jin10.com/example")
        self.assertNotIn("content", record["data"])
        self.assertNotIn("next_cursor", record["data"])
        row.pop("url")
        self.assertEqual(self.parse("list_news", [row])[0]["url"], SOURCE["url"])

    def test_date_only_news_never_gets_a_fake_midnight(self):
        row = {"id": "article-a", "time": "2026-10-10", "title": "仅日期的新闻"}
        record = self.parse("list_news", [row])[0]
        self.assertIsNone(record["published_at"])
        self.assertEqual(record["data"]["published_date"], "2026-10-10")
        self.assertEqual(record["data"]["date_precision"], "day")
        self.assertTrue(record["data"]["background_only"])
        self.assertNotIn("00:00", json.dumps(record))
        row["time"] = None
        unknown = self.parse("list_news", [row])[0]
        self.assertIsNone(unknown["published_at"])
        self.assertEqual(unknown["data"]["date_precision"], "unknown")

    def test_calendar_keeps_zero_source_star_and_date_precision(self):
        row = {"id": "calendar-1", "pub_time": "2026-10-12", "title": "真实经济发布",
               "star": 3, "previous": 0, "consensus": "0.0%", "actual": 0, "revised": None,
               "affect_txt": "供应商影响说明"}
        record = self.parse("list_calendar", [row])[0]
        data = record["data"]
        self.assertEqual(record["kind"], "calendar")
        self.assertIsNone(record["published_at"])
        self.assertEqual(data["event_at"], "2026-10-12")
        self.assertEqual(data["time_precision"], "date")
        self.assertEqual((data["actual"], data["previous"], data["star"]), (0, 0, 3))
        self.assertIsNone(data["revised"])
        self.assertNotIn("region", data)
        self.assertNotIn("importance", data)
        self.assertNotIn("00:00", json.dumps(record))
        self.assertEqual(record["id"], self.parse("list_calendar", [row], observed="2026-10-11T05:00:00+08:00")[0]["id"])

    def test_calendar_literal_region_prefix_and_reader_homepage_preserve_values(self):
        source = {**SOURCE, "region": "全球", "homepage": "https://rili.jin10.com/"}
        rows = [{"title": title, "pub_time": "2026-10-12 09:30", "actual": actual}
                for title, actual in (("中国台湾贸易数据", 0), ("中国香港零售数据", None),
                                      ("中国价格数据", 0), ("澳大利亚PMI", None),
                                      ("关注美国经济发布", None))]
        records = self.parse("list_calendar", rows, source)
        self.assertEqual([record["data"]["region"] for record in records],
                         ["中国台湾", "中国香港", "中国", "澳大利亚", "全球"])
        self.assertTrue(all(record["data"]["region_origin"] == "title_prefix" for record in records[:4]))
        self.assertNotIn("region_origin", records[-1]["data"])
        self.assertEqual([record["data"]["actual"] for record in records], [0, None, 0, None, None])
        self.assertTrue(all(record["url"] == source["homepage"] for record in records))
        self.assertTrue(all("currency" not in record["data"] for record in records))
        explicit = {**rows[0], "url": "https://example.org/calendar/original"}
        self.assertEqual(self.parse("list_calendar", [explicit], source)[0]["url"], explicit["url"])
        with self.assertRaisesRegex(ValueError, "public HTTP"):
            self.parse("list_calendar", rows, {**source, "homepage": "javascript:alert(1)"})

    def test_calendar_epoch_seconds_milliseconds_and_exact_clocks_agree(self):
        expected = "2026-10-12T08:30:00+08:00"
        epoch = int(datetime.fromisoformat(expected).timestamp())
        for value in (epoch, str(epoch), epoch * 1000, str(epoch * 1000), "2026-10-12 08:30:00", "2026-10-12T00:30:00Z"):
            with self.subTest(value=value):
                record = self.parse("list_calendar", [{"pub_time": value, "title": "定时发布"}])[0]
                self.assertEqual(record["data"]["event_at"], expected)
        minute = self.parse("list_calendar", [{"pub_time": "2026-10-12 08:30", "title": "分钟精度"}])[0]
        self.assertEqual(minute["data"]["time_precision"], "minute")
        cutoff = parse_timestamp("2026-10-12T08:30:01+08:00")
        self.assertEqual(filter_records([record], cutoff - timedelta(hours=48), cutoff), [])

    def test_paid_ad_flags_and_legacy_flash_data_are_handled_before_title(self):
        free = {"id": "123456", "time": "2026-10-11 03:00:00", "data": {"title": "旧结构免费标题", "content": "正文"}}
        paid = []
        for key, value in (("vip_level", 1), ("exclusive_to", ["plus"]), ("locked", True)):
            row = deepcopy(free)
            row["data"][key] = value
            row["data"].pop("title")
            paid.append(row)
        paid.append({"voice_status": "locked"})
        paid.append({"extras": {"ad": True}})
        records = self.parse("list_flash", paid + [free, free])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["title"], "旧结构免费标题")

    def test_empty_arrays_are_valid_and_unknown_structures_are_not_silent_success(self):
        for tool in ("list_flash", "list_news", "list_calendar"):
            self.assertEqual(self.parse(tool, []), [])
        for payload in ({}, {"tool": "get_quote", "result": {"data": [], "status": 200}},
                        {"tool": "list_flash", "result": {"data": [], "status": 200}},
                        {"tool": "list_news", "result": {"data": {"items": [{}]}, "status": 200}},
                        {"tool": "list_calendar", "result": {"data": {"items": []}, "status": 200}},
                        {"tool": "list_calendar", "result": {"data": [1], "status": 200}},
                        {"tool": "list_news", "result": {"success": False, "data": {"items": []}, "status": 200}},
                        {"tool": "list_flash", "result": {"data": {"items": []}, "status": 401}},
                        {"tool": "list_flash", "result": {"data": {"items": []}}}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                parse_jin10_mcp(json.dumps(payload), SOURCE, OBSERVED)
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.parse("list_news", [], {**SOURCE, "mcp_tool": "list_flash"})

    def test_ambiguous_times_missing_titles_urls_and_complex_values_raise(self):
        for value in (None, True, 12345, "12:30", "2026-99-12", "2026-10-12 25:30:00", {}, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.parse("list_calendar", [{"pub_time": value, "title": "发布"}])
        for changes in ({"title": ""}, {"title": ["not text"]}, {"actual": {"value": 0}}, {"url": "javascript:alert(1)"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.parse("list_calendar", [{"pub_time": "2026-10-12", "title": "发布", **changes}])
        with self.assertRaisesRegex(ValueError, "numeric detail id"):
            self.parse("list_flash", [{"id": "not-numeric", "time": "2026-10-11", "title": "快讯"}])
        with self.assertRaisesRegex(ValueError, "time field"):
            self.parse("list_news", [{"title": "字段改变的新闻"}])
        with self.assertRaisesRegex(ValueError, "max_items"):
            self.parse("list_news", [], {**SOURCE, "max_items": 0})

    def test_maximum_limits_records_without_inventing_pagination(self):
        rows = [{"id": f"news-{number}", "time": "2026-10-11", "title": f"新闻{number}"} for number in range(3)]
        records = self.parse("list_news", rows, {**SOURCE, "max_items": 2})
        self.assertEqual(len(records), 2)
        self.assertEqual([record["title"] for record in records], ["新闻0", "新闻1"])

    def test_quote_preserves_instrument_price_zero_and_actual_provider_time(self):
        source = {**SOURCE, "mcp_tool": "get_quote", "mcp_arguments": {"code": "XAUUSD"},
                  "homepage": "https://www.jin10.com/", "unit": "美元"}
        row = {"code": "XAUUSD", "name": "现货黄金", "time": "2026-10-10 04:54:59",
               "close": "4194.45", "open": "4136.63", "volume": 0,
               "ups_price": 0, "ups_percent": "0.00"}
        raw = json.dumps({"tool": "get_quote", "result": {"status": 200, "data": row}})
        record = parse_jin10_mcp(raw, source, OBSERVED)[0]
        self.assertEqual(record["url"], source["homepage"])
        self.assertEqual(record["data"]["symbol"], "XAUUSD")
        self.assertEqual(record["data"]["topic"], "market")
        self.assertEqual(record["data"]["value"], "4194.45")
        self.assertEqual(record["data"]["volume"], 0)
        self.assertEqual(record["data"]["ups_percent"], "0.00")
        self.assertEqual(record["data"]["provider_at"], "2026-10-10T04:54:59+08:00")
        self.assertIsNone(record["published_at"])
        cutoff = parse_timestamp(OBSERVED)
        self.assertEqual(filter_records([record], cutoff - timedelta(hours=48), cutoff), [record])
        before_observed = parse_timestamp("2026-10-11T03:14:59+08:00")
        self.assertEqual(filter_records([record], before_observed - timedelta(hours=48), before_observed), [])

    def test_quote_rejects_wrong_instrument_nonfinite_and_placeholder_prices(self):
        source = {**SOURCE, "mcp_tool": "get_quote", "mcp_arguments": {"code": "USOIL"}}
        row = {"code": "USOIL", "name": "WTI原油", "time": "2026-10-10 04:59:59", "close": "90.965"}
        for change in ({"code": "UKOIL"}, {"close": 0}, {"close": "-1"}, {"close": "NaN"},
                       {"close": True}, {"ups_percent": "Infinity"}, {"volume": []}, {"time": "04:59:59"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                raw = json.dumps({"tool": "get_quote", "result": {"status": 200, "data": {**row, **change}}})
                parse_jin10_mcp(raw, source, OBSERVED)
