"""Cutting points out of the downloaded area files, and merging the pieces of a point into one file (via CDO)."""

from __future__ import annotations

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .config import Config, Job, _wrap_lon
from .util import log, run_cdo


def point_box(cfg: Config, point: str) -> tuple[float, float, float, float]:
    """
    (lon1, lon2, lat1, lat2) for `cdo sellonlatbox`: a box around the grid point nearest to the requested one.
    The margin is a tenth of the grid step: it keeps the point itself and can never reach its neighbours.
    """
    lat, lon = cfg.snapped_points[point]
    dlat, dlon = cfg.grid_steps
    lon = _wrap_lon(lon, cfg.area_box[1])          # same longitude convention as the area file
    return lon - dlon / 10, lon + dlon / 10, lat - dlat / 10, lat + dlat / 10


def _replace(tmp: Path, final: Path) -> None:
    if not tmp.exists() or tmp.stat().st_size == 0:
        raise RuntimeError(f"nothing was written to {final.name}: the point is probably not inside the file")
    tmp.replace(final)


def extract_points_from_file(cfg: Config, job: Job, max_workers: int = 4, log=log) -> list[Path]:
    """
    Cuts every point out of ONE downloaded area file and, if cfg.delete_area_files, deletes the area file.
    Points that were already cut out are left alone, so this can be repeated safely after an interruption.
    The area file is deleted only after every point has been written.
    """
    def extract(item):
        name = item
        target = cfg.point_piece_path(name, job.piece)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return target
        tmp = target.with_name(target.name + ".part")
        tmp.unlink(missing_ok=True)
        if cfg.is_single_point_area:
            shutil.copy(job.path, tmp)            # nothing to select, and CDO cannot select from one point
        else:
            lon1, lon2, lat1, lat2 = point_box(cfg, name)
            run_cdo("-O", f"sellonlatbox,{lon1},{lon2},{lat1},{lat2}", job.path, tmp)
        _replace(tmp, target)
        return target

    log(f"Extracting {len(cfg.points)} point(s) from {job.path.name}")
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        paths = list(pool.map(extract, cfg.points))
    if cfg.delete_area_files:
        job.path.unlink()
    return paths


def merge_point(cfg: Config, point: str, log=log) -> Path:
    """
    Merges in time (cdo mergetime) all the pieces of one point into data_dir/merged/<point>.grib.
    A piece that is missing is cut out of its area file if that is still on disk, otherwise this raises.
    """
    pieces = []
    for job in cfg.jobs():
        target = cfg.point_piece_path(point, job.piece)
        if not target.exists():
            if not job.path.exists():
                raise FileNotFoundError(
                    f"Cannot build {point}: its piece {target.name} is missing and so is the area file "
                    f"{job.path.name} it would be cut from. Run `fetch` to download what is missing."
                )
            extract_points_from_file(cfg, job, log=log)
        pieces.append(target)

    output = cfg.merged_path(point)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + ".part")
    tmp.unlink(missing_ok=True)
    log(f"Merging {len(pieces)} pieces of {point} -> {output}")
    run_cdo("-O", "mergetime", *pieces, tmp)
    _replace(tmp, output)
    return output


def needs_merge(cfg: Config, point: str) -> bool:
    """True if the merged file of a point is missing or older than any of its pieces."""
    merged = cfg.merged_path(point)
    if not merged.exists():
        return True
    newest = max((cfg.point_piece_path(point, j.piece).stat().st_mtime
                  for j in cfg.jobs() if cfg.point_piece_path(point, j.piece).exists()), default=0)
    return merged.stat().st_mtime < newest
