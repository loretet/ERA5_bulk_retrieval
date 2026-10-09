from datetime import date, datetime, timedelta

import pytest

from era5_bulk.dates import (count_values, expected_timestamps, parse_dates, parse_times, piece_tag,
                             split_dates)


def test_documented_example():
    assert split_dates("2020-01-01/2020-03-31", 16) == [
        "2020-01-01/2020-01-15", "2020-01-16/2020-01-31",
        "2020-02-01/2020-02-14", "2020-02-15/2020-02-29",
        "2020-03-01/2020-03-15", "2020-03-16/2020-03-31",
    ]


def test_two_years_is_48_pieces():
    assert len(split_dates("2020-01-01/2021-12-31", 16)) == 48


@pytest.mark.parametrize("period", [
    "2020-01-01/2020-01-01", "2020-02-27/2020-03-02", "2019-12-30/2020-01-02", "2020-01-15/2020-06-20",
    "2021-02-01/2021-02-28", "2020-01-01/2022-12-31",
])
@pytest.mark.parametrize("max_days", [1, 5, 15, 16, 31])
def test_pieces_tile_the_period_exactly(period, max_days):
    first, last = parse_dates(period)
    days = []
    for piece in split_dates(period, max_days):
        a, b = parse_dates(piece)
        assert (b - a).days + 1 <= max_days                      # never longer than allowed
        assert (a.year, a.month) == (b.year, b.month)            # never crosses a month
        days += [a + timedelta(d) for d in range((b - a).days + 1)]
    assert days == [first + timedelta(d) for d in range((last - first).days + 1)]   # every day once, in order


def test_no_tiny_leftover_piece():
    sizes = [(parse_dates(p)[1] - parse_dates(p)[0]).days + 1 for p in split_dates("2020-01-01/2020-12-31", 16)]
    assert min(sizes) >= 14


def test_mars_form_and_errors():
    assert parse_dates("2020-01-01/to/2020-01-05") == (date(2020, 1, 1), date(2020, 1, 5))
    for bad in ("2020-01-01", "2020-02-01/2020-01-01", "yesterday/today"):
        with pytest.raises(ValueError):
            parse_dates(bad)


def test_piece_tag():
    assert piece_tag("2020-01-01/2020-01-15") == "20200101-20200115"


@pytest.mark.parametrize("spec,hours", [
    ("00/to/23/by/1", list(range(24))), ("00/to/21/by/3", list(range(0, 24, 3))),
    ("00/06/12/18", [0, 6, 12, 18]), ("12/00", [0, 12]),
])
def test_parse_times(spec, hours):
    assert parse_times(spec) == [timedelta(hours=h) for h in hours]


def test_expected_timestamps():
    ts = expected_timestamps("2020-01-01/2020-01-02", "00/to/23/by/6")
    assert ts[0] == datetime(2020, 1, 1) and ts[-1] == datetime(2020, 1, 2, 18) and len(ts) == 8
    assert len(expected_timestamps("2020-01-01/2021-12-31", "00/to/23/by/1")) == 731 * 24


@pytest.mark.parametrize("spec,n", [("110/to/137", 28), ("1/to/137", 137), ("1/to/137/by/2", 69),
                                    ("130/131/132", 3), ("152", 1)])
def test_count_values(spec, n):
    assert count_values(spec) == n
