"""Offline editorial poster rendering. All finance content comes from evidence."""

from __future__ import annotations

import html
import math
import struct
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


def _text(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _safe_url(value: Any) -> str:
    """Accept ordinary web links; never turn source data into executable URLs."""
    raw = "" if value is None else str(value).strip()
    if any(ord(character) < 32 for character in raw):
        return ""
    try:
        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in {"https", "http"} or not parsed.netloc:
            return ""
        if parsed.username or parsed.password:
            return ""
    except ValueError:
        return ""
    return raw


def _timestamp(value: Any, timezone: str, *, short: bool = False) -> str:
    if not value:
        return "未标注发布时间"
    raw = str(value)
    if len(raw) == 10:
        return raw + " · 时间待公布"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return raw + " · 来源未标注时区"
        local = parsed.astimezone(ZoneInfo(timezone))
        return local.strftime("%m/%d %H:%M" if short else "%Y-%m-%d %H:%M")
    except (ValueError, KeyError):
        return raw


def _day(value: Any) -> tuple[str, str, str]:
    raw = str(value or "")
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
        weekday = "一二三四五六日"[parsed.weekday()]
        return parsed.strftime("%Y"), parsed.strftime("%m.%d"), f"星期{weekday}"
    except ValueError:
        return "", raw, ""


def _local_calendar_date(record: dict[str, Any] | None, event_at: Any) -> str | None:
    """Keep an official New York date as a date, never assume a midnight time."""
    if not record or record.get("kind") != "calendar":
        return None
    data = record.get("data") or {}
    if (
        data.get("event_at") != event_at or data.get("time_precision") != "date"
        or data.get("provider_timezone") != "America/New_York"
    ):
        return None
    raw = str(event_at or "")
    if len(raw) != 10:
        return None
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return None
    return f"{parsed.month}月{parsed.day}日 · 美国当地日期"


def _evidence_time(record: dict[str, Any], timezone: str) -> str:
    """Describe the evidence's actual time precision without adding a clock time."""
    data = record.get("data") or {}
    local_calendar_date = _local_calendar_date(record, data.get("event_at"))
    if local_calendar_date:
        return "事件 " + local_calendar_date
    if record.get("published_at"):
        return _timestamp(record["published_at"], timezone, short=True)
    if data.get("published_date") and data.get("date_precision") == "day":
        date = str(data["published_date"])
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            pass
        else:
            return date + " · 仅日期" + (" · 背景资料" if data.get("background_only") else "")
    if record.get("kind") == "macro":
        if data.get("topic") == "technical":
            if data.get("provider_at"):
                return "来源更新 " + _timestamp(data["provider_at"], timezone, short=True)
            return "观察时点 " + _timestamp(record.get("observed_at"), timezone, short=True) + " · 来源更新时间未确认"
        if data.get("topic") == "market":
            return "报价时点 " + _timestamp(data.get("provider_at") or data.get("period") or record.get("observed_at"), timezone, short=True)
        period = f"统计期 {data['period']} · " if data.get("period") else ""
        return period + "截至 " + _timestamp(record.get("observed_at"), timezone, short=True)
    if record.get("kind") == "calendar" and data.get("event_at"):
        return "事件 " + _timestamp(data["event_at"], timezone, short=True)
    return "背景资料 · 来源未标注发布日期"


def render_html(brief: dict[str, Any], dataset: dict[str, Any]) -> str:
    """Render the selected news edition with concise source names and times.

    Full data tables remain in the research website. The poster uses only the
    editorial brief; dataset records provide provenance, never extra panels.
    """
    timezone = str(dataset.get("timezone", "Asia/Shanghai"))
    records = {
        str(record["id"]): record for record in dataset.get("records", [])
        if record.get("id") is not None
    }
    sources = {str(source.get("id")): source for source in dataset.get("sources", [])}
    lead = brief.get("lead") or {}
    articles = [lead, *brief.get("stories", []), *brief.get("calendar", []), *brief.get("watchlist", [])]
    for article in articles:
        for identifier in article.get("evidence_ids", []):
            if str(identifier) not in records:
                raise ValueError(f"日报引用了不存在的证据：{identifier}")

    def provenance(ids: list[Any]) -> str:
        descriptions, seen = [], set()
        for identifier in ids:
            record = records[str(identifier)]
            source = sources.get(str(record.get("source_id")), {})
            data = record.get("data") or {}
            name = str(source.get("name") or data.get("publisher") or data.get("provider") or "原始来源")
            time = _evidence_time(record, timezone)
            identity = (name, time)
            if identity in seen:
                continue
            seen.add(identity)
            url = _safe_url(record.get("url"))
            label = _text(name)
            if url:
                label = (
                    f'<a class="source-credit" href="{_text(url)}" target="_blank" '
                    f'rel="noopener noreferrer" title="{_text(record.get("title") or name)}">{label}</a>'
                )
            descriptions.append(label + " · " + _text(time))
        return " / ".join(descriptions)

    def section_heading(number: str, name: str, subtitle: str) -> str:
        return (
            '<div class="section-heading"><div class="section-name">'
            f'<span class="section-number">{number}</span><h2>{_text(name)}</h2></div>'
            f'<p>{_text(subtitle)}</p></div>'
        )

    def fact_block(story: dict[str, Any]) -> str:
        source_line = provenance(story.get("evidence_ids", []))
        return (
            '<div class="fact-analysis"><div class="fact">'
            '<span class="mini-label">事实 / FACT</span>'
            f'<p>{_text(story.get("fact"))}</p></div>'
            '<div class="analysis"><span class="mini-label">解读 / VIEW</span>'
            f'<p>{_text(story.get("analysis"))}</p></div></div>'
            + (
                '<div class="story-watch"><span>接下来观察</span>'
                f'<p>{_text(story.get("watch"))}</p></div>' if story.get("watch") else ""
            )
            + (f'<p class="provenance">{source_line}</p>' if source_line else "")
        )

    year, month_day, weekday = _day(dataset.get("date"))
    headline = str(brief.get("headline", "全球财经日报"))
    if "，" in headline and len(headline) > 12:
        first, second = headline.split("，", 1)
        lines = [first + "，", second]
        headline_html = "".join(f'<span class="headline-line">{_text(line)}</span>' for line in lines)
        headline_attrs = f'class="segmented" style="--headline-chars:{max(len(line) for line in lines)}"'
    else:
        headline_html, headline_attrs = _text(headline), ""
    demo_banner = (
        '<div class="demo-banner"><span>DEMO / 离线演示</span>'
        '<span>演示样刊 · 非当日真实资讯</span></div>' if dataset.get("mode") == "demo" else ""
    )
    story_html = []
    for index, story in enumerate(brief.get("stories", []), 1):
        story_html.append(
            '<article class="story">'
            f'<div class="story-index">{index:02d}</div><div class="story-body">'
            f'<p class="eyebrow">{_text(story.get("category", "全球财经"))}</p>'
            f'<h3>{_text(story.get("title"))}</h3>{fact_block(story)}</div></article>'
        )
    news_section = (
        '<section class="news-section">'
        + section_heading("01", "要闻与资产解读", "事件、传导与后续观察")
        + "".join(story_html) + '</section>' if story_html else ""
    )

    calendar_html = []
    has_local_calendar_dates = False
    for event in brief.get("calendar", []):
        ids = event.get("evidence_ids", [])
        evidence = next((records[str(identifier)] for identifier in ids
                         if records[str(identifier)].get("kind") == "calendar"
                         and (records[str(identifier)].get("data") or {}).get("event_at") == event.get("event_at")), None)
        local_calendar_date = _local_calendar_date(evidence, event.get("event_at"))
        has_local_calendar_dates = has_local_calendar_dates or bool(local_calendar_date)
        if local_calendar_date:
            date_label, locality = local_calendar_date.split(" · ", 1)
            event_time_html = f'{_text(date_label)}<br><small>{_text(locality)}</small>'
        else:
            event_time_html = _text(_timestamp(event.get("event_at"), timezone, short=True))
        source_line = provenance(ids)
        calendar_html.append(
            '<article class="calendar-event">'
            f'<div class="event-time">{event_time_html}</div><div class="event-main">'
            f'<p class="event-region">{_text(event.get("region") or "全球")}</p>'
            f'<h3>{_text(event.get("title"))}</h3><p class="event-note">{_text(event.get("note"))}</p>'
            + (f'<p class="provenance">{source_line}</p>' if source_line else "")
            + '</div></article>'
        )
    observations = []
    for index, item in enumerate(brief.get("watchlist", []), 1):
        source_line = provenance(item.get("evidence_ids", []))
        observations.append(
            '<article class="observation">'
            f'<span class="observation-number">{index:02d}</span><div>'
            f'<h3>{_text(item.get("title"))}</h3><p class="observation-detail">{_text(item.get("detail"))}</p>'
            + (f'<p class="provenance">{source_line}</p>' if source_line else "")
            + '</div></article>'
        )
    cutoff = _text(_timestamp(dataset.get("cutoff"), timezone))
    display_timezone = "北京时间" if timezone == "Asia/Shanghai" else timezone
    calendar_time_note = (
        "日程时刻为北京时间；仅有日期的美国日程按当地日期显示。"
        if has_local_calendar_dates and timezone == "Asia/Shanghai" else
        f"{display_timezone} · 具体发布安排以官方公告为准。"
    )
    calendar_section = (
        '<section class="calendar-section">'
        + section_heading("02", "接下来，关注什么", "财经日历 / UPCOMING")
        + f'<p class="timezone-note">{_text(calendar_time_note)}</p>'
        + '<div class="calendar-list">' + "".join(calendar_html) + '</div></section>' if calendar_html else ""
    )
    watch_section = (
        '<section class="watch-section">'
        + section_heading("03", "观察清单", "值得追踪的问题")
        + "".join(observations)
        + '<p class="editorial-note">本文观点仅供参考，不构成投资建议。</p></section>' if observations else ""
    )
    css = Path(__file__).with_name("assets").joinpath("poster.css").read_text(encoding="utf-8")
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'none'; script-src 'none'; base-uri 'none'; form-action 'none'">
<title>{_text(brief.get("headline"))} · Toki Quant Lab</title><style>{css}</style></head>
<body><main class="poster">
{demo_banner}
<header class="masthead"><div class="brand"><span class="brand-symbol">TQ<span>↗</span></span>
<div><p>Toki <b>Quant Lab</b></p><small>GLOBAL FINANCE / 全球财经观察</small></div></div>
<div class="date-block"><span>{_text(year)}</span><strong>{_text(month_day)}</strong><small>{_text(weekday)}</small></div></header>
<section class="cover"><div class="cover-kicker"><span>DAILY BRIEFING</span><span>{_text(brief.get("edition", "全球宏观观察"))}</span></div>
<h1 {headline_attrs}>{headline_html}</h1><p class="dek">{_text(brief.get("dek"))}</p>
<div class="cover-meta"><span>截至 {cutoff}</span><span>{_text(display_timezone)}</span></div></section>
<section class="lead-section"><div class="lead-top"><span class="lime-label">TODAY’S FOCUS / 今日主线</span>
<span class="lead-category">{_text(lead.get("category"))}</span></div>
<div class="lead-heading"><h2>{_text(lead.get("title"))}</h2></div>{fact_block(lead)}</section>
{news_section}
{calendar_section}
{watch_section}
<footer><div class="footer-brand"><span>Toki Quant Lab</span><span>跟踪全球金融脉动</span></div></footer>
</main></body></html>"""


def render_png(
    html_path: str | Path, png_path: str | Path, width: int = 1080,
    scale: float | None = None,
) -> dict[str, Any]:
    """Render CSS layout width at an independent physical pixel density.

    Narrow layouts default to 2x pixels for crisp mobile sharing. Diagnostics
    width/height use CSS pixels; pixel_width/pixel_height come from the PNG.
    """
    if not isinstance(width, int) or isinstance(width, bool) or width < 320 or width > 4096:
        raise ValueError("渲染宽度必须在 320–4096 像素之间。")
    if scale is None:
        scale = 2.0 if width <= 700 else 1.0
    try:
        scale = float(scale)
    except (TypeError, ValueError) as exc:
        raise ValueError("像素倍率必须在 1–4 之间。") from exc
    if not math.isfinite(scale) or not 1 <= scale <= 4:
        raise ValueError("像素倍率必须在 1–4 之间。")
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "PNG 渲染需要 Playwright。执行 python -m pip install '.[poster]'，"
            "再执行 python -m playwright install chromium。HTML 可直接离线打开。"
        ) from exc
    input_path = Path(html_path).expanduser().resolve()
    output_path = Path(png_path).expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"HTML 文件不存在：{input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(
                    viewport={"width": width, "height": 1200},
                    device_scale_factor=scale,
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                )
                # The renderer needs no remote resources; block any accidental requests.
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(("file:", "data:")) else route.abort())
                page.goto(input_path.as_uri(), wait_until="load")
                page.evaluate("document.fonts.ready")
                diagnostics = page.evaluate("""() => {
                    const horizontal = [];
                    const textOverflow = [];
                    const width = document.documentElement.clientWidth;
                    for (const element of document.querySelectorAll('main *')) {
                        const rect = element.getBoundingClientRect();
                        const style = getComputedStyle(element);
                        const summary = {
                            tag: element.tagName.toLowerCase(),
                            class: element.className.baseVal ?? element.className,
                            text: (element.textContent || '').trim().slice(0, 100)
                        };
                        if (rect.left < -1 || rect.right > width + 1) horizontal.push(summary);
                        if (element.children.length === 0 && !['SVG', 'PATH', 'CIRCLE', 'ELLIPSE'].includes(element.tagName)
                            && ((element.scrollWidth > element.clientWidth + 1 && style.overflowX !== 'visible')
                            || (element.scrollHeight > element.clientHeight + 1 && style.overflowY !== 'visible'))) {
                            textOverflow.push(summary);
                        }
                    }
                    return {
                        width: width,
                        height: Math.ceil(document.documentElement.scrollHeight),
                        horizontal_overflow: horizontal,
                        text_overflow: textOverflow,
                        document_horizontal_overflow: document.documentElement.scrollWidth > width,
                        fonts_ready: document.fonts.status === 'loaded'
                    };
                }""")
                png_bytes = page.screenshot(
                    path=str(output_path), type="png", full_page=True,
                    animations="disabled", scale="device",
                )
                pixel_width, pixel_height = struct.unpack(">II", png_bytes[16:24])
                diagnostics["device_scale_factor"] = scale
                diagnostics["pixel_width"] = pixel_width
                diagnostics["pixel_height"] = pixel_height
                diagnostics["html_path"] = str(input_path)
                diagnostics["png_path"] = str(output_path)
                diagnostics["ok"] = not (
                    diagnostics["horizontal_overflow"]
                    or diagnostics["text_overflow"]
                    or diagnostics["document_horizontal_overflow"]
                )
                return diagnostics
            finally:
                browser.close()
    except Error as exc:
        message = str(exc)
        if "Executable doesn't exist" in message or "browserType.launch" in message:
            raise RuntimeError(
                "Chromium 尚未安装或无法启动。执行 python -m playwright install chromium；"
                "Linux 缺少系统依赖时执行 python -m playwright install --with-deps chromium。"
            ) from exc
        raise RuntimeError(f"PNG 渲染失败：{message}") from exc
