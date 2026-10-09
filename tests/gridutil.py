"""Builds small synthetic ERA5-like GRIB files with CDO (no network needed) and looks values up independently."""

from __future__ import annotations

import subprocess
from pathlib import Path

from era5_bulk.dates import parse_dates, parse_times

GRID = "r1440x721"      # the ERA5 0.25 degree grid: nodes at multiples of 0.25, from 90N to 90S


def _cdo(*args):
    result = subprocess.run(["cdo", "-s", *map(str, args)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout


def make_area_grib(path, area: str, piece: str, time_spec: str = "00/to/23/by/1", n_params: int = 2,
                   edition: int = 2) -> Path:
    """A GRIB file shaped like what the CDS returns for `area` and `piece`: hourly steps, n_params fields."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    north, west, south, east = (float(x) for x in area.split("/"))
    first, last = parse_dates(piece)
    steps_per_day = len(parse_times(time_spec))
    n_steps = ((last - first).days + 1) * steps_per_day
    step_hours = 24 // steps_per_day
    fmt = "grb2" if edition == 2 else "grb1"

    parts = []
    for p in range(n_params):
        part = path.with_name(f"{path.stem}.p{p}.tmp")
        param = f"0.{p}.0" if edition == 2 else f"{130 + p}.128"
        field = f"-random,{GRID},{1 + p}"
        if north == south and west == east:
            region = f"-remapnn,lon={west}_lat={north}"        # a one-point area
        else:
            region = f"-sellonlatbox,{west},{east},{south},{north}"
        _cdo("-O", "-f", fmt, f"settaxis,{first},00:00:00,{step_hours}hour", f"-setparam,{param}",
             f"-duplicate,{n_steps}", region, field, part)
        parts.append(part)
    _cdo("-O", "merge", *parts, path)
    for part in parts:
        part.unlink()
    return path


def truth(param: int, lat: float, lon: float) -> float:
    """The value the synthetic field `param` has at (lat, lon), read from the global field."""
    glob = Path(f"/tmp/era5_bulk_truth_{param}.grb")
    if not glob.exists():
        _cdo("-O", "-f", "grb2", f"setparam,0.{param}.0", f"-random,{GRID},{1 + param}", glob)
    return float(_cdo("output", f"-remapnn,lon={lon}_lat={lat}", glob).split()[0])


def first_value(path, param: int) -> float:
    return float(_cdo("output", "-seltimestep,1", f"-selparam,0.{param}.0", path).split()[0])
