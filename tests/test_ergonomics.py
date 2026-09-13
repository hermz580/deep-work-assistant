"""Tests for the ported height-calibrated ergonomics.

The expected values are KinePost's own outputs for a 178 cm user, so these
tests prove the Python port did not drift from the TypeScript original.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deep_work_assistant.ergonomics import (
    WORKSTATION_TYPES,
    calculate_anthropometrics,
    cm_to_feet_inches,
    feet_inches_to_cm,
    get_workstation_tips,
)


# --- faithful-port checks (KinePost ergonomics.ts, 178 cm) -------------------
def test_178cm_matches_kinepost_outputs():
    a = calculate_anthropometrics(178)
    assert a.chair_cm == 45.4
    assert a.desk_cm == 70.3
    assert a.eye_height_cm == 121.9
    assert a.monitor_top_cm == 119.9
    assert a.viewing_distance_cm == 71.2


def test_178cm_inch_conversions_match_kinepost():
    a = calculate_anthropometrics(178)
    assert a.chair_in == 17.9
    assert a.desk_in == 27.7
    assert a.eye_height_in == 48.0
    assert a.monitor_top_in == 47.2
    assert a.viewing_distance_in == 28.0
    assert a.pixel_scale_multiplier == 0.99


def test_ratios_hold_across_heights():
    for h in (150, 165, 178, 190, 205):
        a = calculate_anthropometrics(h)
        assert a.chair_cm == round(h * 0.255, 1)
        assert a.desk_cm == round(h * 0.395, 1)
        assert a.monitor_top_cm == round(a.eye_height_cm - 2.0, 1)


def test_viewing_distance_is_about_arms_length():
    a = calculate_anthropometrics(178)
    # 40% of stature — sanity band, not a precise claim
    assert 0.38 * 178 <= a.viewing_distance_cm <= 0.42 * 178


# --- unit helpers -----------------------------------------------------------
def test_feet_inches_round_trip():
    assert feet_inches_to_cm(5, 10) == 178
    assert cm_to_feet_inches(178) == {"feet": 5, "inches": 10}


def test_feet_inches_to_cm_handles_fractional_inches():
    assert feet_inches_to_cm(6, 0.5) == 184


# --- tips -------------------------------------------------------------------
def test_tips_are_height_calibrated_and_use_requested_unit():
    inches = get_workstation_tips("laptop", 178, "in")
    cm = get_workstation_tips("laptop", 178, "cm")
    assert any('17.9"' in t for t in inches)
    assert any("45.4 cm" in t for t in cm)
    assert not any('17.9"' in t for t in cm)


def test_every_workstation_type_gets_a_lead_tip():
    seen = set()
    for wtype in WORKSTATION_TYPES:
        tips = get_workstation_tips(wtype)
        assert len(tips) >= 5
        assert tips[0].strip(), f"{wtype} has an empty lead tip"
        seen.add(tips[0])
    # Each workstation type must give its own advice, not a shared default.
    assert len(seen) == len(WORKSTATION_TYPES)


def test_laptop_gets_the_laptop_warning():
    tips = get_workstation_tips("laptop")
    assert "Laptop Warning" in tips[0]


def test_unknown_workstation_falls_back_without_crashing():
    tips = get_workstation_tips("something_else")
    assert len(tips) >= 5


# --- guards -----------------------------------------------------------------
def test_bad_height_is_rejected():
    for bad in (0, -5, None):
        with pytest.raises(ValueError):
            calculate_anthropometrics(bad)


def test_as_dict_is_json_friendly():
    import json
    d = calculate_anthropometrics(178).as_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["user_height_cm"] == 178.0
