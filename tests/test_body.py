"""M2 Embodied Truth: consent, calibration, confidence, inaccurate, estimates."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from deep_work_assistant import body
from deep_work_assistant.body import (
    BodyProfileStore,
    body_summary,
    cervical_load_lbs,
    compute_baseline,
    personal_threshold,
    posture_state,
    run_calibration,
    workstation_targets,
)
from deep_work_assistant.vision import DEFAULT_FWD_HEAD_THRESHOLD
from deep_work_assistant.vision_runtime import VisionSampler


def _posture(fwd: float, visible: bool = True) -> dict:
    return {"fwd_head_deg": fwd, "shoulder_tilt_deg": 1.0, "nose_y_frac": 0.3, "visible": visible}


# ── Pure logic ─────────────────────────────────────────────────────

def test_cervical_load_follows_hansraj_points_and_interpolates() -> None:
    assert cervical_load_lbs(0) == 12.0
    assert cervical_load_lbs(15) == 27.0
    assert cervical_load_lbs(30) == 40.0
    assert cervical_load_lbs(60) == 60.0
    assert cervical_load_lbs(90) == 60.0
    assert cervical_load_lbs(7.5) == 19.5
    assert cervical_load_lbs(None) is None


def test_workstation_targets_scale_with_height_and_reject_nonsense() -> None:
    result = workstation_targets(180, "standing_desk")
    targets = result["targets_cm"]
    assert targets["seat_height_cm"] == 45.9
    assert targets["desk_height_cm"] == 71.1
    assert targets["monitor_top_cm"] == targets["eye_height_cm"] - 2.0
    assert "standing_desk_height_cm" in targets
    assert "approximate" in result["basis"]
    assert workstation_targets(170, "unknown")["workstation"] == "laptop"
    with pytest.raises(ValueError):
        workstation_targets(40)


def test_baseline_is_robust_to_one_bad_frame() -> None:
    samples = [_posture(v) for v in (14, 15, 15, 16, 14, 15, 16, 70)]
    baseline = compute_baseline(samples)
    assert baseline["fwd_head_median"] == 15.0
    assert baseline["fwd_head_spread"] <= 1.0
    assert baseline["samples"] == 8
    assert baseline["confidence"] == "high"


def test_baseline_refuses_without_enough_visible_evidence() -> None:
    samples = [_posture(15)] * 3 + [_posture(15, visible=False)] * 5 + [None, {"fwd_head_deg": True, "visible": True}]
    with pytest.raises(ValueError, match="at least 5 visible"):
        compute_baseline(samples)


def test_noisy_baseline_reports_low_confidence() -> None:
    baseline = compute_baseline([_posture(v) for v in (5, 30, 10, 25, 15, 35)])
    assert baseline["confidence"] == "low"


def test_personal_threshold_is_generic_without_baseline() -> None:
    assert personal_threshold(None) == DEFAULT_FWD_HEAD_THRESHOLD


def test_personal_threshold_uses_margin_spread_offset_and_bounds() -> None:
    tight = {"fwd_head_median": 8.0, "fwd_head_spread": 1.0}
    assert personal_threshold(tight) == 18.0
    noisy = {"fwd_head_median": 8.0, "fwd_head_spread": 5.0}
    assert personal_threshold(noisy) == 23.0
    assert personal_threshold(tight, inaccurate_offset=6.0) == 24.0
    assert personal_threshold({"fwd_head_median": 0.0, "fwd_head_spread": 0.0}) == 12.0
    assert personal_threshold({"fwd_head_median": 58.0, "fwd_head_spread": 0.0}) == 60.0


def test_posture_state_names_neutral_drifting_forward() -> None:
    baseline = {"fwd_head_median": 10.0}
    assert posture_state(None, 20.0, baseline) == "unknown"
    assert posture_state(11.0, 20.0, baseline) == "neutral"
    assert posture_state(16.0, 20.0, baseline) == "drifting"
    assert posture_state(21.0, 20.0, baseline) == "forward"


# ── Store: consent is the gate ─────────────────────────────────────

def test_store_defaults_to_no_consent(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    profile = store.load()
    assert profile["consent"]["granted"] is False
    assert profile["baseline"] is None
    assert store.threshold() == DEFAULT_FWD_HEAD_THRESHOLD


def test_baseline_cannot_be_saved_without_consent(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    with pytest.raises(PermissionError):
        store.save_baseline(compute_baseline([_posture(10)] * 6))


def test_revoking_consent_deletes_baseline(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    store.set_consent(True)
    store.save_baseline(compute_baseline([_posture(10)] * 8))
    store.report_inaccurate()
    assert store.threshold() == 23.0
    store.set_consent(False)
    profile = store.load()
    assert profile["baseline"] is None
    assert profile["inaccurate_reports"] == 0
    assert store.threshold() == DEFAULT_FWD_HEAD_THRESHOLD


def test_inaccurate_reports_are_capped(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    for _ in range(20):
        store.report_inaccurate()
    assert store.load()["inaccurate_reports"] == 5


def test_store_never_persists_frames_or_unknown_keys(tmp_path: Path) -> None:
    path = tmp_path / "body.json"
    path.write_text(json.dumps({
        "consent": {"granted": True},
        "frame": "base64...",
        "baseline": {"fwd_head_median": 10.0, "image": "x"},
    }), encoding="utf-8")
    store = BodyProfileStore(path)
    store.update_profile(height_cm=175, workstation="dual_monitor")
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert "frame" not in saved
    assert "image" not in saved["baseline"]
    assert saved["height_cm"] == 175.0


def test_update_profile_validates(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    with pytest.raises(ValueError):
        store.update_profile(height_cm=500)
    with pytest.raises(ValueError):
        store.update_profile(workstation="couch")


# ── Calibration ────────────────────────────────────────────────────

def test_calibration_requires_consent(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    with pytest.raises(PermissionError):
        run_calibration(store, check_fn=lambda **_: {"available": True, "posture": _posture(10)})


def test_calibration_saves_metrics_from_local_checks(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    store.set_consent(True)
    readings = iter([11, 12, 12, 13, 12, 11, 12, 13])
    calls = []

    def fake_check(**kwargs):
        calls.append(kwargs)
        return {"available": True, "motion": 3.0, "posture": _posture(next(readings))}

    baseline = run_calibration(store, samples=8, check_fn=fake_check)
    assert len(calls) == 8
    assert baseline["fwd_head_median"] == 12.0
    assert store.load()["baseline"]["fwd_head_median"] == 12.0
    assert store.threshold() == 22.0


def test_calibration_surfaces_unavailable_camera(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    store.set_consent(True)
    with pytest.raises(RuntimeError, match="opencv"):
        run_calibration(store, check_fn=lambda **_: {"available": False, "error": "opencv missing"})
    assert store.load()["baseline"] is None


# ── Summary + sampler wiring ───────────────────────────────────────

def test_summary_reads_metrics_only_events(tmp_path: Path) -> None:
    store = BodyProfileStore(tmp_path / "body.json")
    store.set_consent(True)
    store.save_baseline(compute_baseline([_posture(10)] * 8))
    store.update_profile(height_cm=170)
    events = tmp_path / "vision_events.jsonl"
    events.write_text("\n".join(json.dumps(e) for e in [
        {"captured_at": "2026-10-12T10:00:00+00:00", "activity": "typing", "posture": _posture(12), "posture_alert": False},
        {"captured_at": "2026-10-12T10:01:00+00:00", "activity": "typing", "posture": _posture(24), "posture_alert": True},
        {"captured_at": "2026-10-12T10:02:00+00:00", "activity": "unavailable", "posture": None},
    ]) + "\nnot json\n", encoding="utf-8")
    summary = body_summary(store, events_path=events, readiness=(False, "no camera"))
    assert summary["calibrated"] is True
    assert summary["threshold_source"] == "personal"
    assert summary["threshold_deg"] == 20.0
    assert summary["latest"]["fwd_head_deg"] == 24
    assert summary["latest"]["state"] == "forward"
    assert summary["latest"]["cervical_load_lbs"] == cervical_load_lbs(24)
    assert summary["recent_alerts"] == 1
    assert len(summary["series"]) == 3
    assert summary["camera"] == {"ready": False, "reason": "no camera", "step": "install"}
    assert summary["workstation"]["height_cm"] == 170.0


def test_sampler_alerts_at_personal_threshold() -> None:
    sampler = VisionSampler(
        check_fn=lambda **_: {"available": True, "motion": 2.0, "posture": _posture(20)},
        sample_interval_seconds=30,
        posture_threshold=18.0,
    )
    alerts = []
    for minute in range(3):
        observation = sampler.maybe_sample(
            datetime(2026, 10, 12, 10, minute, tzinfo=timezone.utc),
            activity_state="human-active",
            session_active=True,
        )
        alerts.append(observation.posture_alert)
    assert alerts == [False, False, True]


def test_sampler_default_threshold_stays_generic() -> None:
    sampler = VisionSampler(check_fn=lambda **_: {"available": True, "motion": 2.0, "posture": _posture(20)})
    assert sampler.posture_threshold == DEFAULT_FWD_HEAD_THRESHOLD
    with pytest.raises(ValueError):
        VisionSampler(posture_threshold=float("nan"))


# ── HTTP routes ────────────────────────────────────────────────────

@pytest.fixture()
def server(tmp_path: Path, monkeypatch):
    from deep_work_assistant.web_ui_v2 import EnhancedAppState, EnhancedServer, SettingsStore

    monkeypatch.setattr(body, "VISION_EVENTS_PATH", tmp_path / "vision_events.jsonl")
    state = EnhancedAppState(SettingsStore(tmp_path / "settings.json"), BodyProfileStore(tmp_path / "body.json"))
    httpd = EnhancedServer(("127.0.0.1", 0), state)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}", state
    finally:
        httpd.shutdown()
        httpd.server_close()


def _call(url: str, method: str = "GET", payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read().decode())


def test_body_routes_gate_on_consent(server) -> None:
    url, state = server
    status, summary = _call(f"{url}/api/body")
    assert status == 200
    assert summary["consent"]["granted"] is False
    with pytest.raises(HTTPError) as denied:
        _call(f"{url}/api/body/calibrate", "POST", {"samples": 5})
    assert denied.value.code == 403

    _, summary = _call(f"{url}/api/body/consent", "POST", {"granted": True})
    assert summary["consent"]["granted"] is True
    _, summary = _call(f"{url}/api/body/profile", "PATCH", {"height_cm": 182, "workstation": "external_monitor"})
    assert summary["workstation"]["workstation"] == "external_monitor"
    with pytest.raises(HTTPError) as bad:
        _call(f"{url}/api/body/profile", "PATCH", {"height_cm": 20})
    assert bad.value.code == 400

    state.body_store.save_baseline(compute_baseline([_posture(9)] * 8))
    _, summary = _call(f"{url}/api/body/inaccurate", "POST", {})
    assert summary["inaccurate_reports"] == 1
    _, summary = _call(f"{url}/api/body/baseline", "DELETE")
    assert summary["calibrated"] is False


def test_os_shell_and_assets_are_served(server) -> None:
    url, _ = server
    for path, kind in (("/os", "text/html"), ("/os.css", "text/css"), ("/os.js", "text/javascript")):
        with urlopen(f"{url}{path}", timeout=5) as response:
            assert response.status == 200
            assert response.headers["Content-Type"].startswith(kind)
    html = (Path(body.__file__).with_name("ui") / "os.html").read_text(encoding="utf-8")
    assert "https://" not in html  # the shell loads nothing from the network


# ── Fully-functional wiring ────────────────────────────────────────

def test_vision_readiness_names_the_next_step(monkeypatch, tmp_path: Path) -> None:
    from deep_work_assistant import vision

    monkeypatch.setattr(vision, "camera_available", lambda: (False, "opencv missing"))
    assert vision.vision_readiness()["step"] == "install"
    monkeypatch.setattr(vision, "camera_available", lambda: (True, ""))
    monkeypatch.setattr(vision, "model_cache_dir", lambda: tmp_path)
    assert vision.vision_readiness()["step"] == "provision"
    (tmp_path / vision.POSE_MODEL_FILENAME).write_bytes(b"x" * 2000)
    assert vision.vision_readiness()["step"] == "provision"  # wrong checksum
    monkeypatch.setattr(vision, "POSE_MODEL_SHA256", vision._file_sha256(tmp_path / vision.POSE_MODEL_FILENAME))
    vision._MODEL_CHECK_CACHE.clear()  # same file, new pin: a real download changes mtime instead
    assert vision.vision_readiness() == {"ready": True, "libraries": True, "model": True, "step": None, "reason": ""}


def test_ui_assistant_turns_posture_sampling_on_only_after_consent(monkeypatch, tmp_path: Path) -> None:
    import subprocess
    from deep_work_assistant import vision
    from deep_work_assistant import web_ui_v2

    launched = []

    class FakeProcess:
        pid = 4242
        def poll(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", lambda command, **_: launched.append(command) or FakeProcess())
    monkeypatch.setattr(vision, "vision_readiness", lambda: {"ready": True})
    monkeypatch.setattr(web_ui_v2, "ASSISTANT_LOG_PATH", tmp_path / "log.txt")
    store = BodyProfileStore(tmp_path / "body.json")
    state = web_ui_v2.EnhancedAppState(web_ui_v2.SettingsStore(tmp_path / "s.json"), store)
    state.log_path = tmp_path / "log.txt"

    state.start_assistant({})
    assert "--vision" not in launched[-1]
    state.assistant_process = None

    store.set_consent(True)
    status = state.start_assistant({})
    assert "--vision" in launched[-1]
    assert status["posture_sampling"] is True
    state._close_log()


def test_provision_route_explains_missing_libraries(server, monkeypatch) -> None:
    from deep_work_assistant import vision

    monkeypatch.setattr(vision, "camera_available", lambda: (False, "opencv-python is not installed"))
    url, _ = server
    with pytest.raises(HTTPError) as refused:
        _call(f"{url}/api/body/provision", "POST", {})
    assert refused.value.code == 409


def test_run_off_windows_exits_cleanly(capsys, monkeypatch, tmp_path: Path) -> None:
    from deep_work_assistant import cli

    class NoWindows:
        def __init__(self):
            raise RuntimeError("Live activity capture requires Windows.")

    monkeypatch.setattr(cli, "WindowsActivityProbe", NoWindows)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert cli.main(["run", "--history", str(tmp_path / "h.jsonl"), "--no-hermes"]) == 2
    assert "requires Windows" in capsys.readouterr().out


def test_calibrate_route_reports_camera_faults_instead_of_dropping(server, monkeypatch) -> None:
    from deep_work_assistant import body as body_module

    def explode(*_, **__):
        raise OSError("libEGL.so.1: cannot open shared object file")

    monkeypatch.setattr(body_module, "run_calibration", explode)
    import deep_work_assistant.web_ui_v2 as web
    monkeypatch.setattr(web, "run_calibration", explode)
    url, state = server
    state.body_store.set_consent(True)
    with pytest.raises(HTTPError) as fault:
        _call(f"{url}/api/body/calibrate", "POST", {"samples": 5})
    assert fault.value.code == 500
    assert "libEGL" in json.loads(fault.value.read().decode())["error"]


def test_posture_popup_offers_not_accurate_and_others_do_not() -> None:
    from deep_work_assistant.interactive_popup import build_popup_script

    posture = build_popup_script("posture", "Sit back")
    assert "Not accurate" in posture and "respond('inaccurate')" in posture
    compile(posture, "<popup>", "exec")
    assert "Posture check" in posture
    hydration = build_popup_script("hydration", "Drink")
    compile(hydration, "<popup>", "exec")


def test_inaccurate_popup_answer_widens_live_threshold(tmp_path: Path) -> None:
    from deep_work_assistant import cli

    store = BodyProfileStore(tmp_path / "body.json")
    store.set_consent(True)
    store.save_baseline(compute_baseline([_posture(10)] * 8))

    class Live:
        sampler = VisionSampler(check_fn=lambda **_: {}, posture_threshold=20.0)

    cli._handle_posture_response("inaccurate", Live, store)
    assert Live.sampler.posture_threshold == 23.0
    assert store.load()["inaccurate_reports"] == 1
    cli._handle_posture_response("confirmed", Live, store)
    assert store.load()["inaccurate_reports"] == 1
