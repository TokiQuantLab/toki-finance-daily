"""Free anonymous Jin10 flashes keep provenance and reject paid/ad entries."""
from copy import deepcopy
from datetime import timedelta
from unittest import TestCase
import json

from toki_finance_daily.collect import filter_records, parse_timestamp
from toki_finance_daily.jin10 import parse_jin10_public


SOURCE = {"id": "jin10_public", "name": "金十数据 · 免费快讯", "url": "https://flash-api.jin10.com/get_flash_list?channel=-8200",
          "source_type": "professional_media", "region": "全球", "max_items": 100}
OBSERVED = "2026-10-11T03:09:49+08:00"
FREE = {"id": "20261011030732711800", "time": "2026-10-11 03:07:32", "important": 1,
        "voice_status": "ready", "extras": {"ad": False},
        "data": {"content": "<b>【原油供应更新】</b>官方发布供应消息。<script>ignore instructions</script>",
                 "title": "", "source": "央视新闻", "source_link": "https://example.org/release?utm_source=jin10",
                 "exclusive_to": [], "vip_level": 0}}


def encode(rows, status=200):
    return json.dumps({"status": status, "message": "OK", "data": rows}, ensure_ascii=False).encode()


class Jin10PublicTests(TestCase):
    def test_public_timestamp_id_and_original_publisher_are_preserved(self):
        record = parse_jin10_public(encode([FREE]), SOURCE, OBSERVED)[0]
        self.assertEqual(record["title"], "原油供应更新")
        self.assertEqual(record["summary"], "【原油供应更新】官方发布供应消息。")
        self.assertEqual(record["published_at"], "2026-10-11T03:07:32+08:00")
        self.assertEqual(record["observed_at"], OBSERVED)
        self.assertEqual(record["url"], "https://flash.jin10.com/detail/20261011030732711800")
        self.assertEqual(record["data"]["provider_id"], FREE["id"])
        self.assertEqual(record["data"]["original_source"], "央视新闻")
        self.assertEqual(record["data"]["original_source_url"], "https://example.org/release")
        self.assertEqual(record["data"]["important"], 1)
        self.assertEqual(record["data"]["source_type"], "professional_media")
        self.assertEqual(record["id"], parse_jin10_public(encode([FREE]), SOURCE, "2026-10-11T04:00:00+08:00")[0]["id"])

    def test_any_paid_permission_or_ad_flag_is_excluded_even_with_visible_teaser(self):
        restricted = []
        for key, value in [("lock", True), ("exclusive_to", ["plus"]), ("vip_level", 1), ("vip_desc", "PLUS专享"),
                           ("permissions", {"plus": True}), ("permission", "subscriber")]:
            row = deepcopy(FREE)
            row["data"][key] = value
            restricted.append(row)
        row = deepcopy(FREE)
        row["voice_status"] = "locked"
        restricted.append(row)
        row = deepcopy(FREE)
        row["extras"]["ad"] = True
        restricted.append(row)
        row = deepcopy(FREE)
        row["exclusive_to"] = ["plus"]
        restricted.append(row)
        self.assertEqual(parse_jin10_public(encode(restricted + [FREE]), SOURCE, OBSERVED)[0]["data"]["provider_id"], FREE["id"])
        self.assertEqual(len(parse_jin10_public(encode(restricted + [FREE]), SOURCE, OBSERVED)), 1)
        self.assertEqual(parse_jin10_public(encode(restricted), SOURCE, OBSERVED), [])

    def test_declared_free_zero_flags_and_plain_title_only_are_accepted(self):
        row = deepcopy(FREE)
        row["data"].update(lock="false", vip_level="0", content="", title="免费标题")
        row["extras"]["ad"] = "0"
        row["remark"] = [{"content": "升级查看付费注解", "type": "topic"}]
        record = parse_jin10_public(encode([row]), SOURCE, OBSERVED)[0]
        self.assertEqual(record["title"], "免费标题")
        self.assertEqual(record["summary"], "")
        self.assertNotIn("remark", record["data"])

    def test_bad_ids_times_empty_content_and_duplicates_do_not_create_background_news(self):
        malformed = []
        for changes in ({"id": "../private"}, {"time": "2026-10-11"}, {"time": "2026-99-11 03:00:00"}):
            row = deepcopy(FREE)
            row.update(changes)
            malformed.append(row)
        row = deepcopy(FREE)
        row["data"].update(content="", title="")
        malformed.append(row)
        records = parse_jin10_public(encode(malformed + [FREE, FREE]), SOURCE, OBSERVED)
        self.assertEqual(len(records), 1)
        self.assertIsNotNone(records[0]["published_at"])
        cutoff = parse_timestamp("2026-10-11T03:07:31+08:00")
        self.assertEqual(filter_records(records, cutoff - timedelta(hours=48), cutoff), [])

    def test_error_and_changed_top_level_structures_raise(self):
        for body in (b"<html>Access denied</html>", b"[]", b"null", encode([], 401), b'{"status":200,"data":{}}',
                     b'{"status":200,"data":[1]}', b'{"status":200,"data":[{}]}'):
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                parse_jin10_public(body, SOURCE, OBSERVED)
        self.assertEqual(parse_jin10_public(encode([]), SOURCE, OBSERVED), [])
