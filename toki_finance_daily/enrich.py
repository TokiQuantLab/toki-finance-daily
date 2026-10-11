"""Fetch a small set of article originals without delegating scraping to the AI."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import re

from .brief import _candidate_priority, make_packet
from .collect import DISPLAY_ZONE, _fetch, _wallstreetcn_paid, canonical_url, parse_timestamp, plain, wallstreetcn_page_data

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_SKIP_TAGS = {"script", "style", "nav", "footer", "aside", "form", "button", "noscript", "iframe", "svg"}
_BLOCK_TAGS = {"p", "div", "section", "article", "main", "br", "li", "h1", "h2", "h3", "h4", "blockquote", "tr"}
_BOILERPLATE = re.compile(r"(?:^|[\s_-])(?:ad|ads|advert|advertisement|share|social|related|recommend|recommended|comment|comments|copyright|breadcrumb|toolbar|subscribe|footer|navigation)(?:$|[\s_-])", re.I)


def _container_priority(tag, attrs):
    identity = attrs.get("id", "").lower()
    classes = attrs.get("class", "").lower().split()
    if identity in {"ucap-content", "artibody", "contentbody"}:
        return 140
    if identity == "article" or any(c.startswith("articlebody") or re.match(r"_articlebody_", c) for c in classes):
        return 130
    if identity in {"trs_editor", "trs-editor"} or any(c in {"trs_editor", "trs_editor_view", "trs_ueditor"} for c in classes):
        return 120
    if tag == "article":
        return 90
    if tag == "main" or attrs.get("role", "").lower() == "main":
        return 70
    if identity == "content":
        return 40
    return 0


class _ArticleParser(HTMLParser):
    def __init__(self, *, tradingview=False):
        super().__init__(convert_charrefs=True)
        self.stack, self.candidates, self.publications = [], [], []
        self.tradingview = tradingview

    def _append(self, value):
        if self.stack and self.stack[-1]["skip"]:
            return
        for frame in self.stack:
            if frame.get("candidate") is not None:
                self.candidates[frame["candidate"]]["parts"].append(value)

    def handle_starttag(self, tag, attributes):
        attrs = {key.lower(): value or "" for key, value in attributes}
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("itemprop")
            if key and attrs.get("content"):
                self.publications.append((key.strip().lower(), attrs["content"].strip()))
        elif tag == "time" and attrs.get("itemprop", "").lower() == "datepublished" and attrs.get("datetime"):
            self.publications.append(("datepublished", attrs["datetime"]))
        parent_skip = self.stack[-1]["skip"] if self.stack else False
        labels = attrs.get("id", "") + " " + attrs.get("class", "")
        skip = (parent_skip or tag in _SKIP_TAGS or "hidden" in attrs or attrs.get("aria-hidden") == "true"
                or self.tradingview and (attrs.get("role") == "tab" or "symbolscontainer" in labels.lower())
                or "appendqr" in labels.lower() or bool(_BOILERPLATE.search(labels)))
        if not skip and tag in _BLOCK_TAGS:
            self._append("\n")
        if tag in _VOID:
            return
        priority = 0 if skip else _container_priority(tag, attrs)
        if not skip and self.tradingview and tag == "div" and any(c.startswith("body-") for c in attrs.get("class", "").split()):
            priority = 150
        frame = {"tag": tag, "skip": skip, "candidate": None}
        if priority:
            frame["candidate"] = len(self.candidates)
            self.candidates.append({"priority": priority, "container": attrs.get("id") or attrs.get("class") or tag, "parts": []})
        self.stack.append(frame)

    def handle_startendtag(self, tag, attributes):
        self.handle_starttag(tag, attributes)
        if tag not in _VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _BLOCK_TAGS:
            self._append("\n")
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break

    def handle_data(self, value):
        self._append(value)


def _decode(body):
    if isinstance(body, str):
        return body
    header = body[:4096].decode("ascii", errors="ignore")
    charset = re.search(r"charset\s*=\s*[\"']?([a-zA-Z0-9_-]+)", header, re.I)
    encoding = charset.group(1) if charset else "utf-8-sig"
    try:
        return body.decode(encoding, errors="replace")
    except LookupError:
        return body.decode("utf-8-sig", errors="replace")


def _publication_time(publications, url):
    host = (urlsplit(url).hostname or "").lower()
    if host == "gov.cn" or host.endswith(".gov.cn"):
        zone = "Asia/Shanghai"
    elif host == "sina.com.cn" or host.endswith(".sina.com.cn") or host == "eastmoney.com" or host.endswith(".eastmoney.com"):
        zone = "Asia/Shanghai"
    elif host == "federalreserve.gov" or host.endswith(".federalreserve.gov") or host == "cnbc.com" or host.endswith(".cnbc.com"):
        zone = "America/New_York"
    else:
        zone = None
    priorities = {"article:published_time": 1, "bytedance:published_time": 2, "datepublished": 3,
                  "firstpublishedtime": 4, "pubdate": 5, "weibo: article:create_at": 6}
    for field, value in sorted(publications, key=lambda item: priorities.get(item[0], 100)):
        if field not in priorities:
            continue
        # Govt publication metadata uses YYYY-MM-DD-HH:mm:ss. Do not infer a
        # timestamp from URL dates, modified times, copyright, or page generation.
        value = re.sub(r"^(\d{4}-\d{2}-\d{2})-(\d{2}:\d{2})", r"\1T\2", value)
        if not re.search(r"\d{2}:\d{2}", value):
            continue
        explicit_zone = re.search(r"(?:Z|[+-]\d{2}:?\d{2})$", value)
        if not explicit_zone and zone is None:
            continue
        if field in {"firstpublishedtime", "pubdate"} and not (host == "gov.cn" or host.endswith(".gov.cn")):
            continue
        stamp = parse_timestamp(value, zone or "UTC")
        if stamp:
            return stamp.isoformat(), field
    return None, None


class _LinkedData(HTMLParser):
    """Decode publisher JSON-LD without evaluating scripts."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.active, self.parts, self.documents = False, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.active, self.parts = True, []

    def handle_data(self, value):
        if self.active:
            self.parts.append(value)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            try:
                value = json.loads("".join(self.parts))
            except ValueError:
                value = None
            if value is not None:
                self.documents.extend(value if isinstance(value, list) else [value])
            self.active = False


def _tradingview_free_story(text, url):
    linked = _LinkedData()
    linked.feed(text)
    stories = [value for value in linked.documents if isinstance(value, dict)
               and value.get("@type") == "NewsArticle"
               and canonical_url(value.get("url") or value.get("mainEntityOfPage")) == canonical_url(url)]
    if len(stories) != 1:
        raise ValueError("TradingView article must have matching NewsArticle metadata")
    story = stories[0]
    parts = story.get("hasPart")
    parts = parts if isinstance(parts, list) else [parts]
    if story.get("isAccessibleForFree") is not True or not parts or any(
        not isinstance(part, dict) or part.get("isAccessibleForFree") is not True for part in parts
    ):
        raise ValueError("TradingView paid, preview or unconfirmed article skipped")
    return story


def extract_article(body, url):
    """Return identified article text and reliable publication metadata.

    Deliberately has no whole-page fallback: an unfamiliar layout is an explicit
    parsing failure rather than a navigation/ad dump presented as evidence.
    """
    text = _decode(body)
    host = (urlsplit(url).hostname or "").lower()
    if host in {"wallstreetcn.com", "www.wallstreetcn.com"} and re.search(r"\b__SSR__\s*=", text):
        page = wallstreetcn_page_data(text)
        item = page.get("article") or page.get("livenews")
        if isinstance(item, dict) and _wallstreetcn_paid(item):
            raise ValueError("Subscription article skipped; enrichment only accepts public free content")
    tradingview_story = _tradingview_free_story(text, url) if host in {"cn.tradingview.com", "www.tradingview.com"} else None
    parser = _ArticleParser(tradingview=tradingview_story is not None)
    parser.feed(text)
    candidates = []
    for candidate in parser.candidates:
        paragraphs = [re.sub(r"\s+", " ", part).strip() for part in "".join(candidate["parts"]).split("\n")]
        text = "\n".join(part for part in paragraphs if part)
        if len(text) >= 40:
            candidates.append({**candidate, "text": text})
    if not candidates:
        raise ValueError("No recognized article container with substantive text; layout may have changed")
    if tradingview_story:
        candidates = [candidate for candidate in candidates if candidate["priority"] == 150]
        if not candidates:
            raise ValueError("TradingView free article body missing; no navigation fallback")
    best = max(candidates, key=lambda candidate: (candidate["priority"], len(candidate["text"])))
    published_at, field = _publication_time(parser.publications, url)
    if tradingview_story:
        # The page's NewsArticle explicitly labels publication, not modification.
        value = tradingview_story.get("datePublished")
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) and re.search(r"[T ]\d{2}:\d{2}", value) else None
        except ValueError:
            stamp = None
        if stamp is None or stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("TradingView article requires an explicit publication timestamp")
        published_at, field = stamp.astimezone(DISPLAY_ZONE).isoformat(), "NewsArticle.datePublished"
    return {"text": best["text"], "container": best["container"], "published_at": published_at,
            "publication_field": field}


def _choose(dataset, ids, limit):
    records = {record["id"]: record for record in dataset["records"]}
    if ids is not None:
        ids = [ids] if isinstance(ids, str) else list(ids)
        if len(ids) != len(set(ids)):
            raise ValueError("Selected evidence IDs must be unique")
        if len(ids) > limit:
            raise ValueError("Selected evidence IDs exceed limit; explicitly increase limit")
        for identifier in ids:
            if identifier not in records:
                raise ValueError(f"Unknown evidence ID: {identifier}")
            if records[identifier]["kind"] != "news":
                raise ValueError(f"Article enrichment only accepts news evidence: {identifier}")
        chosen = [records[identifier] for identifier in ids]
    else:
        packet = make_packet(dataset, limit=max(48, limit * 4))
        candidates = [records[r["id"]] for r in packet["records"] if r["kind"] == "news"]
        buckets = {}
        for record in candidates:
            buckets.setdefault(record["source_id"], []).append(record)
        for items in buckets.values():
            items.sort(key=lambda record: (_candidate_priority(record), record.get("published_at") or record.get("data", {}).get("published_date", "")), reverse=True)
        groups = sorted(buckets.values(), key=lambda items: _candidate_priority(items[0]), reverse=True)
        chosen = []
        while len(chosen) < limit and any(groups):
            for items in groups:
                if items and len(chosen) < limit:
                    chosen.append(items.pop(0))
    # Validate every path and URL before any network request or output mutation.
    for record in chosen:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", str(record["id"])):
            raise ValueError(f"Evidence ID is not filename-safe: {record['id']}")
        parsed = urlsplit(record["url"])
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(f"Article URL must be HTTP(S) without credentials: {record['id']}")
    return chosen


def enrich_dataset(dataset, ids=None, limit=8, out_dir=None, timeout=12):
    """Deep-copy Dataset and add bounded selected-article evidence.

    Does not call an LLM, add macro records, or modify source collection statuses.
    Explicit IDs must exist, be news, and fit the requested limit before fetching.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 24:
        raise ValueError("limit must be an integer between 1 and 24")
    if not 1 <= float(timeout) <= 60:
        raise ValueError("timeout must be between 1 and 60 seconds")
    result = deepcopy(dataset)
    chosen = _choose(result, ids, limit)
    output = Path(out_dir) if out_dir else None
    if output and chosen:
        (output / "raw").mkdir(parents=True, exist_ok=True)
    network = {"timeout_seconds": float(timeout), "max_bytes": 3000000,
               "retries": 1, "tls_system_curl_fallback": True}

    def one(record):
        fetched_at = datetime.now(DISPLAY_ZONE).isoformat()
        status = {"id": record["id"], "url": record["url"], "status": "error", "error": None, "fetched_at": fetched_at}
        data = record.setdefault("data", {})
        try:
            host = (urlsplit(record["url"]).hostname or "").lower()
            if host in {"cn.tradingview.com", "www.tradingview.com"}:
                if data.get("paywall") is not False or "permission" in data:
                    raise ValueError("TradingView enrichment requires an explicitly free list item")
                from .page_cache import fetch_source
                article_source = {"id": record["id"], "url": record["url"], "adapter": "tradingview_news_article",
                                  "minimum_interval_seconds": 86400, "retries": 0}
                body, transport = fetch_source(article_source, network, _fetch)
            else:
                body, transport = _fetch({"id": record["id"], "url": record["url"]}, network)
            status.update(transport)
            status["raw_sha256"] = hashlib.sha256(body).hexdigest()
            if output:
                html_path = output / "raw" / (record["id"] + "-article.html")
                html_path.write_bytes(body)
                status["raw_path"] = str(html_path.relative_to(output))
            article = extract_article(body, record["url"])
            if host in {"cn.tradingview.com", "www.tradingview.com"}:
                article_stamp = parse_timestamp(article["published_at"])
                list_stamp = parse_timestamp(record.get("published_at"))
                status["article_published_at"] = article["published_at"]
                if not parse_timestamp(result["window_start"]) <= article_stamp <= parse_timestamp(result["cutoff"]):
                    raise ValueError("TradingView article publication is outside the edition window")
                if list_stamp != article_stamp:
                    raise ValueError("TradingView article publication differs from the captured list")
            text = article["text"]
            status.update(status="ok", text_chars=len(text), container=article["container"],
                          text_sha256=hashlib.sha256(text.encode()).hexdigest())
            if output:
                text_path = output / "raw" / (record["id"] + "-article.txt")
                text_path.write_text(text, encoding="utf-8")
                status["text_path"] = str(text_path.relative_to(output))
                data["article_text_path"] = status["text_path"]
            record["summary"] = plain(text, limit=4000)
            data.update(article_text_sha256=status["text_sha256"], article_text_chars=len(text))
            if host in {"cn.tradingview.com", "www.tradingview.com"}:
                data["content_scope"] = "article"
            stamp = article["published_at"]
            if stamp:
                status["article_published_at"] = stamp
                data["article_publication_field"] = article["publication_field"]
                original_day = data.get("published_date")
                parsed = parse_timestamp(stamp)
                cutoff = parse_timestamp(result["cutoff"])
                start = parse_timestamp(result["window_start"])
                if record.get("published_at") is None and start <= parsed <= cutoff and (not original_day or parsed.date().isoformat() == original_day):
                    record["published_at"] = stamp
                    data["background_only"] = False
                    data["publication_precision"] = "timestamp"
                    data["date_precision"] = "timestamp"
        except Exception as error:
            status.update(status="error", error=f"{type(error).__name__}: {error}"[:400])
        data["enrichment_status"] = status["status"]
        data["enrichment_error"] = status["error"]
        if output:
            (output / "raw" / (record["id"] + "-article.meta.json")).write_text(
                json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        return status

    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = list(executor.map(one, chosen))
    result["enrichment"] = statuses
    return result
