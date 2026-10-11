"""Parse the official anonymous free flash list without unlocking content."""
from __future__ import annotations

from urllib.parse import urlsplit
import json
import re


def _nonempty(value):
    """Permission flags sometimes arrive as JSON booleans, numbers or strings."""
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "null"}
    return bool(value)


def _restricted(row, content):
    for item in (row, content):
        if str(item.get("voice_status", "")).strip().lower() == "locked":
            return True
        if any(_nonempty(item.get(key)) for key in (
                "lock", "locked", "is_locked", "exclusive_to", "vip_level", "vip_desc", "vip_title", "is_vip", "permission", "permissions", "paywall")):
            return True
    extras = row.get("extras")
    return isinstance(extras, dict) and _nonempty(extras.get("ad"))


def parse_jin10_public(body, source, observed_at):
    """Return only free textual flashes present in the anonymous public response.

    Item time is the provider's Shanghai publication time. Detail links use the
    publisher's configured public flashDetailBaseUrl, not a guessed API endpoint.
    References to paid annotations are not consumed as free article content.
    """
    # Local import keeps collect's parser registry free of an import cycle.
    from .collect import _record, canonical_url, parse_timestamp, plain

    payload = json.loads(body)
    if not isinstance(payload, dict) or str(payload.get("status")) != "200":
        raise ValueError("Jin10 public list returned an error or non-object response")
    rows = payload.get("data")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Jin10 public list requires a data array of flash objects")
    maximum = int(source.get("max_items", 100))
    if not 1 <= maximum <= 500:
        raise ValueError("Jin10 max_items must be between 1 and 500")
    records, seen = [], set()
    for row in rows[:maximum]:
        content = row.get("data")
        if not isinstance(content, dict):
            raise ValueError("Jin10 flash requires its public data object")
        if _restricted(row, content):
            continue
        identifier = str(row.get("id", ""))
        raw_time = row.get("time")
        if not re.fullmatch(r"\d{1,32}", identifier) or identifier in seen:
            continue
        if not isinstance(raw_time, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", raw_time):
            continue
        stamp = parse_timestamp(raw_time, "Asia/Shanghai")
        if stamp is None:
            continue
        raw_title, raw_content = content.get("title"), content.get("content")
        if raw_title is not None and not isinstance(raw_title, str):
            continue
        if raw_content is not None and not isinstance(raw_content, str):
            continue
        summary = plain(raw_content, 1800)
        title = plain(raw_title, 360)
        if not title and summary:
            headline = re.match(r"^【([^】]+)】", summary)
            title = headline.group(1)[:360] if headline else summary[:180]
        if not title:
            continue
        data = {"publisher": source.get("name", "金十数据"), "region": source.get("region", "全球"),
                "provider_id": identifier, "provider_time_raw": raw_time, "provider_timezone": "Asia/Shanghai",
                "important": row.get("important"), "public_free": True}
        original_source = content.get("source")
        if isinstance(original_source, str) and original_source.strip():
            data["original_source"] = plain(original_source, 200)
        source_link = content.get("source_link")
        if isinstance(source_link, str) and urlsplit(source_link).scheme in {"http", "https"} and urlsplit(source_link).netloc:
            data["original_source_url"] = canonical_url(source_link)
        record = _record(source, "news", title, f"https://flash.jin10.com/detail/{identifier}",
                         observed_at, stamp.isoformat(), summary, data)
        if record:
            seen.add(identifier)
            records.append(record)
    return records
