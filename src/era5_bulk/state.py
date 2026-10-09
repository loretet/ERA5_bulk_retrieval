"""
The state file (data_dir/cds_requests.json): the only memory of the whole system.

    {
     "signature": "9f2c...",                      what is being requested (see Config.signature)
     "pending":  {"era5_20200101-20200115.grib": "<CDS request id>", ...},   sent to the CDS, not downloaded yet
     "done":     ["era5_20200116-20200131.grib", ...],                        downloaded AND processed
     "attempts": {"era5_20200201-20200214.grib": 1}                          times the CDS failed/rejected it
    }

Everything else is inferred, never stored:
    in neither pending nor done  -> still to be sent
    file on disk but not in done -> downloaded, waiting to be processed (e.g. the run was interrupted)
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

from .config import Config

try:
    import fcntl
except ImportError:  # Windows: no locking, the user has to avoid running two instances at once
    fcntl = None


class StateMismatchError(RuntimeError):
    pass


class AlreadyRunningError(RuntimeError):
    pass


@contextlib.contextmanager
def locked(cfg: Config):
    """
    Only one era5-bulk process may work in a data_dir at a time (two cron runs overlapping would both
    download the same files). The lock is released by the system even if the process is killed.
    """
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.data_dir / ".era5_bulk.lock", "w") as handle:
        if fcntl is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise AlreadyRunningError(f"another era5-bulk is already running in {cfg.data_dir}") from None
        yield


def load_state(cfg: Config) -> dict:
    state = {"signature": None, "pending": {}, "done": [], "attempts": {}}
    if cfg.state_file.exists():
        state.update(json.loads(cfg.state_file.read_text()))
    if state["signature"] not in (None, cfg.signature):
        raise StateMismatchError(
            f"{cfg.state_file} belongs to a different request (another area, levels, parameters, time or "
            f"grid).\nUse another data_dir for this retrieval, or delete that file if you really want to "
            f"start over. Nothing was changed."
        )
    state["signature"] = cfg.signature
    return state


def save_state(cfg: Config, state: dict) -> None:
    """Written after EVERY change, atomically, so that nothing is lost if the process is killed."""
    cfg.state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(cfg.state_file) + ".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(cfg.state_file)


def mark_done(cfg: Config, state: dict, name: str) -> None:
    state["pending"].pop(name, None)
    if name not in state["done"]:
        state["done"].append(name)
    save_state(cfg, state)
