"""Vision lane — camera sensing for the Deep Work assistant.

Phase 1 (2026-08-08): posture metrics, motion score, activity
classification, and a sustained-posture alert decision, plus model
provisioning. Camera/MediaPipe are OPTIONAL at import time: the pure
logic (landmarks, scores, decisions) works with zero dependencies, and
the camera paths degrade gracefully with a clear message when cv2 or
mediapipe is missing. This keeps the core package light and lets CI
test the logic without a camera.

Privacy contract (see GOVERNANCE.md §5/§7):
- the camera opens only during a probe (a few hundred ms),
- frames are processed in memory and discarded,
- the only persisted artifact is a motion/posture snapshot the user
  opted into,
- nothing is uploaded anywhere.
"""
from __future__ import annotations

import math
import urllib.request
from dataclasses import dataclass
from pathlib import Path

# Canonical model URL (verified 2026-08-08 — the filename carries the
# variant suffix; the old "pose_landmarker.task" key no longer exists).
POSE_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    "pose_landmarker_full/float16/1/pose_landmarker_full.task"
)
POSE_MODEL_FILENAME = "pose_landmarker_full.task"

# MediaPipe Pose landmark indices we read.
NOSE, L_EAR, R_EAR, L_SHOULDER, R_SHOULDER = 0, 7, 8, 11, 12

# Activity contract thresholds (calibrated on the 2026-08-08 spike:
# a person at a desk scores ~5-30 on the 160x120 diff probe).
MOTION_TYPING_MIN = 8.0

# Posture alert: sustained forward-head (degrees), majority of window.
DEFAULT_FWD_HEAD_THRESHOLD = 25.0
DEFAULT_MIN_ABOVE = 3
DEFAULT_WINDOW = 5

# Motion probe resolution (small gray frames are cheap and stable).
PROBE_W, PROBE_H = 160, 120


@dataclass
class Landmark:
    """Plain landmark shape (mirrors mediapipe NormalizedLandmark).

    Camera code can pass mediapipe landmarks directly — they expose the
    same x/y/z/visibility attributes.
    """
    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0


# ── Pure logic (no external deps) ───────────────────────────────────

def posture_metrics(lms) -> dict:
    """Compute posture metrics from landmarks (any object with x/y/z/visibility).

    Returns dict with keys:
      fwd_head_deg      — angle between vertical and shoulder-mid→ear-mid
                          (0 = upright, larger = head forward/down);
                          None when landmark visibility is too low.
      shoulder_tilt_deg — signed shoulder-line angle from horizontal,
                          abs() for magnitude; None when visibility low.
      nose_y_frac       — nose vertical position (0=top, 1=bottom).
      visible           — whether the posture metrics are trustworthy.
    """
    def pt(i):
        lm = lms[i]
        return lm.x, lm.y, lm.visibility

    nose = pt(NOSE)
    le = pt(L_EAR)
    re = pt(R_EAR)
    ls = pt(L_SHOULDER)
    rs = pt(R_SHOULDER)

    vis = min(nose[2], le[2], re[2], ls[2], rs[2])
    if vis < 0.5:
        return {
            "fwd_head_deg": None, "shoulder_tilt_deg": None,
            "nose_y_frac": round(nose[1], 3), "visible": False,
        }

    sh_mid = ((ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2)
    ear_mid = ((le[0] + re[0]) / 2, (le[1] + re[1]) / 2)
    dx = ear_mid[0] - sh_mid[0]
    dy = -(ear_mid[1] - sh_mid[1])  # image y grows down
    fwd = math.degrees(math.atan2(abs(dx), dy)) if dy > 0 else 90.0
    tilt = math.degrees(math.atan2(rs[1] - ls[1], rs[0] - ls[0]))

    return {
        "fwd_head_deg": round(fwd, 1),
        "shoulder_tilt_deg": round(tilt, 1),
        "nose_y_frac": round(nose[1], 3),
        "visible": True,
    }


def motion_score(a, b) -> float:
    """Mean absolute difference between two frames (0-255 scale).

    Accepts numpy arrays (fast path) or plain nested lists (pure path),
    so tests and the camera both work. Frames are expected to be
    grayscale; callers resize before passing.
    """
    if hasattr(a, "mean") and hasattr(b, "mean"):
        diff = a.astype("int16") - b.astype("int16")
        return float(abs(diff).mean())
    # Pure-python fallback over small probe frames.
    total = 0
    count = 0
    for row_a, row_b in zip(a, b):
        for va, vb in zip(row_a, row_b):
            total += abs(int(va) - int(vb))
            count += 1
    return float(total / count) if count else 0.0


def classify_activity(*, present: bool, human_active: bool, motion: float) -> str:
    """Classify the user's state into a strict contract.

    Contract: absent | agent | idle | typing | reading.
      absent  — camera sees no person.
      agent   — person present but input is agent-driven (caller flags
                window activity; we conservatively treat present-but-
                inactive as idle unless caller distinguishes).
      idle    — present, no recent human input.
      typing  — present, active, moving (keyboard/mouse work).
      reading — present, active, still (focused reading/watching).
    """
    if not present:
        return "absent"
    if not human_active:
        return "idle"
    return "typing" if motion >= MOTION_TYPING_MIN else "reading"


def posture_alert(window: list[float], threshold: float = DEFAULT_FWD_HEAD_THRESHOLD,
                  min_above: int = DEFAULT_MIN_ABOVE) -> bool:
    """True only when posture is SUSTAINED: >= min_above of the last
    samples exceed the threshold. Single-frame spikes never alert."""
    if len(window) < min_above:
        return False
    above = sum(1 for v in window if v is not None and v >= threshold)
    return above >= min_above


# ── Model provisioning (stdlib only) ────────────────────────────────

def ensure_model(model_path: Path, url: str = POSE_MODEL_URL) -> Path:
    """Download the pose model to model_path if missing. Returns path."""
    model_path.parent.mkdir(parents=True, exist_ok=True)
    if model_path.exists() and model_path.stat().st_size > 1000:
        return model_path
    req = urllib.request.Request(url, headers={"User-Agent": "DeepWorkAssistant/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    model_path.write_bytes(data)
    return model_path


def model_cache_dir() -> Path:
    """User-scoped model cache (never committed to the repo)."""
    return Path.home() / ".deep_work_assistant" / "models"


# ── Camera paths (guarded imports) ──────────────────────────────────

def _load_cv2():
    try:
        import cv2
        return cv2
    except ImportError:
        return None


def camera_available() -> tuple[bool, str]:
    """True + '' when the camera path is usable; else (False, reason)."""
    if _load_cv2() is None:
        return False, "opencv-python is not installed (pip install deep-work-assistant[vision])"
    try:
        import mediapipe  # noqa: F401
    except ImportError:
        return False, "mediapipe is not installed (pip install deep-work-assistant[vision])"
    return True, ""


def grab_frame(cam_index: int = 0):
    """Open the camera for one frame and release immediately."""
    cv2 = _load_cv2()
    if cv2 is None:
        return None
    cap = cv2.VideoCapture(cam_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        return None
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def probe_motion(cam_index: int = 0, interval: float = 2.0) -> tuple[bool, float]:
    """Grab two frames `interval` apart, return (ok, motion_score).

    Frames are downscaled to grayscale PROBE_W x PROBE_H first. Camera
    is closed between frames — the privacy contract.
    """
    cv2 = _load_cv2()
    if cv2 is None:
        return False, 0.0
    a = grab_frame(cam_index)
    if a is None:
        return False, 0.0
    import time
    time.sleep(interval)
    b = grab_frame(cam_index)
    if b is None:
        return False, 0.0

    def gray_downscale(frame):
        g = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (PROBE_W, PROBE_H))

    return True, motion_score(gray_downscale(a), gray_downscale(b))


def run_check(cam_index: int = 0, interval: float = 2.0) -> dict:
    """One-shot sensing check: motion score + posture metrics.

    Downloads the model on first use (user-scoped cache), opens the
    camera only for the probe frames, processes in memory. Returns a
    dict: available, motion, posture (or None), error.
    """
    ok, reason = camera_available()
    if not ok:
        return {"available": False, "motion": None, "posture": None, "error": reason}

    import cv2
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision

    model = model_cache_dir() / POSE_MODEL_FILENAME
    ensure_model(model, url=POSE_MODEL_URL)

    options = mp_vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(model)),
        running_mode=mp_vision.RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    landmarker = mp_vision.PoseLandmarker.create_from_options(options)

    motion_ok, motion = probe_motion(cam_index, interval)
    frame = grab_frame(cam_index)
    if frame is None:
        return {"available": True, "motion": round(motion, 1), "posture": None,
                "error": "camera open but no frame delivered"}
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
    result = landmarker.detect(mp_image)

    posture = None
    if result.pose_landmarks:
        posture = posture_metrics(result.pose_landmarks[0])
    return {
        "available": True,
        "motion": round(motion, 1),
        "posture": posture,
        "error": None,
    }
