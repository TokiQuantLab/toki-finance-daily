"""Regression: partial technical captures must not become publishable."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from toki_finance_daily.technical_coverage import require_technical_coverage, technical_coverage

ROOT = Path(__file__).resolve().parents[1]


def complete_dataset():
    records = []
    for frame in ("30m", "1h", "5h", "1D", "1W", "1M"):
        for subtype in ("summary", "indicators", "moving_averages", "pivot_points"):
            records.append({"id": frame + subtype, "kind": "macro", "source_id": "gold",
                            "observed_at": "2026-10-12T08:00:00+08:00",
                            "data": {"topic": "technical", "symbol": "XAU/USD", "contract": None,
                                     "timeframe": frame, "subtype": subtype, "rating": "买入",
                                     "selected_timeframe": True, "details": [{"value": 40.5}]}})
    return {"mode": "live", "cutoff": "2026-10-12T08:01:00+08:00", "records": records,
            "focus_assets": [{"key": "gold", "label": "黄金", "source_id": "gold"}]}


class CoverageTests(unittest.TestCase):
    def test_complete_private_and_public_bindings_pass(self):
        dataset = complete_dataset()
        self.assertTrue(require_technical_coverage(dataset)["complete"])
        dataset["focus_assets"][0].pop("source_id")
        dataset["focus_assets"][0]["record_ids"] = [r["id"] for r in dataset["records"]]
        for record in dataset["records"]: record.pop("source_id")
        self.assertTrue(require_technical_coverage(dataset)["complete"])

    def test_six_summaries_and_hourly_details_do_not_count_as_complete(self):
        dataset = complete_dataset()
        dataset["records"] = [r for r in dataset["records"] if r["data"]["subtype"] == "summary" or r["data"]["timeframe"] == "1h"]
        with self.assertRaisesRegex(ValueError, "1D/indicators"): require_technical_coverage(dataset)
        dataset["records"] = []
        self.assertFalse(technical_coverage(dataset)["complete"])
        dataset["mode"] = "demo"
        self.assertFalse(require_technical_coverage(dataset)["complete"])

    def test_empty_numbers_wrong_binding_mixed_symbol_duplicates_and_bad_clocks_fail(self):
        changes = [lambda d: d["records"][1]["data"].update(details=[{"value": None}]),
                   lambda d: d["records"][1]["data"].update(details=[{"value": float("nan")}]),
                   lambda d: d["records"][1].update(source_id="other"),
                   lambda d: d["records"][1]["data"].update(symbol="BTC"),
                   lambda d: d["records"][1].update(observed_at="2026-10-12T08:02:00+08:00"),
                   lambda d: d["records"][1].update(observed_at="2026-10-12T08:00:00"),
                   lambda d: d["records"][0]["data"].update(selected_timeframe=False),
                   lambda d: d["records"].append(deepcopy(d["records"][0]))]
        for change in changes:
            dataset = complete_dataset(); change(dataset)
            with self.subTest(change=change), self.assertRaises(ValueError): require_technical_coverage(dataset)

    def test_finish_and_pages_reject_incomplete_live_before_output_changes(self):
        for name in ("finish", "pages"):
            spec = importlib.util.spec_from_file_location("coverage_" + name, ROOT / f"skills/toki-finance-daily/scripts/{name}.py")
            module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
            dataset = complete_dataset(); dataset["records"] = []
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary); site = root / "site"; site.mkdir()
                (site / "index.html").write_text("previous delivery"); before = (site / "index.html").read_bytes()
                if name == "finish":
                    private = root / "private"; private.mkdir()
                    (private / "dataset.json").write_text("{}"); (private / "brief.json").write_text("{}")
                    with patch.object(module, "_inputs", return_value=(dataset, {})), patch.object(module.cli, "render") as render:
                        with self.assertRaises(ValueError): module.finish(private / "dataset.json", private / "brief.json", private, site)
                        render.assert_not_called()
                else:
                    dataset.update(schema_version=1, date="2026-10-12")
                    for filename in module.READER_FILES:
                        if filename != "index.html": (site / filename).write_text("reader")
                    (site / "public-data.json").write_text(json.dumps(dataset))
                    with self.assertRaisesRegex(module.PagesError, "技术覆盖"): module.prepare(site, root / "staging", "2026-10-12", "https://example.com/")
                    self.assertFalse((root / "staging").exists())
                self.assertEqual((site / "index.html").read_bytes(), before)
