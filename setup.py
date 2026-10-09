"""Installation: `pip install .`  (or `pip install -e .[test]` to work on it)."""

import re
from pathlib import Path

from setuptools import find_packages, setup

here = Path(__file__).parent
version = re.search(r'__version__ = "([^"]+)"', (here / "src" / "era5_bulk" / "__init__.py").read_text()).group(1)

setup(
    name="era5-bulk-retrieval",
    version=version,
    description="Retrieve ERA5 from the Copernicus CDS in bulk: send all requests, fetch them later, "
                "extract your points, and check nothing is missing.",
    long_description=(here / "README.md").read_text(encoding="utf-8"),
    long_description_content_type="text/markdown",
    license="MIT",
    python_requires=">=3.10",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "cdsapi>=0.7.7",                       # new CDS (2024+); provides the submit-without-waiting client
        "tomli>=1.1; python_version < '3.11'",  # reads the config file (built in from Python 3.11)
    ],
    extras_require={
        "test": ["pytest>=7"],
    },
    entry_points={
        "console_scripts": [
            "era5-bulk = era5_bulk.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Intended Audience :: Science/Research",
        "Topic :: Scientific/Engineering :: Atmospheric Science",
    ],
    keywords="ERA5 CDS Copernicus MARS cdsapi reanalysis model-levels download",
)
