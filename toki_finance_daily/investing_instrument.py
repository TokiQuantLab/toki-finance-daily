"""Visible Investing instrument technical snapshots, without requests or scripts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import re

from .technicals import _DOM, _Node

_FRAMES = {"30m": ("30m", "30分钟"), "1h": ("1h", "1小时"), "5h": ("5h", "5小时"),
           "1d": ("1D", "日线"), "1w": ("1W", "周线"), "1mo": ("1M", "月线")}
_RATINGS = {"强力买入": "强力买入", "买入": "买入", "中性": "中性", "卖出": "卖出", "强力卖出": "强力卖出",
            "strong buy": "强力买入", "buy": "买入", "neutral": "中性", "sell": "卖出", "strong sell": "强力卖出"}
_LABELS = {"summary": "技术摘要", "indicators": "技术指标", "moving_averages": "移动平均", "pivot_points": "枢轴点"}


def _rows(table):
    return [[cell.text() for cell in row.parts if isinstance(cell, _Node) and cell.tag in {"th", "td"} and cell.visible()]
            for row in table.walk() if row.tag == "tr"]


def _content_node(node):
    while node is not None:
        if node.tag in {"nav", "aside", "footer", "header"}:
            return False
        node = node.parent
    return True


def _rating(node):
    ratings = {_RATINGS[part.text().lower()] for part in node.walk() if part.text().lower() in _RATINGS}
    return next(iter(ratings)) if len(ratings) == 1 else None


def _locked(tab):
    return ("解锁" in tab.text() or "unlock" in tab.text().lower()
            or any("/pro/" in node.attrs.get("href", "") or node.attrs.get("href", "").endswith("#lock")
                   for node in tab.walk()))


def _section(table):
    node = table.parent
    while node is not None:
        if any(isinstance(part, _Node) and part.tag in {"h2", "h3"} for part in node.parts):
            return node
        node = node.parent
    return table


def _asof(section, date_order):
    """Read only the owning table's explicit GMT timestamp, never a sibling's."""
    matches = set(re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2})\s+GMT\b", section.text()))
    if len(matches) != 1:
        return None, ""
    raw = next(iter(matches))
    try:
        parsed = datetime.strptime(raw, "%d/%m/%Y %H:%M" if date_order == "DMY" else "%m/%d/%Y %H:%M")
    except ValueError:
        return None, raw + " GMT"
    return parsed.replace(tzinfo=timezone.utc).isoformat(), raw + " GMT"


def _table_summary(section):
    match = re.search(r"(?:总结|Summary)\s*:\s*(强力买入|强力卖出|买入|卖出|中性|Strong Buy|Strong Sell|Buy|Sell|Neutral)(?:\s|$)", section.text(), re.I)
    return _RATINGS.get(match.group(1).lower()) if match else None


def parse_investing_instrument_technical(body, source, observed_at):
    """Return free timeframe summaries and details for the visibly selected tab.

    ``source.instrument_name`` or ``source.symbol`` verifies the visible heading.
    Its actual contract is retained, so a future is not relabeled as spot.
    Chinese pages use DMY dates; an explicitly configured MDY page is supported.
    """
    from .collect import _investing_number, _record, canonical_url
    from .browser_pages import is_investing_timeframe_bundle, parse_investing_timeframe_bundle

    if is_investing_timeframe_bundle(body):
        encoded = body.encode("utf-8") if isinstance(body, str) else body
        return parse_investing_timeframe_bundle(encoded, source, observed_at)

    try:
        captured = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Investing instrument snapshot requires an ISO observation timestamp") from error
    if captured.tzinfo is None:
        raise ValueError("Investing instrument observation timestamp requires a timezone")
    configured_symbol = str(source.get("symbol") or "").strip()
    configured_name = str(source.get("instrument_name") or "").strip()
    if not configured_symbol and not configured_name:
        raise ValueError("Investing instrument source requires symbol or instrument_name")
    date_order = source.get("date_order", "DMY")
    if date_order not in {"DMY", "MDY"}:
        raise ValueError("Investing instrument date_order must be DMY or MDY")
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    if not isinstance(text, str) or len(text) > 8_000_000:
        raise ValueError("Investing instrument HTML is missing or exceeds parser budget")
    parser = _DOM()
    parser.feed(text)
    nodes = [node for node in parser.root.walk() if _content_node(node)]
    headings = [node.text() for node in nodes if node.tag == "h1"]
    if len(headings) != 1:
        raise ValueError("Investing visible instrument heading is missing or ambiguous")
    header = headings[0]
    if configured_name and not header.startswith(configured_name):
        raise ValueError("Investing visible instrument heading does not match configured instrument_name")
    header_code = re.search(r"\(([A-Za-z0-9][A-Za-z0-9./!_-]{0,31})\)\s*$", header)
    symbol = header_code.group(1) if header_code else ""
    if not symbol and configured_name:
        # Crypto pages name the asset in h1 and put its actual code in the
        # matching technical-analysis h2. Ignore sidebars and unrelated titles.
        technical_codes = set()
        for node in nodes:
            heading = node.text()
            if (node.tag == "h2" and configured_name.casefold() in heading.casefold()
                    and ("技术分析" in heading or "technical analysis" in heading.lower())):
                technical_codes.update(re.findall(r"\(([A-Za-z0-9][A-Za-z0-9./!_-]{0,31})\)", heading))
        if len(technical_codes) > 1:
            raise ValueError("Investing visible technical instrument code is ambiguous")
        symbol = next(iter(technical_codes), "")
    if configured_symbol and not (re.search(r"(?<![\w])" + re.escape(configured_symbol) + r"(?![\w])", header, re.I)
                                  or symbol.casefold() == configured_symbol.casefold()):
        raise ValueError("Investing visible instrument heading does not match configured symbol")
    symbol = symbol or configured_symbol
    if not symbol:
        raise ValueError("Investing visible instrument symbol is missing")
    instrument_name = configured_name or header.split(" - ", 1)[0]
    contract = symbol if "期货" in header or re.search(r"\bfutures?\b", header, re.I) else None
    currency_labels = {node.text() for node in nodes if node.attrs.get("data-test") == "currency-in-label"}
    if len(currency_labels) > 1:
        raise ValueError("Investing visible quote currency is ambiguous")
    quote_currency_raw = next(iter(currency_labels), "")
    # A currency selector can also contain the menu's other currency choices.
    # Its leading labelled currency is the current quotation, not that menu.
    currency_match = re.match(r"^(?:货币|Currency)\s*:?\s*([A-Z]{3})(?![A-Za-z])", quote_currency_raw)
    quote_currency = currency_match.group(1) if currency_match else None
    if currency_match:
        quote_currency_raw = currency_match.group(0)
    selectors = [node for node in nodes if node.attrs.get("data-test") == "timeframe-selection"]
    if len(selectors) != 1:
        raise ValueError("Investing visible timeframe selector is missing or ambiguous")
    selector = selectors[0]
    tabs = [node for node in selector.walk() if node.tag == "button" and node.attrs.get("role") == "tab" and _content_node(node)]
    free = [node for node in tabs if node.attrs.get("data-test", "").lower() in _FRAMES and not _locked(node)]
    selected = [node for node in tabs if node.attrs.get("aria-selected") == "true"]
    styled = [node for node in tabs if "!border-inv-blue-500" in node.attrs.get("class", "").split()]
    if not selected:
        selected = styled
    if len(selected) != 1 or selected[0] not in free or styled and styled != selected:
        raise ValueError("Investing selected free timeframe is missing or ambiguous")
    selected_frame = _FRAMES[selected[0].attrs["data-test"].lower()][0]
    scope = selector.parent
    tables = [node for node in scope.walk() if node.tag == "table" and _content_node(node)]
    components, component_counts = {}, {}
    for table in tables:
        for row in _rows(table):
            if len(row) == 4 and row[1].lower() in _RATINGS:
                if row[0].rstrip(" :") in {"移动平均指数", "Moving Averages"}:
                    components["moving_average_rating"] = _RATINGS[row[1].lower()]
                    key = "moving_averages"
                elif row[0].rstrip(" :") in {"技术指标", "Technical Indicators"}:
                    components["indicator_rating"] = _RATINGS[row[1].lower()]
                    key = "indicators"
                else:
                    continue
                counts = {}
                for cell in row[2:]:
                    match = re.fullmatch(r"(买入|卖出|Buy|Sell)\s*:\s*\(\s*(\d+)\s*\)", cell, re.I)
                    if match:
                        counts["buy" if match.group(1).lower() in {"买入", "buy"} else "sell"] = int(match.group(2))
                if counts:
                    component_counts[key] = counts
    identity = hashlib.sha256((canonical_url(source["url"]) + "|" + symbol).encode()).hexdigest()[:12]
    records = []

    def emit(subtype, frame, value, details, provider_at=None, raw_asof="", extras=None):
        label = next(label for canonical, label in _FRAMES.values() if canonical == frame)
        name = source.get("symbol_name") or instrument_name
        if contract and contract not in name:
            name = f"{name} ({contract})"
        data = {"topic": "technical", "provider": "Investing.com", "symbol": symbol, "symbol_name": name,
                "instrument_name": instrument_name, "instrument_header": header, "contract": contract, "market": source.get("market"),
                "quote_currency": quote_currency, "quote_currency_raw": quote_currency_raw or None,
                "subtype": subtype, "label": f"{name} · {label} · {_LABELS[subtype]}", "value": value,
                "unit": "", "period": provider_at or "", "provider_at": provider_at,
                "period_label": "来源更新时间", "time_precision": "minute" if provider_at else "unknown",
                "timeframe": frame, "timeframe_label": label, "series_id": f"INVESTING_INSTRUMENT_{identity}_{frame}_{subtype}",
                "source_page": source["url"], "instrument_url": source["url"], "details": details,
                "availability_note": "公开技术页快照；供应商评级不是新闻后的市场反应或实时交易指令"}
        if not provider_at:
            data["availability_rule"] = "observed_snapshot"
        if raw_asof:
            data["provider_asof_raw"] = raw_asof
        if extras:
            data.update(extras)
        if subtype == "summary":
            data.update(rating=value, moving_average_rating=components.get("moving_average_rating") if frame == selected_frame else None,
                        indicator_rating=components.get("indicator_rating") if frame == selected_frame else None)
            if frame == selected_frame:
                data["counts"] = component_counts
        elif subtype == "indicators":
            data["indicators"] = details
        record = _record(source, "macro", data["label"], source["url"], observed_at,
                         summary=f"{name} {label}：{value}", data=data)
        if record:
            records.append(record)

    seen = set()
    for tab in free:
        frame, _ = _FRAMES[tab.attrs["data-test"].lower()]
        rating = _rating(tab)
        if frame in seen or rating is None:
            raise ValueError("Investing free timeframe rating is missing or ambiguous")
        seen.add(frame)
        emit("summary", frame, rating, {}, extras={"selected_timeframe": frame == selected_frame})
    if not seen:
        raise ValueError("Investing public page has no free technical summary ratings")
    detail_types = set()
    for table in tables:
        rows = [row for row in _rows(table) if row]
        if not rows:
            continue
        headers, values = rows[0], rows[1:201]
        section = _section(table)
        provider_at, raw_asof = _asof(section, date_order)
        # A visible source clock later than the actual observation is not a
        # trustworthy update timestamp. Keep the text without guessing its zone.
        source_clock_ahead = bool(provider_at and datetime.fromisoformat(provider_at) > captured)
        if source_clock_ahead:
            provider_at = None
        subtype, details, value = None, [], None
        if headers in (["名称", "价值", "操作"], ["Name", "Value", "Action"]):
            subtype = "indicators"
            details = [{"name": row[0], "value": _investing_number(row[1]), "value_raw": row[1], "signal": row[2]}
                       for row in values if len(row) == 3 and row[0] and _investing_number(row[1]) is not None]
            value = _table_summary(section)
        elif headers in (["名称", "标准", "移动"], ["Name", "Simple", "Exponential"]):
            subtype = "moving_averages"
            for row in values:
                if len(row) != 3 or not re.fullmatch(r"MA\d+", row[0]):
                    continue
                item = {"name": row[0]}
                for key, raw in zip(("simple", "exponential"), row[1:]):
                    number, _, signal = raw.partition(" ")
                    parsed = _investing_number(number)
                    if parsed is not None:
                        item[key] = {"value": parsed, "value_raw": number, "signal": signal.strip()}
                if len(item) > 1:
                    details.append(item)
            value = _table_summary(section)
        elif headers in (["名称", "S3", "S2", "S1", "枢轴点", "R1", "R2", "R3"], ["Name", "S3", "S2", "S1", "Pivot Points", "R1", "R2", "R3"]):
            subtype = "pivot_points"
            for row in values:
                if len(row) != 8 or not row[0]:
                    continue
                raw = dict(zip(("S3", "S2", "S1", "P", "R1", "R2", "R3"), row[1:]))
                levels = {key: _investing_number(raw_value) for key, raw_value in raw.items()}
                if levels["P"] is not None:
                    details.append({"method": row[0], "levels": levels, "raw_levels": raw})
            value = details[0]["levels"]["P"] if details else None
        if subtype and details:
            if subtype in detail_types:
                raise ValueError("Investing instrument technical detail tables are duplicated")
            detail_types.add(subtype)
            emit(subtype, selected_frame, value, details, provider_at, raw_asof,
                 extras={"source_clock_ahead": True} if source_clock_ahead else None)
    if not detail_types:
        raise ValueError("Investing instrument page has no visible technical detail values")
    return records
