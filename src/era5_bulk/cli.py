"""The `era5-bulk` command."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config, ConfigError
from .state import AlreadyRunningError, StateMismatchError, load_state, locked
from .util import CdoNotFoundError, log

TEMPLATE = """\
# era5-bulk configuration. Every line starting with # is a comment.
# Check it with:   era5-bulk plan this_file.toml

data_dir = "~/ERA5_data"              # where everything goes (also: --data-dir, or $ERA5_BULK_DATA_DIR)
dates    = "2020-01-01/2020-12-31"    # first and last day, both included

# The area to request, "North/West/South/East". Optional when `points` are given: it is then the smallest
# box that contains all the points.
# area = "60/-15/45/10"

# What to request (ERA5 on model levels). Surface fields: levtype = "sfc" and no levelist.
levtype  = "ml"
levelist = "110/to/137"               # the lowest 28 model levels
param    = "130/131/132/133"          # temperature, u, v, specific humidity
time     = "00/to/23/by/1"            # every hour
grid     = "0.25/0.25"

max_days          = 16                # longest request. The CDS rejects model level requests of > ~15-17 days
max_queued        = 0                 # >0: never keep more than this many requests queued at the CDS
delete_area_files = true              # with points: delete each big file once the points are cut out of it

# Points to keep (name = [latitude, longitude]). The nearest grid point is used. Delete this block to keep
# the whole area instead.
[points]
Cabauw    = [51.97, 4.93]
Mace_Head = [53.33, -9.90]
"""


def _load(args) -> Config:
    return Config.from_file(args.config, data_dir=args.data_dir)


def _cmd_init(args) -> int:
    target = Path(args.path)
    if target.exists():
        print(f"{target} already exists; not overwriting it", file=sys.stderr)
        return 2
    target.write_text(TEMPLATE)
    print(f"Wrote {target}. Edit it, then run:  era5-bulk plan {target}")
    return 0


def _cmd_plan(args) -> int:
    from .plan import format_plan
    print(format_plan(_load(args)))
    return 0


def _cmd_status(args) -> int:
    from .retrieve import get_summary
    cfg = _load(args)
    state = load_state(cfg)
    summary = get_summary(cfg, state)
    print(f"data_dir: {cfg.data_dir}")
    print(summary.line().removeprefix("-- "))
    rows = []
    for job in cfg.jobs():
        if job.name in state["done"]:
            label = "done"
        elif job.path.exists():
            label = "downloaded, not processed"
        elif job.name in state["pending"]:
            label = f"queued at the CDS ({state['pending'][job.name]})"
        elif state["attempts"].get(job.name, 0) >= cfg.max_attempts:
            label = f"FAILED {state['attempts'][job.name]}x on the CDS: given up"
        else:
            label = "to send"
        rows.append((job.piece, label))
    shown = rows if args.verbose else [r for r in rows if r[1] != "done"]
    for piece, label in shown[:60]:
        print(f"  {piece}  {label}")
    if len(shown) > 60:
        print(f"  ... and {len(shown) - 60} more (use -v for everything)")
    return 0


def _cmd_merge(args) -> int:
    from .extract import merge_point
    cfg = _load(args)
    if not cfg.points:
        print("There are no points in this configuration: nothing to merge.", file=sys.stderr)
        return 2
    with locked(cfg):
        for point in cfg.points:
            merge_point(cfg, point)
    return 0


def _cmd_validate(args) -> int:
    from .validate import format_reports, validate
    cfg = _load(args)
    reports = validate(cfg, use_pieces=args.pieces)
    print(format_reports(cfg, reports))
    return 0 if all(r.ok for r in reports) else 1


def _cmd_doctor(args) -> int:
    import importlib.metadata as md
    import os
    problems = 0
    print(f"era5-bulk {__version__}, Python {sys.version.split()[0]}")
    for pkg in ("cdsapi", "ecmwf-datastores-client"):
        try:
            print(f"  ok       {pkg} {md.version(pkg)}")
        except md.PackageNotFoundError:
            print(f"  MISSING  {pkg}   ->  pip install -U cdsapi")
            problems += 1
    rc = Path.home() / ".cdsapirc"
    if rc.exists() or os.environ.get("CDSAPI_KEY"):
        print(f"  ok       CDS credentials ({'~/.cdsapirc' if rc.exists() else '$CDSAPI_KEY'})")
    else:
        print("  MISSING  CDS credentials: create ~/.cdsapirc (https://cds.climate.copernicus.eu/how-to-api)")
        problems += 1
    cdo = shutil.which("cdo")
    if cdo:
        print(f"  ok       cdo ({cdo})")
    else:
        print("  MISSING  cdo: needed to cut points out of the area files and to merge them.\n"
              "           conda install -c conda-forge cdo   |   brew install cdo   |   sudo apt install cdo\n"
              "           (not needed if you keep whole areas: no `points`)")
    return 1 if problems else 0


def _cmd_transfer(args) -> int:
    """submit / fetch / run"""
    from . import retrieve
    cfg = _load(args)
    try:
        with locked(cfg):
            if args.command == "submit":
                summary = retrieve.submit(cfg, limit=args.limit)
            elif args.command == "fetch":
                summary = retrieve.fetch(cfg, once=args.once, wait_minutes=args.wait_minutes)
            else:
                summary = retrieve.run(cfg, once=args.once, wait_minutes=args.wait_minutes)
    except AlreadyRunningError as error:
        log(f"{error}: nothing done")      # not an error: typically an overlapping scheduled run
        return 0
    return 1 if (summary.errors or summary.gave_up) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="era5-bulk",
        description="Retrieve ERA5 from the CDS in bulk: send all the requests, collect them later, "
                    "cut out your points, and check nothing is missing.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")

    def with_config(name, help_, **kw):
        p = sub.add_parser(name, help=help_, description=help_, **kw)
        p.add_argument("config", help="TOML configuration file (see `era5-bulk init`)")
        p.add_argument("--data-dir", help="overrides data_dir of the config file (and $ERA5_BULK_DATA_DIR)")
        return p

    p = sub.add_parser("init", help="write an example configuration file")
    p.add_argument("path", nargs="?", default="era5-bulk.toml")
    p.set_defaults(func=_cmd_init)

    p = sub.add_parser("doctor", help="check that cdsapi, the CDS credentials and CDO are available")
    p.set_defaults(func=_cmd_doctor)

    p = with_config("plan", "show what will be requested and how big it is (sends nothing)")
    p.set_defaults(func=_cmd_plan)

    p = with_config("submit", "send the requests to the CDS and return (the computer can then be switched off)")
    p.add_argument("--limit", type=int, help="send at most this many requests (to try things out)")
    p.set_defaults(func=_cmd_transfer)

    for name, help_ in (("fetch", "download the results that are ready and process them"),
                        ("run", "fetch what is ready, send more, repeat until everything is done")):
        p = with_config(name, help_)
        p.add_argument("--once", action="store_true",
                       help="look once and return instead of waiting for the CDS (use this in scheduled jobs)")
        p.add_argument("--wait-minutes", type=float, default=5, help="pause between checks (default 5)")
        p.set_defaults(func=_cmd_transfer)

    p = with_config("status", "show what is done, queued and still to send (does not contact the CDS)")
    p.add_argument("-v", "--verbose", action="store_true", help="list the pieces that are done too")
    p.set_defaults(func=_cmd_status)

    p = with_config("merge", "merge the pieces of every point into one file for the whole period")
    p.set_defaults(func=_cmd_merge)

    p = with_config("validate", "check that every timestep and field is there")
    p.add_argument("--pieces", action="store_true", help="check the pieces instead of the merged files")
    p.set_defaults(func=_cmd_validate)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, StateMismatchError, CdoNotFoundError, FileNotFoundError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted. Everything done so far is recorded: run the same command to continue.",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
