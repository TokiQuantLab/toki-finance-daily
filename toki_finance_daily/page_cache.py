"""Persist low-frequency public-page attempts, including failures."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _cache_paths(source, network):
    root = Path(os.environ.get("TOKI_DAILY_CACHE_DIR") or network.get("cache_dir") or
                Path.home() / ".cache" / "toki-finance-daily" / "public-pages").expanduser()
    request_identity = {"url": source["url"], "headers": source.get("headers", {}), "adapter": source["adapter"]}
    if source.get("browser_timeframes") is not None:
        from .browser_pages import investing_requested_timeframes

        request_identity["browser_timeframes"] = sorted(investing_requested_timeframes(source))
    # A corrected visible-browser configuration must not replay an attempt
    # made by the old headless transport. Existing evidence stays on disk.
    if source.get("transport") == "browser-visible-page" and source.get("browser_headless", True) is False:
        request_identity["browser_headless"] = False
    if source.get("transport") == "jin10-mcp":
        request_identity.update(mcp_tool=source["mcp_tool"], mcp_arguments=source.get("mcp_arguments", {}))
    identity = json.dumps(request_identity, sort_keys=True)
    key = hashlib.sha256(identity.encode()).hexdigest()
    root.mkdir(parents=True, exist_ok=True)
    return tuple(root / (key + suffix) for suffix in (".json", ".bin", ".lock"))


def _clock(now):
    stamp = now or datetime.now(timezone.utc)
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("cache clock must include a timezone")
    return stamp


def _take_lock(lock_path):
    try:
        descriptor = os.open(lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise RuntimeError("This public page is already being collected; no second request was sent") from error
    os.close(descriptor)


def admit_browser_snapshot(source, network, body, observed_at, *, now=None):
    """Import a validated, normally visible Investing page without a request.

    ``observed_at`` is the actual browser capture time, not the import time. A
    successful admission supersedes an earlier failed HTTP attempt explicitly;
    it never assigns that failed response a successful HTTP status. The shared
    response cache then serves the capture for the configured daily interval.
    """
    stamp = _clock(now)
    try:
        captured = observed_at if isinstance(observed_at, datetime) else datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("Browser snapshot observed_at must be an ISO timestamp") from error
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise ValueError("Browser snapshot observed_at must include a timezone")
    if captured > stamp:
        raise ValueError("Browser snapshot observation cannot be in the future")
    observed_at = observed_at if isinstance(observed_at, str) else captured.isoformat()
    interval = source.get("minimum_interval_seconds", 0)
    if isinstance(interval, bool) or not isinstance(interval, int) or interval < 86400:
        raise ValueError("Browser snapshot admission requires a daily minimum_interval_seconds of at least 86400")
    from .browser_pages import is_investing_timeframe_bundle, validate_browser_snapshot

    # Validate before touching cache state, including the real free tab/parser.
    records = validate_browser_snapshot(source, network, body, observed_at)
    bundle = json.loads(body) if is_investing_timeframe_bundle(body) else None
    if bundle is not None:
        # An import-envelope time must not replace the real DOM capture time.
        observed_at = bundle["observed_at"]
        captured = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
    frames = sorted({r["data"]["timeframe"] for r in records if r["data"].get("subtype") != "summary"})
    metadata = {"transport": "browser-visible-page", "snapshot_origin": "local-browser-import",
                "snapshot_format": "visible-dom-projection",
                "content_type": "text/html; charset=utf-8", "fetched_at": observed_at,
                "imported_at": stamp.isoformat(),
                "validated_records": len(records)}
    if bundle is not None:
        metadata.update(snapshot_format=bundle["format"], content_type="application/json; charset=utf-8",
                        requested_timeframes=bundle["requested_timeframes"], captured_timeframes=frames,
                        missing_timeframes=[item["timeframe"] for item in bundle["errors"]],
                        timeframe_errors=bundle["errors"])
    else:
        metadata["selected_timeframe"] = frames[0]
    meta_path, body_path, lock_path = _cache_paths(source, network)
    _take_lock(lock_path)
    try:
        previous = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else None
        if previous:
            previous_attempt = datetime.fromisoformat(previous.get("attempted_at", previous["fetched_at"]).replace("Z", "+00:00"))
            if previous_attempt > stamp:
                raise ValueError("Public-page cache time is in the future")
            if previous.get("state") == "ok":
                previous_capture = datetime.fromisoformat(previous["fetched_at"].replace("Z", "+00:00"))
                if previous_capture > captured:
                    raise ValueError("Browser snapshot cannot replace a newer successful observation")
                if previous.get("superseded_attempt"):
                    metadata["superseded_attempt"] = previous["superseded_attempt"]
                old_body = body_path.read_bytes()
                old_hash = hashlib.sha256(old_body).hexdigest()
                if old_hash != previous["sha256"]:
                    raise ValueError("Previous public-page cache checksum does not match")
                if old_body != body:
                    history = meta_path.parent / "history" / meta_path.stem
                    history.mkdir(parents=True, exist_ok=True)
                    name = previous_capture.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + old_hash[:12]
                    historical_body = history / (name + ".bin")
                    historical_meta = history / (name + ".json")
                    historical_body.write_bytes(old_body)
                    _write_json(historical_meta, previous)
                    metadata["superseded_snapshot"] = {"fetched_at": previous["fetched_at"], "sha256": old_hash,
                                                       "body_path": str(historical_body), "metadata_path": str(historical_meta)}
            elif previous.get("state") in {"error", "attempting"}:
                metadata["superseded_attempt"] = previous
            else:
                raise ValueError("Public-page cache contains an unknown attempt state")
        temporary = body_path.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(body_path)
        success = {"state": "ok", "attempted_at": stamp.isoformat(), "fetched_at": observed_at,
                   "sha256": hashlib.sha256(body).hexdigest(), "metadata": metadata}
        if metadata.get("superseded_attempt"):
            success["superseded_attempt"] = metadata["superseded_attempt"]
        _write_json(meta_path, success)
        return body, {**metadata, "cache_hit": False}
    finally:
        lock_path.unlink(missing_ok=True)


def fetch_source(source, network, fetch, *, now=None):
    """Reuse a source response until its interval expires; never retry these pages.

    This cache is outside edition folders so collecting another edition does not
    trigger another request. The original fetch time survives cache reuse.
    """
    interval = int(source.get("minimum_interval_seconds", 0))
    if interval < 0:
        raise ValueError("minimum_interval_seconds cannot be negative")
    if not interval:
        return fetch(source, {**network, "retries": source.get("retries", network.get("retries", 1))})
    stamp = _clock(now)
    meta_path, body_path, lock_path = _cache_paths(source, network)
    _take_lock(lock_path)
    try:
        if meta_path.is_file():
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
            elapsed = (stamp - datetime.fromisoformat(previous.get("attempted_at", previous["fetched_at"]))).total_seconds()
            if elapsed < 0:
                raise ValueError("Public-page cache time is in the future")
            if elapsed < interval:
                if previous.get("state") != "ok":
                    raise RuntimeError("Previous public-page attempt is retained for the daily interval: " +
                                       str(previous.get("error") or "incomplete attempt"))
                body = body_path.read_bytes()
                if hashlib.sha256(body).hexdigest() != previous["sha256"]:
                    raise ValueError("Public-page cache checksum does not match")
                return body, {**previous["metadata"], "cache_hit": True,
                              "fetched_at": previous["fetched_at"]}
        attempt = {"state": "attempting", "attempted_at": stamp.isoformat(), "fetched_at": stamp.isoformat()}
        _write_json(meta_path, attempt)
        try:
            body, metadata = fetch(source, {**network, "retries": 0})
        except Exception as error:
            _write_json(meta_path, {**attempt, "state": "error",
                                   "error": f"{type(error).__name__}: {error}"[:400]})
            raise
        temporary = body_path.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(body_path)
        captured_at = metadata.get("fetched_at") or (stamp if now is not None else datetime.now(timezone.utc)).isoformat()
        _write_json(meta_path, {**attempt, "fetched_at": captured_at, "state": "ok", "metadata": metadata,
                               "sha256": hashlib.sha256(body).hexdigest()})
        return body, {**metadata, "cache_hit": False, "fetched_at": captured_at}
    finally:
        lock_path.unlink(missing_ok=True)
