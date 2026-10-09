"""
The description of one retrieval: what to ask the CDS for, where to put it, and which points to cut out of it.

A retrieval is ONE area over a period of dates. The period is cut into pieces (see dates.split_dates) and every
piece is one request. The points (optional) are cut out of every downloaded piece afterwards, locally.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path

from .dates import piece_tag, split_dates, parse_dates
from .util import safe_name

try:  # Python 3.11+ reads TOML out of the box
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 and older need the backport
    try:
        import tomli as tomllib
    except ModuleNotFoundError as error:
        raise ModuleNotFoundError(
            "Cannot read the configuration file: no TOML reader. The standard-library `tomllib` is not "
            f"available (it comes with Python 3.11; this is Python {sys.version_info.major}."
            f"{sys.version_info.minor}) and the `tomli` backport is not installed.\n"
            "Install this package properly (`pip install .` from the repository, which pulls tomli in), or "
            "just `pip install tomli`.\n"
            "Python 3.11 and later need neither."
        ) from error

#: The CDS dataset with the complete ERA5 archive (model levels, and every other level type, through MARS).
COLLECTION = "reanalysis-era5-complete"

#: Environment variable that overrides `data_dir` (handy on a cluster: same config file, different disk).
DATA_DIR_ENV = "ERA5_BULK_DATA_DIR"

_KNOWN_KEYS = {
    "name", "data_dir", "dates", "area", "levtype", "levelist", "param", "time", "grid", "type", "stream",
    "format", "max_days", "max_queued", "max_attempts", "delete_area_files", "merge_when_complete",
    "extra", "points",
}


class ConfigError(ValueError):
    pass


def _fmt(x: float) -> str:
    """12.0 -> '12', 52.25 -> '52.25' (MARS areas are written without needless decimals)."""
    return f"{x:.6f}".rstrip("0").rstrip(".") or "0"


def _wrap_lon(lon: float, west: float) -> float:
    """The longitude `lon` written in the convention of an area that starts at `west` (west <= result < west+360)."""
    return west + ((lon - west) % 360.0)


@dataclass(frozen=True)
class Job:
    """One request: a piece of the period, the MARS request for it and the file it is downloaded to."""
    piece: str      # '2020-01-01/2020-01-15'
    name: str       # 'era5_20200101-20200115.grib' : the identity of the job, used as the key in the state file
    path: Path      # where the downloaded (area) file is stored
    request: dict


@dataclass
class Config:
    data_dir: Path
    dates: str
    #: "N/W/S/E". If not given it is the smallest box containing all the points.
    area: str | None = None
    #: name -> (latitude, longitude). Cut out of every downloaded file (nearest grid point).
    points: dict = field(default_factory=dict)
    #: prefix of the file names
    name: str = "era5"
    levtype: str = "ml"
    levelist: str | None = "110/to/137"
    param: str = "130/131/132/133/152"
    time: str = "00/to/23/by/1"
    grid: str = "0.25/0.25"
    type: str = "an"
    stream: str = "oper"
    format: str = "grib"
    #: longest request, in days (the CDS refuses model level requests of more than ~15-17 days)
    max_days: int = 16
    #: never have more than this many requests queued at the CDS (0 = no limit besides the CDS's own)
    max_queued: int = 0
    #: stop re-sending a request that the CDS has failed/rejected this many times
    max_attempts: int = 3
    #: delete each downloaded area file once the points are cut out of it (only if there are points)
    delete_area_files: bool = True
    #: when everything is downloaded, merge the pieces of every point into one file for the whole period
    merge_when_complete: bool = True
    #: any other MARS keyword, added to (or replacing) the ones above
    extra: dict = field(default_factory=dict)

    def __post_init__(self):
        self.data_dir = Path(self.data_dir).expanduser()
        parse_dates(self.dates)                       # fails early on a malformed period
        self.name = safe_name(self.name)
        self.points = {str(k): self._parse_point(k, v) for k, v in dict(self.points).items()}
        if self.levtype == "sfc" or not self.levelist:
            self.levelist = None                      # surface fields have no levels
        if not self.points:
            self.delete_area_files = False            # without points the area files ARE the result
        names = [safe_name(n) for n in self.points]
        if len(set(names)) != len(names):
            raise ConfigError("two points have names that end up as the same file name; rename one of them")
        if self.area is None:
            if not self.points:
                raise ConfigError("give an `area` ('N/W/S/E') or at least one point")
            self.area = self._bounding_box()
        self._check_area_and_points()

    # ----------------------------------------------------------------------------------------------------
    # Loading
    # ----------------------------------------------------------------------------------------------------

    @classmethod
    def from_file(cls, path: str | Path, data_dir: str | Path | None = None) -> "Config":
        """
        Reads a TOML config file (see examples/).
        Where the data goes, in order of priority: the `data_dir` argument, the ERA5_BULK_DATA_DIR environment
        variable, `data_dir` in the file.
        """
        path = Path(path)
        try:
            with open(path, "rb") as handle:
                raw = tomllib.load(handle)
        except FileNotFoundError:
            raise ConfigError(f"config file not found: {path}") from None
        unknown = set(raw) - _KNOWN_KEYS
        if unknown:
            raise ConfigError(f"unknown key(s) in {path.name}: {', '.join(sorted(unknown))}")
        chosen = data_dir or os.environ.get(DATA_DIR_ENV)
        if chosen:
            raw["data_dir"] = str(chosen)
        for required in ("data_dir", "dates"):
            if required not in raw:
                raise ConfigError(f"`{required}` is missing in {path.name}")
        return cls(**raw)

    @staticmethod
    def _parse_point(name, value) -> tuple[float, float]:
        try:
            if isinstance(value, dict):
                lat, lon = value["lat"], value["lon"]
            else:
                lat, lon = value
            lat, lon = float(lat), float(lon)
        except (KeyError, TypeError, ValueError):
            raise ConfigError(f"point {name!r} must be [latitude, longitude] (or {{lat=..., lon=...}})") from None
        if not -90 <= lat <= 90:
            raise ConfigError(f"point {name!r}: latitude {lat} is outside -90..90")
        if not -180 <= lon <= 360:
            raise ConfigError(f"point {name!r}: longitude {lon} is outside -180..360")
        return lat, lon

    # ----------------------------------------------------------------------------------------------------
    # Grid and area
    # ----------------------------------------------------------------------------------------------------

    @cached_property
    def grid_steps(self) -> tuple[float, float]:
        """(latitude step, longitude step) in degrees."""
        try:
            parts = [float(x) for x in self.grid.split("/")]
            if len(parts) == 1:
                parts = parts * 2
            if len(parts) != 2 or min(parts) <= 0:
                raise ValueError
        except ValueError:
            raise ConfigError(f"grid must look like '0.25/0.25', got {self.grid!r}") from None
        return parts[0], parts[1]

    def snap(self, lat: float, lon: float) -> tuple[float, float]:
        """The grid point nearest to (lat, lon). The grid starts at the equator and at longitude 0."""
        dlat, dlon = self.grid_steps
        return round(round(lat / dlat) * dlat, 6), round(round(lon / dlon) * dlon, 6)

    @cached_property
    def snapped_points(self) -> dict:
        return {name: self.snap(lat, lon) for name, (lat, lon) in self.points.items()}

    def _bounding_box(self) -> str:
        pts = [self.snap(lat, lon) for lat, lon in self.points.values()]
        lats = [p[0] for p in pts]
        lons = [((p[1] + 180) % 360) - 180 for p in pts]       # -180..180
        return "/".join(_fmt(x) for x in (max(lats), min(lons), min(lats), max(lons)))

    @cached_property
    def area_box(self) -> tuple[float, float, float, float]:
        """(north, west, south, east)"""
        try:
            n, w, s, e = (float(x) for x in str(self.area).split("/"))
        except ValueError:
            raise ConfigError(f"area must look like 'N/W/S/E', got {self.area!r}") from None
        if n < s:
            raise ConfigError(f"area {self.area!r}: north is below south")
        return n, w, s, e

    def _check_area_and_points(self) -> None:
        n, w, s, e = self.area_box
        width = (e - w) % 360.0
        for name, (lat, lon) in self.snapped_points.items():
            off = _wrap_lon(lon, w) - w
            if not (s - 1e-6 <= lat <= n + 1e-6) or off > width + 1e-6:
                raise ConfigError(
                    f"point {name!r} (nearest grid point {lat}, {lon}) is outside the area {self.area}"
                )

    @property
    def is_single_point_area(self) -> bool:
        n, w, s, e = self.area_box
        return n == s and w == e

    # ----------------------------------------------------------------------------------------------------
    # The requests
    # ----------------------------------------------------------------------------------------------------

    @cached_property
    def pieces(self) -> list[str]:
        return split_dates(self.dates, self.max_days)

    def request_for(self, piece: str) -> dict:
        """The MARS request for one piece of the period."""
        request = {
            "date": piece.replace("/", "/to/"),
            "levtype": self.levtype,
            "param": self.param,
            "stream": self.stream,
            "time": self.time,
            "type": self.type,
            "area": self.area,
            "grid": self.grid,
            "format": self.format,
        }
        if self.levelist:
            request["levelist"] = self.levelist
        request.update(self.extra)
        return request

    def jobs(self) -> list[Job]:
        out = []
        for piece in self.pieces:
            name = f"{self.name}_{piece_tag(piece)}.grib"
            out.append(Job(piece, name, self.area_dir / name, self.request_for(piece)))
        return out

    @cached_property
    def signature(self) -> str:
        """
        Fingerprint of everything that decides WHAT is requested, except the dates (so the period can be
        extended later). The state file stores it: a data_dir that already holds requests for something else
        (another area, other levels...) is refused instead of silently mixing the two.
        """
        request = self.request_for(self.pieces[0])
        request.pop("date")
        return hashlib.sha1(json.dumps(request, sort_keys=True).encode()).hexdigest()[:16]

    # ----------------------------------------------------------------------------------------------------
    # Files
    # ----------------------------------------------------------------------------------------------------

    @property
    def state_file(self) -> Path:
        return self.data_dir / "cds_requests.json"

    @property
    def area_dir(self) -> Path:
        return self.data_dir / "area_files"

    def point_dir(self, point: str) -> Path:
        return self.data_dir / "points" / safe_name(point)

    def point_piece_path(self, point: str, piece: str) -> Path:
        """The piece of one point, e.g. data_dir/points/Cabauw/Cabauw_20200101-20200115.grib"""
        return self.point_dir(point) / f"{safe_name(point)}_{piece_tag(piece)}.grib"

    def merged_path(self, point: str) -> Path:
        """The whole period of one point, e.g. data_dir/merged/Cabauw.grib"""
        return self.data_dir / "merged" / f"{safe_name(point)}.grib"
