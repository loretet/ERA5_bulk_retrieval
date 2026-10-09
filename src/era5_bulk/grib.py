"""
A tiny GRIB header reader: for every message, WHEN it is valid and WHICH field it is. Nothing else is decoded.

Why not CDO or cfgrib? Both index the whole file, which takes ~0.7 ms per message: about 20 minutes for the
2 million messages of a two-year model level file. The headers are all we need to answer "is every hour there,
with all its fields?", and reading just those takes a few seconds, with no dependency at all.

Supports GRIB edition 1 and 2 with ONE field per message, which is how the CDS delivers ERA5.
"""

from __future__ import annotations

import mmap
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

# GRIB code table 4.4 (edition 2) and table 4 (edition 1): time unit -> timedelta of 1 unit
_UNITS_2 = {0: timedelta(minutes=1), 1: timedelta(hours=1), 2: timedelta(days=1), 10: timedelta(hours=3),
            11: timedelta(hours=6), 12: timedelta(hours=12), 13: timedelta(seconds=1)}
_UNITS_1 = {0: timedelta(minutes=1), 1: timedelta(hours=1), 2: timedelta(days=1), 10: timedelta(hours=3),
            11: timedelta(hours=6), 12: timedelta(hours=12), 13: timedelta(minutes=15), 14: timedelta(minutes=30),
            254: timedelta(seconds=1)}


class GribError(ValueError):
    pass


def _u(buf, pos: int, n: int) -> int:
    return int.from_bytes(buf[pos:pos + n], "big")


def _grib2(buf, start: int, total: int):
    """(valid time, field key) of one edition 2 message."""
    discipline = buf[start + 6]
    pos = start + 16
    end = start + total
    reference = None
    while pos + 5 <= end:
        length, number = _u(buf, pos, 4), buf[pos + 4]
        if length < 5:
            raise GribError("corrupt section length")
        if number == 1:
            reference = datetime(_u(buf, pos + 12, 2), buf[pos + 14], buf[pos + 15],
                                 buf[pos + 16], buf[pos + 17], buf[pos + 18])
        elif number == 4:
            if reference is None:
                raise GribError("section 4 before section 1")
            unit = _UNITS_2.get(buf[pos + 17], timedelta(hours=1))
            valid = reference + _u(buf, pos + 18, 4) * unit
            key = (discipline, buf[pos + 9], buf[pos + 10], buf[pos + 22], _u(buf, pos + 24, 4))
            return valid, key
        pos += length
    raise GribError("no product definition section")


def _grib1(buf, start: int):
    """(valid time, field key) of one edition 1 message."""
    pds = start + 8
    table, param, level_type = buf[pds + 3], buf[pds + 8], buf[pds + 9]
    level = _u(buf, pds + 10, 2)
    century = buf[pds + 24]
    year = (century - 1) * 100 + buf[pds + 12]
    reference = datetime(year, buf[pds + 13], buf[pds + 14], buf[pds + 15], buf[pds + 16])
    unit = _UNITS_1.get(buf[pds + 17], timedelta(hours=1))
    p1, p2, time_range = buf[pds + 18], buf[pds + 19], buf[pds + 20]
    # time range 0/1: the field is valid at reference + P1. 2-5: a range from P1 to P2, valid at its end.
    offset = p2 if time_range in (2, 3, 4, 5) else p1
    return reference + offset * unit, (table, param, level_type, level)


@dataclass
class GribScan:
    path: Path
    n_messages: int = 0
    #: timestamp -> number of messages
    per_time: dict = field(default_factory=dict)
    #: timestamp -> bit mask of the fields present (bit i = i-th distinct field met in the file)
    masks: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)       # field key -> bit index
    truncated_at: int | None = None                # byte offset of a message cut short by the end of the file
    error: str | None = None


def scan_grib(path: str | Path, scan: GribScan | None = None) -> GribScan:
    """
    Reads the header of every message. Pass an existing `scan` to add more files (e.g. all the pieces of a
    point) to the same result.
    """
    path = Path(path)
    scan = scan or GribScan(path)
    size = path.stat().st_size
    if size == 0:
        scan.error = f"{path.name} is empty"
        return scan

    with open(path, "rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        pos = 0
        while pos < size:
            if buf[pos:pos + 4] != b"GRIB":
                nxt = buf.find(b"GRIB", pos)         # tolerate padding between messages
                if nxt < 0:
                    break
                pos = nxt
            edition = buf[pos + 7]
            try:
                if edition == 2:
                    total = _u(buf, pos + 8, 8)
                elif edition == 1:
                    total = _u(buf, pos + 4, 3)
                else:
                    raise GribError(f"unknown GRIB edition {edition}")
                if total < 16:
                    raise GribError("corrupt message length")
                if pos + total > size:
                    scan.truncated_at = pos
                    break
                valid, key = _grib2(buf, pos, total) if edition == 2 else _grib1(buf, pos)
            except (GribError, ValueError, IndexError) as error:
                scan.error = f"{path.name}: unreadable message at byte {pos}: {error}"
                break

            bit = scan.keys.get(key)
            if bit is None:
                bit = scan.keys[key] = len(scan.keys)
            scan.per_time[valid] = scan.per_time.get(valid, 0) + 1
            scan.masks[valid] = scan.masks.get(valid, 0) | (1 << bit)
            scan.n_messages += 1
            pos += total
    return scan


def incomplete_timesteps(scan: GribScan) -> list:
    """Timestamps that lack some of the fields the file has at other times: [(timestamp, n_missing), ...]."""
    full = (1 << len(scan.keys)) - 1
    return sorted((t, len(scan.keys) - bin(m).count("1")) for t, m in scan.masks.items() if m != full)
