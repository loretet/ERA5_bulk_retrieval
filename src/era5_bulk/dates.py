"""Dates and times: how the period is cut into requests, and which timestamps a complete download contains."""

from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta


def parse_dates(dates: str) -> tuple[date, date]:
    """'YYYY-MM-DD/YYYY-MM-DD' (or the MARS form 'YYYY-MM-DD/to/YYYY-MM-DD') -> (first day, last day)."""
    parts = [p.strip() for p in str(dates).replace("/to/", "/").split("/")]
    if len(parts) != 2:
        raise ValueError(f"dates must look like 'YYYY-MM-DD/YYYY-MM-DD', got {dates!r}")
    first, last = (date.fromisoformat(p) for p in parts)
    if last < first:
        raise ValueError(f"the period ends before it starts: {dates!r}")
    return first, last


def split_dates(dates: str, max_days: int = 16) -> list[str]:
    """
    Cuts the period into pieces of at most `max_days` days, one request per piece.

    Every month is split on its own, in pieces of (almost) the same length, so that:
      * a month with 28-31 days never leaves a tiny piece of one or two days, and
      * a piece never crosses the end of a month, which keeps the data of each request close together in the
        archive.

    Example (max_days=16): '2020-01-01/2020-03-31' ->
        2020-01-01/2020-01-15, 2020-01-16/2020-01-31, 2020-02-01/2020-02-14, 2020-02-15/2020-02-29,
        2020-03-01/2020-03-15, 2020-03-16/2020-03-31
    """
    if max_days < 1:
        raise ValueError("max_days must be at least 1")
    start, end = parse_dates(dates)

    pieces = []
    first = start
    while first <= end:
        # Days of this month that are inside the period (the first and last month can be incomplete)
        last = min(end, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1]))
        n_days = (last - first).days + 1
        n_pieces = math.ceil(n_days / max_days)
        for i in range(n_pieces):
            piece_first = first + timedelta(days=i * n_days // n_pieces)
            piece_last = first + timedelta(days=(i + 1) * n_days // n_pieces - 1)
            pieces.append(f"{piece_first}/{piece_last}")
        first = last + timedelta(days=1)
    return pieces


def piece_tag(piece: str) -> str:
    """'2020-01-01/2020-01-15' -> '20200101-20200115' (used in file names)."""
    return piece.replace("-", "").replace("/", "-")


def _to_timedelta(token: str) -> timedelta:
    token = token.strip()
    if ":" in token:
        hours, minutes = token.split(":")[:2]
        return timedelta(hours=int(hours), minutes=int(minutes))
    return timedelta(hours=int(token))


def parse_times(spec: str) -> list[timedelta]:
    """
    Time of day offsets of a MARS `time` specification, e.g.
      '00/to/23/by/1'   -> every hour          '00/to/21/by/3' -> every 3 hours
      '00/06/12/18'     -> the four listed     '00:00/12:00'   -> also accepted
    """
    tokens = [t.strip() for t in str(spec).split("/") if t.strip()]
    low = [t.lower() for t in tokens]
    if "to" in low:
        i = low.index("to")
        start, end = _to_timedelta(tokens[i - 1]), _to_timedelta(tokens[i + 1])
        step = timedelta(hours=float(tokens[low.index("by") + 1])) if "by" in low else timedelta(hours=1)
        if step <= timedelta(0):
            raise ValueError(f"the step of the time specification must be positive: {spec!r}")
        out, t = [], start
        while t <= end:
            out.append(t)
            t += step
        return out
    return sorted({_to_timedelta(t) for t in tokens})


def expected_timestamps(dates: str, time_spec: str) -> list[datetime]:
    """Every timestamp that a complete download of `dates` with the given `time` specification contains."""
    first, last = parse_dates(dates)
    offsets = parse_times(time_spec)
    out = []
    day = first
    while day <= last:
        midnight = datetime(day.year, day.month, day.day)
        out.extend(midnight + off for off in offsets)
        day += timedelta(days=1)
    return out


def count_values(spec: str) -> int:
    """
    How many values a MARS list has: '110/to/137' -> 28, '1/to/137/by/2' -> 69, '130/131/132' -> 3.
    Used to estimate the size of the requests.
    """
    tokens = [t.strip() for t in str(spec).split("/") if t.strip()]
    low = [t.lower() for t in tokens]
    if "to" in low:
        i = low.index("to")
        start, end = float(tokens[i - 1]), float(tokens[i + 1])
        step = float(tokens[low.index("by") + 1]) if "by" in low else 1.0
        return int(math.floor((end - start) / step + 1e-9)) + 1
    return len(tokens)
