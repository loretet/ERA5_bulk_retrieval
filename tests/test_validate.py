import subprocess

import pytest

from era5_bulk import validate
from era5_bulk.grib import scan_grib

from .conftest import needs_cdo
from .gridutil import make_area_grib

pytestmark = needs_cdo


def build(cfg, drop_piece=None):
    """Pieces and merged files of a complete (or incomplete) retrieval, without involving the fetch logic."""
    for job in cfg.jobs():
        if job.piece == drop_piece:
            continue
        for name, (lat, lon) in cfg.snapped_points.items():
            make_area_grib(cfg.point_piece_path(name, job.piece), f"{lat}/{lon}/{lat}/{lon}", job.piece, cfg.time)


def merge(cfg, name):
    pieces = [str(cfg.point_piece_path(name, j.piece)) for j in cfg.jobs() if cfg.point_piece_path(name, j.piece).exists()]
    cfg.merged_path(name).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["cdo", "-s", "-O", "mergetime", *pieces, str(cfg.merged_path(name))], check=True)


def test_complete_data_passes(cfg):
    build(cfg)
    for name in cfg.points:
        merge(cfg, name)
    reports = validate(cfg)
    assert [r.ok for r in reports] == [True, True], [r.problems for r in reports]
    assert "24 timesteps" in reports[0].info


def test_a_missing_piece_names_the_missing_days(cfg):
    build(cfg, drop_piece="2020-01-04/2020-01-06")
    for name in cfg.points:
        merge(cfg, name)
    r = validate(cfg)[0]
    assert not r.ok
    assert "12 of 24 timesteps are MISSING" in r.problems[0]
    assert all(day in r.problems[0] for day in ("2020-01-04", "2020-01-05", "2020-01-06"))
    assert "2020-01-03" not in r.problems[0]


def test_missing_hours_within_a_day_are_found(cfg, tmp_path):
    build(cfg)
    merge(cfg, "Cabauw")
    gap = tmp_path / "gap.grib"
    subprocess.run(["cdo", "-s", "-O", "delete,timestep=7,8", str(cfg.merged_path("Cabauw")), str(gap)], check=True)
    gap.replace(cfg.merged_path("Cabauw"))
    r = validate(cfg)[1] if "Cabauw" == list(cfg.points)[1] else validate(cfg)[0]
    assert "2 of 24 timesteps are MISSING" in r.problems[0]


def drop_message(path, index):
    """Rewrites a GRIB2 file without its `index`-th message (CDO itself refuses to produce such a file)."""
    data = path.read_bytes()
    messages, pos = [], 0
    while pos < len(data):
        length = int.from_bytes(data[pos + 8:pos + 16], "big")
        messages.append(data[pos:pos + length])
        pos += length
    del messages[index]
    path.write_bytes(b"".join(messages))


def test_a_half_written_timestep_is_found(cfg):
    """Every timestamp is present, but one field is missing at one of them: only the field check sees this."""
    build(cfg)
    merge(cfg, "Cabauw")
    drop_message(cfg.merged_path("Cabauw"), 5)           # the 2nd field of the 3rd timestep
    r = [r for r in validate(cfg) if r.name == "Cabauw"][0]
    assert not r.ok
    assert not any("MISSING" in p for p in r.problems), "all the timestamps are still there"
    assert any("lack some of their fields" in p and "2020-01-01 12:00:00" in p for p in r.problems)


def test_truncated_file_is_reported(cfg):
    build(cfg)
    for name in cfg.points:
        merge(cfg, name)
    f = cfg.merged_path("Mace Head")
    f.write_bytes(f.read_bytes()[:-300])
    r = [r for r in validate(cfg) if r.name == "Mace Head"][0]
    assert any("truncated" in p for p in r.problems)


def test_no_file_at_all(cfg):
    assert all("no file found" in r.problems[0] for r in validate(cfg))


def test_pieces_are_used_when_there_is_no_merged_file(cfg):
    build(cfg)
    assert all(r.ok for r in validate(cfg))
    assert all(r.ok for r in validate(cfg, use_pieces=True))


def test_wrong_number_of_fields_is_a_warning(cfg):
    build(cfg)
    cfg.param = "130/131/132"      # the request says 3 params x 28 levels = 84 fields; the files hold 2
    r = validate(cfg)[0]
    assert r.ok and any("distinct fields" in w for w in r.warnings)


@pytest.mark.parametrize("edition", [1, 2])
def test_both_grib_editions_are_read(tmp_path, edition):
    p = make_area_grib(tmp_path / "e.grib", "53.25/-10/52/5", "2020-01-01/2020-01-02", "00/to/23/by/6",
                       edition=edition)
    scan = scan_grib(p)
    assert len(scan.per_time) == 8 and scan.n_messages == 16 and not scan.error
    ref = subprocess.run(["cdo", "-s", "showtimestamp", str(p)], capture_output=True, text=True).stdout.split()
    assert [t.strftime("%Y-%m-%dT%H:%M:%S") for t in sorted(scan.per_time)] == ref
