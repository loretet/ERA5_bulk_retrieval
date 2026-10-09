import pytest

from era5_bulk import Config, ConfigError
from era5_bulk.plan import fields_per_step, format_plan, grid_shape


def test_area_is_the_box_around_the_points(cfg):
    assert cfg.area == "53.25/-10/52/5"
    assert grid_shape(cfg) == (6, 61)          # the same shape as the 'Europe' box of the original project


def test_points_snap_to_the_grid(tmp_path):
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", points={"a": (51.97, 4.93), "b": (53.33, -9.9)})
    assert c.snapped_points == {"a": (52.0, 5.0), "b": (53.25, -10.0)}


def test_request_is_what_the_cds_expects(cfg):
    r = cfg.request_for("2020-01-01/2020-01-03")
    assert r["date"] == "2020-01-01/to/2020-01-03"
    assert r["levtype"] == "ml" and r["area"] == cfg.area and r["grid"] == "0.25/0.25"
    assert r["levelist"] == "110/to/137" and r["type"] == "an" and r["stream"] == "oper"


def test_surface_request_has_no_levelist(tmp_path):
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", levtype="sfc", param="167.128",
               area="60/0/50/10")
    assert "levelist" not in c.request_for("2020-01-01/2020-01-02")


def test_extra_overrides_the_request(tmp_path):
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10", extra={"class": "ea"})
    assert c.request_for("2020-01-01/2020-01-02")["class"] == "ea"


def test_point_outside_an_explicit_area(tmp_path):
    with pytest.raises(ConfigError, match="outside the area"):
        Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10", points={"x": (70, 5)})


def test_longitude_conventions_agree(tmp_path):
    # the same point written as -97.5 and as 262.5, and an area written either way
    for lon in (-97.5, 262.5):
        for area in ("40/-100/35/-95", "40/260/35/265"):
            Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area=area, points={"x": (36.5, lon)})


def test_needs_area_or_points(tmp_path):
    with pytest.raises(ConfigError):
        Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02")


def test_colliding_point_names(tmp_path):
    with pytest.raises(ConfigError, match="same file name"):
        Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", points={"A b": (1, 1), "A_b": (2, 2)})


def test_without_points_area_files_are_kept(tmp_path):
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10", delete_area_files=True)
    assert c.delete_area_files is False


def test_signature_ignores_dates_but_not_the_request(tmp_path):
    a = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10")
    b = Config(data_dir=tmp_path, dates="2021-05-01/2021-06-30", area="60/0/50/10")
    c = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/11")
    d = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10", levelist="1/to/137")
    assert a.signature == b.signature
    assert len({a.signature, c.signature, d.signature}) == 3


def test_from_file_and_data_dir_priority(tmp_path, monkeypatch):
    f = tmp_path / "c.toml"
    f.write_text('data_dir = "/from/file"\ndates = "2020-01-01/2020-01-02"\n[points]\nA = [52, 5]\n')
    assert str(Config.from_file(f).data_dir) == "/from/file"
    monkeypatch.setenv("ERA5_BULK_DATA_DIR", "/from/env")
    assert str(Config.from_file(f).data_dir) == "/from/env"
    assert str(Config.from_file(f, data_dir="/from/cli").data_dir) == "/from/cli"


def test_unknown_key_is_a_typo(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('data_dir = "x"\ndates = "2020-01-01/2020-01-02"\narea = "1/1/1/1"\nlevlist = "1"\n')
    with pytest.raises(ConfigError, match="levlist"):
        Config.from_file(f)


def test_fields_per_step_counts_lnsp_once(tmp_path):
    kw = dict(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10")
    assert fields_per_step(Config(param="130/131/132/133", **kw)) == 4 * 28
    assert fields_per_step(Config(param="130/131/132/133/152", levelist="1/to/137", **kw)) == 4 * 137 + 1


def test_plan_warns_about_lnsp_without_level_1(tmp_path):
    kw = dict(data_dir=tmp_path, dates="2020-01-01/2020-01-02", area="60/0/50/10")
    assert "level 1 only" in format_plan(Config(param="130/152", **kw))
    assert "level 1 only" not in format_plan(Config(param="130/152", levelist="1/to/137", **kw))
    assert "level 1 only" not in format_plan(Config(param="130/131", **kw))


def test_plan_warns_when_too_many_requests_or_too_big(tmp_path):
    big = Config(data_dir=tmp_path, dates="2000-01-01/2020-12-31", area="60/0/50/10", max_days=3)
    assert "max_queued" in format_plan(big)
    huge = Config(data_dir=tmp_path, dates="2020-01-01/2020-01-31", area="90/-180/-90/179.75",
                  levelist="1/to/137", param="130/131/132/133")
    assert "CDS support advises" in format_plan(huge)
