"""Check the actual focus-asset records before a complete edition is delivered."""
from datetime import datetime
import math

FRAMES = ("30m", "1h", "5h", "1D", "1W", "1M")
SUBTYPES = ("summary", "indicators", "moving_averages", "pivot_points")
RATINGS = {"强力买入", "买入", "中性", "卖出", "强力卖出"}


def _finite(value):
    if isinstance(value, dict):
        return any(_finite(v) for v in value.values())
    if isinstance(value, list):
        return any(_finite(v) for v in value)
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result if result.utcoffset() is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def technical_coverage(dataset):
    """Accept private source bindings or public record-ID bindings, never names.

    Legacy datasets without configured focus assets remain usable. For a
    configured asset all six summaries and all eighteen detail records must
    exist, including actual numbers and each period's own observation time.
    """
    assets, cutoff = [], _timestamp(dataset.get("cutoff"))
    for asset in dataset.get("focus_assets", []):
        ids = set(asset.get("record_ids", []))
        source = asset.get("source_id")
        records = [r for r in dataset.get("records", []) if r.get("data", {}).get("topic") == "technical"
                   and (r.get("source_id") == source if source else r.get("id") in ids)]
        groups, invalid, identities = {}, [], set()
        for record in records:
            data = record["data"]
            pair = (data.get("timeframe"), data.get("subtype"))
            if pair[0] not in FRAMES or pair[1] not in SUBTYPES:
                continue
            groups.setdefault(pair, []).append(record)
            identities.add((data.get("symbol"), data.get("contract")))
            observed = _timestamp(record.get("observed_at"))
            valid = (record.get("kind") == "macro" and bool(data.get("symbol"))
                     and cutoff is not None and observed is not None and observed <= cutoff)
            if pair[1] == "summary":
                valid = valid and data.get("rating") in RATINGS and data.get("selected_timeframe") is True
            else:
                valid = valid and _finite(data.get("details"))
            if not valid:
                invalid.append(record.get("id"))
        missing = [f"{frame}/{subtype}" for frame in FRAMES for subtype in SUBTYPES if (frame, subtype) not in groups]
        duplicates = ["/".join(pair) for pair, items in groups.items() if len(items) != 1]
        assets.append({"key": asset.get("key"), "label": asset.get("label"), "records": len(records),
                       "missing": missing, "invalid": invalid, "duplicates": duplicates,
                       "mixed_instruments": len(identities) > 1,
                       "complete": not (missing or invalid or duplicates or len(identities) > 1)})
    return {"complete": all(a["complete"] for a in assets), "assets": assets}


def require_technical_coverage(dataset):
    report = technical_coverage(dataset)
    if dataset.get("mode") == "live" and not report["complete"]:
        problems = []
        for asset in report["assets"]:
            if not asset["complete"]:
                detail = ", ".join(asset["missing"])
                if asset["invalid"] or asset["duplicates"] or asset["mixed_instruments"]:
                    detail += "；记录无效、重复或混合标的"
                problems.append(f"{asset['label'] or asset['key']}：{detail}")
        raise ValueError("四品种技术覆盖未完成，停止交付与发布：" + "；".join(problems))
    return report
