"""Response formatting and error pattern grouping."""

import json
import re
from collections import Counter
from datetime import datetime, timezone

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-" r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_IP_RE = re.compile(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}")
_NUMERIC_ID_RE = re.compile(r"\b\d{6,}\b")
_ISO_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?")
_SYSLOG_TS_RE = re.compile(r"[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}")


def _extract_message(raw_line: str) -> tuple[str, dict | None]:
    try:
        parsed = json.loads(raw_line)
        if isinstance(parsed, dict):
            message = parsed.get("message", raw_line)
            return message, parsed
    except (json.JSONDecodeError, TypeError):
        pass
    return raw_line, None


def format_log_streams(data: dict, limit: int) -> list[dict]:
    results = data.get("data", {}).get("result", [])
    log_lines = []
    for stream in results:
        labels = stream.get("stream", {})
        for ts_val, line in stream.get("values", []):
            message, fields = _extract_message(line)
            entry = {
                "timestamp": datetime.fromtimestamp(int(ts_val) / 1_000_000_000, tz=timezone.utc).isoformat(),
                "line": message,
                "labels": labels,
            }
            if fields is not None:
                entry["fields"] = fields
            log_lines.append(entry)
    log_lines.sort(key=lambda x: x["timestamp"], reverse=True)
    return log_lines[:limit]


def format_metric_series(data: dict) -> list[dict]:
    results = data.get("data", {}).get("result", [])
    data_points = []
    for series in results:
        labels = series.get("metric", {})
        for ts_val, count in series.get("values", []):
            data_points.append(
                {
                    "time": datetime.fromtimestamp(int(float(ts_val)), tz=timezone.utc).isoformat(),
                    "value": int(float(count)),
                    "labels": labels,
                }
            )
    return data_points


def _normalize_message(line: str) -> str:
    normalized = _ISO_TS_RE.sub("", line)
    normalized = _SYSLOG_TS_RE.sub("", normalized)
    normalized = _UUID_RE.sub("<UUID>", normalized)
    normalized = _IP_RE.sub("<IP>", normalized)
    normalized = _NUMERIC_ID_RE.sub("<ID>", normalized)
    return normalized.strip()


def group_error_patterns(log_lines: list[dict], top_n: int) -> list[dict]:
    if not log_lines:
        return []

    groups: dict[str, dict] = {}
    pattern_counter: Counter[str] = Counter()

    for entry in log_lines:
        line = entry.get("line", "")
        ts = entry.get("timestamp", "")
        normalized = _normalize_message(line)

        pattern_counter[normalized] += 1
        if normalized not in groups:
            groups[normalized] = {
                "first_seen": ts,
                "last_seen": ts,
                "sample_line": line,
            }
        else:
            grp = groups[normalized]
            if ts and ts < grp["first_seen"]:
                grp["first_seen"] = ts
            if ts and ts > grp["last_seen"]:
                grp["last_seen"] = ts

    total = sum(pattern_counter.values())
    result = []
    for pattern, count in pattern_counter.most_common(top_n):
        grp = groups[pattern]
        result.append(
            {
                "pattern": pattern,
                "count": count,
                "percentage": round(count / total * 100, 1) if total else 0,
                "first_seen": grp["first_seen"],
                "last_seen": grp["last_seen"],
                "sample_line": grp["sample_line"],
            }
        )

    return result
