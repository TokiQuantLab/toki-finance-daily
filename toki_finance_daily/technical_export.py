"""Deterministic long-form CSV for collected technical evidence, without scoring."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from tempfile import NamedTemporaryFile
import csv
import hashlib
import math
import re
import unicodedata

COLUMNS = (
    "record_id", "source_id", "provider", "symbol", "symbol_name", "instrument_header", "contract",
    "timeframe", "subtype", "field", "name", "ma_type", "method", "value",
    "value_text", "signal", "unit", "quote_currency", "provider_at", "observed_at", "source_url",
)


def _scalar(value):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        raise ValueError("Technical CSV cells require scalar source fields")
    return str(value)


def _safe_text(value):
    """Keep text literal in spreadsheet readers; numeric cells bypass this."""
    text = _scalar(value)
    probe = unicodedata.normalize("NFKC", text)
    leading_control = False
    while probe and (probe[0].isspace() or unicodedata.category(probe[0]) == "Cf"):
        leading_control |= probe[0] in "\t\r\n"
        probe = probe[1:]
    if leading_control or probe.startswith(("=", "+", "-", "@", "−")):
        return "'" + text, True
    return text, False


def _numeric(value):
    if isinstance(value, bool):
        raise ValueError("Boolean technical values must not become numeric CSV values")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Technical CSV numeric values must be finite")
        return value
    return ""


def _features(data):
    """Yield observed fields from the overview, instrument and TradingView shapes."""
    subtype = data.get("subtype") or "summary"

    def feature(field, name, value=None, raw=None, signal=None, *, kind=None, ma_type=None, method=None, unit=None):
        if value is None and raw in (None, "") and signal in (None, ""):
            return None
        return {"subtype": kind or subtype, "field": field, "name": name,
                "ma_type": ma_type, "method": method, "value": _numeric(value),
                "value_text": raw if raw is not None else value, "signal": signal,
                "unit": data.get("unit") if unit is None else unit}

    def rating(key, name):
        value = data.get(key)
        if key == "rating" and key not in data and subtype == "summary":
            value = data.get("value")
        if value in (None, ""):
            return None
        return feature(key, name, value, data.get(key + "_raw", value), value, kind="summary")

    for key, name in (("rating", "summary"), ("moving_average_rating", "moving_averages"),
                      ("indicator_rating", "indicators")):
        row = rating(key, name)
        if row:
            yield row
    counts = data.get("counts") or {}
    if not isinstance(counts, dict):
        raise ValueError("Technical counts must be an object")
    for group, values in sorted(counts.items()):
        if not isinstance(values, dict):
            raise ValueError("Technical grouped counts must be objects")
        for label, value in sorted(values.items()):
            row = feature("count", f"{group}.{label}", value, signal=label if label != "total" else None)
            if row:
                yield row

    def indicators(items):
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError("Technical indicator rows require real names")
            row = feature("value", item["name"], item.get("value"), item.get("value_raw"),
                          item.get("signal", item.get("action", item.get("rating"))),
                          kind="indicators", unit=item.get("unit", data.get("unit")))
            if row:
                yield row

    def moving_averages(items):
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError("Technical moving averages require real names")
            if "simple" in item or "exponential" in item:
                variants = [(key, item[key]) for key in ("simple", "exponential") if key in item]
            else:
                explicit_type = item.get("ma_type")
                if not explicit_type:
                    match = re.match(r"^(Simple|Exponential) Moving Average \(", str(item["name"]))
                    explicit_type = match.group(1).lower() if match else None
                variants = [(explicit_type, item)]
            for ma_type, details in variants:
                if not isinstance(details, dict):
                    raise ValueError("Technical moving average variants must be objects")
                row = feature("value", item["name"], details.get("value"),
                              details.get("value_raw", details.get("raw")),
                              details.get("signal", details.get("action", details.get("rating"))),
                              kind="moving_averages", ma_type=ma_type,
                              unit=details.get("unit", data.get("unit")))
                if row:
                    yield row

    def pivot_levels(method, values, raw_values=None):
        if not isinstance(values, dict) or (raw_values is not None and not isinstance(raw_values, dict)):
            raise ValueError("Technical pivot levels must be objects")
        for level, value in sorted(values.items()):
            row = feature("level", level, value, (raw_values or {}).get(level),
                          kind="pivot_points", method=method)
            if row:
                yield row

    for key in ("oscillators", "indicators"):
        if key in data:
            if not isinstance(data[key], list):
                raise ValueError("Technical indicator tables must be arrays")
            yield from indicators(data[key])
    if "moving_averages" in data:
        if not isinstance(data["moving_averages"], list):
            raise ValueError("Technical moving average tables must be arrays")
        yield from moving_averages(data["moving_averages"])
    if "pivots" in data:
        if not isinstance(data["pivots"], list):
            raise ValueError("Technical pivot tables must be arrays")
        for item in data["pivots"]:
            if not isinstance(item, dict) or not item.get("name"):
                raise ValueError("Technical pivot rows require real level names")
            raw_values = item.get("value_raw") or {}
            if not isinstance(raw_values, dict):
                raise ValueError("Technical pivot raw values must be an object")
            for method in ("classic", "fibonacci", "camarilla", "woodie", "dm"):
                if method in item:
                    row = feature("level", item["name"], item[method], raw_values.get(method),
                                  kind="pivot_points", method=method)
                    if row:
                        yield row

    details = data.get("details")
    if subtype == "moving_averages" and details is not None:
        if not isinstance(details, list):
            raise ValueError("Technical moving average details must be an array")
        yield from moving_averages(details)
    elif subtype == "pivot_points" and details is not None:
        if isinstance(details, dict):
            yield from pivot_levels(details.get("method"), details.get("levels"), details.get("raw_levels"))
        elif isinstance(details, list):
            for item in details:
                if not isinstance(item, dict):
                    raise ValueError("Technical pivot method details must be objects")
                yield from pivot_levels(item.get("method"), item.get("levels"), item.get("raw_levels"))
        else:
            raise ValueError("Technical pivot details have an unsupported shape")
    elif subtype == "indicators" and details is not None:
        if isinstance(details, list):
            # Instrument collection also exposes this table as ``indicators``
            # for the renderer. Export its actual evidence only once.
            if not any(details == data.get(key) for key in ("indicators", "oscillators")):
                yield from indicators(details)
        elif isinstance(details, dict):
            summary = details.get("指标总结")
            if summary not in (None, ""):
                yield feature("rating", "indicators", summary, signal=summary)
            for label, value in sorted((details.get("counts") or {}).items()):
                yield feature("count", label, value, details.get(label), label)
            row = feature("value", "最新价", details.get("last_price"), details.get("最新价"))
            if row:
                yield row
            row = feature("classification", "ATR(14)", raw=details.get("ATR(14)"))
            if row:
                yield row
        else:
            raise ValueError("Technical indicator details have an unsupported shape")
    elif subtype == "candlestick_patterns" and details is not None:
        if not isinstance(details, list):
            raise ValueError("Technical candlestick details must be an array")
        for item in details:
            if not isinstance(item, dict) or not item.get("pattern"):
                raise ValueError("Technical candlestick rows require real pattern names")
            name = item["pattern"]
            yield feature("pattern", name, raw=name, signal=item.get("direction"))
            for key in ("stage", "reliability", "pattern_date"):
                row = feature(key, name, raw=item.get(key))
                if row:
                    yield row
            row = feature("bars_ago", name, item.get("bars_ago"), item.get("bars_ago_raw"))
            if row:
                yield row
    elif subtype not in {"summary", "indicators", "moving_averages", "pivot_points", "candlestick_patterns"}:
        row = feature("value", data.get("label") or subtype, data.get("value"))
        if row:
            yield row


def export_technicals(dataset, path):
    """Write UTF-8-with-BOM CSV and return counts, columns and its content hash.

    Only ``macro`` records with ``data.topic == 'technical'`` are exported.
    ``value`` contains original typed finite numbers; text and rating labels go
    to ``value_text``/``signal``. Unknown times, periods, contracts and MA types
    remain empty. Provider timestamps never inherit the observation timestamp.
    """
    if not isinstance(dataset, dict) or not isinstance(dataset.get("records"), list):
        raise ValueError("Technical CSV export requires a dataset records array")
    rows, record_count = [], 0
    for record in dataset["records"]:
        if not isinstance(record, dict):
            raise ValueError("Dataset records must be objects")
        data = record.get("data") or {}
        if record.get("kind") != "macro" or not isinstance(data, dict) or data.get("topic") != "technical":
            continue
        record_count += 1
        base = {"record_id": record.get("id"), "source_id": record.get("source_id"),
                "provider": data.get("provider"), "symbol": data.get("symbol"),
                "symbol_name": data.get("symbol_name") or data.get("instrument_header") or data.get("instrument_name"),
                "instrument_header": data.get("instrument_header"),
                "contract": data.get("contract"), "timeframe": data.get("timeframe"),
                "quote_currency": data.get("quote_currency"),
                "provider_at": data.get("provider_at"), "observed_at": record.get("observed_at"),
                "source_url": record.get("url")}
        for feature in _features(data):
            if feature is not None:
                rows.append({**base, **feature})
    # Sorting includes every cell to make output independent of dataset order.
    rows.sort(key=lambda row: tuple(_scalar(row.get(column)) for column in COLUMNS))
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    escaped, numeric = 0, 0
    temporary = None
    try:
        with NamedTemporaryFile(mode="w", encoding="utf-8-sig", newline="", dir=target.parent,
                                prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            # CRLF ensures both CR and LF inside source cells are quoted by the
            # CSV writer, so either kind of embedded newline stays in one cell.
            writer = csv.DictWriter(stream, fieldnames=COLUMNS, lineterminator="\r\n")
            writer.writeheader()
            for row in rows:
                safe = {}
                for column in COLUMNS:
                    value = row.get(column)
                    if column == "value" and isinstance(value, (int, float)) and not isinstance(value, bool):
                        safe[column] = value
                        numeric += 1
                    else:
                        safe[column], changed = _safe_text(value)
                        escaped += changed
                writer.writerow(safe)
        temporary.replace(target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return {"path": str(target), "records": record_count, "rows": len(rows),
            "numeric_rows": numeric, "columns": list(COLUMNS), "escaped_cells": escaped,
            "rows_by_source": dict(sorted(Counter(_scalar(row.get("source_id")) for row in rows).items())),
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}
