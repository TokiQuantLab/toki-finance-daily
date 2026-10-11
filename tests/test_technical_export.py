"""Offline technical CSV contracts, source precision and spreadsheet safety."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import csv

from toki_finance_daily.technical_export import COLUMNS, export_technicals


def record(identifier, data, **fields):
    return {"id": identifier, "source_id": "fixture", "kind": "macro", "url": "https://example.org/technicals/",
            "observed_at": "2026-10-11T04:00:00+08:00",
            "data": {"topic": "technical", "provider": "Investing.com", "symbol": "GCZ6",
                     "instrument_header": "黄金期货 - 2026年12月 (GCZ6)", "contract": "GCZ6",
                     "provider_at": None, "timeframe": "1h", **data}, **fields}


class TechnicalExportTests(TestCase):
    def export(self, records):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "technicals.csv"
            metadata = export_technicals({"records": records}, path)
            content = path.read_bytes()
            with path.open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
        return rows, metadata, content

    def test_only_technical_macro_records_and_ratings_keep_text_without_scores(self):
        source = record("summary", {"subtype": "summary", "rating": "强力买入", "value": "强力买入",
                                    "moving_average_rating": None, "indicator_rating": None})
        rows, metadata, content = self.export([source, record("sentiment", {"topic": "sentiment", "value": 50}),
                                              record("news", {"rating": "买入"}, kind="news")])
        self.assertEqual(metadata["records"], 1)
        self.assertEqual(metadata["rows"], 1)
        self.assertEqual(metadata["numeric_rows"], 0)
        self.assertEqual(rows[0]["value"], "")
        self.assertEqual((rows[0]["value_text"], rows[0]["signal"]), ("强力买入", "强力买入"))
        self.assertEqual(rows[0]["contract"], "GCZ6")
        self.assertEqual(rows[0]["symbol_name"], "黄金期货 - 2026年12月 (GCZ6)")
        self.assertEqual(rows[0]["provider_at"], "")
        self.assertTrue(content.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(tuple(rows[0]), COLUMNS)

    def test_instrument_selected_timeframe_nested_ma_and_method_are_distinct(self):
        rows, metadata, _ = self.export([
            record("indicators", {"subtype": "indicators", "provider_at": "2026-10-10T00:01:00+00:00",
                                  "details": [{"name": "Williams %R", "value": -14.48, "value_raw": "-14.48", "signal": "中性"}]}),
            record("ma", {"subtype": "moving_averages", "details": [{"name": "MA5",
                         "simple": {"value": 2650.5, "value_raw": "2,650.50", "signal": "买入"},
                         "exponential": {"value": 2649.0, "value_raw": "2,649.00", "signal": "卖出"}}]}),
            record("pivot", {"subtype": "pivot_points", "details": [
                {"method": "经典", "levels": {"P": 2600.0, "S1": 0.0}, "raw_levels": {"P": "2600.00", "S1": "0"}},
                {"method": "DeMark", "levels": {"P": 2601.0, "S3": None}, "raw_levels": {"P": "2601.00", "S3": "—"}}]})])
        self.assertTrue(all(row["timeframe"] == "1h" for row in rows))
        self.assertEqual({row["ma_type"] for row in rows if row["subtype"] == "moving_averages"}, {"simple", "exponential"})
        self.assertEqual({row["method"] for row in rows if row["subtype"] == "pivot_points"}, {"经典", "DeMark"})
        negative = next(row for row in rows if row["name"] == "Williams %R")
        self.assertEqual(negative["value"], "-14.48")
        self.assertEqual(negative["value_text"], "'-14.48")
        self.assertEqual(negative["provider_at"], "2026-10-10T00:01:00+00:00")
        missing = next(row for row in rows if row["name"] == "S3")
        self.assertEqual((missing["value"], missing["value_text"]), ("", "—"))
        zero = next(row for row in rows if row["name"] == "S1")
        self.assertEqual(float(zero["value"]), 0)
        self.assertEqual(metadata["numeric_rows"], 6)

    def test_display_code_preserves_actual_future_month_and_currency_metadata(self):
        rows, _, _ = self.export([record("wti", {
            "symbol": "OIL", "symbol_name": "WTI原油期货 (OIL)", "contract": "OIL",
            "instrument_header": "WTI原油期货 - 2026年11月 (OIL)",
            "quote_currency": "USD", "unit": "", "subtype": "indicators",
            "details": [{"name": "RSI(14)", "value": 54.874, "value_raw": "54.874", "signal": "买入"}],
        })])
        self.assertEqual(rows[0]["instrument_header"], "WTI原油期货 - 2026年11月 (OIL)")
        self.assertEqual((rows[0]["symbol"], rows[0]["contract"]), ("OIL", "OIL"))
        self.assertEqual((rows[0]["quote_currency"], rows[0]["unit"]), ("USD", ""))

    def test_tradingview_tables_counts_raw_labels_and_unknown_provider_time(self):
        rows, metadata, _ = self.export([record("tv", {"provider": "TradingView", "symbol": "SP:SPX", "symbol_name": "标普500",
            "contract": None, "timeframe": "1D", "period": "2026-10-11T04:00:00+08:00",
            "rating": "买入", "rating_raw": "Buy", "moving_average_rating": "强力买入",
            "moving_average_rating_raw": "Strong buy", "indicator_rating": "中性", "indicator_rating_raw": "Neutral",
            "counts": {"summary": {"buy": 15, "neutral": 10, "sell": 1, "total": 26}},
            "oscillators": [{"name": "RSI (14)", "value": 59.93, "value_raw": "59.93", "action": "Neutral"}],
            "moving_averages": [{"name": "Simple Moving Average (50)", "value": 7696.92, "value_raw": "7,696.92", "action": "Buy"}],
            "pivots": [{"name": "P", "classic": 7647.17, "dm": None, "value_raw": {"classic": "7,647.17", "dm": "—"}}]})])
        self.assertTrue(all(row["provider_at"] == "" and row["contract"] == "" for row in rows))
        self.assertEqual(next(row for row in rows if row["field"] == "rating")["value_text"], "Buy")
        self.assertEqual(next(row for row in rows if row["name"] == "Simple Moving Average (50)")["ma_type"], "simple")
        self.assertEqual(next(row for row in rows if row["name"] == "summary.buy")["value"], "15")
        self.assertEqual(next(row for row in rows if row["method"] == "classic")["value"], "7647.17")
        self.assertEqual(metadata["numeric_rows"], 7)

    def test_overview_unknown_cycle_and_ma_type_stay_unknown_with_zero_counts(self):
        rows, _, _ = self.export([
            record("ma", {"timeframe": None, "subtype": "moving_averages", "details": [{"name": "MA5", "value": 1.12, "rating": "买入", "raw": "1.12 买入"}]}),
            record("indicator", {"timeframe": None, "subtype": "indicators", "details": {"最新价": "1.1202", "last_price": 1.1202,
                   "ATR(14)": "少波动", "买入": "0", "卖出": "6", "中性": "3", "指标总结": "强力卖出",
                   "counts": {"买入": 0, "卖出": 6, "中性": 3}}}),
            record("pivot", {"timeframe": None, "subtype": "pivot_points", "details": {"method": None, "levels": {"轴点": 1.12}}})])
        self.assertTrue(all(row["timeframe"] == "" for row in rows))
        ma = next(row for row in rows if row["name"] == "MA5")
        self.assertEqual(ma["ma_type"], "")
        self.assertEqual(next(row for row in rows if row["field"] == "count" and row["name"] == "买入")["value"], "0")
        self.assertEqual(next(row for row in rows if row["name"] == "ATR(14)")["value"], "")
        self.assertEqual(next(row for row in rows if row["name"] == "ATR(14)")["value_text"], "少波动")

    def test_pattern_current_bar_does_not_become_zero_or_publication_time(self):
        rows, _, _ = self.export([record("pattern", {"subtype": "candlestick_patterns", "details": [
            {"pattern": "Morning Star", "direction": "牛市逆转", "stage": "显现型态", "reliability": "高",
             "bars_ago": None, "bars_ago_raw": "当前", "pattern_date": "2026-10-10"}]})])
        current = next(row for row in rows if row["field"] == "bars_ago")
        self.assertEqual((current["value"], current["value_text"]), ("", "当前"))
        self.assertTrue(all(row["provider_at"] == "" for row in rows))

    def test_instrument_indicator_alias_exports_once_without_discarding_distinct_tables(self):
        table = [{"name": "RSI(14)", "value": 63.5, "value_raw": "63.500", "signal": "买入"},
                 {"name": "Williams %R", "value": -14.48, "value_raw": "-14.480", "signal": "中性"}]
        rows, metadata, _ = self.export([record("alias", {"subtype": "indicators",
                                "indicators": table, "details": deepcopy(table)})])
        self.assertEqual(metadata["rows"], 2)
        self.assertEqual(metadata["numeric_rows"], 2)
        self.assertEqual({row["name"] for row in rows}, {"RSI(14)", "Williams %R"})
        rows, metadata, _ = self.export([record("distinct", {"subtype": "indicators",
                 "indicators": table, "details": [{"name": "ADX(14)", "value": 27.0}]})])
        self.assertEqual(metadata["rows"], 3)
        self.assertEqual({row["name"] for row in rows}, {"RSI(14)", "Williams %R", "ADX(14)"})

    def test_text_formula_prefixes_are_literal_but_typed_negative_numbers_remain_numeric(self):
        for payload in ("=1+1", "+SUM(A1)", "-cmd", "@SUM(A1)", "\t=1+1", "\r=1+1", "\n=1+1", " ＝1+1", "\ufeff@SUM(A1)"):
            malicious = record(payload, {"provider": payload, "symbol": payload, "contract": payload,
                "subtype": "indicators", "details": [{"name": payload, "value": -3.5, "value_raw": payload, "signal": payload}]},
                source_id=payload, url=payload, observed_at=payload)
            rows, metadata, _ = self.export([malicious])
            self.assertEqual(rows[0]["value"], "-3.5")
            for key in ("record_id", "source_id", "provider", "symbol", "contract", "name", "value_text", "signal", "source_url", "observed_at"):
                self.assertEqual(rows[0][key], "'" + payload, (payload, key))
            self.assertGreaterEqual(metadata["escaped_cells"], 10)

    def test_deterministic_rows_independent_of_record_and_detail_order(self):
        records = [record("b", {"subtype": "indicators", "details": [{"name": "B", "value": 2}, {"name": "A", "value": 1}]}),
                   record("a", {"subtype": "summary", "rating": "中性"})]
        _, first, content1 = self.export(records)
        changed = deepcopy(list(reversed(records)))
        changed[1]["data"]["details"].reverse()
        _, second, content2 = self.export(changed)
        self.assertEqual(content1, content2)
        self.assertEqual(first["sha256"], second["sha256"])

    def test_nonfinite_and_boolean_numeric_evidence_cannot_silently_write_invalid_values(self):
        for value in (float("nan"), float("inf"), -float("inf"), True):
            with TemporaryDirectory() as folder:
                path = Path(folder) / "existing.csv"
                path.write_text("prior export")
                with self.assertRaises(ValueError):
                    export_technicals({"records": [record("bad", {"subtype": "indicators", "details": [{"name": "RSI", "value": value}]})]}, path)
                self.assertEqual(path.read_text(), "prior export")
        rows, metadata, _ = self.export([])
        self.assertEqual(rows, [])
        self.assertEqual(metadata["rows"], 0)
