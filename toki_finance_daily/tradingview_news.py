"""Read explicitly free headline lists embedded in TradingView's public page."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
import json
import math
import re
from urllib.parse import unquote, urlsplit

_WIDGETS = ("news_markets", "news_corp_activity", "top_providers")


class _InitData(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active = False
        self.parts, self.documents = [], []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            self.active = dict(attrs).get("type") == "application/prs.init-data+json"
            self.parts = []

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.documents.append("".join(self.parts))
            self.active = False
            self.parts = []


def _rows(body):
    page = _InitData()
    page.feed(body.decode("utf-8-sig") if isinstance(body, bytes) else body)
    found, rows = False, []
    for document in page.documents:
        try:
            payload = json.loads(document)
        except ValueError as error:
            raise ValueError("TradingView init data is not valid JSON") from error
        if not isinstance(payload, dict):
            raise ValueError("TradingView init data must be an object")
        # The page's root component key is dynamic. Only inspect its immediate
        # widgets and these three observed news columns, never recommendation trees.
        for component in payload.values():
            if not isinstance(component, dict) or not isinstance(component.get("widgets"), dict):
                continue
            widgets = component["widgets"]
            for name in _WIDGETS:
                if name not in widgets:
                    continue
                found = True
                try:
                    items = widgets[name]["data"]["news"]["data"]["items"]
                except (KeyError, TypeError) as error:
                    raise ValueError(f"TradingView {name} news list structure changed") from error
                if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                    raise ValueError(f"TradingView {name} requires an array of news objects")
                rows.extend(items)
    if not found:
        raise ValueError("TradingView public news widgets missing; page may be blocked or structure changed")
    return rows


def _public_url(value):
    if not isinstance(value, str) or not value or any(ord(char) < 32 or char.isspace() for char in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            return None
        _ = parts.port
    except ValueError:
        return None
    return value


def _story_url(path):
    if not isinstance(path, str) or not re.fullmatch(r"/news/[^/\s?#]+/?", path):
        return None
    decoded = unquote(path)
    if (not re.fullmatch(r"/news/[^/\s?#]+/?", decoded) or any(ord(char) < 32 for char in decoded)
            or decoded.rstrip("/").rsplit("/", 1)[-1] in {".", ".."}):
        return None
    return _public_url("https://cn.tradingview.com" + path)


def parse_tradingview_news(body, source, observed_at):
    """Keep free list headlines, actual providers and second-based UTC times.

    The anonymous list has no article body. Permission-bearing items remain
    excluded even when their title or preview is visible and paywall is false.
    """
    from .collect import _record, canonical_url, parse_timestamp, plain

    try:
        observation = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        if observation.tzinfo is None or observation.utcoffset() is None:
            raise ValueError()
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("TradingView observed_at must include an ISO timezone") from error
    maximum = source.get("max_items", 100)
    if type(maximum) is not int or not 1 <= maximum <= 500:
        raise ValueError("TradingView max_items must be an integer between 1 and 500")
    records, seen = [], set()
    for row in _rows(body):
        if row.get("paywall") is not False or "permission" in row:
            continue
        identifier, title, published = row.get("id"), row.get("title"), row.get("published")
        provider = row.get("provider")
        if (not isinstance(identifier, str) or not identifier.strip() or identifier in seen
                or not isinstance(title, str) or not plain(title)
                or isinstance(published, bool) or not isinstance(published, (int, float)) or not math.isfinite(published)
                or not isinstance(provider, dict) or not isinstance(provider.get("id"), str) or not provider["id"].strip()
                or not isinstance(provider.get("name"), str) or not plain(provider["name"])):
            continue
        stamp, url = parse_timestamp(published), _story_url(row.get("storyPath"))
        link = row.get("link")
        if stamp is None or url is None or (link is not None and not _public_url(link)):
            continue
        metadata = {
            "publisher": source["name"], "region": source.get("region", "全球"),
            "source_record_id": identifier, "storyPath": row["storyPath"], "published": published,
            "original_source": plain(provider["name"], 200),
            "provider": {"id": provider["id"], "name": provider["name"]},
            "aggregator": "TradingView", "content_scope": "headline", "public_free": True, "paywall": False,
        }
        if _public_url(provider.get("url")):
            metadata["provider"]["url"] = provider["url"]
        if link:
            metadata.update(link=link, original_source_url=canonical_url(link))
        for key in ("relatedSymbols", "urgency", "is_flash"):
            if key in row:
                metadata[key] = deepcopy(row[key])
        record = _record(source, "news", title, url, observed_at, stamp.isoformat(), "", metadata)
        if record:
            seen.add(identifier)
            records.append(record)
            if len(records) == maximum:
                break
    return records
