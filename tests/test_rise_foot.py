"""The shot-time definition (``splitsmith.rise_foot``, docs/METHODOLOGY.md).

``tests/fixtures/rise_foot/cases.json`` is read here and by the app's
``lib/peak-snap.test.ts``: a case holds on both sides or not at all.
"""

import json
import math
from pathlib import Path

import pytest

from splitsmith.rise_foot import rise_foot

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "rise_foot" / "cases.json").read_text())


def build(case: dict) -> list[float]:
    """The case's 1 ms envelope: floor, straight-line segments, standard bursts."""
    shape = FIXTURE["burst"]
    floor = case["floor"]
    p = [floor] * FIXTURE["bins"]
    for start, end, a, b in case.get("lines", []):
        for k in range(start, end):
            p[k] = max(p[k], a + (b - a) * (k - start) / max(1, end - start))
    for start, peak in case.get("bursts", []):
        rise = shape["rise_bins"]
        for i in range(rise):
            p[start + i] = max(p[start + i], floor + (peak - floor) * (i + 1) / rise)
        for i in range(rise, shape["decay_bins"] + rise):
            v = floor + (peak - floor) * math.exp(-(i - rise) / shape["decay_tau_bins"])
            p[start + i] = max(p[start + i], v)
    return p


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda c: c["name"])
def test_rise_foot_case(case: dict) -> None:
    got = rise_foot(build(case), FIXTURE["duration"], case["time"])
    if case["expect"] is None:
        assert got is None
    else:
        assert got == pytest.approx(case["expect"], abs=1e-6)
