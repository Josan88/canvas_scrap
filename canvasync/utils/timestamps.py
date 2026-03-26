from typing import Dict, List, Optional


def _parse_iso_utc(dt_str: str):
    """Parse an ISO 8601 string (potentially with trailing 'Z') into a timezone-aware datetime in UTC.

    Returns None if parsing fails or input is falsy.
    """
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        from datetime import datetime, timezone

        ds = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(ds)
        # Ensure tz-aware and in UTC
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _to_utc_datetime(value):
    """Best-effort conversion of various timestamp representations to UTC datetime.

    Supports:
    - ISO 8601 strings (with or without 'Z')
    - POSIX timestamps (float/int seconds since epoch)
    Returns None if conversion fails.
    """
    from datetime import datetime, timezone

    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except Exception:
            return None
    if isinstance(value, str):
        return _parse_iso_utc(value)
    return None


def _max_iso_datetime(values: List[str]):
    """Return the max ISO timestamp (UTC) from a list of timestamp strings."""
    try:
        from datetime import timezone

        timestamps = [_parse_iso_utc(v) for v in values if v]
        timestamps = [t for t in timestamps if t]
        if not timestamps:
            return None
        return max(timestamps).astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _max_timestamp_from_items(items: List[Dict], keys: List[str]):
    """Best-effort extraction of the newest timestamp from a list of dict items."""
    if not items:
        return None
    collected = []
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in keys:
            value = item.get(key)
            if value:
                collected.append(value)
    return _max_iso_datetime(collected)


def _should_regenerate_resource(existing_metadata, newest_iso: Optional[str]):
    """Decide whether to regenerate a derived resource based on newest item timestamp."""
    if not existing_metadata:
        return True
    if newest_iso:
        existing_dt = _to_utc_datetime(existing_metadata.get("modified_time"))
        newest_dt = _parse_iso_utc(newest_iso)
        if existing_dt and newest_dt and newest_dt <= existing_dt:
            return False
    return True
