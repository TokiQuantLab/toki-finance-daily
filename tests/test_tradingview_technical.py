"""Loaded browser DOM versus the public page's unhydrated placeholders."""
from unittest import TestCase

from toki_finance_daily.technicals import parse_tradingview_technical

SOURCE = {"id": "tradingview_spx", "name": "TradingView S&P 500 技术评级", "symbol": "SP:SPX",
          "symbol_name": "标普500指数", "market": "美国股指", "timeframe": "1D",
          "url": "https://www.tradingview.com/symbols/SPX/technicals/"}
OBSERVED = "2026-10-11T03:04:53.225813+08:00"


def gauge(title, rating, counts):
    counters = "".join(f'<div class="counterWrapper-test"><span class="counterTitle-test">{label}</span>'
                       f'<span class="counterNumber-test">{count}</span></div>'
                       for label, count in zip(("Sell", "Neutral", "Buy"), counts))
    return (f'<div class="speedometerWrapper-test"><div><div class="title-test">{title}</div>'
            f'<div class="status-test"><span data-phase="out">Sell</span>'
            f'<span data-phase="in">{rating}</span></div></div>{counters}</div>')


def page(loaded=True, selected="1D", symbol="SP:SPX"):
    gauges = (("Oscillators", "Buy", (0, 9, 2)), ("Summary", "Buy", (1, 10, 15)),
              ("Moving Averages", "Strong buy", (1, 1, 13)))
    body = (f'<div class="js-symbol-header-ticker" data-symbol="{symbol}"></div>'
            '<div>At close on Oct 10, 05:29 GMT+8</div>'
            f'<button role="tab" aria-selected="true" id="{selected}" '
            f'data-overflow-tooltip-text="{"1 day" if selected == "1D" else "1 week"}">1 day1 day</button>')
    body += "".join(gauge(title, rating if loaded else "—", counts if loaded else (0, 0, 0))
                    for title, rating, counts in gauges)
    body += ('<div><div class="title-table">Oscillators</div><table>'
             '<tr><th>Name</th><th>Value</th><th>Action</th></tr>'
             '<tr><td>Williams Percent Range (14)</td><td>−14.48</td><td>Neutral</td></tr>'
             '</table></div><div><div class="title-table">Moving Averages</div><table>'
             '<tr><th>Name</th><th>Value</th><th>Action</th></tr>'
             '<tr><td>Simple Moving Average (10)</td><td>7,736.68</td><td>Buy</td></tr></table></div>'
             '<table><tr><th>Pivot</th><th>Classic</th><th>Fibonacci</th><th>Camarilla</th>'
             '<th>Woodie</th><th>DM</th></tr><tr><td>R3</td><td>8,196.01</td><td>7,921.59</td>'
             '<td>7,727.01</td><td>8,078.10</td><td>—</td></tr></table>')
    return body


class TradingViewTechnicalTests(TestCase):
    def test_loaded_daily_preserves_observation_ratings_counts_and_partial_details(self):
        record = parse_tradingview_technical(page().encode(), SOURCE, OBSERVED)[0]
        data = record["data"]
        self.assertEqual((record["kind"], data["topic"], data["symbol"], data["timeframe"]),
                         ("macro", "technical", "SP:SPX", "1D"))
        self.assertEqual((data["value"], data["rating"], data["rating_raw"]), ("买入", "买入", "Buy"))
        self.assertEqual(data["moving_average_rating_raw"], "Strong buy")
        self.assertEqual(data["counts"]["summary"], {"sell": 1, "neutral": 10, "buy": 15, "total": 26})
        self.assertEqual(data["counts"]["oscillators"]["total"], 11)
        self.assertEqual(data["counts"]["moving_averages"]["total"], 15)
        self.assertEqual((data["period"], record["observed_at"]), (OBSERVED, OBSERVED))
        self.assertIsNone(data["provider_at"])
        self.assertIsNone(record["published_at"])
        self.assertEqual(data["availability_rule"], "observed_snapshot")
        self.assertIn("SP:SPX:1D", data["series_id"])
        self.assertEqual(len(data["moving_averages"]), 1)  # No invented missing rows.
        self.assertEqual(data["oscillators"][0]["value"], -14.48)
        self.assertEqual(data["oscillators"][0]["value_raw"], "−14.48")
        self.assertIsNone(data["pivots"][0]["dm"])
        self.assertEqual(data["pivots"][0]["value_raw"]["dm"], "—")
        self.assertEqual(record["id"], parse_tradingview_technical(page(), SOURCE, OBSERVED)[0]["id"])

    def test_selected_daily_placeholder_rejected_even_if_script_contains_real_signals(self):
        body = page(False) + f'<script type="application/json">{page()}</script>'
        with self.assertRaisesRegex(ValueError, "placeholder"):
            parse_tradingview_technical(body, SOURCE, OBSERVED)

    def test_visible_selected_period_must_match_not_just_mention_one_day(self):
        for body in (page(selected="1W"), page().replace('aria-selected="true"', 'aria-selected="false"')):
            with self.subTest(body=body[:100]), self.assertRaisesRegex(ValueError, "selected timeframe"):
                parse_tradingview_technical(body, SOURCE, OBSERVED)

    def test_symbol_and_aware_observation_are_required(self):
        with self.assertRaisesRegex(ValueError, "symbol"):
            parse_tradingview_technical(page(symbol="NASDAQ:NDX"), SOURCE, OBSERVED)
        with self.assertRaisesRegex(ValueError, "timezone"):
            parse_tradingview_technical(page(), SOURCE, "2026-10-11T03:04:53")

    def test_inconsistent_component_counts_are_rejected(self):
        body = page().replace(gauge("Summary", "Buy", (1, 10, 15)), gauge("Summary", "Buy", (0, 11, 15)))
        with self.assertRaisesRegex(ValueError, "disagree"):
            parse_tradingview_technical(body, SOURCE, OBSERVED)
