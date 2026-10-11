"""Real visible page fragments and offline instrument/time precision regressions."""
from datetime import timedelta
from pathlib import Path
import unittest

from toki_finance_daily.collect import deduplicate, filter_records, parse_timestamp
from toki_finance_daily.investing_instrument import parse_investing_instrument_technical

FIXTURES = Path(__file__).parent / "fixtures"
OBSERVED = "2026-10-10T19:39:53.262745+00:00"
GOLD = {"id": "investing_gold_technical", "instrument_name": "黄金期货", "market": "商品期货",
        "url": "https://cn.investing.com/commodities/gold-technical", "source_type": "market_technical"}
BRENT = {"id": "investing_brent_technical", "instrument_name": "伦敦布伦特原油期货", "market": "商品期货",
         "url": "https://cn.investing.com/commodities/brent-oil-technical", "source_type": "market_technical"}
WTI = {"id": "investing_wti_technical", "instrument_name": "WTI原油期货", "market": "商品期货 · WTI",
       "url": "https://cn.investing.com/commodities/crude-oil-technical", "source_type": "market_technical"}
BITCOIN = {"id": "investing_bitcoin_technical", "instrument_name": "比特币", "symbol_name": "比特币 / 美元",
           "market": "加密资产", "url": "https://cn.investing.com/crypto/bitcoin/technical", "source_type": "market_technical"}
XAUUSD = {"id": "investing_xauusd_technical", "instrument_name": "XAU/USD", "symbol": "XAU/USD",
          "symbol_name": "黄金现货 / 美元", "market": "黄金现货", "url": "https://cn.investing.com/currencies/xau-usd-technical"}
NAS100 = {"id": "investing_nas100_technical", "instrument_name": "纳斯达克100指数", "market": "美国股票现金指数",
          "url": "https://cn.investing.com/indices/nq-100-technical", "source_type": "market_technical"}


class InvestingInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.gold = (FIXTURES / "investing-gold-technical.html").read_text()
        self.brent = (FIXTURES / "investing-brent-technical.html").read_text()
        self.wti = (FIXTURES / "investing-wti-technical.html").read_text()
        self.bitcoin = (FIXTURES / "investing-bitcoin-visible.html").read_text()
        self.xauusd = (FIXTURES / "investing-xauusd-technical.html").read_text()
        self.nas100 = (FIXTURES / "investing-nas100-technical.html").read_text()

    def parse(self, body=None, source=None, observed=OBSERVED):
        return parse_investing_instrument_technical(body if body is not None else self.gold, source or GOLD, observed)

    def test_free_periods_and_selected_components_preserve_actual_futures_contract(self):
        records = self.parse()
        self.assertEqual(len(records), 9)
        summaries = {r["data"]["timeframe"]: r["data"] for r in records if r["data"]["subtype"] == "summary"}
        self.assertEqual(set(summaries), {"30m", "1h", "5h", "1D", "1W", "1M"})
        self.assertEqual(summaries["1D"]["rating"], "强力卖出")
        self.assertEqual(summaries["1h"]["rating"], "强力买入")
        self.assertEqual(summaries["1h"]["moving_average_rating"], "买入")
        self.assertEqual(summaries["1h"]["indicator_rating"], "强力买入")
        self.assertEqual(summaries["1h"]["counts"]["moving_averages"], {"buy": 11, "sell": 1})
        self.assertNotIn("neutral", summaries["1h"]["counts"]["moving_averages"])
        for frame, data in summaries.items():
            self.assertEqual((data["symbol"], data["contract"]), ("GCZ6", "GCZ6"))
            self.assertEqual(data["instrument_header"], "黄金期货 - 2026年12月 (GCZ6)")
            self.assertIsNone(data["provider_at"])
            self.assertEqual(data["period"], "")
            self.assertEqual(data["availability_rule"], "observed_snapshot")
            if frame != "1h":
                self.assertIsNone(data["moving_average_rating"])
                self.assertIsNone(data["indicator_rating"])
        self.assertTrue(all(r["observed_at"] == OBSERVED for r in records))
        self.assertTrue(all(r["data"]["source_type"] == "market_technical" for r in records))
        cutoff = parse_timestamp(OBSERVED)
        self.assertEqual(len(deduplicate(filter_records(records, cutoff - timedelta(hours=48), cutoff))), 9)

    def test_real_detail_values_types_and_sparse_pivots_are_not_filled(self):
        details = {r["data"]["subtype"]: r["data"] for r in self.parse() if r["data"]["subtype"] != "summary"}
        indicators = details["indicators"]
        self.assertEqual(len(indicators["details"]), 12)
        self.assertEqual(indicators["indicators"], indicators["details"])
        self.assertEqual(indicators["details"][0], {"name": "RSI(14)", "value": 64.006, "value_raw": "64.006", "signal": "买入"})
        moving = details["moving_averages"]["details"]
        self.assertEqual(len(moving), 6)
        self.assertEqual(moving[0], {"name": "MA5", "simple": {"value": 4220.36, "value_raw": "4,220.36", "signal": "卖出"},
                                    "exponential": {"value": 4219.37, "value_raw": "4,219.37", "signal": "买入"}})
        pivots = details["pivot_points"]["details"]
        self.assertEqual(len(pivots), 5)
        self.assertEqual(pivots[0]["levels"]["P"], 4221.67)
        demark = next(row for row in pivots if row["method"] == "DeMark's")
        self.assertIsNone(demark["levels"]["S3"])
        self.assertEqual(demark["raw_levels"]["S3"], "-")
        self.assertEqual(demark["levels"]["S1"], 4214.6)
        self.assertTrue(all(data["timeframe"] == "1h" for data in details.values()))

    def test_brent_uses_its_own_contract_values_and_each_table_time(self):
        records = self.parse(self.brent, BRENT, "2026-10-10T19:44:37.143428+00:00")
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["contract"] == "LCOZ6" for r in records))
        self.assertTrue(all(r["data"]["rating"] == "强力买入" for r in records if r["data"]["subtype"] == "summary"))
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        for subtype in ("indicators", "moving_averages"):
            self.assertEqual(details[subtype]["provider_at"], "2026-10-10T00:02:00+00:00")
        self.assertIsNone(details["pivot_points"]["provider_at"])
        self.assertEqual(details["pivot_points"]["value"], 104.32)
        selected = next(r["data"] for r in records if r["data"].get("selected_timeframe"))
        self.assertEqual(selected["counts"]["moving_averages"], {"buy": 12, "sell": 0})

    def test_provider_times_are_local_to_table_and_unknown_snapshot_cannot_backdate(self):
        changed = self.gold.replace("10/10/2026 00:01 GMT", "10/10/2026 00:03 GMT", 1)
        records = self.parse(changed)
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        self.assertEqual(details["indicators"]["provider_at"], "2026-10-10T00:03:00+00:00")
        self.assertEqual(details["moving_averages"]["provider_at"], "2026-10-10T00:01:00+00:00")
        self.assertIsNone(details["pivot_points"]["provider_at"])
        cutoff = parse_timestamp("2026-10-10T00:02:00+00:00")
        kept = filter_records(records, cutoff - timedelta(hours=48), cutoff)
        self.assertEqual([r["data"]["subtype"] for r in kept], ["moving_averages"])
        unknown = self.parse(self.gold.replace("00:01 GMT", "00:01"))
        self.assertTrue(all(r["data"]["provider_at"] is None for r in unknown))
        self.assertEqual(filter_records(unknown, cutoff - timedelta(hours=48), cutoff), [])

    def test_paid_periods_sidebars_scripts_and_hidden_fragments_are_not_data(self):
        contaminated = self.gold.replace("1 分钟", "1 分钟 强力买入", 1)
        ignored = "<aside>" + self.gold + "</aside><nav>" + self.gold + "</nav><template>" + self.gold + "</template><script>" + self.gold + "</script><div hidden>" + self.gold + "</div>"
        records = self.parse(contaminated + ignored)
        self.assertEqual(len(records), 9)
        self.assertFalse({r["data"]["timeframe"] for r in records}.intersection({"1m", "5m", "15m"}))
        with self.assertRaisesRegex(ValueError, "heading"):
            self.parse(ignored)

    def test_missing_or_conflicting_selection_and_instrument_mismatch_are_rejected(self):
        for body in (self.gold.replace(' class="!border-inv-blue-500"', ''),
                     self.gold.replace('data-test="30m"', 'data-test="30m" class="!border-inv-blue-500"')):
            with self.assertRaisesRegex(ValueError, "selected free timeframe"):
                self.parse(body)
        with self.assertRaisesRegex(ValueError, "configured instrument_name"):
            self.parse(source={**GOLD, "instrument_name": "现货黄金"})
        with self.assertRaisesRegex(ValueError, "configured symbol"):
            self.parse(source={**GOLD, "symbol": "XAUUSD"})
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.parse(observed="2026-10-10T19:39:53")
        with self.assertRaisesRegex(ValueError, "heading"):
            self.parse("<h1>Access denied</h1><script>RSI 64.006</script>")

    def test_nonfinite_cells_do_not_create_numerical_facts(self):
        records = self.parse(self.gold.replace(">64.006<", ">NaN<").replace(">4,220.36<", ">Infinity<"))
        details = {r["data"]["subtype"]: r["data"]["details"] for r in records if r["data"]["subtype"] != "summary"}
        self.assertNotIn("RSI(14)", {row["name"] for row in details["indicators"]})
        self.assertNotIn("simple", details["moving_averages"][0])
        self.assertEqual(details["moving_averages"][0]["exponential"]["value"], 4219.37)

    def test_contract_roll_changes_ids_but_capture_time_does_not(self):
        original = self.parse()
        rolled = self.parse(self.gold.replace("GCZ6", "GCG7"), {**GOLD, "symbol_name": "黄金技术"})
        self.assertFalse({r["id"] for r in original}.intersection(r["id"] for r in rolled))
        self.assertTrue(all("GCG7" in r["title"] for r in rolled))
        self.assertTrue(all(r["data"]["contract"] == "GCG7" for r in rolled))
        later_capture = self.parse(observed="2026-10-11T19:39:53.262745+00:00")
        self.assertEqual([r["id"] for r in original], [r["id"] for r in later_capture])

    def test_real_wti_preserves_provider_symbol_currency_and_selected_detail_times(self):
        records = self.parse(self.wti, WTI, "2026-10-10T20:10:15.450400+00:00")
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["symbol"] == "OIL" and r["data"]["contract"] == "OIL" for r in records))
        self.assertTrue(all(r["data"]["instrument_header"] == "WTI原油期货 - 2026年11月 (OIL)" for r in records))
        self.assertTrue(all(r["data"]["market"] == "商品期货 · WTI" for r in records))
        self.assertTrue(all(r["data"]["quote_currency"] == "USD" and r["data"]["quote_currency_raw"] == "货币 USD" for r in records))
        # The price quotation currency is not the unit of RSI, ROC or a rating.
        self.assertTrue(all(r["data"]["unit"] == "" for r in records))
        summaries = {r["data"]["timeframe"]: r["data"] for r in records if r["data"]["subtype"] == "summary"}
        self.assertEqual(summaries["1D"]["rating"], "中性")
        self.assertEqual(summaries["1h"]["rating"], "强力买入")
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        self.assertEqual(len(details["indicators"]["details"]), 12)
        self.assertEqual(len(details["moving_averages"]["details"]), 6)
        self.assertEqual(len(details["pivot_points"]["details"]), 5)
        self.assertEqual(details["pivot_points"]["value"], 91.35)
        self.assertTrue(all(d["timeframe"] == "1h" for d in details.values()))
        self.assertTrue(all(details[k]["provider_at"] == "2026-10-10T00:02:00+00:00" for k in ("indicators", "moving_averages")))
        self.assertIsNone(details["pivot_points"]["provider_at"])

    def test_constructed_non_futures_crypto_heading_is_never_a_futures_contract(self):
        # A controlled identity regression over the common table shape, not a
        # claim that this body was captured from the blocked Bitcoin endpoint.
        body = self.wti.replace("WTI原油期货 - 2026年11月 (OIL)", "比特币 (BTC/USD)")
        source = {**WTI, "id": "crypto_fixture", "instrument_name": "比特币", "symbol": "BTC/USD",
                  "symbol_name": "比特币 / 美元", "market": "加密资产", "url": "https://example.org/bitcoin/technical"}
        records = self.parse(body, source)
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["symbol"] == "BTC/USD" for r in records))
        self.assertTrue(all(r["data"]["contract"] is None for r in records))
        self.assertTrue(all(r["data"]["symbol_name"] == "比特币 / 美元" for r in records))
        self.assertTrue(all(r["data"]["quote_currency"] == "USD" for r in records))
        self.assertTrue(all("期货" not in r["title"] for r in records))

    def test_quote_currency_is_visible_source_text_without_unit_inference(self):
        hidden = '<aside><div data-test="currency-in-label">货币 CNY</div></aside><div hidden data-test="currency-in-label">货币 EUR</div>'
        records = self.parse(self.wti + hidden, WTI)
        self.assertTrue(all(r["data"]["quote_currency"] == "USD" for r in records))
        missing = self.parse(self.wti.replace('data-test="currency-in-label"', 'data-test="unrelated"'), WTI)
        self.assertTrue(all(r["data"]["quote_currency"] is None for r in missing))
        self.assertTrue(all(r["data"]["quote_currency_raw"] is None for r in missing))
        with self.assertRaisesRegex(ValueError, "quote currency is ambiguous"):
            self.parse(self.wti + '<div data-test="currency-in-label">货币 EUR</div>', WTI)

    def test_future_labelled_gmt_clock_keeps_visible_snapshot_without_guessing_timezone(self):
        observed = "2026-10-10T20:11:00+00:00"
        body = self.wti.replace("10/10/2026 00:02 GMT", "11/10/2026 04:11 GMT", 1)
        records = self.parse(body, WTI, observed)
        details = {r["data"]["subtype"]: r for r in records if r["data"]["subtype"] != "summary"}
        ahead = details["indicators"]["data"]
        self.assertIsNone(ahead["provider_at"])
        self.assertEqual(ahead["period"], "")
        self.assertEqual(ahead["provider_asof_raw"], "11/10/2026 04:11 GMT")
        self.assertTrue(ahead["source_clock_ahead"])
        self.assertEqual(ahead["availability_rule"], "observed_snapshot")
        self.assertEqual(len(ahead["details"]), 12)
        self.assertEqual(details["moving_averages"]["data"]["provider_at"], "2026-10-10T00:02:00+00:00")
        self.assertNotIn("source_clock_ahead", details["moving_averages"]["data"])
        cutoff = parse_timestamp(observed)
        self.assertEqual(len(filter_records(records, cutoff - timedelta(hours=48), cutoff)), 9)
        before_observation = cutoff - timedelta(minutes=1)
        kept = filter_records(records, before_observation - timedelta(hours=48), before_observation)
        self.assertEqual([r["data"]["subtype"] for r in kept], ["moving_averages"])

    def test_actual_bitcoin_visible_dom_code_and_future_source_clock(self):
        observed = "2026-10-10T20:16:36.194370+00:00"
        records = self.parse(self.bitcoin, BITCOIN, observed)
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["symbol"] == "BTC" and r["data"]["contract"] is None for r in records))
        self.assertTrue(all(r["data"]["instrument_header"] == "比特币 - Bitcoin" for r in records))
        self.assertTrue(all(r["data"]["provider_at"] is None and r["data"]["period"] == "" for r in records))
        self.assertTrue(all(r["data"]["availability_rule"] == "observed_snapshot" for r in records))
        self.assertTrue(all(r["data"]["quote_currency"] == "USD" for r in records))
        summaries = {r["data"]["timeframe"]: r["data"] for r in records if r["data"]["subtype"] == "summary"}
        self.assertEqual(summaries["1D"]["rating"], "卖出")
        self.assertEqual(summaries["1h"]["counts"]["moving_averages"], {"buy": 9, "sell": 3})
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        for kind in ("indicators", "moving_averages"):
            self.assertTrue(details[kind]["source_clock_ahead"])
            self.assertEqual(details[kind]["provider_asof_raw"], "11/10/2026 04:11 GMT")
        self.assertEqual(len(details["indicators"]["details"]), 12)
        self.assertEqual(details["indicators"]["details"][0]["value"], 60.189)
        self.assertEqual(details["moving_averages"]["details"][0]["simple"]["value"], 83057.3)
        self.assertEqual(len(details["pivot_points"]["details"]), 5)
        self.assertEqual(details["pivot_points"]["value"], 83063.3)
        cutoff = parse_timestamp(observed)
        self.assertEqual(len(filter_records(records, cutoff - timedelta(hours=48), cutoff)), 9)
        before = cutoff - timedelta(seconds=1)
        self.assertEqual(filter_records(records, before - timedelta(hours=48), before), [])

    def test_secondary_code_requires_matching_visible_instrument_technical_heading(self):
        self.assertTrue(all(r["data"]["symbol"] == "BTC" for r in self.parse(self.bitcoin + '<aside><h2>比特币 (ETH)技术分析</h2></aside>', BITCOIN)))
        for replacement in ("以太坊 (ETH)技术分析", "比特币 常见问题 (BTC)"):
            body = self.bitcoin.replace("Bitcoin - 比特币 (BTC)技术分析", replacement)
            with self.assertRaisesRegex(ValueError, "symbol is missing"):
                self.parse(body, BITCOIN)
        with self.assertRaisesRegex(ValueError, "code is ambiguous"):
            self.parse(self.bitcoin + '<h2>比特币 (ETH)技术分析</h2>', BITCOIN)
        with self.assertRaisesRegex(ValueError, "configured symbol"):
            self.parse(self.bitcoin, {**BITCOIN, "symbol": "ETH"})

    def test_actual_spot_gold_prefix_symbol_and_currency_selector_preserve_cash_scope(self):
        records = self.parse(self.xauusd, XAUUSD, "2026-10-10T20:17:46.006729+00:00")
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["symbol"] == "XAU/USD" and r["data"]["contract"] is None for r in records))
        self.assertTrue(all(r["data"]["instrument_header"] == "XAU/USD - 黄金现货 美元" for r in records))
        self.assertTrue(all(r["data"]["quote_currency"] == "USD" and r["data"]["quote_currency_raw"] == "货币 USD" for r in records))
        self.assertTrue(all("期货" not in r["title"] for r in records))
        summaries = {r["data"]["timeframe"]: r["data"] for r in records if r["data"]["subtype"] == "summary"}
        self.assertEqual(summaries["1D"]["rating"], "强力卖出")
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        self.assertTrue(all(details[k]["provider_at"] == "2026-10-09T23:59:00+00:00" for k in ("indicators", "moving_averages")))
        self.assertEqual(details["pivot_points"]["value"], 4196.44)
        self.assertTrue(all(d["timeframe"] == "1h" for d in details.values()))

    def test_actual_nasdaq100_retains_ndx_cash_code_instead_of_broker_or_future(self):
        records = self.parse(self.nas100, NAS100, "2026-10-10T20:17:45.469548+00:00")
        self.assertEqual(len(records), 9)
        self.assertTrue(all(r["data"]["symbol"] == "NDX" and r["data"]["contract"] is None for r in records))
        self.assertTrue(all(r["data"]["instrument_header"] == "纳斯达克100指数 (NDX)" for r in records))
        self.assertTrue(all(r["data"]["market"] == "美国股票现金指数" for r in records))
        details = {r["data"]["subtype"]: r["data"] for r in records if r["data"]["subtype"] != "summary"}
        self.assertTrue(all(details[k]["provider_at"] == "2026-10-10T00:01:00+00:00" for k in ("indicators", "moving_averages")))
        self.assertEqual(details["pivot_points"]["value"], 30884.52)
        self.assertIsNone(details["pivot_points"]["provider_at"])


if __name__ == "__main__":
    unittest.main()
