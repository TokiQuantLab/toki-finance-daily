"""Turn documented Jin10 MCP result objects into bounded evidence records.

The transport owns authentication and stores the complete tool response. This
module does not access the network or retain tokens, cursors or full article
objects on every record.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
import json
import math
import re

from .jin10 import _restricted

_TOOLS = {"list_flash", "list_news", "list_calendar", "get_quote"}
_ZONE = ZoneInfo("Asia/Shanghai")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_CLOCK = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}"
    r"(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})?\Z")
_CALENDAR_REGIONS = tuple(sorted((
    "中国台湾", "中国香港", "澳大利亚", "新西兰", "加拿大", "新加坡", "意大利", "西班牙",
    "欧元区", "美国", "中国", "日本", "英国", "德国", "法国", "瑞士", "印度", "韩国",
    "巴西", "南非", "俄罗斯", "瑞典", "挪威", "土耳其", "墨西哥",
), key=len, reverse=True))


def _time(value, *, required=False):
    """Return a true provider instant or date, together with its precision.

    Jin10's unzoned clock strings are Shanghai time. Only explicit ten-digit
    epoch seconds or thirteen-digit epoch milliseconds are accepted as numeric
    timestamps; ambiguous numeric units are rejected.
    """
    if value is None or value == "":
        if required:
            raise ValueError("Jin10 calendar requires a real pub_time")
        return None, "unknown"
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("Jin10 provider time must be a date, clock string or epoch")
    raw = str(value).strip()
    if _DATE.fullmatch(raw):
        try:
            return date.fromisoformat(raw).isoformat(), "date"
        except ValueError as error:
            raise ValueError("Jin10 provider date is invalid") from error
    epoch = re.fullmatch(r"(?:\d{10}(?:\.\d{1,6})?|\d{13})", raw)
    if epoch:
        number = float(raw)
        if not math.isfinite(number):
            raise ValueError("Jin10 provider epoch is not finite")
        if len(raw) == 13 and "." not in raw:
            number /= 1000
        try:
            return datetime.fromtimestamp(number, timezone.utc).astimezone(_ZONE).isoformat(), "second"
        except (ValueError, OverflowError, OSError) as error:
            raise ValueError("Jin10 provider epoch is invalid") from error
    if not isinstance(value, str) or not _CLOCK.fullmatch(raw):
        raise ValueError("Jin10 provider time has an unsupported or ambiguous format")
    try:
        instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("Jin10 provider clock is invalid") from error
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=_ZONE)
    precision = "second" if re.search(r"\d{2}:\d{2}:\d{2}", raw) else "minute"
    return instant.astimezone(_ZONE).isoformat(), precision


def _url(value):
    if not isinstance(value, str):
        raise ValueError("Jin10 evidence URL must be a string")
    parts = urlsplit(value)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Jin10 evidence URL must be a public HTTP(S) URL")
    return value


def _text(item, key, *, required=False):
    from .collect import plain

    value = item.get(key)
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValueError(f"Jin10 {key} must be textual")
    text = plain(value, 1800 if key != "title" else 360)
    if required and not text:
        raise ValueError("Jin10 MCP item requires its real nonempty title")
    return text


def _identifier(row):
    value = row.get("id")
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError("Jin10 provider id must be a string or integer")
    identifier = str(value).strip()
    if not identifier or len(identifier) > 200:
        raise ValueError("Jin10 provider id is empty or excessive")
    return identifier


def _calendar_values(row, data):
    """Preserve optional scalar facts, including zero and explicit null."""
    for key in ("star", "previous", "consensus", "actual", "revised", "affect_txt"):
        if key not in row:
            continue
        value = row[key]
        if isinstance(value, (dict, list)) or not isinstance(value, (str, int, float, bool, type(None))):
            raise ValueError(f"Jin10 calendar {key} must be a scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"Jin10 calendar {key} must be finite")
        if isinstance(value, str) and len(value) > 2000:
            raise ValueError(f"Jin10 calendar {key} is unexpectedly large")
        data[key] = value


def _quote_record(row, source, observed_at):
    from .collect import _record

    if not isinstance(row, dict):
        raise ValueError("Jin10 quote requires a data object")
    code = row.get("code")
    if not isinstance(code, str) or code != source.get("mcp_arguments", {}).get("code"):
        raise ValueError("Jin10 quote code must match the requested instrument")
    name = _text(row, "name", required=True)
    instant, precision = _time(row.get("time"), required=True)
    values = {}
    for key in ("open", "close", "high", "low", "volume", "ups_price", "ups_percent"):
        if key not in row or row[key] is None:
            continue
        value = row[key]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ValueError("Jin10 quote numeric fields must be finite numeric values")
        raw = str(value).strip()
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", raw) or not math.isfinite(float(raw)):
            raise ValueError("Jin10 quote numeric field is not a finite number")
        values[key] = value
    if "close" not in values or float(values["close"]) <= 0:
        raise ValueError("Jin10 quote requires a positive last price")
    facts = {"topic": "market", "label": name, "value": values["close"],
             "unit": source.get("unit", ""), "series_id": "JIN10_QUOTE:" + code,
             "symbol": code, "symbol_name": name, "provider": "金十数据",
             "mcp_tool": "get_quote", "provider_time_raw": row["time"],
             "provider_timezone": "Asia/Shanghai", "time_precision": precision,
             "period": instant, "provider_at": instant if precision != "date" else None,
             "availability_rule": "observed_snapshot",
             **values}
    return _record(source, "macro", name + "报价", source.get("homepage", source["url"]),
                   observed_at, data=facts)


def parse_jin10_mcp(body, source, observed_at):
    """Parse ``{tool, result: structuredContent}``, retaining source precision.

    News/flash tools require ``result.data.items``. Calendars require a
    ``result.data`` array. Empty arrays are valid; an unfamiliar structure raises
    rather than presenting a successful empty collection. Flash's documented
    legacy ``data`` content object is accepted without inventing missing fields.
    """
    from .collect import _record

    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("tool") not in _TOOLS:
        raise ValueError("Jin10 MCP wrapper requires a supported tool")
    tool = payload["tool"]
    if source.get("mcp_tool") and source["mcp_tool"] != tool:
        raise ValueError("Jin10 MCP result tool does not match its configured source")
    result = payload.get("result")
    if (not isinstance(result, dict) or str(result.get("status")) != "200"
            or result.get("isError") is True or result.get("success") is False or result.get("error")):
        raise ValueError("Jin10 MCP tool returned an error or non-object structured result")
    data = result.get("data")
    if tool == "get_quote":
        return [_quote_record(data, source, observed_at)]
    rows = data if tool == "list_calendar" else data.get("items") if isinstance(data, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Jin10 MCP tool data has an unsupported item structure")
    maximum = int(source.get("max_items", 500 if tool == "list_calendar" else 100))
    if not 1 <= maximum <= 500:
        raise ValueError("Jin10 MCP max_items must be between 1 and 500")
    endpoint = _url(source["url"])
    records, seen = [], set()
    for row in rows[:maximum]:
        content = row
        if tool == "list_flash" and "data" in row:
            if not isinstance(row["data"], dict):
                raise ValueError("Jin10 legacy flash data must be an object")
            content = row["data"]
        if _restricted(row, content):
            continue
        introduction, article = _text(content, "introduction"), _text(content, "content")
        title = _text(content, "title", required=tool != "list_flash")
        title_origin = None
        if not title and tool == "list_flash":
            if not article:
                raise ValueError("Jin10 flash requires its real title or textual content")
            headline = re.match(r"^【([^】]+)】", article)
            title = headline.group(1)[:180] if headline else re.split(r"[。！？]", article, maxsplit=1)[0][:180]
            if not title:
                raise ValueError("Jin10 flash content cannot provide a real title excerpt")
            title_origin = "content_excerpt"
        identifier = _identifier(row)
        facts = {"publisher": source.get("name", "金十数据"), "mcp_tool": tool,
                 "provider_timezone": "Asia/Shanghai"}
        if identifier is not None:
            facts["provider_id"] = identifier
        if title_origin:
            facts["title_origin"] = title_origin
        if source.get("region"):
            facts["region"] = source["region"]
        raw_url = row.get("url")
        if raw_url not in (None, ""):
            evidence_url = _url(raw_url)
        elif tool == "list_flash":
            if identifier is None or not re.fullmatch(r"\d{1,32}", identifier):
                raise ValueError("Jin10 flash requires its real URL or numeric detail id")
            evidence_url = f"https://flash.jin10.com/detail/{identifier}"
        elif tool == "list_calendar":
            evidence_url = _url(source.get("homepage") or endpoint)
        else:
            evidence_url = endpoint
        if tool == "list_calendar":
            event_at, precision = _time(row.get("pub_time"), required=True)
            facts.update(event_at=event_at, time_precision=precision, provider_time_raw=row["pub_time"])
            region = next((name for name in _CALENDAR_REGIONS if title.startswith(name)), None)
            if region is not None:
                facts.update(region=region, region_origin="title_prefix")
            _calendar_values(row, facts)
            record = _record(source, "calendar", title, evidence_url, observed_at, data=facts)
        else:
            if "time" not in row:
                raise ValueError("Jin10 MCP news/flash requires its documented time field")
            published, precision = _time(row["time"])
            facts["provider_time_raw"] = row["time"]
            if precision == "date":
                facts.update(published_date=published, date_precision="day", background_only=True)
                published = None
            elif published is None:
                facts.update(date_precision="unknown", background_only=True)
            else:
                facts["time_precision"] = precision
            record = _record(source, "news", title, evidence_url, observed_at,
                             published, introduction or article, facts)
        if record is None:
            raise ValueError("Jin10 MCP evidence could not be represented as a record")
        if record["id"] not in seen:
            seen.add(record["id"])
            records.append(record)
    return records
