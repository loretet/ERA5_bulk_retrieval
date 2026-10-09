import json

from era5_bulk.cli import main

from .conftest import needs_cdo

CONFIG = """
data_dir = "{dir}"
dates = "2020-01-01/2020-01-06"
time = "00/to/23/by/6"
max_days = 3
[points]
Cabauw = [52.0, 5.0]
"Mace Head" = [53.25, -10.0]
"""


def write(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text(CONFIG.format(dir=tmp_path / "data"))
    return str(f)


def test_init_writes_a_template_that_loads(tmp_path, capsys):
    target = tmp_path / "t.toml"
    assert main(["init", str(target)]) == 0
    assert main(["init", str(target)]) == 2                 # never overwrites
    assert main(["plan", str(target)]) == 0
    assert "Requests" in capsys.readouterr().out


def test_plan_and_status_send_nothing(tmp_path, capsys):
    f = write(tmp_path)
    assert main(["plan", f]) == 0 and main(["status", f]) == 0
    out = capsys.readouterr().out
    assert "2 pieces" in out and "to send" in out
    assert not (tmp_path / "data" / "cds_requests.json").exists()


def test_config_errors_exit_2(tmp_path, capsys):
    assert main(["plan", str(tmp_path / "missing.toml")]) == 2
    assert "error:" in capsys.readouterr().err


@needs_cdo
def test_validate_exit_code(tmp_path, capsys):
    f = write(tmp_path)
    assert main(["validate", f]) == 1                       # nothing downloaded yet
    assert "no file found" in capsys.readouterr().out


def test_doctor_runs(capsys):
    main(["doctor"])
    assert "era5-bulk" in capsys.readouterr().out


def test_status_lists_state(tmp_path, capsys):
    f = write(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    from era5_bulk import Config
    cfg = Config.from_file(f)
    (data / "cds_requests.json").write_text(json.dumps({
        "signature": cfg.signature, "pending": {cfg.jobs()[0].name: "abc-123"}, "done": [], "attempts": {}}))
    main(["status", f])
    out = capsys.readouterr().out
    assert "queued at the CDS (abc-123)" in out and "to send" in out


@needs_cdo
def test_submit_fetch_run_through_the_command_line(tmp_path, monkeypatch, capsys):
    from era5_bulk import retrieve
    from .fakecds import FakeClient
    client = FakeClient()
    monkeypatch.setattr(retrieve, "make_client", lambda **kw: client)
    f = write(tmp_path)

    assert main(["submit", f, "--limit", "1"]) == 0
    assert len(client.submitted) == 1
    assert main(["fetch", f, "--once"]) == 0                 # nothing ready: returns at once, exit 0
    client.finish()
    assert main(["run", f, "--once"]) == 0                   # collects the first, sends the second
    assert len(client.submitted) == 2
    client.finish()
    assert main(["fetch", f, "--once"]) == 0
    assert main(["validate", f]) == 0
    assert "2/2 complete" in capsys.readouterr().out


@needs_cdo
def test_a_second_instance_does_nothing(tmp_path, monkeypatch, capsys):
    """Two overlapping scheduled runs must not both work in the same folder."""
    from era5_bulk import Config, retrieve
    from era5_bulk.state import locked
    from .fakecds import FakeClient
    client = FakeClient()
    monkeypatch.setattr(retrieve, "make_client", lambda **kw: client)
    f = write(tmp_path)
    with locked(Config.from_file(f)):
        assert main(["submit", f]) == 0
    assert client.submitted == [] and "already running" in capsys.readouterr().out
    assert main(["submit", f]) == 0 and len(client.submitted) == 2      # and once it is free, it works


def test_failures_make_the_exit_code_nonzero(tmp_path, monkeypatch):
    from era5_bulk import retrieve
    from .fakecds import FakeClient
    client = FakeClient()
    monkeypatch.setattr(retrieve, "make_client", lambda **kw: client)
    f = write(tmp_path)
    main(["submit", f])
    client.finish(status="failed")
    for _ in range(3):
        main(["fetch", f, "--once"])
        main(["submit", f])
        client.finish(status="failed")
    main(["fetch", f, "--once"])
    assert main(["fetch", f, "--once"]) == 1          # cron / the user sees that something needs attention


def test_once_mode_uses_the_fast_fail_client(monkeypatch, tmp_path):
    """A scheduled --once run must not retry a dead connection for hours."""
    from era5_bulk import retrieve
    seen = {}

    def fake(**kw):
        seen.update(kw)
        raise RuntimeError("stop here")

    monkeypatch.setattr(retrieve, "make_client", fake)
    from era5_bulk import Config
    cfg = Config.from_file(write(tmp_path))
    try:
        retrieve.fetch(cfg, once=True)
    except RuntimeError:
        pass
    assert seen == retrieve.FAST_FAIL
