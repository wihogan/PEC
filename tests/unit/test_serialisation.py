"""_safe_float and _dates_to_strings (plan §6.1)."""
import math

import numpy as np
import pandas as pd
import pytest

from refresh_v2 import _dates_to_strings, _safe_float


@pytest.mark.parametrize("v", [None, float("nan"), np.nan, float("inf"), float("-inf"), np.inf, pd.NA])
def test_safe_float_non_finite_is_none(v):
    assert _safe_float(v) is None


@pytest.mark.parametrize("v", ["abc", "", object(), [1]])
def test_safe_float_uncoercible_is_none(v):
    assert _safe_float(v) is None


@pytest.mark.parametrize(
    "v, expected",
    [
        (4.375, 4.375),
        (1.23456, 1.2346),
        (1.23454, 1.2345),
        (-0.000049, -0.0),
        (6912.123456789, 6912.1235),
        (np.float64(3.14159265), 3.1416),
        (7, 7.0),
        ("2.718281", 2.7183),
    ],
)
def test_safe_float_rounds_to_4dp(v, expected):
    out = _safe_float(v)
    assert isinstance(out, float)
    assert out == expected


def test_safe_float_output_is_json_safe():
    import json

    vals = [_safe_float(v) for v in [1.0, float("nan"), float("inf"), 2.123456]]
    assert json.dumps(vals, allow_nan=False) == "[1.0, null, null, 2.1235]"


def test_dates_to_strings_iso():
    df = pd.DataFrame(
        {
            "date": pd.to_datetime(["2026-01-02", "2026-11-20 15:30:00", "1999-12-31"], format="ISO8601"),
            "value": [1, 2, 3],
        }
    )
    assert _dates_to_strings(df) == ["2026-01-02", "2026-11-20", "1999-12-31"]


def test_dates_to_strings_empty():
    df = pd.DataFrame({"date": pd.to_datetime([]), "value": []})
    assert _dates_to_strings(df) == []


def test_dates_to_strings_requires_datetime_column():
    df = pd.DataFrame({"date": ["2026-01-02"], "value": [1]})
    with pytest.raises(AttributeError):
        _dates_to_strings(df)
