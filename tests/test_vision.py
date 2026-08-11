"""Vision lane tests — pure logic, no camera/mediapipe required.

Spec (Given/When/Then):
  GIVEN a set of pose landmarks, WHEN posture_metrics runs,
    THEN forward-head angle and shoulder tilt are computed correctly
    (upright ~0deg, hunched > 25deg) and low-visibility landmarks
    produce a degraded (None) metric instead of garbage numbers.
  GIVEN two frames, WHEN motion_score runs, THEN identical frames
    score 0 and differing frames score > 0.
  GIVEN window/input/presence/motion signals, WHEN classify_activity
    runs, THEN the user's state is classified into a strict contract
    (absent | agent | idle | typing | reading).
  GIVEN a window of forward-head samples, WHEN posture_alert runs,
    THEN an alert fires only when posture is SUSTAINED (>=3 of last 5
    over threshold), never on a single-frame spike.
  GIVEN a missing model file, WHEN ensure_model runs, THEN it
    downloads to the cache; WHEN the file exists, THEN it does not.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import types

import pytest

from deep_work_assistant.vision import (
    L_EAR,
    L_SHOULDER,
    NOSE,
    R_EAR,
    R_SHOULDER,
    Landmark,
    classify_activity,
    motion_score,
    posture_alert,
    posture_metrics,
)


@dataclass
class L:
    """Test landmark stub: same attribute shape as mediapipe landmarks."""
    x: float
    y: float
    z: float = 0.0
    visibility: float = 1.0


def _pose33() -> list[L]:
    """Full 33-landmark pose (mediapipe layout), neutral defaults."""
    return [L(0.5, 0.5) for _ in range(33)]


def _upright() -> list[L]:
    """Ears directly above shoulders, level shoulders, full visibility."""
    lms = _pose33()
    lms[NOSE] = L(0.5, 0.30)         # nose
    lms[L_EAR] = L(0.42, 0.28)       # left ear
    lms[R_EAR] = L(0.58, 0.28)       # right ear
    lms[L_SHOULDER] = L(0.42, 0.55)  # left shoulder
    lms[R_SHOULDER] = L(0.58, 0.55)  # right shoulder
    return lms


def _hunched() -> list[L]:
    """Head displaced forward: both ears shift 0.15 in image x together
    (ear-mid 0.35 vs shoulder-mid 0.50 -> ~29 deg forward-head)."""
    lms = _upright()
    lms[L_EAR].x = 0.27
    lms[R_EAR].x = 0.43
    return lms


# ── 1. posture_metrics ─────────────────────────────────────────────

def test_upright_posture_is_near_zero_forward_head() -> None:
    m = posture_metrics(_upright())
    assert m["fwd_head_deg"] is not None
    assert m["fwd_head_deg"] < 10, f"expected upright, got {m['fwd_head_deg']}"


def test_hunched_posture_exceeds_25_degrees() -> None:
    m = posture_metrics(_hunched())
    assert m["fwd_head_deg"] is not None
    assert m["fwd_head_deg"] > 25, f"expected hunched, got {m['fwd_head_deg']}"


def test_level_shoulders_report_near_zero_tilt() -> None:
    m = posture_metrics(_upright())
    assert abs(m["shoulder_tilt_deg"]) < 5


def test_mirrored_level_shoulders_normalize_near_zero_tilt() -> None:
    lms = _upright()
    lms[L_SHOULDER].x, lms[R_SHOULDER].x = lms[R_SHOULDER].x, lms[L_SHOULDER].x
    m = posture_metrics(lms)
    assert abs(m["shoulder_tilt_deg"]) < 5


def test_uneven_shoulders_report_tilt() -> None:
    lms = _upright()
    lms[L_SHOULDER].y = 0.65  # left shoulder dropped
    m = posture_metrics(lms)
    assert abs(m["shoulder_tilt_deg"]) > 10


def test_low_visibility_shoulders_degrade_metric() -> None:
    lms = _upright()
    lms[L_SHOULDER].visibility = 0.1
    lms[R_SHOULDER].visibility = 0.1
    m = posture_metrics(lms)
    assert m["fwd_head_deg"] is None, "low-visibility landmarks must not yield fake angles"


def test_landmark_stub_accepts_mediapipe_shape() -> None:
    """The dataclass stub and mediapipe landmarks both expose x/y/z/visibility."""
    for attr in ("x", "y", "z", "visibility"):
        assert hasattr(L(0, 0), attr), f"stub missing {attr}"


# ── 2. motion_score ────────────────────────────────────────────────

def test_identical_frames_score_zero() -> None:
    frame = [[10] * 8] * 6
    assert motion_score(frame, frame) == 0.0


def test_different_frames_score_above_zero() -> None:
    a = [[10] * 8] * 6
    b = [[10] * 8] * 6
    b[3][4] = 200
    assert motion_score(a, b) > 0.0


def test_motion_score_scales_with_difference() -> None:
    a = [[0] * 8] * 6
    small = [[0] * 8] * 6
    large = [[0] * 8] * 6
    small[3][4] = 50
    large[3][4] = 250
    assert motion_score(a, large) > motion_score(a, small)


# ── 3. classify_activity ───────────────────────────────────────────

def test_absent_when_no_presence() -> None:
    assert classify_activity(present=False, human_active=True, motion=5.0) == "absent"


def test_typing_when_active_and_moving() -> None:
    assert classify_activity(present=True, human_active=True, motion=12.0) == "typing"


def test_reading_when_active_and_still() -> None:
    assert classify_activity(present=True, human_active=True, motion=2.0) == "reading"


def test_idle_when_present_but_no_human_input() -> None:
    assert classify_activity(present=True, human_active=False, motion=0.0) == "idle"


def test_unknown_signal_is_idle_not_crash() -> None:
    assert classify_activity(present=True, human_active=False, motion=99.0) in ("idle", "agent")


# ── 4. posture_alert (sustained, not spikes) ───────────────────────

def test_single_spike_does_not_alert() -> None:
    window = [5, 6, 40, 5, 5]  # one hunched sample
    assert posture_alert(window, threshold=25, min_above=3) is False


def test_sustained_hunch_alerts() -> None:
    window = [30, 34, 5, 28, 31]  # 4 of 5 over threshold
    assert posture_alert(window, threshold=25, min_above=3) is True


def test_empty_window_never_alerts() -> None:
    assert posture_alert([], threshold=25, min_above=3) is False


def test_short_window_never_alerts() -> None:
    assert posture_alert([30, 30], threshold=25, min_above=3) is False


# ── 5. ensure_model (download only when missing) ───────────────────

def test_ensure_model_downloads_when_missing(tmp_path, monkeypatch) -> None:
    calls = {}

    def fake_urlopen(req, timeout=30):
        calls["url"] = req.full_url
        class R:
            def read(self):
                return b"FAKEMODEL"
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    from deep_work_assistant.vision import ensure_model

    model_path = tmp_path / "models" / "pose.task"
    url = "https://example.test/pose_full.task"
    ensure_model(model_path, url=url)
    assert model_path.exists()
    assert model_path.read_bytes() == b"FAKEMODEL"
    assert calls["url"] == url


def test_ensure_model_skips_when_present(tmp_path, monkeypatch) -> None:
    from deep_work_assistant.vision import ensure_model

    model_path = tmp_path / "models" / "pose.task"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"E" * 2000)  # > 1KB size guard
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")))
    ensure_model(model_path, url="https://example.test/pose.task")
    assert model_path.read_bytes() == b"E" * 2000


def test_repeated_runtime_check_closes_pose_landmarker(tmp_path, monkeypatch) -> None:
    """Phase 2 repeats checks for hours; every native landmarker must close."""
    from deep_work_assistant import vision as module

    closed = {"value": False}

    class FakeLandmarker:
        def detect(self, image):
            return types.SimpleNamespace(pose_landmarks=[])

        def close(self):
            closed["value"] = True

    class FakePoseLandmarker:
        @staticmethod
        def create_from_options(options):
            return FakeLandmarker()

    fake_cv2 = types.ModuleType("cv2")
    fake_cv2.COLOR_BGR2RGB = 1
    fake_cv2.cvtColor = lambda frame, code: frame

    fake_mp = types.ModuleType("mediapipe")
    fake_mp.ImageFormat = types.SimpleNamespace(SRGB=1)
    fake_mp.Image = lambda **kwargs: object()

    fake_mp_python = types.ModuleType("mediapipe.tasks.python")
    fake_mp_python.BaseOptions = lambda **kwargs: object()
    fake_mp_vision = types.ModuleType("mediapipe.tasks.python.vision")
    fake_mp_vision.RunningMode = types.SimpleNamespace(IMAGE=1)
    fake_mp_vision.PoseLandmarkerOptions = lambda **kwargs: object()
    fake_mp_vision.PoseLandmarker = FakePoseLandmarker
    fake_mp_python.vision = fake_mp_vision
    fake_tasks = types.ModuleType("mediapipe.tasks")
    fake_tasks.python = fake_mp_python
    fake_mp.tasks = fake_tasks

    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)
    monkeypatch.setitem(sys.modules, "mediapipe", fake_mp)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks", fake_tasks)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks.python", fake_mp_python)
    monkeypatch.setitem(sys.modules, "mediapipe.tasks.python.vision", fake_mp_vision)
    monkeypatch.setattr(module, "camera_available", lambda: (True, ""))
    monkeypatch.setattr(module, "model_cache_dir", lambda: tmp_path)
    (tmp_path / module.POSE_MODEL_FILENAME).write_bytes(b"M" * 2000)
    monkeypatch.setattr(module, "_file_sha256", lambda path: module.POSE_MODEL_SHA256)
    monkeypatch.setattr(module, "probe_motion", lambda cam_index, interval, **kwargs: (True, 2.0))
    monkeypatch.setattr(module, "grab_frame", lambda cam_index: object())

    result = module.run_check(interval=0.0)

    assert result["available"] is True
    assert closed["value"] is True


def test_runtime_check_never_downloads_a_missing_model(tmp_path, monkeypatch) -> None:
    """Live sampling is offline-only; provisioning requires a separate command."""
    from deep_work_assistant import vision as module

    monkeypatch.setattr(module, "camera_available", lambda: (True, ""))
    monkeypatch.setattr(module, "model_cache_dir", lambda: tmp_path)
    monkeypatch.setattr(
        module,
        "ensure_model",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network path must not run")),
    )

    result = module.run_check(interval=0.0)

    assert result["available"] is False
    assert "model" in result["error"].lower()
    assert "provision" in result["error"].lower()


def test_runtime_rejects_a_pose_model_with_wrong_checksum(tmp_path, monkeypatch) -> None:
    from deep_work_assistant import vision as module

    model = tmp_path / module.POSE_MODEL_FILENAME
    model.write_bytes(b"tampered" * 400)
    monkeypatch.setattr(module, "camera_available", lambda: (True, ""))
    monkeypatch.setattr(module, "model_cache_dir", lambda: tmp_path)

    result = module.run_check(interval=0.0)

    assert result["available"] is False
    assert "checksum" in result["error"].lower()


def test_model_provisioning_rejects_wrong_checksum(tmp_path, monkeypatch) -> None:
    from deep_work_assistant.vision import ensure_model

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b"not-the-pinned-model" * 100

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())
    model = tmp_path / "pose.task"

    with pytest.raises(ValueError, match="checksum"):
        ensure_model(
            model,
            url="https://example.test/pose.task",
            expected_sha256="0" * 64,
        )

    assert not model.exists()


def test_grab_frame_releases_camera_when_open_fails(monkeypatch) -> None:
    from deep_work_assistant import vision as module

    class Capture:
        released = False

        def isOpened(self):
            return False

        def release(self):
            self.released = True

    capture = Capture()
    fake_cv2 = types.SimpleNamespace(
        CAP_DSHOW=1,
        VideoCapture=lambda *args: capture,
    )
    monkeypatch.setattr(module, "_load_cv2", lambda: fake_cv2)

    assert module.grab_frame() is None
    assert capture.released is True


def test_grab_frame_releases_camera_when_read_raises(monkeypatch) -> None:
    from deep_work_assistant import vision as module

    class Capture:
        released = False

        def isOpened(self):
            return True

        def read(self):
            raise RuntimeError("camera read failed")

        def release(self):
            self.released = True

    capture = Capture()
    fake_cv2 = types.SimpleNamespace(
        CAP_DSHOW=1,
        VideoCapture=lambda *args: capture,
    )
    monkeypatch.setattr(module, "_load_cv2", lambda: fake_cv2)

    with pytest.raises(RuntimeError, match="camera read failed"):
        module.grab_frame()
    assert capture.released is True
