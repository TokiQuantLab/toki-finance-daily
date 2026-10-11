"""Offline contracts for visible public technical tables and their time scope."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import json

from toki_finance_daily.collect import collect, deduplicate, filter_records, parse_investing_technical, parse_timestamp


OBSERVED = "2026-10-11T03:04:17+08:00"
SOURCE = {"id": "investing_technical", "name": "Investing public technical page", "adapter": "investing_technical",
          "url": "https://cn.investing.com/technical/", "enabled": True, "source_type": "market_indicator",
          "topic_scope": "technical_analysis", "retries": 0}


def summary_table(asof="2026年10月10日 02:59 (GMT+8:00)"):
    rows = []
    for pair, name, path in [("1", "EUR/USD", "currencies/eur-usd"), ("169", "道琼斯指数", "indices/us-30")]:
        for subtype, ratings in [("movingAverages", ("买入", "卖出")), ("indicators", ("强力买入", "强力卖出")),
                                  ("summary", ("强力买入", "卖出"))]:
            symbol = f'<td rowspan="3"><a href="/{path}" title="{name}">{name}</a><p>1,234.50</p></td>' if subtype == "movingAverages" else ""
            values = "".join(f'<td class="js-socket-elem">{rating}</td>' for rating in ratings)
            rows.append(f'<tr data-pairid="{pair}" data-row-type="{subtype}">{symbol}<td>{subtype}</td>{values}</tr>')
    return f'''<div id="technical_overview_data"><span class="dateHP">{asof}</span>
    <h2>技术摘要</h2><table class="technicalSummaryTbl"><thead><tr><th>名称</th><th>类型</th>
    <th data-period="300">5 分钟</th><th data-period="86400">1天</th></tr></thead>
    <tbody>{''.join(rows)}</tbody></table></div>'''


DETAILS = '''
<div id="classical"><table class="movingAvgsTbl"><tr><th>名称</th><th>MA5</th><th>MA10</th></tr>
<tr><td><a href="/currencies/eur-usd">EUR/USD</a></td><td>1.1202<br/><span>买入</span></td><td>1.1209<br/>卖出</td></tr></table></div>
<div id="classical"><table class="crossRatesTbl"><tr><th>名称</th><th>S3</th><th>S2</th><th>S1</th><th>轴点</th><th>R1</th><th>R2</th><th>R3</th></tr>
<tr><td><a href="/currencies/eur-usd">EUR/USD</a></td><td>1.1188</td><td>1.1192</td><td>1.1196</td><td>1.1200</td><td>1.1204</td><td>1.1208</td><td>1.1212</td></tr></table></div>
<table class="crossRatesTbl"><tr><th>名称</th><th>最新价</th><th>ATR(14)</th><th>买入</th><th>卖出</th><th>中性</th><th>指标总结</th></tr>
<tr><td><a href="/indices/us-30">道琼斯指数</a></td><td>51,654.95</td><td>少波动</td><td>7</td><td>0</td><td>0</td><td>强力买入</td></tr></table>
<table class="patternTable"><tr><th></th><th>名称</th><th>时间范围</th><th>可靠性</th><th>型态</th><th>K线#</th><th></th></tr>
<tr><td class="theDay" colspan="7">显现型态</td></tr>
<tr data-time=""><td></td><td><a href="/equities/ibm-candlestick">IBM</a></td><td>1D</td><td title="高"></td><td>Morning Star</td><td>当前</td><td title="牛市逆转"></td></tr>
<tr><td class="theDay" colspan="7">完整型态</td></tr>
<tr data-time="2026年10月8日"><td></td><td><a href="/equities/ibm-candlestick">IBM</a></td><td>1D</td><td title="高"></td><td>Dark Cloud Cover</td><td>1</td><td title="熊市逆转"></td></tr>
<template><table><tr><td>未识别K线型态</td></tr></table></template></table>
'''


class InvestingTechnicalTests(TestCase):
    def records(self, body=None):
        return parse_investing_technical(body or summary_table() + DETAILS, SOURCE, OBSERVED)

    def test_summary_rowspan_dimensions_and_update_time_are_not_capture_time(self):
        records = self.records()
        summaries = [record for record in records if record["data"]["subtype"] == "summary"]
        self.assertEqual(len(summaries), 4)
        self.assertEqual({record["data"]["timeframe"] for record in summaries}, {"5m", "1D"})
        eur = next(record["data"] for record in summaries if record["data"]["symbol"] == "EUR/USD" and record["data"]["timeframe"] == "1D")
        self.assertEqual((eur["rating"], eur["moving_average_rating"], eur["indicator_rating"]), ("卖出", "卖出", "强力卖出"))
        self.assertEqual(eur["provider_at"], "2026-10-10T02:59:00+08:00")
        self.assertEqual(eur["period"], eur["provider_at"])
        self.assertEqual(eur["time_precision"], "minute")
        cutoff = parse_timestamp(OBSERVED)
        kept = deduplicate(filter_records(records, cutoff - timedelta(hours=48), cutoff))
        self.assertEqual(len(kept), len(records))
        self.assertEqual(len({record["data"]["series_id"] for record in kept}), len(records))

    def test_details_keep_real_numbers_zero_counts_and_candle_date_separate(self):
        details = {record["data"]["subtype"]: record["data"] for record in self.records() if record["data"]["subtype"] != "summary"}
        self.assertEqual(details["moving_averages"]["details"][0], {"name": "MA5", "value": 1.1202, "rating": "买入", "raw": "1.1202 买入"})
        pivot = details["pivot_points"]
        self.assertEqual((pivot["value"], pivot["details"]["method"], pivot["details"]["levels"]["R3"]), (1.12, "经典", 1.1212))
        indicators = details["indicators"]["details"]
        self.assertEqual(indicators["counts"], {"买入": 7, "卖出": 0, "中性": 0})
        self.assertEqual(indicators["last_price"], 51654.95)
        self.assertEqual(indicators["ATR(14)"], "少波动")
        candle = details["candlestick_patterns"]
        self.assertEqual(candle["timeframe"], "1D")
        self.assertEqual(len(candle["details"]), 2)
        self.assertEqual(candle["details"][0]["stage"], "显现型态")
        self.assertIsNone(candle["details"][0]["bars_ago"])
        self.assertEqual(candle["details"][1]["pattern_date"], "2026-10-08")
        self.assertEqual(candle["details"][1]["direction"], "熊市逆转")
        for data in details.values():
            self.assertEqual(data["period"], "")
            self.assertIsNone(data["provider_at"])
            self.assertEqual(data["availability_rule"], "observed_snapshot")
            self.assertNotIn("provider_asof_raw", data)
        self.assertIsNone(details["moving_averages"]["timeframe"])

    def test_precise_future_timestamp_is_excluded_and_missing_timezone_is_unknown(self):
        cutoff = parse_timestamp("2026-10-10T02:58:59+08:00")
        summaries = self.records(summary_table())
        self.assertEqual(filter_records(summaries, cutoff - timedelta(hours=48), cutoff), [])
        for asof in ("2026年10月10日 02:59", "2026年10月10日 02:59 (GMT+8:99)"):
            unknown = self.records(summary_table(asof))
            self.assertTrue(all(record["data"]["provider_at"] is None and record["data"]["period"] == "" for record in unknown))
            self.assertTrue(all(record["data"]["availability_rule"] == "observed_snapshot" for record in unknown))

    def test_scripts_hidden_templates_and_access_denied_are_not_summary_data(self):
        visible = summary_table()
        ignored = '<nav>' + visible + '</nav><template>' + visible + '</template><script>' + visible + '</script><div style="display:none">' + visible + '</div>'
        self.assertEqual(len(self.records(visible + ignored)), 4)
        for body in ("<html><body>Access denied</body></html>", ignored, visible.replace("强力买入", "—").replace("卖出", "—")):
            with self.assertRaisesRegex(ValueError, "no visible technical summary"):
                self.records(body)

    def test_invalid_numeric_cells_are_not_fabricated(self):
        records = self.records(summary_table() + DETAILS.replace("1.1202<br/>", "NaN<br/>").replace("<td>1.1212</td>", "<td>Infinity</td>"))
        data = [record["data"] for record in records]
        ma = next(item for item in data if item["subtype"] == "moving_averages")
        self.assertEqual([item["name"] for item in ma["details"]], ["MA10"])
        self.assertNotIn("pivot_points", {item["subtype"] for item in data})

    def test_collector_keeps_raw_evidence_and_one_failure_does_not_hide_page(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "sources.json"
            config.write_text(json.dumps({"sources": [SOURCE, {**SOURCE, "id": "failed", "url": "https://example.org/denied"}],
                                          "network": {"retries": 1}}))
            def fetch(source, network):
                self.assertEqual(network["retries"], 0)
                if source["id"] == "failed":
                    raise PermissionError("public page unavailable")
                return (summary_table() + DETAILS).encode(), {"transport": "fixture"}
            with patch("toki_finance_daily.collect._fetch", side_effect=fetch) as mocked:
                result = collect(config, cutoff="2026-10-10T03:00:00+08:00", out_dir=root / "edition")
            self.assertEqual(mocked.call_count, 2)
            states = {item["id"]: item for item in result["sources"]}
            self.assertEqual(states["investing_technical"]["status"], "ok")
            self.assertEqual(states["failed"]["status"], "error")
            self.assertEqual(len(result["records"]), 4)  # Unknown-time details were not yet observed at this historical cutoff.
            self.assertTrue((root / "edition" / states["investing_technical"]["raw_path"]).is_file())
            self.assertEqual(len(states["investing_technical"]["raw_sha256"]), 64)
