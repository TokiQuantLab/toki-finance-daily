"""Read an ordinary public technical page after its visible tables load."""
from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
import hashlib
import json
import math
import re
import time


_INVESTING_PATHS = {
    "/crypto/bitcoin/technical": "bitcoin",
    "/commodities/gold-technical": "gold-futures",
    "/commodities/crude-oil-technical": "wti-futures",
    "/commodities/brent-oil-technical": "brent-futures",
    "/currencies/xau-usd-technical": "gold-spot",
    "/indices/nq-100-technical": "nasdaq-100",
}
FREE_INVESTING_TIMEFRAMES = ("30m", "1h", "5h", "1D", "1W", "1M")
_INVESTING_TAB_CODES = {"30m": "30m", "1h": "1h", "5h": "5h", "1D": "1d", "1W": "1w", "1M": "1mo"}
_BUNDLE_FORMAT = "investing-visible-timeframes"


def investing_requested_timeframes(source):
    """An explicit, bounded request for public periods; no locked minute tabs."""
    frames = source.get("browser_timeframes")
    if frames is None:
        return None
    if (not isinstance(frames, list) or not 1 <= len(frames) <= 6
            or any(not isinstance(frame, str) or frame not in FREE_INVESTING_TIMEFRAMES for frame in frames)
            or len(set(frames)) != len(frames)):
        raise ValueError("browser_timeframes must contain unique free Investing timeframes")
    if _browser_kind(source) == "tradingview":
        raise ValueError("browser_timeframes sessions support known Investing instrument pages only")
    return frames


def is_investing_timeframe_bundle(body):
    """HTML remains the backwards-compatible single selected-period input."""
    return isinstance(body, (bytes, str)) and body.lstrip().startswith(b"{" if isinstance(body, bytes) else "{")


def _aware_timestamp(value, label):
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError(f"{label} must be an ISO observation timestamp") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{label} must include a timezone")
    return instant


def _bundle_document(source, network, body, observed_at):
    if _browser_kind(source) == "tradingview":
        raise ValueError("Timeframe bundles support known Investing instrument pages only")
    if not isinstance(body, bytes) or not body or len(body) > _byte_limit(network):
        raise ValueError("Timeframe bundle must be nonempty bytes within the response byte limit")
    try:
        bundle = json.loads(body)
    except (UnicodeError, ValueError) as error:
        raise ValueError("Investing timeframe bundle is not valid UTF-8 JSON") from error
    if (not isinstance(bundle, dict) or bundle.get("format") != _BUNDLE_FORMAT
            or type(bundle.get("version")) is not int or bundle["version"] != 1
            or bundle.get("source_url") != source["url"]):
        raise ValueError("Investing timeframe bundle format, version or source URL does not match")
    requested = investing_requested_timeframes({**source, "browser_timeframes": bundle.get("requested_timeframes")})
    configured = investing_requested_timeframes(source)
    if requested is None or configured is not None and set(configured) != set(requested):
        raise ValueError("Investing timeframe bundle must match the requested free periods")
    completed = _aware_timestamp(bundle.get("observed_at"), "Bundle observed_at")
    if completed > _aware_timestamp(observed_at, "Snapshot observed_at"):
        raise ValueError("Timeframe bundle observation cannot be later than the collection observation")
    snapshots, errors = bundle.get("snapshots"), bundle.get("errors")
    if (not isinstance(snapshots, list) or not 1 <= len(snapshots) <= len(requested)
            or not isinstance(errors, list) or len(errors) > len(requested)):
        raise ValueError("Timeframe bundle requires bounded successful snapshots and errors")
    seen, captures = set(), []
    for item in snapshots:
        if not isinstance(item, dict) or item.get("timeframe") not in requested or item["timeframe"] in seen:
            raise ValueError("Timeframe bundle successful periods must be unique and requested")
        seen.add(item["timeframe"])
        captured = _aware_timestamp(item.get("observed_at"), "Period observed_at")
        if captured > completed:
            raise ValueError("Period observation cannot be later than the bundle observation")
        captures.append(captured)
        html = item.get("html")
        if not isinstance(html, str) or not html or item.get("sha256") != hashlib.sha256(html.encode()).hexdigest():
            raise ValueError("Timeframe bundle snapshot checksum does not match its HTML")
    if completed != max(captures):
        raise ValueError("Bundle observed_at must retain the latest real period capture time")
    missing = set()
    for error in errors:
        if (not isinstance(error, dict) or error.get("timeframe") not in requested
                or error["timeframe"] in seen or error["timeframe"] in missing
                or not isinstance(error.get("error"), str) or not error["error"] or len(error["error"]) > 300):
            raise ValueError("Timeframe bundle errors must describe unique uncaptured requested periods")
        missing.add(error["timeframe"])
    if seen | missing != set(requested):
        raise ValueError("Timeframe bundle must explicitly describe every requested period")
    return bundle


def parse_investing_timeframe_bundle(body, source, observed_at, network=None):
    """Use every period's own selected tables, timestamps and observation.

    Six summaries remain available on a partial capture, but only an actually
    selected snapshot supplies its period's details and component ratings.
    """
    bundle = _bundle_document(source, network or {}, body, observed_at)
    summaries, details, instrument = {}, {}, None
    samples = sorted(bundle["snapshots"], key=lambda item: _aware_timestamp(item["observed_at"], "Period observed_at"))
    for item in samples:
        records = validate_browser_snapshot(source, network or {}, item["html"].encode(), item["observed_at"])
        identity = (records[0]["data"].get("symbol"), records[0]["data"].get("contract"))
        if instrument is not None and instrument != identity:
            raise ValueError("Timeframe bundle cannot mix visible instruments or contracts")
        instrument = identity
        frames = {record["data"]["timeframe"] for record in records if record["data"].get("subtype") != "summary"}
        if frames != {item["timeframe"]}:
            raise ValueError("Timeframe bundle label does not match the DOM's actually selected free period")
        for record in records:
            data = record["data"]
            frame, subtype = data["timeframe"], data["subtype"]
            if subtype == "summary":
                if frame == item["timeframe"] or frame not in summaries or not summaries[frame]["data"].get("selected_timeframe"):
                    summaries[frame] = record
            else:
                details[(frame, subtype)] = record
    result = [summaries[frame] for frame in FREE_INVESTING_TIMEFRAMES if frame in summaries]
    for frame in FREE_INVESTING_TIMEFRAMES:
        result.extend(details[(frame, subtype)] for subtype in ("indicators", "moving_averages", "pivot_points") if (frame, subtype) in details)
    return result


def investing_visible_capture_script():
    """Shared expression for a normal browser's visible technical DOM capture."""
    return (Path(__file__).parent / "assets" / "investing-visible-capture.js").read_text(encoding="utf-8")


class _SnapshotShape(HTMLParser):
    def __init__(self):
        super().__init__()
        self.invalid = False
        self.tables = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        if (tag in {"script", "style", "template", "iframe", "form", "nav", "aside", "footer", "header"}
                or "hidden" in attrs or attrs.get("aria-hidden") == "true" or "hidden" in classes
                or "display:none" in style or "visibility:hidden" in style):
            self.invalid = True
        if tag == "table":
            self.tables += 1


def _browser_kind(source):
    parts = urlsplit(source["url"])
    if (parts.scheme != "https" or parts.username or parts.password or parts.query or parts.fragment
            or parts.port not in {None, 443}):
        raise ValueError("Browser transport requires an allowed public HTTPS technical URL")
    if (parts.hostname == "www.tradingview.com" and source.get("adapter") == "tradingview_technical"
            and re.fullmatch(r"/symbols/[A-Za-z0-9_.!:-]+/technicals/", parts.path)):
        return "tradingview"
    if (parts.hostname == "cn.investing.com" and source.get("adapter") == "investing_instrument_technical"
            and parts.path in _INVESTING_PATHS):
        return _INVESTING_PATHS[parts.path]
    raise ValueError("Browser transport supports only configured public TradingView and known Investing technical pages")


def _byte_limit(network):
    value = network.get("max_bytes", 3000000)
    if isinstance(value, bool) or not isinstance(value, int) or not 1024 <= value <= 20000000:
        raise ValueError("max_bytes must be an integer between 1024 and 20000000")
    return value


def validate_browser_snapshot(source, network, body, observed_at):
    """Validate a public Investing DOM using its production finance parser.

    Known paths also bind the visible instrument identity, independent of a
    mistakenly configured source name. Locked tabs may be present, but may not
    be the selected detail timeframe. No request or browser is opened here.
    """
    if is_investing_timeframe_bundle(body):
        return parse_investing_timeframe_bundle(body, source, observed_at, network)
    kind = _browser_kind(source)
    if kind == "tradingview":
        raise ValueError("Local snapshot admission supports known Investing instrument pages only")
    limit = _byte_limit(network)
    if not isinstance(body, bytes) or not body or len(body) > limit:
        raise ValueError("Browser snapshot must be nonempty bytes within the response byte limit")
    from .investing_instrument import parse_investing_instrument_technical

    records = parse_investing_instrument_technical(body, source, observed_at)
    if not records:
        raise ValueError("Browser snapshot contains no public technical records")
    shape = _SnapshotShape()
    shape.feed(body.decode("utf-8-sig"))
    if shape.invalid or shape.tables != 4:
        raise ValueError("Browser snapshot must contain only a visible projection of four public technical tables")
    captured = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    header = records[0]["data"].get("instrument_header", "").strip()
    symbol = records[0]["data"].get("symbol", "").upper()
    prefixes = {"gold-futures": ("黄金期货", "Gold Futures"),
                "wti-futures": ("WTI原油期货", "Crude Oil WTI Futures"),
                "brent-futures": ("伦敦布伦特原油期货", "Brent Oil Futures")}
    if kind in prefixes:
        matches = header.startswith(prefixes[kind]) and bool(records[0]["data"].get("contract"))
    else:
        symbols = {"bitcoin": {"BTC", "BTC/USD", "BTCUSD"},
                   "gold-spot": {"XAU", "XAU/USD", "XAUUSD"},
                   "nasdaq-100": {"NDX", "NAS100", "NASDAQ100"}}
        matches = symbol in symbols[kind] and not records[0]["data"].get("contract")
    if not matches:
        raise ValueError("Browser snapshot visible instrument does not match its allowed source URL")
    numeric = False
    frames = set()
    detail_types = set()
    summary_frames = set()
    for record in records:
        data = record["data"]
        provider_at = data.get("provider_at")
        if provider_at and datetime.fromisoformat(provider_at.replace("Z", "+00:00")) > captured:
            raise ValueError("Browser snapshot provider timestamp cannot be later than its observation")
        if data.get("subtype") != "summary":
            detail_types.add(data.get("subtype"))
            frames.add(data.get("timeframe"))
            def finite(value):
                if isinstance(value, dict):
                    return any(finite(v) for v in value.values())
                if isinstance(value, list):
                    return any(finite(v) for v in value)
                return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            numeric = numeric or finite(data.get("details"))
        else:
            summary_frames.add(data.get("timeframe"))
    free_frames = {"30m", "1h", "5h", "1D", "1W", "1M"}
    if (not numeric or len(frames) != 1 or frames.pop() not in free_frames
            or summary_frames != free_frames or detail_types != {"indicators", "moving_averages", "pivot_points"}):
        raise ValueError("Browser snapshot requires finite visible values for one selected free timeframe")
    return records


_INVESTING_TABLE_SIGNATURE = """() => {
    const selector=document.querySelector('[data-test="timeframe-selection"]');
    return selector ? [...selector.parentElement.querySelectorAll('table')]
        .filter(t=>t.getClientRects().length && t.rows.length && [3,8].includes(t.rows[0].children.length))
        .map(t=>t.innerText.replace(/\\s+/g,' ').trim()).join('||') : '';
}"""
_INVESTING_SELECTED_TAB = """() => {
    const tabs=[...document.querySelectorAll('[data-test="timeframe-selection"] button[role="tab"]')];
    const selected=tabs.filter(t=>t.getAttribute('aria-selected')==='true'||t.classList.contains('!border-inv-blue-500'));
    return selected.length===1 ? selected[0].getAttribute('data-test') : null;
}"""
_INVESTING_PERIOD_READY = """args => {
    const selector=document.querySelector('[data-test="timeframe-selection"]');
    if(!selector)return false;
    const scope=selector.parentElement;
    const tabs=[...selector.querySelectorAll('button[role="tab"]')];
    const selected=tabs.filter(t=>t.getAttribute('aria-selected')==='true'||t.classList.contains('!border-inv-blue-500'));
    if(selected.length!==1||selected[0].dataset.test!==args.code)return false;
    if([...scope.querySelectorAll('[aria-busy="true"],.animate-spin,.animate-pulse')].some(n=>n.getClientRects().length))return false;
    const tables=[...scope.querySelectorAll('table')].filter(t=>t.getClientRects().length);
    if(tables.length!==4)return false;
    const signature=tables.filter(t=>t.rows.length && [3,8].includes(t.rows[0].children.length))
        .map(t=>t.innerText.replace(/\\s+/g,' ').trim()).join('||');
    if(args.previous && signature===args.previous)return false;
    const numeric=tables.every(table=>[...table.rows].some(row=>{
        const cells=[...row.children].filter(c=>['TH','TD'].includes(c.tagName)&&c.getClientRects().length);
        if(!cells.length||cells[0].tagName!=='TD')return false;
        if(cells.length===4)return /买入|卖出|中性|buy|sell|neutral/i.test(cells[1].innerText);
        const number=cell=>/^[-+]?\\d[\\d,.]*(?:\\s|$)/.test(cell.innerText.trim());
        return cells.length===3 ? number(cells[1]) : cells.length===8 && number(cells[4]);
    }));
    if(!numeric)return false;
    const now=performance.now(), previous=scope.__tokiTechnicalPeriodStability;
    if(!previous || previous.code!==args.code || previous.signature!==signature){
        scope.__tokiTechnicalPeriodStability={code:args.code,signature,since:now,reads:1};
        return false;
    }
    previous.reads+=1;
    return previous.reads>=2 && now-previous.since>=(args.stableMillis ?? 250);
}"""


def capture_investing_timeframes(page, source, network, *, http_status=None):
    """Capture a normally opened page, activating each requested free tab once.

    A selected class alone is insufficient: after a switch, the visible table
    signature must change and all four tables must contain actual values. A
    blocked or unchanged tab is an explicit gap; there is no second click.
    """
    requested = investing_requested_timeframes(source)
    if requested is None:
        raise ValueError("A multi-period capture requires explicit browser_timeframes")
    limit = _byte_limit(network)
    tab_timeout = float(source.get("browser_timeframe_timeout_seconds", 12))
    session_timeout = float(source.get("browser_session_timeout_seconds", 120))
    if not 1 <= tab_timeout <= 30 or not 1 <= session_timeout <= 180:
        raise ValueError("Public period/session time budgets are invalid")
    deadline = time.monotonic() + session_timeout
    page.locator('[data-test="timeframe-selection"]').wait_for(state="visible", timeout=min(int(tab_timeout * 1000), max(1, int((deadline - time.monotonic()) * 1000))))
    initial = page.evaluate(_INVESTING_SELECTED_TAB)
    initial = next((frame for frame, code in _INVESTING_TAB_CODES.items() if code == initial), None)
    order = ([initial] if initial in requested else []) + [frame for frame in requested if frame != initial]
    snapshots, errors = [], []
    for frame in order:
        if time.monotonic() >= deadline:
            errors.append({"timeframe": frame, "error": "Public timeframe session time budget exhausted"})
            continue
        try:
            code = _INVESTING_TAB_CODES[frame]
            previous = ""
            if not (frame == initial and not snapshots and not errors):
                previous = page.evaluate(_INVESTING_TABLE_SIGNATURE)
                tab = page.locator(f'[data-test="timeframe-selection"] button[role="tab"][data-test="{code}"]')
                tab.wait_for(state="visible", timeout=min(int(tab_timeout * 1000), max(1, int((deadline - time.monotonic()) * 1000))))
                # Native button activation matches the observed normal UI.
                # No force click, paid tab, challenge handling or refreshing.
                tab.press("Space", timeout=min(int(tab_timeout * 1000), max(1, int((deadline - time.monotonic()) * 1000))))
            page.wait_for_function(_INVESTING_PERIOD_READY, arg={"code": code, "previous": previous},
                                   timeout=min(int(tab_timeout * 1000), max(1, int((deadline - time.monotonic()) * 1000))))
            html = page.evaluate(investing_visible_capture_script())
            observed_at = datetime.now(timezone.utc).isoformat()
            body = html.encode("utf-8")
            records = validate_browser_snapshot(source, network, body, observed_at)
            selected = {r["data"]["timeframe"] for r in records if r["data"].get("subtype") != "summary"}
            if selected != {frame}:
                raise ValueError("Visible tables do not belong to the requested free period")
            snapshots.append({"timeframe": frame, "observed_at": observed_at, "html": html,
                              "sha256": hashlib.sha256(body).hexdigest()})
        except Exception as error:
            errors.append({"timeframe": frame, "error": f"{type(error).__name__}: {error}"[:300]})
    if not snapshots:
        raise RuntimeError("No requested free technical period was captured; " + "; ".join(error["error"] for error in errors)[:400])
    observed_at = max(snapshots, key=lambda item: _aware_timestamp(item["observed_at"], "Period observed_at"))["observed_at"]
    bundle = {"format": _BUNDLE_FORMAT, "version": 1, "source_url": source["url"],
              "requested_timeframes": requested, "observed_at": observed_at, "snapshots": snapshots, "errors": errors}
    body = json.dumps(bundle, ensure_ascii=False, allow_nan=False).encode()
    if len(body) > limit:
        raise ValueError("Rendered timeframe session exceeds response byte limit")
    records = parse_investing_timeframe_bundle(body, source, observed_at, network)
    metadata = {"transport": "browser-visible-page", "content_type": "application/json; charset=utf-8",
                "snapshot_format": _BUNDLE_FORMAT, "fetched_at": observed_at,
                "requested_timeframes": requested, "captured_timeframes": [item["timeframe"] for item in snapshots],
                "missing_timeframes": [item["timeframe"] for item in errors], "timeframe_errors": errors,
                "validated_records": len(records)}
    if http_status is not None:
        metadata["http_status"] = http_status
    return body, metadata


def fetch_browser_page(source, network):
    """Read a normally rendered public page; never interact with access gates."""
    kind = _browser_kind(source)
    requested = investing_requested_timeframes(source)
    timeframe = source.get("timeframe", "1D")
    if kind == "tradingview" and timeframe not in {"1m", "5m", "15m", "30m", "1h", "2h", "4h", "1D", "1W", "1M"}:
        raise ValueError("Unsupported technical timeframe")
    timeout = float(source.get("browser_timeout_seconds", 25))
    headless = source.get("browser_headless", True)
    if type(headless) is not bool:
        raise ValueError("browser_headless must be a boolean")
    if not 1 <= timeout <= 60:
        raise ValueError("browser_timeout_seconds must be between 1 and 60")
    limit = _byte_limit(network)
    from playwright.sync_api import sync_playwright

    url = source["url"]
    deadline = time.monotonic() + timeout
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, timeout=max(1, int((deadline - time.monotonic()) * 1000)))
        try:
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            response = page.goto(url, wait_until="domcontentloaded", timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            if response is None or response.status >= 400:
                raise RuntimeError(f"Public technical page returned HTTP {response.status if response else 'unknown'}")
            if requested is not None:
                body, metadata = capture_investing_timeframes(page, source, network, http_status=response.status)
                return body, {**metadata, "browser_headless": headless}
            remaining = max(1, int((deadline - time.monotonic()) * 1000))
            if kind == "tradingview":
                tab = page.locator(f'button[role="tab"][id="{timeframe}"]')
                tab.wait_for(state="visible", timeout=remaining)
                if tab.get_attribute("aria-selected") != "true":
                    tab.click(timeout=max(1, int((deadline - time.monotonic()) * 1000)))
            else:
                # Investing supplies its currently selected free timeframe.
                # Do not click a tab to manufacture another requested period.
                page.locator('[data-test="timeframe-selection"]').wait_for(state="visible", timeout=remaining)
            values_ready = """() => Array.from(document.querySelectorAll('table tr')).some(row => {
                    const cells = row.querySelectorAll('td');
                    return row.getClientRects().length && cells.length >= 3 &&
                           /[0-9]/.test(cells[1].textContent) &&
                           /^(Buy|Sell|Neutral|Strong buy|Strong sell|买入|卖出|中性|超买|超卖|少波动|高波动)$/i.test(cells[2].textContent.trim());
                })"""
            if kind != "tradingview":
                values_ready = """() => {
                    const selector=document.querySelector('[data-test="timeframe-selection"]');
                    if(!selector)return false;
                    const tables=[...selector.parentElement.querySelectorAll('table')].filter(t=>t.getClientRects().length);
                    return tables.length===4 && tables.every(table=>[...table.rows].some(row=>{
                        const cells=[...row.children].filter(c=>['TH','TD'].includes(c.tagName)&&c.getClientRects().length);
                        if(!cells.length||cells[0].tagName!=='TD')return false;
                        if(cells.length===4)return /买入|卖出|中性|buy|sell|neutral/i.test(cells[1].innerText);
                        const number=cell=>/^[-+]?\d[\d,.]*(?:\s|$)/.test(cell.innerText.trim());
                        return cells.length===3 ? number(cells[1]) : cells.length===8 && number(cells[4]);
                    }));
                }"""
            page.wait_for_function(
                values_ready,
                timeout=max(1, int((deadline - time.monotonic()) * 1000)),
            )
            if kind == "tradingview":
                body = page.content().encode("utf-8")
            else:
                body = page.evaluate(investing_visible_capture_script()).encode("utf-8")
            if len(body) > limit:
                raise ValueError("Rendered public page exceeds response byte limit")
            observed_at = datetime.now(timezone.utc).isoformat()
            metadata = {"transport": "browser-visible-page", "content_type": "text/html; charset=utf-8",
                        "http_status": response.status, "fetched_at": observed_at}
            if kind == "tradingview":
                metadata["requested_timeframe"] = timeframe
            else:
                records = validate_browser_snapshot(source, network, body, observed_at)
                metadata["selected_timeframe"] = next(r["data"]["timeframe"] for r in records if r["data"].get("subtype") != "summary")
                metadata["snapshot_format"] = "visible-dom-projection"
            return body, metadata
        finally:
            browser.close()
