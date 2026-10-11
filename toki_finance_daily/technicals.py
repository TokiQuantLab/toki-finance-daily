"""Technical snapshots from the public, visibly loaded TradingView DOM.

This parser performs no network requests and reads no embedded application/API
payloads. The supported daily view must identify its symbol and selected tab.
"""
from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser
import re

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_SKIP = {"script", "style", "noscript", "template", "svg"}
_RATINGS = {"strong sell": "强力卖出", "sell": "卖出", "neutral": "中性",
            "buy": "买入", "strong buy": "强力买入"}
_GAUGES = {"Oscillators": ("oscillators", 11), "Moving Averages": ("moving_averages", 15),
           "Summary": ("summary", 26)}


class _Node:
    def __init__(self, tag="", attrs=(), parent=None):
        self.tag, self.attrs, self.parent = tag, dict(attrs), parent
        self.parts = []

    def has_class(self, prefix):
        return any(value.startswith(prefix) for value in self.attrs.get("class", "").split())

    def visible(self):
        style = re.sub(r"\s+", "", self.attrs.get("style", "")).lower()
        return (self.tag not in _SKIP and "hidden" not in self.attrs
                and self.attrs.get("aria-hidden") != "true"
                and "i-hidden" not in self.attrs.get("class", "").split()
                and "display:none" not in style and "visibility:hidden" not in style)

    def walk(self):
        if self.visible():
            yield self
            for part in self.parts:
                if isinstance(part, _Node):
                    yield from part.walk()

    def text(self):
        if not self.visible():
            return ""
        return re.sub(r"\s+", " ", " ".join(
            part.text() if isinstance(part, _Node) else part for part in self.parts)).strip()


class _DOM(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = _Node()

    def handle_starttag(self, tag, attrs):
        child = _Node(tag, attrs, self.current)
        self.current.parts.append(child)
        if tag not in _VOID:
            self.current = child

    def handle_startendtag(self, tag, attrs):
        self.current.parts.append(_Node(tag, attrs, self.current))

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                return
            node = node.parent

    def handle_data(self, value):
        self.current.parts.append(value)


def _ancestor(node, class_prefix):
    while node is not None:
        if node.has_class(class_prefix):
            return node
        node = node.parent
    return None


def _gauge(card):
    statuses = [node for node in card.walk() if node.has_class("status-")]
    if len(statuses) != 1:
        raise ValueError("TradingView technical gauge status is missing or ambiguous")
    active = [node.text() for node in statuses[0].walk()
              if node.attrs.get("data-phase") == "in"]
    raw_rating = active[0] if len(active) == 1 else statuses[0].text()
    if raw_rating.lower() not in _RATINGS:
        raise ValueError("TradingView technical rating is an unhydrated placeholder or unknown label")
    counts = {}
    for wrapper in card.walk():
        if not wrapper.has_class("counterWrapper-"):
            continue
        titles = [node.text() for node in wrapper.walk() if node.has_class("counterTitle-")]
        numbers = [node.text() for node in wrapper.walk() if node.has_class("counterNumber-")]
        if len(titles) != 1 or len(numbers) != 1 or not re.fullmatch(r"\d{1,2}", numbers[0]):
            raise ValueError("TradingView technical counter is missing or malformed")
        key = titles[0].lower()
        if key not in {"sell", "neutral", "buy"} or key in counts:
            raise ValueError("TradingView technical counter labels changed")
        counts[key] = int(numbers[0])
    if set(counts) != {"sell", "neutral", "buy"} or not sum(counts.values()):
        raise ValueError("TradingView technical counters are empty/unhydrated placeholders")
    counts["total"] = sum(counts.values())
    return raw_rating, counts


def _number(raw):
    if raw in {"", "—", "–", "-", "N/A"}:
        return None
    normalized = raw.replace(",", "").replace("−", "-")
    if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", normalized):
        raise ValueError("TradingView technical table contains an unexpected numeric value")
    return float(normalized)


def _tables(root):
    result = {}
    for table in root.walk():
        if table.tag != "table":
            continue
        headers = [node.text() for node in table.walk() if node.tag == "th"]
        rows = [[node.text() for node in row.parts if isinstance(node, _Node) and node.tag == "td"]
                for row in table.walk() if row.tag == "tr"]
        rows = [row for row in rows if row]
        if headers == ["Name", "Value", "Action"]:
            group, title = table.parent, None
            while group is not None and title is None:
                title = next((node.text() for node in group.parts if isinstance(node, _Node)
                              and node.has_class("title-")
                              and node.text() in {"Oscillators", "Moving Averages"}), None)
                group = group.parent
            if title is not None:
                key = _GAUGES[title][0]
                result[key] = [{"name": row[0], "value": _number(row[1]),
                                "value_raw": row[1], "action": row[2]}
                               for row in rows if len(row) == 3 and row[0]]
        elif headers == ["Pivot", "Classic", "Fibonacci", "Camarilla", "Woodie", "DM"]:
            result["pivots"] = []
            for row in rows:
                if len(row) != len(headers) or not row[0]:
                    continue
                values = dict(zip((header.lower() for header in headers[1:]), row[1:]))
                result["pivots"].append({"name": row[0], "value_raw": values,
                                         **{key: _number(value) for key, value in values.items()}})
    return result


def parse_tradingview_technical(body, source, observed_at):
    """Return one daily macro record, or reject incomplete/mismatched DOM.

    ``observed_at`` is the cache's original browser fetch time. The quote's close
    timestamp is not a timestamp for the technical rating and is never reused.
    """
    for key in ("symbol", "name", "market", "timeframe"):
        if not source.get(key):
            raise ValueError(f"TradingView technical source requires {key}")
    if source["timeframe"] != "1D":
        raise ValueError("TradingView technical parser currently requires daily timeframe 1D")
    try:
        stamp = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (TypeError, AttributeError, ValueError) as error:
        raise ValueError("TradingView snapshot requires an ISO observation timestamp") from error
    if stamp.tzinfo is None:
        raise ValueError("TradingView snapshot observation timestamp requires a timezone")
    parser = _DOM()
    parser.feed(body.decode("utf-8-sig") if isinstance(body, bytes) else body)
    nodes = list(parser.root.walk())
    symbols = {node.attrs.get("data-symbol") for node in nodes
               if "js-symbol-header-ticker" in node.attrs.get("class", "").split()}
    if symbols != {source["symbol"]}:
        raise ValueError("TradingView visible page symbol does not match configured symbol")
    selected = [node for node in nodes if node.tag == "button" and node.attrs.get("role") == "tab"
                and node.attrs.get("aria-selected") == "true"]
    if (len(selected) != 1 or selected[0].attrs.get("id") != "1D"
            or selected[0].attrs.get("data-overflow-tooltip-text") != "1 day"):
        raise ValueError("TradingView visible selected timeframe must be 1 day (1D)")
    gauges = {}
    for node in nodes:
        if not node.has_class("title-") or node.text() not in _GAUGES:
            continue
        card = _ancestor(node, "speedometerWrapper-")
        if card is None:
            continue
        key, expected_total = _GAUGES[node.text()]
        if key in gauges:
            raise ValueError("TradingView technical gauges are duplicated")
        raw_rating, counts = _gauge(card)
        if counts["total"] != expected_total:
            raise ValueError("TradingView technical counters are incomplete for the daily snapshot")
        gauges[key] = (raw_rating, counts)
    if set(gauges) != {"summary", "oscillators", "moving_averages"}:
        raise ValueError("TradingView visible technical gauges are missing")
    for key in ("sell", "neutral", "buy"):
        if gauges["summary"][1][key] != gauges["oscillators"][1][key] + gauges["moving_averages"][1][key]:
            raise ValueError("TradingView technical counters disagree between summary and components")
    ratings = {key: _RATINGS[value[0].lower()] for key, value in gauges.items()}
    symbol_name = source.get("symbol_name") or source["name"]
    data = {"topic": "technical", "label": f"{symbol_name} · 日线", "symbol": source["symbol"],
            "symbol_name": symbol_name, "market": source["market"], "provider": "TradingView",
            "timeframe": "1D", "timeframe_label": "日线", "value": ratings["summary"], "unit": "",
            "rating": ratings["summary"], "rating_raw": gauges["summary"][0],
            "moving_average_rating": ratings["moving_averages"],
            "moving_average_rating_raw": gauges["moving_averages"][0],
            "indicator_rating": ratings["oscillators"], "indicator_rating_raw": gauges["oscillators"][0],
            "counts": {key: value[1] for key, value in gauges.items()},
            "period": observed_at, "period_label": "页面观测（供应商未标技术评级更新时间）",
            "provider_at": None, "availability_rule": "observed_snapshot",
            "series_id": f"tradingview:{source['symbol']}:1D",
            "method_note": "技术评级来自价格与技术指标，非文本舆情；原页面未标技术评级更新时间，仅记录本次观测。",
            **_tables(parser.root)}
    # Kept local so collector registry imports cannot create a module cycle.
    from .collect import _record
    record = _record(source, "macro", f"{symbol_name} 日线技术评级", source["url"], observed_at,
                     summary=f"日线综合{ratings['summary']}；均线{ratings['moving_averages']}，震荡指标{ratings['oscillators']}。", data=data)
    if record is None:
        raise ValueError("TradingView technical source URL is invalid")
    return [record]
