"""Small helpers shared by the other modules: logging, file names and running CDO."""

from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime


def log(message: str = "") -> None:
    """Print a timestamped line (timestamps make the logs of unattended runs readable)."""
    if not message:
        print(flush=True)
        return
    print(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}", flush=True)


def safe_name(name: str) -> str:
    """A name that is safe to use in a file name, e.g. 'Mace Head' -> 'Mace_Head'."""
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name).strip()).strip("_")
    return cleaned or "point"


class CdoNotFoundError(RuntimeError):
    pass


def require_cdo() -> str:
    """Path of the cdo executable, or a clear error saying how to install it."""
    path = shutil.which("cdo")
    if path is None:
        raise CdoNotFoundError(
            "CDO (Climate Data Operators) is needed to cut points out of the downloaded files, merge them "
            "and validate them, but it is not on the PATH.\n"
            "  conda:          conda install -c conda-forge cdo\n"
            "  macOS/Homebrew: brew install cdo\n"
            "  Debian/Ubuntu:  sudo apt install cdo"
        )
    return path


def run_cdo(*args) -> str:
    """Run `cdo -s <args>` and return its standard output. Raises RuntimeError with CDO's message on failure."""
    cmd = [require_cdo(), "-s", *[str(a) for a in args]]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"CDO failed: {' '.join(cmd)}\n{result.stderr.strip()}")
    return result.stdout
