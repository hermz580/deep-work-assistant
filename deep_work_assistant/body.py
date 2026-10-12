"""Body lane — the real DWA camera helper.

This is the part of the KinePost concept (``hermz580/dwa-cam-helper-``) that
passes the GOVERNANCE.md value framework: it serves health directly, runs
locally, and is built on the existing privacy-gated vision pipeline instead of
mock analyses or cloud image upload.

It owns milestone M2 — Embodied Truth:

- **Consent**: posture guidance starts only after an explicit, revocable grant.
- **Calibration**: a personal neutral baseline from several local samples.
- **Confidence**: every baseline reports how trustworthy it is.
- **Inaccurate**: the user can say an alert was wrong; the threshold widens.
- **Estimates, labeled as estimates**: cervical load and workstation targets
  are rules of thumb, never presented as clinical measurements.

Only metrics are stored (``~/.deep_work_assistant/body_profile.json``).
Frames never reach this module.
"""
from __future__ import annotations

import json
import math
import statistics
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from .vision import DEFAULT_FWD_HEAD_THRESHOLD

BODY_PROFILE_PATH = Path.home() / ".deep_work_assistant" / "body_profile.json"
VISION_EVENTS_PATH = Path.home() / ".deep_work_assistant" / "vision_events.jsonl"

CONSENT_VERSION = 1
MIN_CALIBRATION_SAMPLES = 5
DEFAULT_CALIBRATION_SAMPLES = 8
MAX_CALIBRATION_SAMPLES = 20

# A personal threshold sits this far above the neutral baseline (degrees),
# or three robust spreads above it, whichever is larger.
MIN_THRESHOLD_MARGIN = 10.0
SPREAD_MULTIPLIER = 3.0
THRESHOLD_FLOOR = 12.0
THRESHOLD_CEILING = 60.0
# Each "that alert was inaccurate" report widens the threshold by this much.
INACCURATE_STEP = 3.0
MAX_INACCURATE_OFFSET = 15.0

WORKSTATIONS = ("laptop", "external_monitor", "dual_monitor", "standing_desk")

# Hansraj (2014, Surgical Technology International) modelled effective load
# on the cervical spine against neck flexion. DWA's forward-head metric is a
# shoulder→ear angle from vertical, which is a proxy for flexion, so the
# result is an *estimate* for motivation, not a measurement.
_HANSRAJ_POINTS = ((0.0, 12.0), (15.0, 27.0), (30.0, 40.0), (45.0, 49.0), (60.0, 60.0))


# ── Pure logic ─────────────────────────────────────────────────────

def cervical_load_lbs(fwd_head_deg: float | None) -> float | None:
    """Estimate effective head load (lbs) by interpolating Hansraj's curve."""
    if fwd_head_deg is None or not math.isfinite(fwd_head_deg):
        return None
    angle = max(0.0, min(60.0, float(fwd_head_deg)))
    for (a0, l0), (a1, l1) in zip(_HANSRAJ_POINTS, _HANSRAJ_POINTS[1:]):
        if angle <= a1:
            return round(l0 + (l1 - l0) * (angle - a0) / (a1 - a0), 1)
    return _HANSRAJ_POINTS[-1][1]


def workstation_targets(height_cm: float, workstation: str = "laptop") -> dict[str, Any]:
    """Seated workstation rules of thumb scaled from standing height.

    Ratios are population averages (seat ≈ popliteal height, desk ≈ seated
    elbow height, eye ≈ seated eye height). They are starting points to be
    adjusted by feel, which is why the UI labels them as approximate.
    """
    height = float(height_cm)
    if not math.isfinite(height) or not 120.0 <= height <= 230.0:
        raise ValueError("height must be between 120 and 230 cm")
    if workstation not in WORKSTATIONS:
        workstation = "laptop"
    seat = height * 0.255
    desk = height * 0.395
    eye = height * 0.685
    targets = {
        "seat_height_cm": round(seat, 1),
        "desk_height_cm": round(desk, 1),
        "eye_height_cm": round(eye, 1),
        "monitor_top_cm": round(eye - 2.0, 1),
        "viewing_distance_cm": round(height * 0.40, 1),
    }
    if workstation == "standing_desk":
        targets["standing_desk_height_cm"] = round(height * 0.63, 1)
    tips = {
        "laptop": "A laptop screen on the desk pulls your gaze down. Raise it and use a separate keyboard.",
        "external_monitor": "Center the main monitor on your midline, top edge at or just below eye level.",
        "dual_monitor": "Put the screen you use most directly in front of you; angle the second one beside it.",
        "standing_desk": "Alternate: sit about 20 minutes, stand about 8, move about 2.",
    }
    return {
        "height_cm": round(height, 1),
        "workstation": workstation,
        "targets_cm": targets,
        "tip": tips[workstation],
        "basis": "approximate population ratios; adjust by comfort",
    }


def compute_baseline(samples: Iterable[dict[str, Any] | None]) -> dict[str, Any]:
    """Build a neutral posture baseline from visible posture samples.

    Uses the median and the median absolute deviation, so one bad frame
    cannot move the baseline. Raises ValueError when there is not enough
    visible evidence to calibrate honestly.
    """
    fwd: list[float] = []
    tilt: list[float] = []
    for sample in samples:
        if not isinstance(sample, dict) or sample.get("visible") is not True:
            continue
        value = sample.get("fwd_head_deg")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            fwd.append(float(value))
            t = sample.get("shoulder_tilt_deg")
            if isinstance(t, (int, float)) and not isinstance(t, bool) and math.isfinite(t):
                tilt.append(float(t))
    if len(fwd) < MIN_CALIBRATION_SAMPLES:
        raise ValueError(
            f"calibration needs at least {MIN_CALIBRATION_SAMPLES} visible samples; got {len(fwd)}. "
            "Check that your ears and shoulders are in frame and the room is lit."
        )
    median = statistics.median(fwd)
    spread = statistics.median(abs(v - median) for v in fwd)
    if len(fwd) >= 8 and spread <= 3.0:
        confidence = "high"
    elif spread <= 6.0:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "fwd_head_median": round(median, 1),
        "fwd_head_spread": round(spread, 1),
        "shoulder_tilt_median": round(statistics.median(tilt), 1) if tilt else None,
        "samples": len(fwd),
        "confidence": confidence,
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
    }


def personal_threshold(baseline: dict[str, Any] | None, inaccurate_offset: float = 0.0) -> float:
    """Forward-head alert threshold for this person (degrees).

    Without a baseline this is the generic DWA threshold, so behavior is
    unchanged for anyone who has not calibrated.
    """
    offset = max(0.0, min(MAX_INACCURATE_OFFSET, float(inaccurate_offset or 0.0)))
    if not baseline:
        return round(DEFAULT_FWD_HEAD_THRESHOLD + offset, 1)
    median = float(baseline["fwd_head_median"])
    spread = float(baseline.get("fwd_head_spread") or 0.0)
    margin = max(MIN_THRESHOLD_MARGIN, SPREAD_MULTIPLIER * spread)
    value = median + margin + offset
    return round(max(THRESHOLD_FLOOR, min(THRESHOLD_CEILING, value)), 1)


def posture_state(fwd_head_deg: float | None, threshold: float, baseline: dict[str, Any] | None) -> str:
    """Name where a reading sits: unknown | neutral | drifting | forward."""
    if fwd_head_deg is None:
        return "unknown"
    if fwd_head_deg >= threshold:
        return "forward"
    neutral = float(baseline["fwd_head_median"]) if baseline else threshold - MIN_THRESHOLD_MARGIN
    if fwd_head_deg >= (neutral + threshold) / 2:
        return "drifting"
    return "neutral"


# ── Persistence ────────────────────────────────────────────────────

DEFAULT_PROFILE: dict[str, Any] = {
    "consent": {"granted": False, "granted_at": None, "version": CONSENT_VERSION},
    "height_cm": None,
    "workstation": "laptop",
    "baseline": None,
    "inaccurate_reports": 0,
}


class BodyProfileStore:
    """Atomic JSON store for consent, profile, and baseline metrics."""

    def __init__(self, path: str | Path = BODY_PROFILE_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def load(self) -> dict[str, Any]:
        with self._lock:
            stored: dict[str, Any] = {}
            if self.path.exists():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(value, dict):
                        stored = value
                except (OSError, json.JSONDecodeError):
                    stored = {}
            return self._validate(stored)

    def _write(self, profile: dict[str, Any]) -> dict[str, Any]:
        clean = self._validate(profile)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
        temp.replace(self.path)
        return clean

    def set_consent(self, granted: bool) -> dict[str, Any]:
        with self._lock:
            profile = self.load()
            profile["consent"] = {
                "granted": bool(granted),
                "granted_at": datetime.now(timezone.utc).isoformat() if granted else None,
                "version": CONSENT_VERSION,
            }
            if not granted:
                # Revoking consent removes the personal baseline too.
                profile["baseline"] = None
                profile["inaccurate_reports"] = 0
            return self._write(profile)

    def update_profile(self, *, height_cm: Any = None, workstation: Any = None) -> dict[str, Any]:
        with self._lock:
            profile = self.load()
            if height_cm is not None:
                height = float(height_cm)
                if not math.isfinite(height) or not 120.0 <= height <= 230.0:
                    raise ValueError("height must be between 120 and 230 cm")
                profile["height_cm"] = round(height, 1)
            if workstation is not None:
                if workstation not in WORKSTATIONS:
                    raise ValueError(f"workstation must be one of: {', '.join(WORKSTATIONS)}")
                profile["workstation"] = workstation
            return self._write(profile)

    def save_baseline(self, baseline: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            profile = self.load()
            if not profile["consent"]["granted"]:
                raise PermissionError("posture consent has not been granted")
            profile["baseline"] = baseline
            profile["inaccurate_reports"] = 0
            return self._write(profile)

    def clear_baseline(self) -> dict[str, Any]:
        with self._lock:
            profile = self.load()
            profile["baseline"] = None
            profile["inaccurate_reports"] = 0
            return self._write(profile)

    def report_inaccurate(self) -> dict[str, Any]:
        with self._lock:
            profile = self.load()
            cap = int(MAX_INACCURATE_OFFSET / INACCURATE_STEP)
            profile["inaccurate_reports"] = min(cap, int(profile["inaccurate_reports"]) + 1)
            return self._write(profile)

    def threshold(self) -> float:
        profile = self.load()
        baseline = profile["baseline"] if profile["consent"]["granted"] else None
        return personal_threshold(baseline, profile["inaccurate_reports"] * INACCURATE_STEP)

    @staticmethod
    def _validate(value: dict[str, Any]) -> dict[str, Any]:
        clean = json.loads(json.dumps(DEFAULT_PROFILE))
        consent = value.get("consent")
        if isinstance(consent, dict):
            clean["consent"]["granted"] = consent.get("granted") is True
            granted_at = consent.get("granted_at")
            clean["consent"]["granted_at"] = granted_at if isinstance(granted_at, str) else None
        height = value.get("height_cm")
        if isinstance(height, (int, float)) and not isinstance(height, bool) and 120 <= height <= 230:
            clean["height_cm"] = float(height)
        if value.get("workstation") in WORKSTATIONS:
            clean["workstation"] = value["workstation"]
        baseline = value.get("baseline")
        if isinstance(baseline, dict) and isinstance(baseline.get("fwd_head_median"), (int, float)):
            clean["baseline"] = {
                key: baseline.get(key)
                for key in (
                    "fwd_head_median", "fwd_head_spread", "shoulder_tilt_median",
                    "samples", "confidence", "calibrated_at",
                )
            }
        reports = value.get("inaccurate_reports")
        if isinstance(reports, int) and not isinstance(reports, bool):
            clean["inaccurate_reports"] = max(0, min(int(MAX_INACCURATE_OFFSET / INACCURATE_STEP), reports))
        return clean


# ── Calibration + summaries ────────────────────────────────────────

def run_calibration(
    store: BodyProfileStore,
    *,
    samples: int = DEFAULT_CALIBRATION_SAMPLES,
    check_fn: Callable[..., dict[str, Any]] | None = None,
    interval: float = 0.4,
) -> dict[str, Any]:
    """Take local posture samples and save a personal baseline.

    The camera opens only inside ``check_fn`` (``vision.run_check``), frames
    are discarded there, and only the resulting metrics are kept.
    """
    if not store.load()["consent"]["granted"]:
        raise PermissionError("posture consent has not been granted")
    count = max(MIN_CALIBRATION_SAMPLES, min(MAX_CALIBRATION_SAMPLES, int(samples)))
    if check_fn is None:
        from .vision import run_check as check_fn
    readings: list[dict[str, Any] | None] = []
    for _ in range(count):
        result = check_fn(interval=interval)
        if not result.get("available"):
            raise RuntimeError(str(result.get("error") or "vision unavailable"))
        readings.append(result.get("posture"))
    baseline = compute_baseline(readings)
    store.save_baseline(baseline)
    return baseline


def recent_vision_events(path: Path = VISION_EVENTS_PATH, limit: int = 60) -> list[dict[str, Any]]:
    """Read the newest metrics-only vision records (newest last)."""
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(1, limit):]
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def body_summary(
    store: BodyProfileStore,
    *,
    events_path: Path = VISION_EVENTS_PATH,
    readiness: tuple[bool, str] | dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Everything the Body app shows, assembled from local metrics only."""
    profile = store.load()
    consented = profile["consent"]["granted"]
    baseline = profile["baseline"] if consented else None
    threshold = store.threshold()
    events = recent_vision_events(events_path)
    series = []
    for event in events:
        posture = event.get("posture") if isinstance(event.get("posture"), dict) else {}
        fwd = posture.get("fwd_head_deg") if posture.get("visible") else None
        series.append({
            "captured_at": event.get("captured_at"),
            "activity": event.get("activity"),
            "fwd_head_deg": fwd,
            "state": posture_state(fwd, threshold, baseline),
        })
    latest = next((point for point in reversed(series) if point["fwd_head_deg"] is not None), None)
    if readiness is None:
        from .vision import vision_readiness
        readiness = vision_readiness()
    if isinstance(readiness, tuple):  # (ready, reason) from callers and tests
        readiness = {
            "ready": bool(readiness[0]), "reason": readiness[1] or "",
            "step": None if readiness[0] else "install",
        }
    targets = None
    if profile["height_cm"]:
        targets = workstation_targets(profile["height_cm"], profile["workstation"])
    alerts = sum(1 for e in events if e.get("posture_alert") is True)
    return {
        "consent": profile["consent"],
        "camera": {
            "ready": bool(readiness.get("ready")),
            "reason": readiness.get("reason") or None,
            "step": readiness.get("step"),
        },
        "baseline": baseline,
        "calibrated": baseline is not None,
        "threshold_deg": threshold,
        "threshold_source": "personal" if baseline else "generic",
        "inaccurate_reports": profile["inaccurate_reports"],
        "latest": (
            {**latest, "cervical_load_lbs": cervical_load_lbs(latest["fwd_head_deg"])}
            if latest else None
        ),
        "neutral_load_lbs": cervical_load_lbs(baseline["fwd_head_median"]) if baseline else None,
        "series": series,
        "recent_alerts": alerts,
        "profile": {"height_cm": profile["height_cm"], "workstation": profile["workstation"]},
        "workstation": targets,
    }
