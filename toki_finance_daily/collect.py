"""Bounded, source-transparent collection. Uses Python's standard library.

No feed content is executed. Parsing and time filters are deterministic; editorial
judgment belongs to the Skill. Failure of one publisher never hides other results.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date as Date, datetime, time as Time, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo
import csv
import hashlib
import io
import json
import math
import re
import shutil
import ssl
import subprocess
import time
import unicodedata
import xml.etree.ElementTree as ET

DISPLAY_ZONE = ZoneInfo("Asia/Shanghai")


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "div", "li"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)


def plain(value, limit=1800):
    parser = _Text()
    parser.feed(str(value or ""))
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()[:limit]


def parse_timestamp(value, zone="UTC"):
    """Parse supported dates without inventing timezone or publication time.

    Date-only news fields are separated by the adapters so a parsed midnight is
    never presented as their actual publication hour. Calendars retain dates.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, (int, float)) or re.fullmatch(r"\d{10}(?:\.\d+)?", str(value)):
        try:
            result = datetime.fromtimestamp(float(value), timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    else:
        value = str(value).strip()
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            try:
                result = parsedate_to_datetime(value)
            except (ValueError, TypeError, OverflowError):
                return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=ZoneInfo(zone))
    return result.astimezone(DISPLAY_ZONE)


def canonical_url(url):
    parts = urlsplit(str(url or ""))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in ("from", "spm", "ref")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, urlencode(query), ""))


def _record(source, kind, title, url, observed_at, published_at=None, summary="", data=None):
    title, url = plain(title, 360), canonical_url(url)
    if not title or urlsplit(url).scheme not in ("http", "https"):
        return None
    data = dict(data or {})
    for key in ("source_type", "topic_scope", "editorial_note"):
        if source.get(key):
            data.setdefault(key, source[key])
    identity = data.get("event_at") or data.get("period") or published_at or data.get("published_date") or "undated"
    digest = hashlib.sha256(f"{kind}|{url}|{title}|{identity}".encode()).hexdigest()[:20]
    return {"id": f"{kind}_{digest}", "kind": kind, "source_id": source["id"],
            "title": title, "url": url, "published_at": published_at,
            "observed_at": observed_at, "summary": plain(summary), "data": data}


def _xml_text(element, names):
    for child in element:
        if child.tag.rsplit("}", 1)[-1] in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(body, source, observed_at):
    root = ET.fromstring(body)
    records = []
    items = [x for x in root.iter() if x.tag.rsplit("}", 1)[-1] in ("item", "entry")]
    for item in items:
        url = _xml_text(item, ("link",))
        if not url:
            links = [x for x in item if x.tag.rsplit("}", 1)[-1] == "link"]
            url = next((x.get("href", "") for x in links if x.get("rel", "alternate") == "alternate"), "")
        raw_date = _xml_text(item, ("pubDate", "published", "date")) or _xml_text(item, ("updated",))
        stamp = parse_timestamp(raw_date, source.get("timezone", "UTC"))
        data = {"publisher": source["name"], "region": source.get("region", "全球")}
        original_source = _xml_text(item, ("source",))
        author = _xml_text(item, ("author", "creator"))
        if original_source:
            data["original_source"] = plain(original_source, 200)
        if author:
            data["author"] = plain(author, 200)
        if raw_date and stamp is None:
            data["unparsed_published_at"] = raw_date
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            data.update(published_date=raw_date, date_precision="day", background_only=True)
            stamp = None
        record = _record(source, "news", _xml_text(item, ("title",)), urljoin(source["url"], url),
                         observed_at, stamp.isoformat() if stamp else None,
                         _xml_text(item, ("description", "summary", "encoded", "content")), data)
        if record:
            records.append(record)
    if root.tag.rsplit("}", 1)[-1] not in ("rss", "feed", "RDF"):
        raise ValueError("Expected RSS/Atom XML; publisher returned another document")
    return records


def parse_json_news(body, source, observed_at):
    payload = json.loads(body)
    adapter = source["adapter"]
    if adapter == "sina":
        result = payload.get("result", {})
        if result.get("status", {}).get("code") != 0:
            raise ValueError("Sina API returned an error")
        rows = result.get("data", [])
    elif adapter == "eastmoney":
        if str(payload.get("code")) != "1" or not isinstance(payload.get("data"), dict):
            raise ValueError(f"Eastmoney API: {payload.get('message', 'invalid response')}")
        rows = payload["data"].get("fastNewsList", [])
    elif adapter == "gov_cn":
        rows = payload
    else:
        raise ValueError(f"Unknown JSON news adapter: {adapter}")
    if not isinstance(rows, list):
        raise ValueError("News API list structure changed")
    records = []
    for row in rows[:int(source.get("max_items", 100))]:
        if adapter == "sina":
            title, url, raw_date = row.get("title"), row.get("url"), row.get("ctime")
            summary = row.get("summary") or row.get("intro") or row.get("wapsummary")
            publisher = row.get("media_name") or source["name"]
        elif adapter == "eastmoney":
            title, raw_date = row.get("title"), row.get("showTime")
            code = str(row.get("code", ""))
            url = f"https://finance.eastmoney.com/a/{code}.html" if code else ""
            summary, publisher = row.get("summary"), source["name"]
        else:
            title, url, raw_date = row.get("TITLE"), row.get("URL"), row.get("DOCRELPUBTIME")
            summary, publisher = row.get("SUB_TITLE"), source["name"]
        stamp = parse_timestamp(raw_date, source.get("timezone", "Asia/Shanghai"))
        data = {"publisher": publisher, "region": source.get("region", "中国")}
        if adapter == "sina" and row.get("media_name"):
            data["original_source"] = plain(row["media_name"], 200)
        if row.get("author"):
            data["author"] = plain(row["author"], 200)
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw_date or "")):
            data.update(published_date=str(raw_date), date_precision="day", background_only=True)
            stamp = None
        if raw_date and stamp is None and "published_date" not in data:
            data["unparsed_published_at"] = str(raw_date)
        record = _record(source, "news", title, url, observed_at,
                         stamp.isoformat() if stamp else None, summary, data)
        if record:
            records.append(record)
    return records


def wallstreetcn_page_data(body):
    """Read public server-rendered JSON; never execute the enclosing script."""
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    marker = re.search(r"\b__SSR__\s*=\s*", text)
    if not marker:
        raise ValueError("WallstreetCN public SSR data missing; page may be a challenge or layout changed")
    try:
        payload, _ = json.JSONDecoder().raw_decode(text[marker.end():])
        data = payload["state"]["default"]["children"]["default"]["data"]
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError("WallstreetCN public SSR JSON structure changed") from error
    if not isinstance(data, dict):
        raise ValueError("WallstreetCN public SSR data must be an object")
    return data


def _wallstreetcn_paid(row):
    return any(row.get(field) in (True, 1, "1", "true") for field in
               ("is_priced", "is_paid", "is_in_vip_privilege")) or bool(row.get("vip_type"))


def parse_wallstreetcn(body, source, observed_at):
    """Parse the official public live/news page, excluding subscription items."""
    data = wallstreetcn_page_data(body)
    if source["adapter"] == "wallstreetcn_live":
        rows = data.get("lives")
    elif source["adapter"] == "wallstreetcn_news":
        items = data.get("items")
        if not isinstance(items, list):
            raise ValueError("WallstreetCN public news list structure changed")
        rows = [item.get("resource") for item in items if isinstance(item, dict)
                and item.get("resource_type") == "article"]
    else:
        raise ValueError("Unknown WallstreetCN adapter")
    if not isinstance(rows, list):
        raise ValueError("WallstreetCN public live list structure changed")
    records = []
    for row in rows[:int(source.get("max_items", 30))]:
        if not isinstance(row, dict) or _wallstreetcn_paid(row):
            continue
        url = row.get("uri", "")
        # Only article/live links rendered by the public site; no app routes,
        # commercial report URLs, calendars, or arbitrary embedded links.
        parsed_url = urlsplit(url)
        if parsed_url.hostname not in {"wallstreetcn.com", "www.wallstreetcn.com"} or not re.fullmatch(r"/(?:livenews|articles)/\d+", parsed_url.path):
            continue
        summary = row.get("content_text") or row.get("content_short") or row.get("content") or ""
        title = row.get("title") or plain(summary, 100)
        raw_date = row.get("display_time")
        stamp = parse_timestamp(raw_date, source.get("timezone", "Asia/Shanghai"))
        metadata = {"publisher": source["name"], "region": source.get("region", "全球"),
                    "source_record_id": str(row.get("id", "")), "public_page_only": True}
        if row.get("source_name"):
            metadata["original_source"] = plain(row["source_name"], 200)
        if row.get("source_uri"):
            metadata["original_source_url"] = str(row["source_uri"])[:1000]
        author = row.get("author")
        if isinstance(author, dict) and author.get("display_name"):
            metadata["author"] = plain(author["display_name"], 200)
        if row.get("reference"):
            metadata["reference"] = plain(row["reference"], 500)
        if raw_date and stamp is None:
            metadata["unparsed_published_at"] = str(raw_date)
        record = _record(source, "news", title, url, observed_at,
                         stamp.isoformat() if stamp else None, summary, metadata)
        if record:
            records.append(record)
    return records


def parse_html_news(body, source, observed_at):
    text = body.decode(source.get("encoding", "utf-8"), errors="replace") if isinstance(body, bytes) else body
    records = []
    for block in re.findall(r"<li\b[^>]*>.*?</li>", text, re.I | re.S):
        stamp_match = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", plain(block))
        if not stamp_match:
            continue
        raw_date = stamp_match.group(1)
        for href, title in re.findall(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', block, re.I | re.S):
            if not re.search(source.get("link_pattern", r"\.html?"), href):
                continue
            record = _record(source, "news", title, urljoin(source["url"], href), observed_at,
                             None, "", {"region": source.get("region", "中国"), "published_date": raw_date,
                             "date_precision": "day", "background_only": True, "publisher": source["name"]})
            if record:
                records.append(record)
    if not records:
        raise ValueError("No dated publication links found; HTML layout may have changed")
    return records


def _ics_value(value):
    return value.replace("\\n", " ").replace("\\N", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def parse_ics(body, source, observed_at):
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    if "BEGIN:VCALENDAR" not in text:
        raise ValueError("Expected iCalendar; publisher returned another document")
    text = re.sub(r"\r?\n[ \t]", "", text)
    records = []
    for event in re.findall(r"BEGIN:VEVENT\r?\n(.*?)END:VEVENT", text, re.S):
        fields, parameters = {}, {}
        for line in event.splitlines():
            if ":" not in line:
                continue
            head, value = line.split(":", 1)
            key = head.split(";", 1)[0]
            fields[key], parameters[key] = value, head
        if fields.get("STATUS") == "CANCELLED" or "DTSTART" not in fields:
            continue
        value = fields["DTSTART"]
        if re.fullmatch(r"\d{8}", value):
            event_at = datetime.strptime(value, "%Y%m%d").date().isoformat()
        else:
            local_zone = re.search(r"TZID=([^;:]+)", parameters["DTSTART"])
            zone = "UTC" if value.endswith("Z") else (local_zone.group(1) if local_zone else source.get("timezone", "UTC"))
            try:
                event_at = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(zone)).astimezone(DISPLAY_ZONE).isoformat()
            except (ValueError, KeyError):
                continue
        stamp = parse_timestamp(fields.get("DTSTAMP"), "UTC") if "-" in fields.get("DTSTAMP", "") else None
        data = {"event_at": event_at, "region": source.get("region", "美国"),
                "importance": None, "actual": None, "forecast": None, "previous": None,
                "schedule_note": "官方发布计划，可能调整", "uid": fields.get("UID")}
        record = _record(source, "calendar", _ics_value(fields.get("SUMMARY", "")),
                         fields.get("URL") or source.get("homepage") or source["url"],
                         observed_at, stamp.isoformat() if stamp else None,
                         _ics_value(fields.get("DESCRIPTION", "")), data)
        if record:
            records.append(record)
    return records


def parse_nbs_calendar(body, source, observed_at):
    text = body.decode("utf-8") if isinstance(body, bytes) else body
    year_match = re.search(r"(20\d{2})年国家统计局主要统计信息发布日程表", plain(text))
    if not year_match:
        raise ValueError("NBS calendar year/header structure changed")
    year = int(year_match.group(1))
    table = re.search(r"<table\b.*?</table>", text, re.I | re.S)
    if not table:
        raise ValueError("NBS calendar table missing")
    rows = re.findall(r"<tr\b.*?</tr>", table.group(), re.I | re.S)
    records = []
    for index, row in enumerate(rows):
        cells = re.findall(r"(<td\b[^>]*>)(.*?)</td>", row, re.I | re.S)
        if len(cells) != 14 or not plain(cells[0][1]).isdigit():
            continue
        title = plain(cells[1][1])
        times = []
        if index + 1 < len(rows):
            times = [plain(value) for value in re.findall(r"<td\b[^>]*>(.*?)</td>", rows[index + 1], re.I | re.S)]
        time_index = 0
        for month, (head, content) in enumerate(cells[2:], 1):
            days = re.findall(r"(?<!\d)(\d{1,2})/[一二三四五六日天]", plain(content))
            rowspan = re.search(r'rowspan=["\'](\d+)', head, re.I)
            clock = ""
            if not rowspan or int(rowspan.group(1)) == 1:
                clock = times[time_index] if time_index < len(times) else ""
                time_index += 1
            for day in days:
                event_date = Date(year, month, int(day))
                if re.fullmatch(r"\d{1,2}[:：]\d{2}", clock):
                    hour, minute = map(int, clock.replace("：", ":").split(":"))
                    event_at = datetime.combine(event_date, Time(hour, minute), DISPLAY_ZONE).isoformat()
                else:
                    event_at = event_date.isoformat()
                record = _record(source, "calendar", title, source["url"], observed_at, data={
                    "event_at": event_at, "region": "中国", "importance": None,
                    "actual": None, "forecast": None, "previous": None,
                    "schedule_note": "国家统计局年度初步发布计划，届时可能调整"})
                if record:
                    records.append(record)
    if not records:
        raise ValueError("NBS calendar contains no recognized scheduled entries")
    return records


class _FomcCalendar(HTMLParser):
    """Extract only the Fed's year headings and labeled meeting cells."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.year, self.stack, self.rows, self.notes = None, [], [], []

    def handle_starttag(self, tag, attrs):
        if tag in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            return
        classes = (dict(attrs).get("class") or "").split()
        field = "header" if tag == "h4" else "note" if tag == "p" else None
        if "fomc-meeting__month" in classes:
            field = "month"
        elif "fomc-meeting__date" in classes:
            field = "days"
        meeting = {"year": self.year, "month": "", "days": ""} if "fomc-meeting" in classes else None
        self.stack.append({"tag": tag, "field": field, "parts": [], "meeting": meeting,
                           "skip": tag in {"script", "style"} or bool(self.stack and self.stack[-1]["skip"])})

    def handle_data(self, value):
        if self.stack and self.stack[-1]["skip"]:
            return
        for frame in self.stack:
            if frame["field"]:
                frame["parts"].append(value)

    def handle_endtag(self, tag):
        index = next((i for i in range(len(self.stack) - 1, -1, -1) if self.stack[i]["tag"] == tag), None)
        if index is None:
            return
        for frame in reversed(self.stack[index:]):
            value = re.sub(r"\s+", " ", "".join(frame["parts"])).strip()
            if frame["field"] == "header":
                match = re.fullmatch(r"(20\d{2})\s+FOMC Meetings", value)
                if match:
                    self.year = int(match.group(1))
                elif "FOMC Meetings" in value:
                    self.year = None
            elif frame["field"] in {"month", "days"}:
                meeting = next((parent["meeting"] for parent in reversed(self.stack) if parent["meeting"] is not None), None)
                if meeting is not None:
                    meeting[frame["field"]] = value
            elif frame["field"] == "note":
                self.notes.append(value)
            if frame["meeting"] is not None:
                self.rows.append(frame["meeting"])
        del self.stack[index:]


def parse_fed_fomc_calendar(body, source, observed_at):
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    parser = _FomcCalendar()
    parser.feed(text)
    month_names = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")
    months = {alias.lower(): number for number, name in enumerate(month_names, 1) for alias in (name, name[:3])}
    months["sept"] = 9
    page_text = plain(text, len(text))
    sep_key = "Meeting associated with a Summary of Economic Projections"
    has_sep_legend = sep_key in page_text
    tentative = next((note for note in parser.notes if "Each meeting date is tentative" in note), "")
    rows = list(parser.rows)
    # This official footnote specifies its own year and dates, independent of
    # the last displayed year panel. It does not specify SEP participation.
    for note in parser.notes:
        match = re.search(r"A two-day meeting is scheduled for ([A-Za-z]+)\s+(\d{1,2})[-–](\d{1,2}),\s*(20\d{2})\.", note)
        if match:
            month, first, last, year = match.groups()
            rows.append({"year": int(year), "month": month, "days": f"{first}-{last}", "footnote": True})
    records = []
    for row in rows:
        if "notation vote" in row["days"].lower():
            continue
        try:
            month_parts = [months[name.strip().rstrip(".").lower()] for name in row["month"].split("/")]
            if not row["year"] or not 1 <= len(month_parts) <= 2:
                raise ValueError("missing year or month")
            days = re.fullmatch(r"(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?\s*(\*)?\s*(?:\(([^)]*)\))?", row["days"])
            if days is None:
                raise ValueError("unrecognized meeting date")
            first, last = int(days.group(1)), int(days.group(2) or days.group(1))
            start_month, end_month = month_parts[0], month_parts[-1]
            if len(month_parts) == 2 and end_month != start_month % 12 + 1:
                raise ValueError("nonconsecutive cross-month meeting")
            end_year = row["year"] + (end_month < start_month)
            start, end = Date(row["year"], start_month, first), Date(end_year, end_month, last)
            if end < start:
                raise ValueError("meeting ends before it starts")
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("Fed FOMC year/month/date structure changed") from error
        has_sep = bool(days.group(3)) if has_sep_legend and not row.get("footnote") else None
        note = "官网仅列美国当地会议日期，未提供时分；按会议末日记录"
        if tentative:
            note += "；会议日期待前次会议确认，可能调整"
        data = {"event_at": end.isoformat(), "region": source.get("region", "美国"),
                "time_precision": "date", "date_precision": "day", "provider_timezone": "America/New_York",
                "meeting_start": start.isoformat(), "meeting_end": end.isoformat(), "has_sep": has_sep,
                "source_year": row["year"], "source_month": row["month"], "source_days": row["days"],
                "importance": None, "actual": None, "forecast": None, "previous": None, "schedule_note": note}
        if tentative:
            data["schedule_note_source"] = tentative
        if row.get("footnote"):
            data["source_footnote"] = True
        record = _record(source, "calendar", "美联储 FOMC 议息会议（末日）", source.get("homepage", source["url"]),
                         observed_at, summary=f"会议跨度 {start.isoformat()} 至 {end.isoformat()}；美国当地日期", data=data)
        if record:
            records.append(record)
    if not records:
        raise ValueError("Fed FOMC calendar contains no recognized meeting dates")
    return records


def parse_ecb(body, source, observed_at):
    root = ET.fromstring(body)
    records = []
    for period in root.iter():
        if not period.get("time"):
            continue
        for item in period:
            currency, value = item.get("currency"), item.get("rate")
            if currency not in source.get("currencies", ["USD", "CNY", "JPY"]):
                continue
            record = _record(source, "macro", f"欧元兑{currency}参考汇率", source.get("homepage", source["url"]),
                             observed_at, data={"label": f"EUR/{currency}", "value": float(value),
                             "unit": f"{currency} / 1 EUR", "period": period.get("time"),
                             "series_id": f"ECB_EXR_EUR_{currency}", "reference_only": True,
                             "availability_note": "观测日不等于精确发布时间；历史截点不构成实时回放"})
            if record:
                records.append(record)
    if not records:
        raise ValueError("ECB XML contains no requested currency observations")
    return records


def parse_fred_csv(body, source, observed_at):
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    reader = csv.DictReader(io.StringIO(text))
    records = []
    series = source["series_id"]
    if not reader.fieldnames or series not in reader.fieldnames:
        raise ValueError("FRED CSV header does not contain requested series")
    date_field = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
    for row in reader:
        try:
            value = float(row[series])
            Date.fromisoformat(row[date_field])
        except (ValueError, KeyError, TypeError):
            continue
        if not math.isfinite(value):
            continue
        record = _record(source, "macro", source["label"], source.get("homepage", source["url"]),
                         observed_at, data={"label": source["label"], "value": value,
                         "unit": source["unit"], "period": row[date_field], "series_id": series})
        if record:
            records.append(record)
    return records


def parse_fear_greed(body, source, observed_at):
    """Validate the provider's Bitcoin daily composite index and true history."""
    payload = json.loads(body)
    if (not isinstance(payload, dict) or not isinstance(payload.get("metadata"), dict)
            or "error" not in payload["metadata"]):
        raise ValueError("Alternative.me API metadata missing")
    if payload["metadata"].get("error") not in (None, ""):
        raise ValueError("Alternative.me API returned an error")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Alternative.me API data must contain observations")
    classifications = {"Extreme Fear": "极度恐惧", "Fear": "恐惧", "Neutral": "中性",
                       "Greed": "贪婪", "Extreme Greed": "极度贪婪"}
    observations, seen = [], set()
    for row in rows[:int(source.get("max_items", 8))]:
        if not isinstance(row, dict):
            raise ValueError("Alternative.me observation must be an object")
        raw_value, raw_timestamp = row.get("value"), row.get("timestamp")
        if isinstance(raw_value, bool) or not re.fullmatch(r"\d{1,3}", str(raw_value)):
            raise ValueError("Alternative.me index must be an integer from 0 to 100")
        value = int(raw_value)
        if not 0 <= value <= 100:
            raise ValueError("Alternative.me index is outside 0 to 100")
        if isinstance(raw_timestamp, bool) or not re.fullmatch(r"\d{10}", str(raw_timestamp)):
            raise ValueError("Alternative.me observation requires a Unix UTC timestamp")
        stamp = parse_timestamp(int(raw_timestamp))
        if stamp is None:
            raise ValueError("Alternative.me observation timestamp is invalid")
        classification = row.get("value_classification")
        if not isinstance(classification, str) or not classification.strip():
            raise ValueError("Alternative.me observation classification missing")
        if str(raw_timestamp) in seen:
            raise ValueError("Alternative.me duplicate observation timestamp")
        seen.add(str(raw_timestamp))
        observations.append({"period": stamp.date().isoformat(), "value": value,
                             "classification": classifications.get(classification, classification),
                             "raw_classification": classification,
                             "provider_at": stamp.astimezone(timezone.utc).isoformat()})
    observations.sort(key=lambda item: item["provider_at"])
    records = []
    for index, observation in enumerate(observations):
        history = observations[:index + 1]
        by_day = {point["period"]: point["value"] for point in history}
        day = Date.fromisoformat(observation["period"])
        change = lambda offset: (observation["value"] - by_day[(day - timedelta(days=offset)).isoformat()]
                                 if (day - timedelta(days=offset)).isoformat() in by_day else None)
        data = {**observation, "topic": "sentiment", "market": "比特币",
                "label": "比特币恐惧与贪婪指数", "unit": "点", "range_min": 0, "range_max": 100,
                "series_id": "ALTERNATIVE_ME_BITCOIN_FNG", "provider": "Alternative.me",
                "method_note": "综合价格波动、成交动量与社交热度等因素",
                "history": history, "change_1d": change(1), "change_7d": change(7),
                "change_note": "根据同一指数历史观测计算，非独立指标",
                "attribution_note": "展示数值时在旁明确标注 Alternative.me",
                "availability_note": "按供应商观测时间显示，每日更新，不是实时行情或纯舆情指数"}
        record = _record(source, "macro", data["label"], source.get("homepage", source["url"]),
                         observed_at, data=data)
        if record:
            records.append(record)
    return records


def parse_cboe_vix(body, source, observed_at):
    """Read official daily CLOSE values; dates do not imply publication times."""
    text = body.decode("utf-8-sig") if isinstance(body, bytes) else body
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames or not {"DATE", "CLOSE"}.issubset(reader.fieldnames):
        raise ValueError("Cboe VIX CSV requires DATE and CLOSE columns")
    observations, seen = [], set()
    for row in reader:
        try:
            period = datetime.strptime(row["DATE"], "%m/%d/%Y").date().isoformat()
            value = float(row["CLOSE"])
        except (ValueError, KeyError, TypeError) as error:
            raise ValueError("Cboe VIX daily close or trading date is invalid") from error
        if not math.isfinite(value) or value <= 0:
            raise ValueError("Cboe VIX daily close must be positive and finite")
        if period in seen:
            raise ValueError("Cboe VIX duplicate trading date")
        seen.add(period)
        observations.append({"period": period, "value": value})
    if not observations:
        raise ValueError("Cboe VIX CSV contains no daily closes")
    observations.sort(key=lambda item: item["period"])
    records = []
    for index, observation in enumerate(observations):
        data = {**observation, "label": "VIX 美股隐含波动率", "unit": "点", "topic": "sentiment",
                "market": "美股 · 标普500", "series_id": "CBOE_VIX_DAILY_CLOSE", "provider": "Cboe",
                "method_note": "标普500期权隐含的未来30天波动预期；日收盘值",
                "date_precision": "day", "period_label": "交易日", "provider_timezone": "America/New_York",
                "availability_rule": "previous_provider_day",
                "availability_note": "仅显示截点在纽约日期之前的交易日收盘；无精确发布时间，不是实时数值或纯舆情指数",
                "history": observations[max(0, index - 7):index + 1],
                "change_previous": round(observation["value"] - observations[index - 1]["value"], 6) if index else None,
                "change_note": "与上一交易日收盘的差值，由同一序列计算"}
        record = _record(source, "macro", data["label"], source.get("homepage", source["url"]),
                         observed_at, data=data)
        if record:
            records.append(record)
    return records


class _InvestingTechnical(HTMLParser):
    """Read visible technical tables, never embedded scripts or hidden templates."""
    _void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip, self.summary_depth, self.date_parts, self.divs = [], 0, [], []
        self.date_capture = False
        self.tables, self.table, self.row, self.cell = [], None, None, None
        self.table_depth, self.in_link = 0, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if self.skip:
            if tag not in self._void:
                self.skip.append(tag)
            return
        hidden_style = re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", attrs.get("style", ""), re.I)
        if tag in {"script", "style", "template", "nav", "header", "footer"} or (
                "displayNone" in attrs.get("class", "").split() or "hidden" in attrs or hidden_style):
            self.skip = [tag]
            return
        if tag == "div":
            self.divs.append(attrs.get("id"))
            if self.summary_depth or attrs.get("id") == "technical_overview_data":
                self.summary_depth += 1
        if tag == "span" and self.summary_depth and "dateHP" in attrs.get("class", "").split():
            self.date_capture = True
        if tag == "table":
            if self.table is not None:
                self.table_depth += 1
                return
            classes = set(attrs.get("class", "").split())
            if classes.intersection({"technicalSummaryTbl", "movingAvgsTbl", "patternTable", "crossRatesTbl"}):
                self.table = {"attrs": attrs, "rows": [], "container_id": next((value for value in reversed(self.divs) if value), None),
                              "summary_asof": " ".join(self.date_parts) if self.summary_depth else ""}
                self.table_depth = 1
        if self.table is None or self.table_depth != 1:
            return
        if tag == "tr":
            self.row = {"attrs": attrs, "cells": []}
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = {"attrs": attrs, "parts": [], "link_parts": [], "tag": tag}
        elif self.cell is not None:
            if tag == "a" and not self.cell.get("href"):
                self.cell.update(href=attrs.get("href", ""), link_title=attrs.get("title", ""))
                self.in_link = True
            elif tag == "br":
                self.cell["parts"].append(" ")

    def handle_data(self, value):
        if self.skip:
            return
        if self.date_capture:
            self.date_parts.append(value.strip())
        if self.cell is not None and self.table_depth == 1:
            self.cell["parts"].append(value)
            if self.in_link:
                self.cell["link_parts"].append(value)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in self.skip:
                self.skip = self.skip[:len(self.skip) - 1 - self.skip[::-1].index(tag)]
            return
        if tag == "span":
            self.date_capture = False
        if tag == "div":
            if self.divs:
                self.divs.pop()
            if self.summary_depth:
                self.summary_depth -= 1
        if self.table is None:
            return
        if tag == "table":
            self.table_depth -= 1
            if not self.table_depth:
                self.tables.append(self.table)
                self.table = self.row = self.cell = None
            return
        if self.table_depth != 1:
            return
        if tag == "a":
            self.in_link = False
        elif tag in {"td", "th"} and self.cell is not None:
            self.cell["text"] = re.sub(r"\s+", " ", " ".join(self.cell.pop("parts"))).strip()
            self.cell["link_text"] = re.sub(r"\s+", " ", " ".join(self.cell.pop("link_parts"))).strip()
            self.row["cells"].append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table["rows"].append(self.row)
            self.row = None


def _investing_number(value):
    raw = str(value).strip()
    if not re.fullmatch(r"[+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)", raw):
        return None
    cleaned = raw.replace(",", "")
    result = float(cleaned)
    return result if math.isfinite(result) else None


def _investing_asof(value):
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})\s*\(GMT([+-])(\d{1,2}):(\d{2})\)", value)
    if not match:
        return None
    year, month, day, hour, minute, sign, offset_hour, offset_minute = match.groups()
    if int(offset_hour) > 23 or int(offset_minute) > 59:
        return None
    try:
        offset = timedelta(hours=int(offset_hour), minutes=int(offset_minute))
        zone = timezone(offset if sign == "+" else -offset)
        return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=zone).isoformat()
    except ValueError:
        return None


def parse_investing_technical(body, source, observed_at):
    """One public overview: ratings plus visible MA/pivot/indicator/pattern details.

    Only the summary table has an explicit update time in the current page.
    Other tables retain unknown provider time and cycle instead of inheriting it.
    """
    parser = _InvestingTechnical()
    parser.feed(body.decode("utf-8") if isinstance(body, bytes) else body)
    records = []
    frames = {"300": "5m", "900": "15m", "1800": "30m", "3600": "1h", "18000": "5h", "86400": "1D", "week": "1W", "month": "1M"}
    subtype_labels = {"summary": "技术摘要", "moving_averages": "移动平均", "pivot_points": "枢轴点",
                      "indicators": "技术指标", "candlestick_patterns": "K线形态"}

    def emit(cell, subtype, value, details, timeframe=None, timeframe_label=None, provider_at=None, raw_asof=""):
        symbol, href = cell.get("link_text"), cell.get("href")
        if not symbol or not href:
            return
        instrument_url = urljoin(source["url"], href)
        identity = hashlib.sha256(canonical_url(instrument_url).encode()).hexdigest()[:12]
        data = {"topic": "technical", "provider": "Investing.com", "symbol": symbol,
                "symbol_name": cell.get("link_title") or symbol, "subtype": subtype,
                "label": f"{symbol} · {timeframe_label or '周期未标注'} · {subtype_labels[subtype]}",
                "value": value, "unit": "", "period": provider_at or "", "provider_at": provider_at,
                "period_label": "来源更新时间", "time_precision": "minute" if provider_at else "unknown",
                "timeframe": timeframe, "timeframe_label": timeframe_label,
                "series_id": f"INVESTING_TECH_{identity}_{timeframe or 'unknown'}_{subtype}",
                "source_page": source["url"], "instrument_url": instrument_url,
                "details": details, "availability_note": "公开页面技术分析快照；评级是供应商指标计算结果，不是实时价格或交易指令"}
        if raw_asof:
            data["provider_asof_raw"] = raw_asof
        if not provider_at:
            data.update(availability_rule="observed_snapshot", availability_note="页面未标注此表更新时间；仅代表抓取时可见快照，不推断指标周期或实时性")
        if subtype == "summary":
            data.update(rating=value, moving_average_rating=details.get("movingAverages"), indicator_rating=details.get("indicators"))
        record = _record(source, "macro", data["label"], source["url"], observed_at,
                         summary=f"{symbol} {timeframe_label or '周期未标注'}：{value}", data=data)
        if record:
            records.append(record)

    for table in parser.tables:
        rows = table["rows"]
        if not rows:
            continue
        headers = rows[0]["cells"]
        names = [cell["text"] for cell in headers]
        classes = table["attrs"].get("class", "").split()
        if "technicalSummaryTbl" in classes:
            periods = [(frames.get(cell["attrs"].get("data-period")), cell["text"]) for cell in headers[2:]]
            groups = {}
            for row in rows[1:]:
                cells, attrs = row["cells"], row["attrs"]
                pair, subtype = attrs.get("data-pairid"), attrs.get("data-row-type")
                if not pair or subtype not in {"movingAverages", "indicators", "summary"}:
                    continue
                group = groups.setdefault(pair, {})
                if cells and cells[0].get("href"):
                    group["symbol"] = cells[0]
                values = [cell["text"] for cell in cells if "js-socket-elem" in cell["attrs"].get("class", "").split()]
                if len(values) == len(periods):
                    group[subtype] = values
            raw_asof = table["summary_asof"].strip()
            provider_at = _investing_asof(raw_asof)
            for group in groups.values():
                if "symbol" not in group or "summary" not in group:
                    continue
                for index, (frame, label) in enumerate(periods):
                    value = group["summary"][index]
                    if not frame or not value or value in {"-", "—"}:
                        continue
                    details = {key: values[index] for key, values in group.items() if key != "symbol"}
                    emit(group["symbol"], "summary", value, details, frame, label, provider_at, raw_asof)
        elif "movingAvgsTbl" in classes and len(names) > 1 and all(re.fullmatch(r"MA\d+", name) for name in names[1:]):
            for row in rows[1:]:
                cells = row["cells"]
                if len(cells) != len(names):
                    continue
                details = []
                for name, cell in zip(names[1:], cells[1:]):
                    match = re.fullmatch(r"([+-]?[\d,.]+)\s+(.+)", cell["text"])
                    number = _investing_number(match.group(1)) if match else None
                    if number is not None:
                        details.append({"name": name, "value": number, "rating": match.group(2), "raw": cell["text"]})
                if details:
                    emit(cells[0], "moving_averages", "; ".join(item["raw"] for item in details), details)
        elif {"S3", "S2", "S1", "轴点", "R1", "R2", "R3"}.issubset(names):
            for row in rows[1:]:
                cells = row["cells"]
                if len(cells) != len(names):
                    continue
                details = {name: _investing_number(cell["text"]) for name, cell in zip(names[1:], cells[1:])}
                if all(value is not None for value in details.values()):
                    emit(cells[0], "pivot_points", details["轴点"],
                         {"method": "经典" if table["container_id"] == "classical" else None, "levels": details})
        elif {"ATR(14)", "买入", "卖出", "中性", "指标总结"}.issubset(names):
            for row in rows[1:]:
                cells = row["cells"]
                if len(cells) != len(names):
                    continue
                details = dict(zip(names[1:], (cell["text"] for cell in cells[1:])))
                details["counts"] = {name: int(details[name]) for name in ("买入", "卖出", "中性") if details[name].isdigit()}
                details["last_price"] = _investing_number(details.get("最新价"))
                if details["指标总结"] not in {"", "-", "—"}:
                    emit(cells[0], "indicators", details["指标总结"], details)
        elif "patternTable" in classes and {"时间范围", "型态", "K线#"}.issubset(names):
            stage, groups = None, {}
            for row in rows[1:]:
                cells = row["cells"]
                if len(cells) == 1 and "theDay" in cells[0]["attrs"].get("class", "").split():
                    stage = cells[0]["text"]
                    continue
                if len(cells) != len(names) or not cells[1].get("href"):
                    continue
                symbol, frame = cells[1], cells[2]["text"]
                raw_date = row["attrs"].get("data-time", "")
                match = re.fullmatch(r"(\d{4})年(\d{1,2})月(\d{1,2})日", raw_date)
                pattern_date = None
                if match:
                    try:
                        pattern_date = Date(*(int(value) for value in match.groups())).isoformat()
                    except ValueError:
                        pass
                pattern = {"pattern": cells[4]["text"], "stage": stage, "reliability": cells[3]["attrs"].get("title"),
                           "direction": cells[6]["attrs"].get("title"), "bars_ago_raw": cells[5]["text"],
                           "bars_ago": int(cells[5]["text"]) if cells[5]["text"].isdigit() else None,
                           "pattern_date": pattern_date, "pattern_date_raw": raw_date or None}
                group = groups.setdefault((symbol["href"], frame), {"symbol": symbol, "patterns": []})
                group["patterns"].append(pattern)
            for (_, frame), group in groups.items():
                emit(group["symbol"], "candlestick_patterns", "; ".join(item["pattern"] for item in group["patterns"]),
                     group["patterns"], frame, frame)
    if not any(record["data"]["subtype"] == "summary" for record in records):
        raise ValueError("Investing public page has no visible technical summary ratings; page may be blocked or structure changed")
    return records


def _validate_calendar_lookback(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 31:
        raise ValueError("calendar_lookback_days must be an integer between 0 and 31")
    return value


def filter_records(records, start, cutoff, calendar_days=7, calendar_lookback_days=0):
    calendar_lookback_days = _validate_calendar_lookback(calendar_lookback_days)
    kept, latest_macro = [], {}
    calendar_start = cutoff - timedelta(days=calendar_lookback_days)
    calendar_end = cutoff + timedelta(days=calendar_days)
    for record in records:
        kind = record["kind"]
        if kind == "news":
            stamp = parse_timestamp(record["published_at"])
            if stamp is None:
                record["data"]["background_only"] = True
                published_date = record["data"].get("published_date")
                if not published_date or start.date().isoformat() <= published_date <= cutoff.date().isoformat():
                    kept.append(record)
            elif start <= stamp <= cutoff:
                kept.append(record)
        elif kind == "calendar":
            value = record["data"]["event_at"]
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                provider_timezone = record["data"].get("provider_timezone")
                date_start, date_end, date_cutoff = calendar_start.date(), calendar_end.date(), cutoff.date()
                if provider_timezone:
                    provider_zone = ZoneInfo(provider_timezone)
                    date_start, date_end, date_cutoff = (stamp.astimezone(provider_zone).date() for stamp in (calendar_start, calendar_end, cutoff))
                event_date = Date.fromisoformat(value)
                if date_start <= event_date <= date_end:
                    record["data"]["calendar_scope"] = "past" if event_date < date_cutoff else "upcoming"
                    kept.append(record)
            else:
                stamp = parse_timestamp(value)
                if stamp and calendar_start <= stamp <= calendar_end:
                    record["data"]["calendar_scope"] = "past" if stamp < cutoff else "upcoming"
                    kept.append(record)
        elif kind == "macro":
            period = record["data"]["period"]
            if record["data"].get("availability_rule") == "observed_snapshot":
                observed = parse_timestamp(record.get("observed_at"))
                if observed is None or observed > cutoff:
                    continue
            if record["data"].get("availability_rule") == "previous_provider_day":
                provider_day = cutoff.astimezone(ZoneInfo(record["data"]["provider_timezone"])).date().isoformat()
                if period >= provider_day:
                    continue
            provider_at = record["data"].get("provider_at")
            if provider_at:
                provider_stamp = parse_timestamp(provider_at)
                if provider_stamp is None or provider_stamp > cutoff:
                    continue
            # Periods describe observations, not real-time market prices.
            if re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", period) and period[:10] > cutoff.date().isoformat():
                continue
            key = (record["source_id"], record["data"]["series_id"])
            if key not in latest_macro or period > latest_macro[key]["data"]["period"]:
                latest_macro[key] = record
    kept.extend(latest_macro.values())
    return kept


def deduplicate(records):
    output, seen = [], {}
    for record in records:
        kind, data = record["kind"], record["data"]
        if kind == "news":
            title = re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", record["title"])).lower()
            day = str(record["published_at"] or data.get("published_date") or "undated")[:10]
            keys = [(kind, "url", canonical_url(record["url"])), (kind, "title", title, day)]
            if data.get("aggregator") == "TradingView" and data.get("original_source_url"):
                original = urlsplit(str(data["original_source_url"]))
                if original.scheme in {"http", "https"} and original.hostname and not original.username and not original.password and original.path not in {"", "/"}:
                    keys.append((kind, "url", canonical_url(data["original_source_url"])))
        elif kind == "macro":
            keys = [(kind, data["series_id"], data["period"])]
        else:
            keys = [(kind, record["title"], data["event_at"], data.get("region"))]
        existing = next((seen[key] for key in keys if key in seen), None)
        provenance = {"source_id": record["source_id"], "url": record["url"], "title": record["title"],
                      "published_at": record["published_at"]}
        for key in ("original_source", "original_source_url", "author", "source_type", "topic_scope"):
            if data.get(key):
                provenance[key] = data[key]
        provenance_items = record["data"].get("provenance") or [provenance]
        if existing is None:
            record["data"]["provenance"] = list(provenance_items)
            output.append(record)
            existing = record
        else:
            for item in provenance_items:
                if item not in existing["data"]["provenance"]:
                    existing["data"]["provenance"].append(item)
        for key in keys:
            seen[key] = existing
    return output


def _fetch(source, network):
    if source.get("transport") == "jin10-mcp":
        from .jin10_mcp import fetch_jin10_mcp
        return fetch_jin10_mcp(source, network)
    if source.get("transport") == "browser-visible-page":
        from .browser_pages import fetch_browser_page
        return fetch_browser_page(source, network)
    timeout, limit = float(network.get("timeout_seconds", 12)), int(network.get("max_bytes", 3000000))
    headers = {"User-Agent": "TokiFinanceDaily/0.1 (+public-source research; bounded fetch)",
               "Accept": "application/rss+xml,application/atom+xml,application/json,text/xml,text/html,*/*"}
    headers.update(source.get("headers", {}))
    for attempt in range(int(network.get("retries", 1)) + 1):
        try:
            deadline = time.monotonic() + timeout
            with urlopen(Request(source["url"], headers=headers), timeout=timeout) as response:
                chunks, count = [], 0
                while True:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Source exceeded total response time limit")
                    chunk = response.read1(min(65536, limit + 1 - count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    if count > limit:
                        raise ValueError(f"Source exceeds {limit} byte response limit")
                return b"".join(chunks), {"transport": "urllib", "content_type": response.headers.get("Content-Type", "")}
        except (URLError, ssl.SSLError) as error:
            reason = getattr(error, "reason", error)
            # System curl can use native trust stores on macOS. TLS remains fully
            # verified: no -k, unverified SSLContext, or certificate bypass.
            if isinstance(reason, ssl.SSLCertVerificationError) and network.get("tls_system_curl_fallback", False) and shutil.which("curl"):
                command = ["curl", "--silent", "--show-error", "--fail", "--location", "--max-redirs", "4",
                           "--proto", "=http,https", "--proto-redir", "=http,https", "--max-time", str(timeout),
                           "--max-filesize", str(limit), "--user-agent", headers["User-Agent"], source["url"]]
                result = subprocess.run(command, capture_output=True, timeout=timeout + 2, check=False)
                if result.returncode:
                    raise RuntimeError("System TLS fetch: " + result.stderr.decode(errors="replace")[:250]) from error
                if len(result.stdout) > limit:
                    raise ValueError("System TLS response exceeds byte limit")
                return result.stdout, {"transport": "system-curl-verified-tls", "content_type": ""}
            if isinstance(error, HTTPError) and error.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == int(network.get("retries", 1)):
                raise
        except (TimeoutError, OSError):
            if attempt == int(network.get("retries", 1)):
                raise
        time.sleep(min(0.4 * (attempt + 1), 1))


def parse_tradingview_technical(body, source, observed_at):
    from .technicals import parse_tradingview_technical as parse
    return parse(body, source, observed_at)


def parse_tradingview_news(body, source, observed_at):
    from .tradingview_news import parse_tradingview_news as parse
    return parse(body, source, observed_at)


def parse_jin10_public(body, source, observed_at):
    from .jin10 import parse_jin10_public as parse
    return parse(body, source, observed_at)


def parse_jin10_mcp(body, source, observed_at):
    from .jin10_mcp_records import parse_jin10_mcp as parse
    return parse(body, source, observed_at)


def parse_investing_instrument_technical(body, source, observed_at):
    from .investing_instrument import parse_investing_instrument_technical as parse
    return parse(body, source, observed_at)


_PARSERS = {"rss": parse_feed, "atom": parse_feed, "sina": parse_json_news,
            "eastmoney": parse_json_news, "gov_cn": parse_json_news, "html_news": parse_html_news,
            "wallstreetcn_live": parse_wallstreetcn, "wallstreetcn_news": parse_wallstreetcn,
            "ics": parse_ics, "nbs_calendar": parse_nbs_calendar, "fed_fomc_calendar": parse_fed_fomc_calendar, "ecb_fx": parse_ecb,
            "fred_csv": parse_fred_csv, "fear_greed": parse_fear_greed, "cboe_vix": parse_cboe_vix,
            "investing_technical": parse_investing_technical,
            "investing_instrument_technical": parse_investing_instrument_technical,
            "tradingview_technical": parse_tradingview_technical, "tradingview_news": parse_tradingview_news,
            "jin10_public": parse_jin10_public,
            "jin10_mcp": parse_jin10_mcp, "jin10_quote": parse_jin10_mcp}


def collect(config_path, date=None, hours=48, cutoff=None, out_dir=None, calendar_lookback_days=None):
    """Collect news, a bounded calendar window, and nonfuture macro observations.

    ``out_dir`` writes dataset.json plus original HTTP bodies and SHA256 metadata.
    Passing a historical date filters currently available feeds; it does not
    reconstruct their historical publication state or unrevised macro values.
    """
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    sources, network = config["sources"], config.get("network", {})
    calendar_lookback_days = _validate_calendar_lookback(config.get("calendar_lookback_days", 0) if calendar_lookback_days is None else calendar_lookback_days)
    if not 1 <= float(hours) <= 168:
        raise ValueError("hours must be between 1 and 168")
    if not 1 <= float(network.get("timeout_seconds", 12)) <= 60:
        raise ValueError("timeout_seconds must be between 1 and 60")
    if not 0 <= int(network.get("retries", 1)) <= 3:
        raise ValueError("retries must be between 0 and 3")
    if not 1024 <= int(network.get("max_bytes", 3000000)) <= 20000000:
        raise ValueError("max_bytes must be between 1024 and 20000000")
    source_ids = [s["id"] for s in sources]
    if len(set(source_ids)) != len(source_ids) or any(not re.fullmatch(r"[a-zA-Z0-9_-]+", x) for x in source_ids):
        raise ValueError("Source IDs must be unique filename-safe identifiers")
    focus_assets = config.get("focus_assets", [])
    if not isinstance(focus_assets, list) or len(focus_assets) > 12:
        raise ValueError("focus_assets must be a list with at most 12 entries")
    focus_keys = set()
    for asset in focus_assets:
        if not isinstance(asset, dict) or not re.fullmatch(r"[a-zA-Z0-9_-]+", str(asset.get("key", ""))):
            raise ValueError("Focus assets require a stable key")
        if asset["key"] in focus_keys or not isinstance(asset.get("label"), str) or not asset["label"].strip():
            raise ValueError("Focus asset keys must be unique and labels nonempty")
        if asset.get("source_id") is not None and asset["source_id"] not in source_ids:
            raise ValueError("Focus asset source_id must reference a configured source")
        focus_keys.add(asset["key"])
    now = datetime.now(DISPLAY_ZONE)
    if cutoff is not None:
        if isinstance(cutoff, datetime) and cutoff.utcoffset() is None:
            raise ValueError("cutoff must include an explicit timezone offset")
        if isinstance(cutoff, str) and not re.search(r"(?:Z|[+-]\d{2}:\d{2})$", cutoff):
            raise ValueError("cutoff must include an explicit timezone offset")
        cutoff_dt = parse_timestamp(cutoff)
        if cutoff_dt is None:
            raise ValueError("Invalid cutoff")
    elif date:
        cutoff_dt = min(now, datetime.combine(Date.fromisoformat(str(date)), Time(23, 59, 59), DISPLAY_ZONE))
    else:
        cutoff_dt = now
    if cutoff_dt > now + timedelta(seconds=5):
        raise ValueError("cutoff cannot be in the future")
    edition_date = str(date) if date else cutoff_dt.date().isoformat()
    Date.fromisoformat(edition_date)
    if edition_date != cutoff_dt.date().isoformat():
        raise ValueError("date must match cutoff in Asia/Shanghai")
    start = cutoff_dt - timedelta(hours=float(hours))
    live_cutoff = cutoff is None and (not date or str(date) == now.date().isoformat())
    output = Path(out_dir) if out_dir else None
    if output:
        (output / "raw").mkdir(parents=True, exist_ok=True)

    def one(source):
        fetched_at = datetime.now(DISPLAY_ZONE).isoformat()
        status = {"id": source["id"], "name": source["name"], "url": source["url"],
                  "status": "disabled", "count": 0, "error": None, "fetched_at": fetched_at}
        for key in ("source_type", "topic_scope", "editorial_note"):
            if source.get(key):
                status[key] = source[key]
        if not source.get("enabled", True):
            status["error"] = source.get("disabled_reason", "Disabled in config")
            return status, []
        try:
            if urlsplit(source["url"]).scheme not in ("https", "http"):
                raise ValueError("Only public HTTP(S) source URLs are supported")
            if source["adapter"] not in _PARSERS:
                raise ValueError("Unsupported source adapter")
            from .page_cache import fetch_source
            body, metadata = fetch_source(source, network, _fetch)
            fetched_at = metadata.get("fetched_at") or datetime.now(DISPLAY_ZONE).isoformat()
            metadata = {**metadata, "fetched_at": fetched_at}
            status.update(metadata)
            if output:
                raw_path = output / "raw" / (source["id"] + ".bin")
                raw_path.write_bytes(body)
                status["raw_path"] = str(raw_path.relative_to(output))
                status["raw_sha256"] = hashlib.sha256(body).hexdigest()
                (output / "raw" / (source["id"] + ".meta.json")).write_text(
                    json.dumps({**status, "bytes": len(body)}, ensure_ascii=False, indent=2), encoding="utf-8")
            parsed = _PARSERS[source["adapter"]](body, source, fetched_at)
            status.update(status="ok", parsed_count=len(parsed))
            if output:
                (output / "raw" / (source["id"] + ".meta.json")).write_text(
                    json.dumps({**status, "bytes": len(body)}, ensure_ascii=False, indent=2), encoding="utf-8")
            return status, parsed
        except Exception as error:
            status.update(status="error", error=f"{type(error).__name__}: {error}"[:400])
            if output and status.get("raw_path"):
                (output / "raw" / (source["id"] + ".meta.json")).write_text(
                    json.dumps({**status, "bytes": len(body)}, ensure_ascii=False, indent=2), encoding="utf-8")
            return status, []

    with ThreadPoolExecutor(max_workers=max(1, min(12, int(network.get("max_workers", 6))))) as executor:
        results = list(executor.map(one, sources))
    if live_cutoff:
        cutoff_dt = datetime.now(DISPLAY_ZONE)
        if date:
            cutoff_dt = min(cutoff_dt, datetime.combine(Date.fromisoformat(edition_date), Time(23, 59, 59), DISPLAY_ZONE))
        else:
            edition_date = cutoff_dt.date().isoformat()
        start = cutoff_dt - timedelta(hours=float(hours))
    filtered = []
    for status, parsed in results:
        try:
            kept = deduplicate(filter_records(parsed, start, cutoff_dt, calendar_lookback_days=calendar_lookback_days))
            source = next(s for s in sources if s["id"] == status["id"])
            if status["status"] == "ok" and source["adapter"] in {"fear_greed", "cboe_vix"} and not kept:
                raise ValueError("No indicator observation is available before the requested cutoff")
            status["count"] = len(kept)
        except Exception as error:
            status.update(status="error", count=0, error=f"{type(error).__name__}: {error}"[:400])
            kept = []
        if output and status.get("raw_path"):
            raw_meta = output / "raw" / (status["id"] + ".meta.json")
            metadata = json.loads(raw_meta.read_text(encoding="utf-8"))
            raw_meta.write_text(json.dumps({**metadata, **status}, ensure_ascii=False, indent=2), encoding="utf-8")
        filtered.append((status, kept))
    results = filtered
    records = deduplicate([record for _, items in results for record in items])
    records.sort(key=lambda r: (r["kind"], r["published_at"] or r["data"].get("event_at") or r["data"].get("period") or "", r["id"]), reverse=True)
    dataset = {"schema_version": 1, "date": edition_date, "timezone": "Asia/Shanghai",
               "cutoff": cutoff_dt.isoformat(), "collected_at": datetime.now(DISPLAY_ZONE).isoformat(),
               "window_start": start.isoformat(), "mode": "live", "calendar_lookback_days": calendar_lookback_days,
               "sources": [status for status, _ in results], "records": records}
    if focus_assets:
        dataset["focus_assets"] = [{key: asset.get(key) for key in ("key", "label", "source_id")}
                                   for asset in focus_assets]
    if output:
        (output / "dataset.json").write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    return dataset
