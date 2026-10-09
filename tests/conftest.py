import shutil

import pytest

from era5_bulk import Config

needs_cdo = pytest.mark.skipif(shutil.which("cdo") is None, reason="CDO is not installed")


@pytest.fixture
def cfg(tmp_path):
    """Two points, six days in pieces of three days, four steps a day: 2 requests, ~60x6 grid points each."""
    return Config(
        data_dir=tmp_path / "data",
        dates="2020-01-01/2020-01-06",
        points={"Mace Head": (53.25, -10.0), "Cabauw": (52.0, 5.0)},
        time="00/to/23/by/6",
        max_days=3,
    )
