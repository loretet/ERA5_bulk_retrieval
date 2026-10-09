"""The submit/fetch life-cycle against a fake CDS (and real CDO)."""

import json

import pytest

from era5_bulk import Config, fetch, get_summary, run, submit, validate
from era5_bulk.state import StateMismatchError, load_state

from .conftest import needs_cdo
from .fakecds import FakeClient

pytestmark = needs_cdo


def quiet(_=""):
    pass


def state_of(cfg):
    return json.loads(cfg.state_file.read_text())


def test_submit_sends_everything_once(cfg):
    client = FakeClient()
    s = submit(cfg, client, log=quiet)
    assert (s.total, s.queued, s.to_send) == (2, 2, 0) and len(client.submitted) == 2
    assert set(state_of(cfg)["pending"]) == {j.name for j in cfg.jobs()}
    submit(cfg, client, log=quiet)                       # idempotent: nothing is sent twice
    assert len(client.submitted) == 2


def test_limit_and_max_queued(cfg):
    client = FakeClient()
    assert submit(cfg, client, limit=1, log=quiet).queued == 1
    cfg.max_queued = 1
    assert submit(cfg, client, log=quiet).queued == 1    # already one queued: nothing more is sent
    assert len(client.submitted) == 1


def test_cds_refusal_stops_cleanly_and_resumes(cfg):
    client = FakeClient(queue_limit=1)
    s = submit(cfg, client, log=quiet)
    assert (s.queued, s.to_send) == (1, 1)
    client.finish()                                      # the CDS gets the first one done...
    client.queue_limit = None
    assert submit(cfg, client, log=quiet).queued == 2    # ...and the second is sent when asked again


def test_fetch_downloads_extracts_deletes_and_merges(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)

    s = fetch(cfg, client, once=True, log=quiet)         # nothing is ready yet
    assert (s.done, s.queued) == (0, 2) and client.download_calls == 0

    client.finish()
    s = fetch(cfg, client, once=True, log=quiet)
    assert s.complete and s.errors == []
    assert not list(cfg.area_dir.glob("*.grib")), "the big area files must be deleted after extraction"
    assert set(state_of(cfg)["done"]) == {j.name for j in cfg.jobs()}
    assert state_of(cfg)["pending"] == {}
    assert len(client.deleted) == 2, "results are freed on the CDS after download"
    for point in cfg.points:
        assert cfg.merged_path(point).exists()
        assert len(list(cfg.point_dir(point).glob("*.grib"))) == 2
    assert all(r.ok for r in validate(cfg)), [r.problems for r in validate(cfg)]

    n = client.download_calls
    fetch(cfg, client, once=True, log=quiet)             # a finished retrieval does nothing
    assert client.download_calls == n


def test_nothing_is_downloaded_twice_after_a_restart(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.finish(only={"job-1"})
    fetch(cfg, client, once=True, log=quiet)
    assert client.download_calls == 1
    client.finish()
    fetch(cfg, client, once=True, log=quiet)             # a "new process": only the second one is fetched
    assert client.download_calls == 2


def test_crash_while_processing_loses_nothing(cfg, monkeypatch):
    import era5_bulk.retrieve as retrieve
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.finish()

    def boom(*a, **k):
        raise RuntimeError("cdo exploded")
    real = retrieve.extract_points_from_file
    monkeypatch.setattr(retrieve, "extract_points_from_file", boom)
    s = fetch(cfg, client, once=True, log=quiet)
    assert not s.complete and len(s.errors) == 2 and s.downloaded == 2
    assert len(list(cfg.area_dir.glob("*.grib"))) == 2, "the file must survive a failed extraction"
    assert state_of(cfg)["done"] == [], "and must not be recorded as done"

    monkeypatch.setattr(retrieve, "extract_points_from_file", real)
    downloads = client.download_calls
    s = fetch(cfg, client, once=True, log=quiet)         # recovers WITHOUT downloading again
    assert s.complete and client.download_calls == downloads


def test_interrupted_download_leaves_no_half_file(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.finish()
    client.fail_downloads = 1
    s = fetch(cfg, client, once=True, log=quiet)
    assert len(s.errors) == 1 and s.done == 1
    assert not list(cfg.area_dir.glob("*.part")), "no .part left behind"
    assert not list(cfg.area_dir.glob("*.grib")), "a partial file must never be mistaken for a finished one"
    assert get_summary(cfg).queued == 1, "the failed one stays queued, to be fetched next time"
    assert fetch(cfg, client, once=True, log=quiet).complete


def test_hard_kill_during_a_download_is_recovered(cfg):
    """A process killed mid-download must not leave something that looks like a finished file."""
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.finish()
    client.kill_downloads = 1
    with pytest.raises(KeyboardInterrupt):
        fetch(cfg, client, once=True, log=quiet)
    assert not list(cfg.area_dir.glob("*.grib")), "the half-written file must not sit under its final name"
    assert fetch(cfg, client, once=True, log=quiet).complete        # next run: downloaded again, properly
    assert all(r.ok for r in validate(cfg))


def test_request_unknown_to_the_cds_is_sent_again(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.forget("job-1")                               # e.g. expired
    client.finish()
    s = fetch(cfg, client, once=True, log=quiet)
    assert (s.done, s.to_send) == (1, 1)
    assert submit(cfg, client, log=quiet).queued == 1 and len(client.submitted) == 3


def test_network_error_does_not_cause_a_resubmission(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.network_down = True
    s = fetch(cfg, client, once=True, log=quiet)
    assert (s.queued, s.to_send) == (2, 0), "a network error is not a lost request"
    submit(cfg, client, log=quiet)
    assert len(client.submitted) == 2, "so nothing may be sent again"


def test_failing_request_is_retried_then_given_up(cfg):
    client = FakeClient()
    cfg.max_attempts = 2
    for attempt in (1, 2):
        submit(cfg, client, log=quiet)
        client.finish(status="failed", only={f"job-{len(client.jobs)}"} if attempt else None)
        fetch(cfg, client, once=True, log=quiet)
    assert state_of(cfg)["attempts"]
    sent = len(client.submitted)
    submit(cfg, client, log=quiet)
    assert len(client.submitted) == sent                 # one is still queued, the other is given up: nothing is sent
    gave_up = get_summary(cfg).gave_up
    assert gave_up == 1


def test_a_different_request_in_the_same_folder_is_refused(cfg):
    submit(cfg, FakeClient(), log=quiet)
    other = Config(data_dir=cfg.data_dir, dates=cfg.dates, points=cfg.points, time=cfg.time, max_days=3,
                   levelist="1/to/137")
    with pytest.raises(StateMismatchError):
        load_state(other)


def test_the_period_can_be_extended(cfg):
    client = FakeClient()
    submit(cfg, client, log=quiet)
    client.finish()
    fetch(cfg, client, once=True, log=quiet)
    longer = Config(data_dir=cfg.data_dir, dates="2020-01-01/2020-01-09", points=cfg.points, time=cfg.time,
                    max_days=3)
    s = submit(longer, client, log=quiet)
    assert s.done == 2 and s.queued == 1 and len(client.submitted) == 3, "only the new piece is requested"


def test_run_works_through_more_pieces_than_the_queue_allows(tmp_path):
    cfg = Config(data_dir=tmp_path / "d", dates="2020-01-01/2020-01-09", points={"Cabauw": (52.0, 5.0)},
                 time="00/to/23/by/12", max_days=3, max_queued=1)
    client = FakeClient()
    for _ in range(6):
        s = run(cfg, client, once=True, log=quiet)       # what a scheduled job does, once per day
        assert get_summary(cfg).queued <= 1
        client.finish()
        if s.complete:
            break
    s = run(cfg, client, once=True, log=quiet)
    assert s.complete and len(client.submitted) == 3
