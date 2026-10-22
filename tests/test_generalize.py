import datetime as dt

import pytest

from scrubber.config import load_zip3_restricted
from scrubber.transform.generalize import Generalizer

G = Generalizer(2026, load_zip3_restricted())


def test_hhs_restricted_zip3_list_bundled():
    z = load_zip3_restricted()
    assert len(z) == 17
    assert {"036", "059", "102", "203", "556", "692", "790", "821", "823", "830", "831",
            "878", "879", "884", "890", "893", "063"} == z


@pytest.mark.parametrize("zip_, expected", [
    ("62704", "627XX"), ("62704-1234", "627XX"), ("03601", "000XX"), ("89301", "000XX"),
    ("10201", "000XX"), ("2134", "021XX"), ("90210", "902XX")])
def test_zip_to_three_digits(zip_, expected):
    assert G.zip_text(zip_) == expected


@pytest.mark.parametrize("text, expected", [
    ("March 3, 2021", "2021"), ("03/09/2021", "2021"), ("2021-03-09T10:00:00Z", "2021"), ("3/9/21", "2021"),
    ("3/9/99", "1999"), ("March '21", "2021"), ("Christmas 2020", "2020"), ("March 3", "[DATE]"), ("3/14", "[DATE]")])
def test_dates_keep_only_year(text, expected):
    assert G.date_text(text) == expected


def test_birth_dates_revealing_age_over_89_collapse():
    assert G.date_text("04/12/1931", birth=True) == "90+"
    assert G.date_text("04/12/1960", birth=True) == "1960"
    assert G.date_value(dt.date(1930, 5, 1), birth=True) == "90+"
    assert G.date_value(dt.datetime(1970, 5, 1)) == 1970


@pytest.mark.parametrize("age, expected", [(89, 89), (90, "90+"), ("104", "90+"), ("45", "45")])
def test_ages(age, expected):
    assert G.age(age) == expected


def test_year_alone_is_not_a_date_element():
    assert not G.has_date_element("2021")
    assert G.has_date_element("Jan 2021")
