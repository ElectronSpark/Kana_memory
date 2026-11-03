"""One business calendar for scheduling, evidence and learning UI.

The default provider reads the real clock. Test hosts may explicitly install
a frozen provider; this module never reads control files or environment flags
and exposes no transport. It does not replace monotonic duration/ink clocks,
network deadlines, or real audit/export timestamps.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone, tzinfo
from math import isfinite
from numbers import Real
from threading import RLock
from zoneinfo import ZoneInfo


def _aware(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Business time requires a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _zone(value: str | tzinfo | None) -> tzinfo | None:
    if value == "UTC":
        return timezone.utc
    if isinstance(value, str):
        return ZoneInfo(value)
    if value is not None and not isinstance(value, tzinfo):
        raise ValueError("Time zone must be a zone name or tzinfo")
    return value


class SystemClock:
    """Production provider; no mutable test state or configuration."""

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def local_now(self, tz: str | tzinfo | None = None) -> datetime:
        return self.localize(self.utc_now(), tz)

    def localize(self, value: datetime | str, tz: str | tzinfo | None = None) -> datetime:
        return _aware(value).astimezone(_zone(tz))


class FrozenClock:
    """Explicit synthetic calendar, shared by all application worker threads.

    Forward changes affect only returned calendar time. They neither sleep nor
    edit scheduled records. Backward jumps require set(..., allow_backwards=True)
    so restoring a matched test checkpoint is distinguishable from advancing it.
    """

    def __init__(self, start: datetime | str, time_zone: str | tzinfo = "UTC"):
        self._lock = RLock()
        self._now = _aware(start)
        self._zone = _zone(time_zone)
        if self._zone is None:
            raise ValueError("Frozen time requires an explicit time zone")

    @property
    def time_zone(self) -> str:
        return str(self._zone)

    def utc_now(self) -> datetime:
        with self._lock:
            return self._now

    def local_now(self, tz: str | tzinfo | None = None) -> datetime:
        return self.localize(self.utc_now(), tz)

    def localize(self, value: datetime | str, tz: str | tzinfo | None = None) -> datetime:
        return _aware(value).astimezone(self._zone if tz is None else _zone(tz))

    @property
    def now(self) -> datetime:
        return self.utc_now()

    @now.setter
    def now(self, value: datetime | str):
        self.set(value)

    def set(self, value: datetime | str, *, allow_backwards: bool = False) -> datetime:
        value = _aware(value)
        if type(allow_backwards) is not bool:
            raise ValueError("allow_backwards must be a boolean")
        with self._lock:
            if value < self._now and not allow_backwards:
                raise ValueError("Use an explicit checkpoint restore for backward time")
            self._now = value
            return self._now

    def advance(self, duration: Real | timedelta) -> datetime:
        if isinstance(duration, timedelta):
            delta = duration
        elif isinstance(duration, Real) and not isinstance(duration, bool) and isfinite(duration):
            delta = timedelta(seconds=duration)
        else:
            raise ValueError("Advance requires finite seconds or a timedelta")
        if delta < timedelta(0):
            raise ValueError("Advance cannot move business time backwards")
        with self._lock:
            next_time = self._now + delta
            self._now = next_time
            return next_time


_provider_lock = RLock()
_provider = SystemClock()


def _validated_provider(provider):
    if any(not callable(getattr(provider, name, None)) for name in ("utc_now", "local_now", "localize")):
        raise TypeError("Clock provider must implement utc_now, local_now and localize")
    _aware(provider.utc_now())
    return provider


def utc_now() -> datetime:
    """UTC calendar instant, resolved at call time even for imported aliases."""
    with _provider_lock:
        return _aware(_provider.utc_now())


def local_now(tz: str | tzinfo | None = None) -> datetime:
    """Aware local calendar instant; frozen providers own their test time zone."""
    with _provider_lock:
        value = _provider.local_now(tz)
        _aware(value)
        return value


def localize(value: datetime | str, tz: str | tzinfo | None = None) -> datetime:
    """Display an existing instant in the provider's local zone, including DST."""
    value = _aware(value)
    with _provider_lock:
        localized = _provider.localize(value, tz)
        _aware(localized)
        return localized


def install_clock(provider):
    """Install an explicit process-wide provider and return the previous one."""
    provider = _validated_provider(provider)
    global _provider
    with _provider_lock:
        previous, _provider = _provider, provider
        return previous


def restore_clock(previous):
    """Restore a provider returned by install_clock; no records are changed."""
    return install_clock(previous)


@contextmanager
def using_clock(provider):
    """A scoped test provider; nested scopes restore their own predecessor."""
    previous = install_clock(provider)
    try:
        yield provider
    finally:
        global _provider
        with _provider_lock:
            if _provider is not provider:
                raise RuntimeError("Clock provider changed outside its active scope")
            _provider = previous
