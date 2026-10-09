"""
Submitting the requests to the CDS, and fetching the results once they are ready.

The CDS works on the requests on its own servers, for hours or days, whether your computer is on or not. So:

    submit()  sends the requests and returns in minutes: the computer can then be switched off.
    fetch()   downloads the ones that are ready, one by one, processes each right away (cuts the points out and
              deletes the big file) and records it. It can be stopped and started again at any time.
    run()     does both, in a loop: collect what is ready, send more, repeat.

Which request is in which state is never stored as a list of "to do"; it is worked out every time by comparing
the full list of jobs (computed from the config) with the state file (see state.py).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import COLLECTION, Config, Job
from .extract import extract_points_from_file, merge_point, needs_merge
from .state import load_state, locked, mark_done, save_state
from .util import log as default_log

#: The CDS gave up on the request. Counts as an attempt: after cfg.max_attempts it is not sent again.
FAILED_STATES = {"failed", "rejected", "dismissed"}
#: The CDS no longer has the request (removed, or its result expired). It is simply sent again.
LOST_STATES = {"deleted"}


def make_client(retry_max: int = 10, sleep_max: float = 60):
    """
    The low-level CDS client, which can submit a request WITHOUT waiting for the result (cdsapi's own
    `retrieve` blocks until the file is ready, which is exactly what we want to avoid).
    Reads the credentials from ~/.cdsapirc.

    The client contacts the CDS as soon as it is created, and cdsapi's default is to retry a failing
    connection up to 500 times, pausing up to 2 minutes each time: a scheduled job that starts while the
    computer is offline would hang for most of a day, holding the lock. Here a failing call is tried
    `retry_max` times with pauses of at most `sleep_max` seconds, then reported: the next run tries again.
    """
    import cdsapi

    try:
        legacy = cdsapi.Client(quiet=True, retry_max=retry_max, sleep_max=sleep_max)
    except Exception as error:
        if "configuration file" in str(error):
            raise RuntimeError(
                f"Could not create the CDS client: {error}\n"
                "Put your key in ~/.cdsapirc (see https://cds.climate.copernicus.eu/how-to-api) "
                "and accept the licence of the ERA5 dataset on the CDS website."
            ) from error
        raise RuntimeError(
            f"Could not reach the CDS: {error}\n"
            "Is the network up? Nothing was changed; the next run tries again."
        ) from error
    client = getattr(legacy, "client", None)
    if client is None:
        raise RuntimeError(
            "This cdsapi/key combination has no submit-and-fetch client: your ~/.cdsapirc seems to hold a key "
            "of the old CDS (in the form uid:key). Create a new key on https://cds.climate.copernicus.eu and "
            "use `pip install -U cdsapi`."
        )
    return client


#: For scheduled `--once` runs: give up quickly if the CDS cannot be reached (about a minute).
FAST_FAIL = {"retry_max": 3, "sleep_max": 20}


@dataclass
class Summary:
    total: int = 0
    done: int = 0           # downloaded and processed
    downloaded: int = 0     # on disk but not processed yet (a previous run was interrupted, or processing failed)
    queued: int = 0         # sent to the CDS, not downloaded yet
    to_send: int = 0        # not sent yet
    gave_up: int = 0        # failed on the CDS max_attempts times
    errors: list = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.done == self.total

    def line(self) -> str:
        parts = [f"{self.done}/{self.total} done", f"{self.queued} queued or running at the CDS",
                 f"{self.to_send} still to send"]
        if self.downloaded:
            parts.append(f"{self.downloaded} downloaded but not processed")
        if self.gave_up:
            parts.append(f"{self.gave_up} FAILED repeatedly")
        return "-- " + ", ".join(parts)


def get_summary(cfg: Config, state: dict | None = None) -> Summary:
    state = state or load_state(cfg)
    s = Summary()
    for job in cfg.jobs():
        s.total += 1
        if job.name in state["done"]:
            s.done += 1
        elif job.path.exists():
            s.downloaded += 1
        elif job.name in state["pending"]:
            s.queued += 1
        elif state["attempts"].get(job.name, 0) >= cfg.max_attempts:
            s.gave_up += 1
        else:
            s.to_send += 1
    return s


def _is_not_found(error: Exception) -> bool:
    """True only if the CDS answered 'this request does not exist' (not for network errors and the like)."""
    if getattr(getattr(error, "response", None), "status_code", None) == 404:
        return True
    text = str(error).lower()
    return "404 client error" in text or "not found" in text


# ----------------------------------------------------------------------------------------------------------
# Submit
# ----------------------------------------------------------------------------------------------------------

def submit(cfg: Config, client=None, limit: int | None = None, log=default_log) -> Summary:
    """
    Sends to the CDS every request that is not done, not queued and not given up on, WITHOUT downloading anything.
    Stops at the first refusal from the CDS (e.g. too many requests queued): call it again later and it
    continues where it stopped. `limit` sends at most that many (useful to try things out).
    """
    client = client or make_client()
    state = load_state(cfg)
    sent = 0

    for job in cfg.jobs():
        if job.name in state["done"] or job.name in state["pending"] or job.path.exists():
            continue
        if state["attempts"].get(job.name, 0) >= cfg.max_attempts:
            continue
        if limit is not None and sent >= limit:
            break
        if cfg.max_queued and len(state["pending"]) >= cfg.max_queued:
            log(f"{len(state['pending'])} requests are queued (max_queued = {cfg.max_queued}): the rest "
                f"is sent next time")
            break
        try:
            remote = client.submit(COLLECTION, job.request)
        except Exception as error:
            log(f"The CDS refused {job.name}: {error}")
            if "cost" in str(error).lower() or "too large" in str(error).lower():
                log("--> The request is too big for the CDS: use a smaller max_days, area, or fewer "
                    "levels/parameters (`era5-bulk plan` shows the size).")
            log("--> Stopping here; what was sent so far is recorded. Run again later for the rest.")
            break
        state["pending"][job.name] = remote.request_id
        save_state(cfg, state)             # after EVERY request: nothing is lost if the process is killed
        sent += 1
        log(f"Sent {job.name} ({remote.request_id})")

    summary = get_summary(cfg, state)
    log(f"{sent} new request(s) sent.")
    log(summary.line())
    return summary


# ----------------------------------------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------------------------------------

def _process(cfg: Config, job: Job, state: dict, on_file, errors: list, log) -> bool:
    """Works on a downloaded file (cuts the points out, deletes it) and only THEN marks it as done."""
    try:
        if on_file is not None:
            on_file(job.path, job)
        elif cfg.points:
            extract_points_from_file(cfg, job, log=log)
    except Exception as error:
        message = f"{job.name}: processing failed: {error}"
        log(message)
        errors.append(message)
        return False
    # Marked as done only now: if the process dies before this line, the file is still on disk and is
    # processed again next time, instead of being lost.
    mark_done(cfg, state, job.name)
    return True


def _fetch_pass(cfg: Config, client, on_file, log) -> list:
    """One look at everything that is waiting. Never blocks. Returns the errors that happened."""
    errors: list = []
    state = load_state(cfg)

    for job in cfg.jobs():
        if job.name in state["done"]:
            continue

        if job.path.exists():       # downloaded earlier, but never processed (interrupted run)
            log(f"{job.name} is on disk but was not processed: doing it now")
            _process(cfg, job, state, on_file, errors, log)
            continue

        request_id = state["pending"].get(job.name)
        if request_id is None:
            continue

        try:
            remote = client.get_remote(request_id)
            status = remote.status
        except Exception as error:
            if _is_not_found(error):
                log(f"{job.name}: the CDS does not know request {request_id} (any more): it will be sent again")
                state["pending"].pop(job.name)
                save_state(cfg, state)
            else:   # a network problem or similar: the request is probably fine, look again next time
                log(f"{job.name}: could not check the request now ({error}); trying again next time")
            continue

        if status == "successful":
            job.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = job.path.with_name(job.name + ".part")     # renamed only when complete, so an interrupted
            tmp.unlink(missing_ok=True)                      # download never looks like a finished file
            try:
                log(f"Downloading {job.name}")
                remote.download(str(tmp))
                size = tmp.stat().st_size
                tmp.replace(job.path)
            except Exception as error:
                message = f"{job.name}: download failed: {error}"
                log(message)
                errors.append(message)
                tmp.unlink(missing_ok=True)
                continue
            log(f"Downloaded {job.name} ({size / 1e9:.2f} GB)")
            try:
                remote.delete()     # free the result on the CDS side
            except Exception:
                pass
            _process(cfg, job, state, on_file, errors, log)

        elif status in FAILED_STATES:
            state["attempts"][job.name] = state["attempts"].get(job.name, 0) + 1
            state["pending"].pop(job.name)
            save_state(cfg, state)
            left = cfg.max_attempts - state["attempts"][job.name]
            log(f"{job.name}: {status} on the CDS (attempt {state['attempts'][job.name]}/{cfg.max_attempts})"
                + ("; it will be sent again" if left > 0 else "; giving up on it"))

        elif status in LOST_STATES:
            log(f"{job.name}: {status} on the CDS: it will be sent again")
            state["pending"].pop(job.name)
            save_state(cfg, state)

        # any other status ('accepted', 'running'): still waiting

    return errors


def finalize(cfg: Config, log=default_log) -> list:
    """Merges the pieces of every point into one file for the whole period. Returns the errors."""
    errors = []
    if not (cfg.points and cfg.merge_when_complete):
        return errors
    for point in cfg.points:
        if not needs_merge(cfg, point):
            continue
        try:
            merge_point(cfg, point, log=log)
        except Exception as error:
            errors.append(f"merging {point}: {error}")
            log(f"Could not merge {point}: {error}")
    return errors


def fetch(cfg: Config, client=None, once: bool = False, wait_minutes: float = 5, on_file=None,
          log=default_log, hint: bool = True) -> Summary:
    """
    Downloads the results that are ready, one by one, and processes each as it arrives.

    once=False  keeps checking every `wait_minutes` until everything is downloaded (or nothing is queued
                any more). Needs the computer to stay on for that long.
    once=True   looks once and returns. This is the mode for a daily scheduled job.

    `on_file(path, job)`, if given, replaces the default processing (cutting out the points).
    When everything is done and there are points, the pieces of each point are merged into one file.
    """
    client = client or make_client(**(FAST_FAIL if once else {}))
    while True:
        errors = _fetch_pass(cfg, client, on_file, log)
        summary = get_summary(cfg)
        if summary.complete:
            errors += finalize(cfg, log)
        summary.errors = errors
        log(summary.line())

        if summary.complete:
            log("--- Everything is downloaded ---")
            return summary
        if summary.queued == 0 and not summary.downloaded:
            if summary.to_send and hint:
                log("Nothing is queued at the CDS. Send the missing requests with `submit` (or use `run`).")
            return summary
        if once:
            return summary
        time.sleep(wait_minutes * 60)


# ----------------------------------------------------------------------------------------------------------
# Run
# ----------------------------------------------------------------------------------------------------------

def run(cfg: Config, client=None, once: bool = False, wait_minutes: float = 5, on_file=None,
        log=default_log) -> Summary:
    """
    Collect what is ready, send more requests, repeat. Because the fetch comes first, the slots it frees at the
    CDS are refilled in the same go, which is what makes `max_queued` work for periods with more pieces than
    the CDS lets you queue.
    once=True does one round and returns (for a scheduled job); otherwise it loops until everything is done.
    """
    client = client or make_client(**(FAST_FAIL if once else {}))
    while True:
        summary = fetch(cfg, client, once=True, on_file=on_file, log=log, hint=False)
        if summary.complete:
            return summary
        summary = submit(cfg, client, log=log)
        if once:
            return summary
        if summary.queued == 0 and not summary.downloaded:
            log("Nothing is queued at the CDS and nothing could be sent: stopping.")
            return summary
        time.sleep(wait_minutes * 60)
