"""
era5_bulk: retrieve ERA5 from the Copernicus CDS in bulk, without keeping your computer on.

    from era5_bulk import Config, submit, fetch, run, validate

Send all the requests (submit), collect the results whenever they are ready (fetch), cut your points out of the
big files as they arrive, and check at the end that nothing is missing (validate).
"""

__version__ = "0.1.0"

from .config import Config, ConfigError, Job
from .dates import split_dates
from .extract import extract_points_from_file, merge_point
from .plan import estimate, format_plan
from .retrieve import Summary, fetch, get_summary, make_client, run, submit
from .validate import Report, validate

__all__ = [
    "Config", "ConfigError", "Job", "Summary", "Report",
    "submit", "fetch", "run", "get_summary", "make_client",
    "split_dates", "extract_points_from_file", "merge_point",
    "estimate", "format_plan", "validate",
]
