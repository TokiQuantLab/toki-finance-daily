"""Portable public research edition, built from a frozen dataset and brief."""
from __future__ import annotations

from hashlib import sha256
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import base64
import json
import math
import re

from .brand import logo_data_uri

ASSETS = Path(__file__).parent / "assets"


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        elif tag in {"br", "p", "div", "li"} and not self.hidden:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _text(value):
    if value is None:
        return ""
    if not isinstance(value, (str, int, float, bool)):
        return ""
    parser = _Text()
    parser.feed(str(value))
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip()


def _url(value):
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        return ""
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            return ""
        _ = parts.port
        return urlunsplit(parts)
    except ValueError:
        return ""


def _scalar(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _text(value) if isinstance(value, str) else None


# These are finance fields, not transport metadata. A recursive allowlist keeps
# private diagnostics out even when an adapter adds fields inside a table row.
_DATA_KEYS = {
    "topic", "label", "value", "unit", "period", "provider_at", "provider", "market",
    "symbol", "symbol_name", "instrument_name", "instrument_header", "contract", "quote_currency", "quote_currency_raw",
    "subtype", "timeframe", "timeframe_label", "selected_timeframe", "rating", "rating_raw",
    "moving_average_rating", "moving_average_rating_raw", "indicator_rating", "indicator_rating_raw",
    "details", "counts", "oscillators", "indicators", "moving_averages", "pivots",
    "event_at", "region", "importance", "actual", "forecast", "previous", "revised", "calendar_scope",
    "star", "consensus", "impact", "affect_txt", "affect", "currency", "country", "meeting_start", "meeting_end",
    "provider_timezone", "time_precision", "schedule_note", "reference_only",
    "open", "close", "high", "low", "volume", "ups_price", "ups_percent",
    "classification", "range_min", "range_max",
}
_ROW_KEYS = {
    "name", "value", "value_raw", "raw", "action", "signal", "rating", "unit", "ma_type",
    "simple", "exponential", "method", "levels", "raw_levels", "classic", "fibonacci",
    "camarilla", "woodie", "dm", "pattern", "stage", "reliability", "direction",
    "bars_ago", "bars_ago_raw", "pattern_date", "pattern_date_raw", "counts",
    "sell", "neutral", "buy", "total", "summary", "moving_averages", "oscillators", "indicators",
    "买入", "卖出", "中性", "最新价", "ATR(14)", "指标总结", "last_price",
    "movingAverages", "S3", "S2", "S1", "P", "Pivot", "轴点", "R1", "R2", "R3",
}


def _finance_tree(value, allowed):
    if isinstance(value, dict):
        return {k: _finance_tree(v, _ROW_KEYS) for k, v in value.items() if k in allowed}
    if isinstance(value, list):
        return [_finance_tree(v, _ROW_KEYS) for v in value]
    return _scalar(value)


_TOPICS = {
    "地缘与航运": ("伊朗", "沙特", "俄罗斯", "乌克兰", "战争", "冲突", "袭击", "制裁", "航运", "霍尔木兹", "war", "iran", "russia", "sanctions", "shipping"),
    "能源与商品": ("原油", "石油", "柴油", "燃油", "天然气", "黄金", "白银", "能源", "oil", "diesel", "gold", "energy", "gas"),
    "宏观与央行": ("经济", "央行", "美联储", "利率", "通胀", "就业", "货币", "统计", "fed", "inflation", "jobs", "rates", "economy"),
    "财政与政策": ("财政", "预算", "政策", "税收", "发改委", "关税", "fiscal", "budget", "tariff", "tax"),
    "科技与产业": ("科技", "人工智能", "芯片", "半导体", "研发", "制造", "新质", "宁德", "technology", "chip", "semiconductor", "artificial intelligence"),
    "消费与企业": ("消费", "家庭", "销售", "零售", "餐饮", "财报", "利润", "企业", "earnings", "consumer", "retail", "household"),
    "市场与金融": ("股票", "股市", "指数", "债券", "收益率", "基金", "银行", "金融", "汇率", "stock", "market", "bond", "bank", "yield"),
    "数字资产": ("比特币", "以太坊", "数字资产", "加密", "稳定币", "bitcoin", "crypto", "ethereum", "stablecoin"),
}


def _topics(title, summary):
    corpus = (title + " " + summary).lower()
    return [label for label, words in _TOPICS.items() if any(word in corpus for word in words)] or ["其他资讯"]


def _instrument(record, data):
    url = _url((record.get("data") or {}).get("instrument_url"))
    if not url and record.get("source_id", "").startswith("investing_") and data.get("instrument_name"):
        url = _url(record.get("url"))
    if url:
        p = urlsplit(url)
        path = re.sub(r"-(?:technical|candlestick(?:-patterns)?)$", "", p.path.rstrip("/"))
        return p.hostname + path
    return data.get("symbol_name") or data.get("instrument_name") or data.get("symbol") or record.get("id")


def _article(item):
    return {key: [_text(x) for x in item.get(key, [])] if key == "evidence_ids" else _text(item.get(key))
            for key in ("category", "title", "fact", "analysis", "watch", "evidence_ids")}


def _public(dataset, brief, folder):
    source_names = {_text(s.get("id")): _text(s.get("name")) for s in dataset.get("sources", []) if isinstance(s, dict)}
    records = []
    for record in dataset.get("records", []):
        if record.get("kind") not in {"news", "macro", "calendar"}:
            continue
        original = record.get("data") or {}
        data = _finance_tree(original, _DATA_KEYS)
        title, summary = _text(record.get("title")), _text(record.get("summary"))
        public = {"id": _text(record.get("id")), "kind": record["kind"], "title": title,
                  "url": _url(record.get("url")), "published_at": _text(record.get("published_at")) or None,
                  "observed_at": _text(record.get("observed_at")) or None,
                  "source": source_names.get(record.get("source_id")) or _text(original.get("publisher")) or _text(data.get("provider")) or "原始来源",
                  "summary": summary[:180], "data": data}
        if record["kind"] == "news":
            public["topics"] = _topics(title, summary)
            public["published_date"] = _text(original.get("published_date")) or None
            public["original_source"] = _text(original.get("original_source"))
            public["original_source_url"] = _url(original.get("original_source_url"))
        if data.get("topic") == "technical":
            public["instrument"] = _text(_instrument(record, data))
        records.append(public)
    references = {r["id"] for r in records if r["data"].get("topic") == "technical"}
    default_technical = next((ref for item in [brief.get("lead", {}), *brief.get("stories", []), *brief.get("watchlist", [])]
                              for ref in item.get("evidence_ids", []) if ref in references), None)
    public_brief = {key: _text(brief.get(key)) for key in ("edition", "headline", "dek")}
    public_brief["lead"] = _article(brief.get("lead", {}))
    public_brief["stories"] = [_article(x) for x in brief.get("stories", [])]
    public_brief["calendar"] = [{k: [_text(v) for v in x.get(k, [])] if k == "evidence_ids" else _text(x.get(k))
                                  for k in ("title", "event_at", "region", "note", "evidence_ids")}
                                 for x in brief.get("calendar", [])]
    public_brief["watchlist"] = [{k: [_text(v) for v in x.get(k, [])] if k == "evidence_ids" else _text(x.get(k))
                                   for k in ("title", "detail", "evidence_ids")}
                                  for x in brief.get("watchlist", [])]
    downloads = [{"path": name, "label": label} for name, label in (
        ("poster.png", "日报长图"), ("poster-mobile.png", "手机长图"), ("poster.html", "海报网页版"),
        ("technicals.csv", "技术明细 CSV")) if (folder / name).is_file()]
    focus_assets = []
    for asset in dataset.get("focus_assets", []):
        if not isinstance(asset, dict):
            continue
        source_id = asset.get("source_id")
        # Source configuration remains private; public record IDs bind each
        # column to its own observed instrument without symbol guessing.
        ids = [r["id"] for r in dataset.get("records", [])
               if source_id and r.get("source_id") == source_id
               and (r.get("data") or {}).get("topic") == "technical" and r["id"] in references]
        focus_assets.append({"key": _text(asset.get("key")), "label": _text(asset.get("label")), "record_ids": ids})
    return {"schema_version": 1, "date": _text(dataset.get("date")), "timezone": "Asia/Shanghai",
            "cutoff": _text(dataset.get("cutoff")), "mode": _text(dataset.get("mode")),
            "brief": public_brief, "records": records, "default_technical_id": default_technical,
            "focus_assets": focus_assets,
            "assets": {"downloads": downloads, "online": (folder / "technical.html").is_file()}}


def build_site(dataset, brief, folder):
    """Write only the public edition and return its absolute output paths.

    All data and styles/scripts are inline: opening index.html does not fetch
    JSON or need a server. The optional online companion is a separate iframe.
    """
    folder = Path(folder).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    public = _public(dataset, brief, folder)
    css = (ASSETS / "daily-site.css").read_text(encoding="utf-8")
    js = (ASSETS / "daily-site.js").read_text(encoding="utf-8")
    logo_uri = logo_data_uri()
    payload = json.dumps(public, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    embedded = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    digest = base64.b64encode(sha256(js.encode("utf-8")).digest()).decode("ascii")
    csp = "default-src 'none'; script-src 'sha256-" + digest + "'; style-src 'unsafe-inline'; img-src 'self' data:; frame-src 'self' file:; connect-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
    title = escape(public["brief"]["headline"] or "Toki 财经研究")
    html = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="{escape(csp, quote=True)}"><meta name="color-scheme" content="light"><title>{title} · Toki 财经研究</title>
<link rel="icon" type="image/png" href="{logo_uri}">
<style>{css}</style></head><body>
<a class="skip-link" href="#main">跳到正文</a>
<header class="topbar"><a class="brand" href="#top"><img class="brand-mark" src="{logo_uri}" alt="" width="40" height="40"><span>Toki <b>Quant Lab</b><small>DAILY RESEARCH</small></span></a><nav aria-label="栏目"><a id="focus-nav" href="#focus" hidden>品种总览</a><a href="#today">今日解读</a><a href="#markets">市场数据</a><a href="#news">资讯</a><a href="#technicals">技术分析</a><a href="#calendar">日历</a><a id="online-nav" href="#online">在线观察</a></nav><a class="data-link" href="public-data.json" download>数据 ↓</a></header>
<main id="main"><section class="hero container" id="top"><div class="hero-main"><p class="eyebrow">THE DAILY PERSPECTIVE <span id="edition"></span></p><h1 id="headline">{title}</h1><p class="dek" id="dek"></p><div class="hero-actions"><a id="hero-primary" class="button primary" href="#today">阅读今日解读 <span>↗</span></a><a class="button quiet" href="#technicals">查看技术明细</a></div></div><aside class="edition-card"><div class="edition-orbit" aria-hidden="true"><i></i><i></i><i></i><span>TQ</span></div><p class="eyebrow">RESEARCH EDITION</p><strong id="edition-date"></strong><p class="edition-time" id="edition-time"></p><p class="edition-note">新闻、市场与技术<br>按各自时点阅读</p></aside></section>
<section class="container section focus-section" id="focus" hidden><div class="section-heading"><div><p class="eyebrow">TRADING ASSETS / SIDE BY SIDE</p><h2>交易品种总览</h2></div><a href="#technicals">全部品种与原始表格 ↗</a></div><div class="focus-cards" id="focus-cards"></div><p class="focus-note">评级按各自周期排列，来源与时间分别标注。— 表示未提供，技术信号按来源阅读。</p><div id="focus-ratings"></div></section>
<section class="container section" id="today"><div class="section-heading"><div><p class="eyebrow">01 / THE BIG PICTURE</p><h2>今日解读</h2></div><p>从事件出发，沿证据看传导</p></div><div id="editorial"></div><div class="scenario-grid" id="scenarios"></div></section>
<section class="container section" id="markets"><div class="section-heading"><div><p class="eyebrow">02 / MARKET CONTEXT</p><h2>市场与官方数据</h2></div><p>报价时点与统计期分别标注</p></div><div class="market-grid" id="market-cards"></div></section>
<section class="container section" id="news"><div class="section-heading"><div><p class="eyebrow">03 / THE NEWSROOM</p><h2>资讯全览</h2></div><p id="news-counter"></p></div><div class="news-tools"><label class="search"><span>搜索资讯</span><input id="news-search" type="search" placeholder="关键词、资产或事件" autocomplete="off"></label><label><span>来源</span><select id="news-source"></select></label><label><span>主题</span><select id="news-topic"></select></label></div><p class="result-caption" id="news-result" aria-live="polite"></p><div class="news-reader" id="news-reader"><div class="news-reader-bar" id="news-reader-bar" hidden><p id="news-progress" aria-live="polite"></p><button class="button quiet" id="news-collapse" aria-controls="news-list" aria-expanded="false">收起资讯 ↑</button></div><div class="news-scroll" id="news-scroll" role="region" aria-label="资讯列表" tabindex="0"><div class="news-grid" id="news-list"></div></div></div><div class="more-actions" id="news-actions"><button class="button quiet" id="news-more" aria-controls="news-list" aria-expanded="false">继续阅读</button><button class="text-button" id="news-all" aria-controls="news-list" aria-expanded="false">展开全部资讯</button></div></section>
<section class="technical-wrap" id="technicals"><div class="container section"><div class="section-heading"><div><p class="eyebrow">04 / TECHNICAL PERSPECTIVE</p><h2>技术研究</h2></div><p>各提供者的真实评级与表格</p></div><p class="section-note">选择品种、周期与提供者。评级来自技术指标；未标周期的表格单独阅读，数据时点以每张表为准。</p><div class="technical-tools"><label><span>品种</span><select id="technical-asset"></select></label><label><span>周期</span><select id="technical-period"></select></label><label><span>提供者</span><select id="technical-provider"></select></label></div><div id="technical-list"></div></div></section>
<section class="container section" id="calendar"><div class="section-heading"><div><p class="eyebrow">05 / THE ECONOMIC CALENDAR</p><h2>财经日历</h2></div><p>时间按来源精度与时区显示</p></div><div class="calendar-tools"><label><span>日程</span><select id="calendar-status"><option value="all">全部日程</option><option value="future" selected>即将发布</option><option value="past">已过日程</option></select></label><label><span>开始日期</span><input id="calendar-from" type="date"></label><label><span>结束日期</span><input id="calendar-to" type="date"></label></div><p class="section-note">已过日程仅表示时间经过，不代表实际值已公布。前值、预期、实际与修正沿用来源，未提供的格位显示 —。</p><p class="result-caption" id="calendar-result" aria-live="polite"></p><div class="calendar-reader" id="calendar-reader"><div class="calendar-reader-bar" id="calendar-reader-bar" hidden><p id="calendar-progress" aria-live="polite"></p><button class="button quiet" id="calendar-collapse" aria-controls="calendar-list" aria-expanded="false">收起日程 ↑</button></div><div class="calendar-scroll" id="calendar-scroll" role="region" aria-label="财经日历列表" tabindex="0"><div id="calendar-list"></div></div></div><div class="more-actions calendar-actions" id="calendar-actions"><button class="button quiet" id="calendar-more" aria-controls="calendar-list" aria-expanded="false">继续查看</button><button class="text-button" id="calendar-all" aria-controls="calendar-list" aria-expanded="false">展开全部日程</button></div></section>
<section class="container section online-section" id="online"><div class="section-heading"><div><p class="eyebrow">06 / LIVE WORKBENCH</p><h2>在线观察台</h2></div><a href="technical.html" target="_blank" rel="noopener noreferrer">独立打开 ↗</a></div><p class="section-note">在线组件展示提供者当前内容；上方新闻、报价与表格保留本期时点。黄金现货与期货分别阅读。</p><div id="online-container"></div></section>
<section class="container downloads-section"><div><p class="eyebrow">TAKE THE RESEARCH WITH YOU</p><h2>保存与继续研究</h2></div><div id="downloads"></div></section>
</main><footer><div class="container footer-inner"><span>Toki <b>Quant Lab</b></span><p>财经研究 · 原始来源可追溯 · 解读与事实分别呈现</p><a href="#top">返回顶部 ↑</a></div></footer>
<noscript><p class="container section">请启用浏览器 JavaScript 阅读全部栏目。财经数据亦可下载 <a href="public-data.json">公开数据文件</a>。</p></noscript>
<script type="application/json" id="daily-data">{embedded}</script><script>{js}</script></body></html>'''
    html_path, data_path = folder / "index.html", folder / "public-data.json"
    data_path.write_text(json.dumps(public, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(html, encoding="utf-8")
    return {"html": str(html_path), "public_data": str(data_path)}
