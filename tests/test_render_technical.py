"""Selected supporting data preserves provenance without expanding news into data panels."""
from copy import deepcopy
from datetime import timedelta
from unittest import TestCase

from toki_finance_daily.collect import filter_records, parse_timestamp
from toki_finance_daily.render import render_html


class TechnicalRenderTests(TestCase):
    def setUp(self):
        self.brief = {"date": "2026-10-11", "headline": "技术观察测试", "dek": "离线测试数据",
                      "lead": {"title": "来源说明", "fact": "测试事实", "analysis": "测试分析",
                               "watch": "继续观察", "evidence_ids": []},
                      "stories": [], "calendar": [], "watchlist": []}
        self.dataset = {"date": "2026-10-11", "timezone": "Asia/Shanghai", "mode": "demo",
                        "cutoff": "2026-10-11T08:00:00+08:00",
                        "sources": [{"id": "official", "name": "官方宏观来源"},
                                    {"id": "tv", "name": "TradingView"},
                                    {"id": "invest", "name": "Investing.com"}],
                        "records": [
                            {"id": "macro", "kind": "macro", "source_id": "official", "title": "GDP",
                             "url": "https://example.org/gdp", "published_at": None,
                             "observed_at": "2026-10-11T07:00:00+08:00",
                             "data": {"label": "宏观数据保留", "value": 1.5, "unit": "%",
                                      "period": "2026-Q3", "series_id": "gdp"}},
                            self.technical("tv-daily", "tv", "TradingView", "标普500指数",
                                           observed_at="2026-10-11T07:59:00+08:00"),
                            self.technical("invest-daily", "invest", "Investing.com", "黄金",
                                           observed_at="2026-10-11T07:58:00+08:00",
                                           provider_at="2026-10-10T02:59:00+08:00"),
                        ]}

    @staticmethod
    def technical(identifier, source, provider, name, *, observed_at, provider_at=None, timeframe="1D"):
        return {"id": identifier, "kind": "macro", "source_id": source,
                "title": name + "技术评级", "published_at": None,
                "url": "https://example.org/" + identifier, "observed_at": observed_at,
                "data": {"topic": "technical", "provider": provider, "symbol": name,
                         "symbol_name": name, "timeframe": timeframe, "rating": "买入", "value": "买入",
                         "moving_average_rating": "强力买入", "indicator_rating": "中性",
                         "series_id": identifier, "period": provider_at or observed_at,
                         "provider_at": provider_at,
                         "availability_rule": None if provider_at else "observed_snapshot"}}

    def test_selected_technical_evidence_keeps_each_source_time_without_data_panels(self):
        self.brief["lead"]["evidence_ids"] = ["tv-daily", "invest-daily"]
        self.brief["lead"]["fact"] = "较早的技术评级只描述当时状态。"
        result = render_html(self.brief, self.dataset)
        self.assertIn("TradingView", result)
        self.assertIn("观察时点 10/11 07:59 · 来源更新时间未确认", result)
        self.assertIn("Investing.com", result)
        self.assertIn("来源更新 10/10 02:59", result)
        self.assertNotIn("来源更新 10/11 07:59", result)
        self.assertNotIn("观察时点 10/11 07:58", result)
        self.assertNotIn("宏观数据保留", result)
        self.assertNotIn('class="technical-section"', result)
        self.assertNotIn('class="technical-signals"', result)
        self.assertNotIn("强力买入", result)
        self.assertIn("较早的技术评级只描述当时状态。", result)

    def test_unknown_source_time_discloses_uncertainty_without_private_clock_flags(self):
        record = self.dataset["records"][1]
        record["data"].update(provider_asof_raw="2026-10-11T16:00:00Z", source_clock_ahead=True)
        self.brief["lead"]["evidence_ids"] = [record["id"]]
        result = render_html(self.brief, self.dataset)
        self.assertIn("来源更新时间未确认", result)
        self.assertNotIn("source_clock_ahead", result)
        self.assertNotIn("2026-10-11T16:00:00Z", result)
        self.assertNotIn("来源未标", result)

    def test_snapshot_after_cutoff_is_filtered_before_selecting_editorial_evidence(self):
        late = self.technical("future-observation", "tv", "TradingView", "FUTURE_SNAPSHOT_ONLY",
                              observed_at="2026-10-11T08:00:01+08:00")
        dataset = deepcopy(self.dataset)
        cutoff = parse_timestamp(dataset["cutoff"])
        dataset["records"] = filter_records(dataset["records"] + [late], cutoff - timedelta(hours=48), cutoff)
        self.assertNotIn("future-observation", {record["id"] for record in dataset["records"]})
        self.brief["lead"]["evidence_ids"] = ["tv-daily"]
        result = render_html(self.brief, dataset)
        self.assertIn("TradingView", result)
        self.assertNotIn("FUTURE_SNAPSHOT_ONLY", result)

    def test_selected_market_quote_keeps_its_earlier_time_without_automatic_price_card(self):
        self.dataset["records"].append({
            "id": "spot-quote", "source_id": "invest", "kind": "macro", "title": "现货黄金报价",
            "url": "https://example.org/spot", "published_at": None,
            "observed_at": "2026-10-11T07:58:00+08:00",
            "data": {"topic": "market", "label": "现货黄金", "symbol": "XAUUSD", "value": "4194.45",
                     "unit": "美元", "ups_percent": "1.47", "provider": "金十数据",
                     "period": "2026-10-10T04:54:59+08:00", "provider_at": "2026-10-10T04:54:59+08:00"}})
        before = deepcopy(self.dataset)
        self.brief["lead"]["evidence_ids"] = ["spot-quote"]
        result = render_html(self.brief, self.dataset)
        self.assertIn("报价时点 10/10 04:54", result)
        self.assertNotIn("10/11 07:58", result)
        self.assertNotIn("4194.45", result)
        self.assertNotIn("+1.47%", result)
        self.assertNotIn('class="market-section"', result)
        self.assertEqual(self.dataset, before)
