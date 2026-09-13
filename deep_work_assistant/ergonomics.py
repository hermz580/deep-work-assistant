"""Height-calibrated workstation ergonomics.

Ported from KinePost (`src/utils/ergonomics.ts`, MIT) which grounded its
ratios in ANSI/HFES 100-2007 and ISO 9241-5. This is the part of KinePost
worth keeping: deterministic formulas, no camera, no network, fully auditable.

Why this and not KinePost's camera path (see GOVERNANCE note in the PR):
KinePost does not measure posture. Its `/api/analyze-posture` route sends a
base64 camera frame to Google Gemini and asks a language model to RETURN
rulaScore / cervicalLoadEstimateLbs / cvaDeg in a JSON schema; with no API key
and on any error it returns `SAMPLE_ANALYSIS_SAGITTAL` (fixed demo numbers)
labelled `calibrated_preset`. Its temporal "degradation" curves are arithmetic
interpolation, not measurement. DWA already measures posture for real with
MediaPipe landmarks. So the anthropometrics come across; the "measurements"
do not.

Every ratio here is a population-average anthropometric proportion, so results
are a starting point to adjust against the user's body, not a prescription.
"""
from __future__ import annotations

from dataclasses import dataclass

CM_PER_INCH = 2.54
# Reference stature the pixel-scale multiplier is normalised against.
REFERENCE_STATURE_CM = 180.0

# Biomechanical anthropometric ratios (fraction of standing stature).
POPLITEAL_CHAIR_RATIO = 0.255   # seated popliteal height -> chair pan
SEATED_ELBOW_RATIO = 0.395      # seated elbow height -> desk surface
SEATED_EYE_RATIO = 0.685        # seated eye height from floor
MONITOR_BELOW_EYE_CM = 2.0      # top bezel a touch below eye height
VIEWING_DISTANCE_RATIO = 0.40   # arm's length to screen

WORKSTATION_TYPES = ("laptop", "single_monitor", "dual_monitor", "standing_desk")


def _round1(value: float) -> float:
    return round(value, 1)


def feet_inches_to_cm(feet: int, inches: float) -> int:
    return round((feet * 12 + inches) * CM_PER_INCH)


def cm_to_feet_inches(cm: float) -> dict[str, int]:
    total_inches = cm / CM_PER_INCH
    return {"feet": int(total_inches // 12), "inches": round(total_inches % 12)}


@dataclass(frozen=True)
class Anthropometrics:
    """Height-calibrated targets, in cm and inches."""

    user_height_cm: float
    chair_cm: float
    desk_cm: float
    eye_height_cm: float
    monitor_top_cm: float
    viewing_distance_cm: float

    @property
    def chair_in(self) -> float:
        return _round1(self.chair_cm / CM_PER_INCH)

    @property
    def desk_in(self) -> float:
        return _round1(self.desk_cm / CM_PER_INCH)

    @property
    def eye_height_in(self) -> float:
        return _round1(self.eye_height_cm / CM_PER_INCH)

    @property
    def monitor_top_in(self) -> float:
        return _round1(self.monitor_top_cm / CM_PER_INCH)

    @property
    def viewing_distance_in(self) -> float:
        return _round1(self.viewing_distance_cm / CM_PER_INCH)

    @property
    def pixel_scale_multiplier(self) -> float:
        """Scales landmark pixel distances to this user's proportions."""
        return round(self.user_height_cm / REFERENCE_STATURE_CM, 2)

    def as_dict(self) -> dict[str, float]:
        return {
            "user_height_cm": self.user_height_cm,
            "chair_cm": self.chair_cm,
            "desk_cm": self.desk_cm,
            "eye_height_cm": self.eye_height_cm,
            "monitor_top_cm": self.monitor_top_cm,
            "viewing_distance_cm": self.viewing_distance_cm,
            "chair_in": self.chair_in,
            "desk_in": self.desk_in,
            "eye_height_in": self.eye_height_in,
            "monitor_top_in": self.monitor_top_in,
            "viewing_distance_in": self.viewing_distance_in,
            "pixel_scale_multiplier": self.pixel_scale_multiplier,
        }


def calculate_anthropometrics(height_cm: float) -> Anthropometrics:
    """Derive seated workstation targets from standing height."""
    if height_cm is None or height_cm <= 0:
        raise ValueError("height_cm must be positive")
    height_cm = float(height_cm)
    eye = _round1(height_cm * SEATED_EYE_RATIO)
    return Anthropometrics(
        user_height_cm=height_cm,
        chair_cm=_round1(height_cm * POPLITEAL_CHAIR_RATIO),
        desk_cm=_round1(height_cm * SEATED_ELBOW_RATIO),
        eye_height_cm=eye,
        monitor_top_cm=_round1(eye - MONITOR_BELOW_EYE_CM),
        viewing_distance_cm=_round1(height_cm * VIEWING_DISTANCE_RATIO),
    )


WORKSTATION_LEAD = {
    "laptop": (
        "Laptop Warning: a laptop flat on the desk forces a severe downward gaze "
        "angle and roughly 30+ lbs of cervical strain. Use a riser plus a "
        "separate keyboard."
    ),
    "dual_monitor": (
        "Dual Monitor Notice: centre your primary screen on your midline. Angling "
        "two screens equally drives repetitive cervical rotation."
    ),
    "standing_desk": (
        "Sit-Stand Cadence: follow 20-8-2 - 20 minutes sitting, 8 minutes standing, "
        "2 minutes walking or stretching."
    ),
    "single_monitor": (
        "Single Monitor: top bezel at the height below, screen about an arm's "
        "length away, gaze resting slightly downward."
    ),
}


def get_workstation_tips(
    workstation_type: str = "laptop",
    height_cm: float = 178.0,
    unit: str = "in",
) -> list[str]:
    """Height-calibrated setup tips. `unit` is 'in' or 'cm'."""
    a = calculate_anthropometrics(height_cm)

    def fmt(value_cm: float, value_in: float) -> str:
        return f'{value_in}"' if unit == "in" else f"{value_cm} cm"

    chair = fmt(a.chair_cm, a.chair_in)
    desk = fmt(a.desk_cm, a.desk_in)
    monitor = fmt(a.monitor_top_cm, a.monitor_top_in)
    distance = fmt(a.viewing_distance_cm, a.viewing_distance_in)

    tips = [
        f"Chair seat pan: set to {chair} from the floor so feet rest flat and "
        f"knees sit at 90-100 degrees.",
        f"Desk / keyboard surface: set to {desk} so wrists stay straight without "
        f"forearm contact stress.",
        f"Top of monitor bezel: {monitor} from the floor, with the gaze resting "
        f"slightly downward.",
        f"Screen distance: about {distance} - roughly arm's length.",
        "Editor and terminal: raise the coding font to 14-16px to stop "
        "involuntary forward head craning at small tokens.",
    ]
    lead = WORKSTATION_LEAD.get(workstation_type, WORKSTATION_LEAD["single_monitor"])
    return [lead, *tips]
