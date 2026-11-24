import numpy as np
import pytest

from chillwatch.chill.models import A0, A1, E0, E1, SLOPE, TF, chill_hours, chill_portions, dynamic_model


def _chillr(temp_c: np.ndarray) -> np.ndarray:
    """Line-by-line port of the Dynamic Model loop in chillR's R/chilling.R."""
    tk = temp_c + 273
    sr = np.exp(SLOPE * TF * (tk - TF) / tk)
    xi = sr / (1 + sr)
    xs = (A0 / A1) * np.exp((E1 - E0) / tk)
    ak1 = A1 * np.exp(-E1 / tk)
    e = np.zeros(len(temp_c))
    s = np.zeros(len(temp_c))
    for i in range(1, len(temp_c)):
        s[i] = e[i - 1] if e[i - 1] < 1 else e[i - 1] - e[i - 1] * xi[i - 1]
        e[i] = xs[i] - (xs[i] - s[i]) * np.exp(-ak1[i])
    return np.where(e >= 1, e * xi, 0.0)


@pytest.fixture()
def winter():
    rng = np.random.default_rng(0)
    h = np.arange(24 * 150)
    return 7 + 6 * np.sin(2 * np.pi * (h % 24) / 24) + 4 * np.sin(2 * np.pi * h / 480) + rng.normal(0, 1.5, len(h))


def test_dynamic_model_matches_chillr(winter):
    inc, _ = dynamic_model(winter[1:])
    np.testing.assert_allclose(_chillr(winter)[1:], inc, atol=1e-9)
    assert 50 < inc.sum() < 150          # a plausible Central Valley winter, not a degenerate series


def test_state_continues_a_split_season(winter):
    whole = chill_portions(winter)
    a, s = dynamic_model(winter[:1000])
    b, _ = dynamic_model(winter[1000:], state=s)
    assert np.nansum(a) + np.nansum(b) == pytest.approx(whole, abs=1e-9)


def test_warm_hours_give_nothing_and_cool_hours_accumulate():
    assert chill_portions(np.full(500, 25.0)) == 0.0
    assert chill_portions(np.full(500, -10.0)) < 1e-3          # too cold to form the precursor
    assert chill_portions(np.full(500, 6.0)) > chill_portions(np.full(500, 12.0)) > 0


def test_missing_hours_hold_state():
    t = np.full(300, 6.0)
    gap = t.copy()
    gap[100:120] = np.nan
    inc, _ = dynamic_model(gap)
    assert np.isnan(inc[100:120]).all()
    full, _ = dynamic_model(np.delete(t, range(100, 120)))
    assert np.nansum(inc) == pytest.approx(full.sum())


def test_chill_hours_band():
    out = chill_hours(np.array([-0.1, 0.0, 3.0, 7.2, 7.3, np.nan]))
    np.testing.assert_array_equal(out[:5], [0, 1, 1, 1, 0])
    assert np.isnan(out[5])
