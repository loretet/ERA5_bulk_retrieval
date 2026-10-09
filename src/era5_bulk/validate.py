"""
`validate`: is the downloaded data complete? Run it when everything is downloaded.

For every point (or, without points, for the area files) it checks, from the GRIB headers only:
  * every timestamp of the period is there (and lists the missing days if not),
  * every timestep has ALL its fields (a half-written day shows up here),
  * no file is truncated or unreadable.
It also warns if the number of fields per timestep is not what the request implies.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .dates import expected_timestamps
from .grib import GribScan, incomplete_timesteps, scan_grib
from .plan import fields_per_step


@dataclass
class Report:
    name: str
    files: list = field(default_factory=list)
    problems: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    info: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems


def _days(timestamps) -> str:
    per_day = Counter(t.date() for t in timestamps)
    days = sorted(per_day)
    shown = ", ".join(f"{d} ({per_day[d]} h)" if per_day[d] < 24 else f"{d}" for d in days[:8])
    return f"{shown}" + (f", ... and {len(days) - 8} more days" if len(days) > 8 else "")


def check_files(cfg: Config, name: str, files: list) -> Report:
    report = Report(name, [str(f) for f in files])
    if not files:
        report.problems.append("no file found")
        return report

    scan: GribScan | None = None
    for path in files:
        scan = scan_grib(path, scan)
        if scan.error:
            report.problems.append(scan.error)
        if scan.truncated_at is not None:
            report.problems.append(f"{Path(path).name} is truncated (a message is cut short at byte "
                                   f"{scan.truncated_at}): the download or the copy did not finish")
    assert scan is not None

    found = set(scan.per_time)
    expected = expected_timestamps(cfg.dates, cfg.time)
    missing = [t for t in expected if t not in found]
    extra = sorted(found - set(expected))
    if missing:
        report.problems.append(f"{len(missing)} of {len(expected)} timesteps are MISSING, on "
                               f"{len({t.date() for t in missing})} day(s): {_days(missing)}")
    if extra:
        report.warnings.append(f"{len(extra)} timestep(s) outside the requested period/times "
                               f"(first: {extra[0]}); harmless")

    partial = incomplete_timesteps(scan)
    if partial:
        report.problems.append(f"{len(partial)} timestep(s) lack some of their fields, e.g. {partial[0][0]} is "
                               f"missing {partial[0][1]} of {len(scan.keys)}")

    wanted = fields_per_step(cfg)
    if scan.keys and len(scan.keys) != wanted:
        note = ""
        if cfg.levtype == "ml" and any(p.strip().split(".")[0] == "152" for p in cfg.param.split("/")):
            note = (" (parameter 152, the log of surface pressure, exists only on model level 1: "
                    "it is not returned if level 1 is not in levelist)")
        report.warnings.append(f"{len(scan.keys)} distinct fields per timestep, but the request implies "
                               f"{wanted}{note}")

    if found:
        report.info = (f"{len(found)} timesteps, {min(found)} -> {max(found)}, "
                       f"{len(scan.keys)} fields per timestep, {scan.n_messages:,} messages")
    return report


def validate(cfg: Config, use_pieces: bool = False) -> list:
    """One Report per point (or one for the area files if there are no points)."""
    reports = []
    if cfg.points:
        for name in cfg.points:
            merged = cfg.merged_path(name)
            if merged.exists() and not use_pieces:
                files = [merged]
            else:
                files = [p for p in (cfg.point_piece_path(name, j.piece) for j in cfg.jobs()) if p.exists()]
            reports.append(check_files(cfg, name, files))
    else:
        files = [j.path for j in cfg.jobs() if j.path.exists()]
        reports.append(check_files(cfg, "area files", files))
    return reports


def format_reports(cfg: Config, reports: list) -> str:
    n = len(expected_timestamps(cfg.dates, cfg.time))
    lines = [f"Expected: {n} timesteps, {cfg.dates}, time={cfg.time}", ""]
    for r in reports:
        lines.append(f"{'OK     ' if r.ok else 'PROBLEM'}  {r.name}")
        if r.info:
            lines.append(f"         {r.info}")
        for p in r.problems:
            lines.append(f"         ! {p}")
        for w in r.warnings:
            lines.append(f"         ~ {w}")
    good = sum(r.ok for r in reports)
    lines += ["", f"{good}/{len(reports)} complete."]
    return "\n".join(lines)
