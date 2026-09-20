"""UTC clocks and strict timestamp parsing shared by runtime operations."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> str: ...


def utc_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("INVALID_UTC_TIMESTAMP") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return parsed.astimezone(UTC)


def timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("TIMESTAMP_TIMEZONE_REQUIRED")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class SystemClock:
    def now(self) -> str:
        return timestamp(datetime.now(UTC))


@dataclass(frozen=True)
class FrozenClock:
    value: str

    def __post_init__(self) -> None:
        utc_datetime(self.value)

    def now(self) -> str:
        return self.value


@dataclass(frozen=True)
class WorkflowTimes:
    approved_at: str
    apply_at: str
    expires_at: str


def workflow_times(clock: Clock, ttl_seconds: int = 900) -> WorkflowTimes:
    if not 1 <= ttl_seconds <= 3600:
        raise ValueError("APPROVAL_TTL_INVALID")
    now = utc_datetime(clock.now())
    return WorkflowTimes(timestamp(now), timestamp(now), timestamp(now + timedelta(seconds=ttl_seconds)))
