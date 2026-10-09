"""
`plan`: what a configuration will ask the CDS for, and how big that is, BEFORE anything is sent.

The sizes are estimates. They assume 16 bits per value (what the ERA5 files from the CDS use for the fields
checked so far) plus ~1.3 kB of header per message. Check the first file you download against them.
"""

from __future__ import annotations

from .config import Config
from .dates import count_values, parse_dates, parse_times

BYTES_PER_VALUE = 2
HEADER_BYTES = 1283          # measured on an ERA5 model level GRIB2 message holding a single grid point
CDS_QUEUE_LIMIT = 150        # queued requests per user (as of Oct 2026; the CDS may change it)
CDS_SIZE_ADVICE_GB = 20      # CDS support: keep a single request below about this size


def grid_shape(cfg: Config) -> tuple[int, int]:
    """(number of latitudes, number of longitudes) of the area."""
    north, west, south, east = cfg.area_box
    dlat, dlon = cfg.grid_steps
    n_lat = int(round((north - south) / dlat)) + 1
    width = (east - west) % 360.0
    n_lon = int(round(width / dlon)) + 1
    return n_lat, n_lon


def fields_per_step(cfg: Config) -> int:
    """
    How many fields (messages) one timestep holds. Counts every parameter on every level, except that on model
    levels the log of surface pressure (152) exists only on level 1.
    """
    params = [p.strip() for p in cfg.param.split("/") if p.strip()]
    if cfg.levelist and cfg.levtype in ("ml", "pl"):
        n_levels = count_values(cfg.levelist)
    else:
        n_levels = 1
    total = 0
    for param in params:
        only_level_one = cfg.levtype == "ml" and param.split(".")[0] == "152"
        total += 1 if only_level_one else n_levels
    return total


def estimate(cfg: Config) -> dict:
    n_lat, n_lon = grid_shape(cfg)
    points = n_lat * n_lon
    fields = fields_per_step(cfg)
    steps_per_day = len(parse_times(cfg.time))
    sizes = []
    for piece in cfg.pieces:
        first, last = parse_dates(piece)
        messages = fields * steps_per_day * ((last - first).days + 1)
        sizes.append(messages * (points * BYTES_PER_VALUE + HEADER_BYTES) / 1e9)
    first, last = parse_dates(cfg.dates)
    total_messages = fields * steps_per_day * ((last - first).days + 1)
    return {
        "days": (last - first).days + 1,
        "pieces": len(cfg.pieces),
        "n_lat": n_lat, "n_lon": n_lon, "points": points,
        "fields": fields, "steps_per_day": steps_per_day,
        "request_gb_min": min(sizes), "request_gb_max": max(sizes), "total_gb": sum(sizes),
        "per_point_gb": total_messages * (BYTES_PER_VALUE + HEADER_BYTES) / 1e9,
        "n_points": len(cfg.points),
    }


def warnings(cfg: Config, e: dict) -> list:
    out = []
    if e["pieces"] > CDS_QUEUE_LIMIT and not cfg.max_queued:
        out.append(f"{e['pieces']} requests are more than the {CDS_QUEUE_LIMIT} the CDS lets you queue. "
                   f"Set max_queued (e.g. {CDS_QUEUE_LIMIT - 10}) and use `run`: the rest is sent as the first "
                   f"ones are collected.")
    if cfg.max_queued and cfg.max_queued > CDS_QUEUE_LIMIT:
        out.append(f"max_queued = {cfg.max_queued} is above the CDS limit of {CDS_QUEUE_LIMIT}.")
    if e["request_gb_max"] > CDS_SIZE_ADVICE_GB:
        out.append(f"A request is about {e['request_gb_max']:.0f} GB; CDS support advises staying below "
                   f"~{CDS_SIZE_ADVICE_GB} GB. Use a smaller max_days, fewer levels/parameters, or a smaller "
                   f"area (if the points are far apart, one retrieval per cluster of points).")
    if e["n_lon"] > 720 or (cfg.area_box[3] - cfg.area_box[1]) < 0:
        out.append("The area is very wide or crosses the date line: check that it is what you want.")
    if cfg.levtype == "ml" and any(p.strip().split(".")[0] == "152" for p in cfg.param.split("/")) \
            and cfg.levelist and 1 not in _levels(cfg.levelist):
        out.append("Parameter 152 (log of surface pressure) is stored on model level 1 only: with this levelist "
                   "the CDS will silently NOT return it. Add level 1 to levelist, or request it separately.")
    return out


def _levels(spec: str) -> set:
    tokens = [t.strip() for t in spec.split("/") if t.strip()]
    low = [t.lower() for t in tokens]
    try:
        if "to" in low:
            i = low.index("to")
            step = int(float(tokens[low.index("by") + 1])) if "by" in low else 1
            return set(range(int(float(tokens[i - 1])), int(float(tokens[i + 1])) + 1, step))
        return {int(float(t)) for t in tokens}
    except ValueError:
        return set()


def format_plan(cfg: Config) -> str:
    e = estimate(cfg)
    lines = [
        f"Period           {cfg.dates}  ({e['days']} day{'s' if e['days'] != 1 else ''} -> {e['pieces']} piece{'s' if e['pieces'] != 1 else ''} of at most {cfg.max_days} days)",
        f"Area             {cfg.area}  ({e['n_lat']} x {e['n_lon']} = {e['points']:,} grid points)",
        f"Request          levtype={cfg.levtype}  levelist={cfg.levelist}  param={cfg.param}",
        f"                 time={cfg.time}  grid={cfg.grid}  ->  {e['fields']} fields x {e['steps_per_day']} steps/day",
        f"Requests         {e['pieces']}   (CDS queue limit: {CDS_QUEUE_LIMIT})",
        f"Size / request   {e['request_gb_min']:.1f} - {e['request_gb_max']:.1f} GB   "
        f"(CDS support advises < {CDS_SIZE_ADVICE_GB} GB)",
        f"Total download   {e['total_gb']:.0f} GB   "
        + ("(deleted piece by piece: peak disk use ~one request)" if cfg.delete_area_files
           else "(all kept on disk)"),
    ]
    if cfg.points:
        lines.append(f"Kept             {e['n_points']} point(s), ~{e['per_point_gb']:.1f} GB each for the whole period")
        for name, (lat, lon) in cfg.points.items():
            slat, slon = cfg.snapped_points[name]
            moved = "" if (slat, slon) == (lat, lon) else f"  (requested {lat}, {lon})"
            lines.append(f"                 {name}: grid point {slat}, {slon}{moved}")
    lines.append("Sizes are estimates (16 bits per value); compare with your first downloaded file.")
    for w in warnings(cfg, e):
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)
