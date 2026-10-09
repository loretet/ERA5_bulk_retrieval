import shutil

import pytest

from era5_bulk import Config
from era5_bulk.extract import extract_points_from_file, merge_point, needs_merge
from era5_bulk.grib import scan_grib

from .conftest import needs_cdo
from .gridutil import first_value, make_area_grib, truth

pytestmark = needs_cdo


def area_file(cfg, job):
    return make_area_grib(job.path, cfg.area, job.piece, cfg.time)


def test_points_have_the_right_values(cfg):
    """Each point must be the value of its own grid point, checked against an independent lookup."""
    job = cfg.jobs()[0]
    area_file(cfg, job)
    extract_points_from_file(cfg, job, log=lambda *_: None)
    for name, (lat, lon) in cfg.points.items():
        got = [first_value(cfg.point_piece_path(name, job.piece), p) for p in (0, 1)]
        want = [truth(p, lat, lon) for p in (0, 1)]
        assert got == pytest.approx(want, abs=1e-3), name
    assert not job.path.exists()


def test_points_are_single_grid_points(cfg):
    job = cfg.jobs()[0]
    area_file(cfg, job)
    extract_points_from_file(cfg, job, log=lambda *_: None)
    import subprocess
    out = subprocess.run(["cdo", "-s", "griddes", str(cfg.point_piece_path("Cabauw", job.piece))],
                         capture_output=True, text=True).stdout
    assert "gridsize  = 1" in out


def test_area_file_is_kept_if_asked(cfg):
    cfg.delete_area_files = False
    job = cfg.jobs()[0]
    area_file(cfg, job)
    extract_points_from_file(cfg, job, log=lambda *_: None)
    assert job.path.exists()


def test_one_point_area_is_copied(tmp_path):
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", points={"SGP": (36.5, -97.5)},
               time="00/to/23/by/12", max_days=3)
    assert c.is_single_point_area
    job = c.jobs()[0]
    make_area_grib(job.path, c.area, job.piece, c.time)
    extract_points_from_file(c, job, log=lambda *_: None)
    scan = scan_grib(c.point_piece_path("SGP", job.piece))
    assert len(scan.per_time) == 4 and scan.n_messages == 8      # 2 days x 2 steps, 2 fields


def test_a_point_that_is_not_in_the_file_is_an_error(cfg):
    job = cfg.jobs()[0]
    make_area_grib(job.path, "50/20/49/21", job.piece, cfg.time)       # a file for somewhere else
    with pytest.raises(Exception):
        extract_points_from_file(cfg, job, log=lambda *_: None)
    assert job.path.exists(), "the area file must not be deleted after a failed extraction"


def test_merge_joins_the_pieces_in_time(cfg):
    for job in cfg.jobs():
        area_file(cfg, job)
        extract_points_from_file(cfg, job, log=lambda *_: None)
    assert needs_merge(cfg, "Cabauw")
    out = merge_point(cfg, "Cabauw", log=lambda *_: None)
    scan = scan_grib(out)
    assert len(scan.per_time) == 6 * 4 and scan.n_messages == 6 * 4 * 2
    assert not needs_merge(cfg, "Cabauw")


def test_merge_names_what_is_missing(cfg):
    with pytest.raises(FileNotFoundError, match="is missing"):
        merge_point(cfg, "Cabauw", log=lambda *_: None)
