"""Uniform UTC handling for PostgreSQL TIMESTAMPTZ and legacy naive timestamps."""
from datetime import datetime, timezone


def as_utc(value: datetime) -> datetime:
    """DB legacy-naive timestamps are interpreted as UTC, never local server time."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
