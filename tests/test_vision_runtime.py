"""Phase 2 vision runtime tests — camera probes are opt-in and privacy gated."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from concurrent.futures import Future
import math
from threading import Event

import pytest

from deep_work_assistant.vision_runtime import BackgroundVisionSampler, VisionSampler


BASE = datetime(2026, 8, 10, 12, 0, tzinfo=timezone.utc)


def test_camera_probe_runs_only_during_active_human_session() -> None:
    calls: list[float] = []

    def fake_check(*, interval: float) -> dict:
        calls.append(interval)
        return {
            "available": True,
            "motion": 12.0,
            "posture": {"fwd_head_deg": 10.0, "visible": True},
            "error": None,
        }

    sampler = VisionSampler(check_fn=fake_check, sample_interval_seconds=60, frame_interval_seconds=0.25)

    assert sampler.maybe_sample(BASE, activity_state="agent-active", session_active=True) is None
    assert sampler.maybe_sample(BASE, activity_state="idle", session_active=True) is None
    assert sampler.maybe_sample(BASE, activity_state="human-active", session_active=False) is None

    observation = sampler.maybe_sample(BASE, activity_state="human-active", session_active=True)

    assert observation is not None
    assert observation.activity == "typing"
    assert calls == [0.25]


def test_camera_probe_respects_the_sampling_interval() -> None:
    calls: list[float] = []

    def fake_check(*, interval: float) -> dict:
        calls.append(interval)
        return {
            "available": True,
            "motion": 2.0,
            "posture": {"fwd_head_deg": 8.0, "visible": True},
            "error": None,
        }

    sampler = VisionSampler(check_fn=fake_check, sample_interval_seconds=60)

    first = sampler.maybe_sample(BASE, activity_state="human-active", session_active=True)
    too_soon = sampler.maybe_sample(BASE + timedelta(seconds=59), activity_state="human-active", session_active=True)
    due = sampler.maybe_sample(BASE + timedelta(seconds=60), activity_state="human-active", session_active=True)

    assert first is not None
    assert too_soon is None
    assert due is not None
    assert len(calls) == 2


def test_probe_failure_degrades_without_crashing_the_run_loop() -> None:
    def broken_check(*, interval: float) -> dict:
        raise RuntimeError("camera busy")

    sampler = VisionSampler(check_fn=broken_check, sample_interval_seconds=60)

    observation = sampler.maybe_sample(BASE, activity_state="human-active", session_active=True)

    assert observation is not None
    assert observation.activity == "unavailable"
    assert observation.error == "camera busy"


def test_posture_alert_requires_sustained_bad_samples_and_is_latched() -> None:
    angles = iter([30.0, 5.0, 31.0, 6.0, 32.0, 33.0])

    def fake_check(*, interval: float) -> dict:
        return {
            "available": True,
            "motion": 2.0,
            "posture": {"fwd_head_deg": next(angles), "visible": True},
            "error": None,
        }

    sampler = VisionSampler(check_fn=fake_check, sample_interval_seconds=60)
    alerts = []
    for index in range(6):
        observation = sampler.maybe_sample(
            BASE + timedelta(seconds=index * 60),
            activity_state="human-active",
            session_active=True,
        )
        assert observation is not None
        alerts.append(observation.posture_alert)

    assert alerts == [False, False, False, False, True, False]


def test_new_session_does_not_inherit_old_posture_samples() -> None:
    def fake_check(*, interval: float) -> dict:
        return {
            "available": True,
            "motion": 2.0,
            "posture": {"fwd_head_deg": 32.0, "visible": True},
            "error": None,
        }

    sampler = VisionSampler(check_fn=fake_check, sample_interval_seconds=60)
    for index in range(2):
        observation = sampler.maybe_sample(
            BASE + timedelta(seconds=index * 60),
            activity_state="human-active",
            session_active=True,
        )
        assert observation is not None
        assert observation.posture_alert is False

    assert sampler.maybe_sample(
        BASE + timedelta(seconds=120),
        activity_state="idle",
        session_active=False,
    ) is None
    first_new_session = sampler.maybe_sample(
        BASE + timedelta(seconds=121),
        activity_state="human-active",
        session_active=True,
    )

    assert first_new_session is not None
    assert first_new_session.posture_alert is False


def test_malformed_camera_result_degrades_instead_of_crashing() -> None:
    bad_results = iter([
        None,
        {"available": True, "motion": "not-a-number", "posture": None},
        {"available": True, "motion": 2.0, "posture": "raw-frame-like-data"},
        {
            "available": True,
            "motion": 2.0,
            "posture": {"fwd_head_deg": {"frame": [[1, 2], [3, 4]]}, "visible": True},
        },
    ])

    sampler = VisionSampler(
        check_fn=lambda **kwargs: next(bad_results),
        sample_interval_seconds=30,
    )
    for index in range(4):
        observation = sampler.maybe_sample(
            BASE + timedelta(seconds=index * 30),
            activity_state="human-active",
            session_active=True,
        )
        assert observation is not None
        assert observation.activity == "unavailable"
        assert observation.error


@pytest.mark.parametrize(
    "result",
    [
        {"available": True, "motion": math.nan, "posture": None},
        {"available": True, "motion": math.inf, "posture": None},
        {"available": True, "motion": -0.1, "posture": None},
        {"available": True, "motion": 255.1, "posture": None},
        {"available": True, "motion": 2.0, "posture": {"fwd_head_deg": 180.1, "visible": True}},
        {"available": True, "motion": 2.0, "posture": {"shoulder_tilt_deg": 90.1, "visible": True}},
        {"available": True, "motion": 2.0, "posture": {"nose_y_frac": -0.1, "visible": True}},
    ],
)
def test_non_finite_or_out_of_range_metrics_degrade_to_unavailable(result) -> None:
    sampler = VisionSampler(check_fn=lambda **kwargs: result, sample_interval_seconds=30)

    observation = sampler.maybe_sample(
        BASE,
        activity_state="human-active",
        session_active=True,
    )

    assert observation is not None
    assert observation.activity == "unavailable"
    assert observation.posture is None


def test_worker_shutdown_cancels_probe_before_it_can_reopen_camera() -> None:
    started = Event()
    release = Event()
    finished = Event()
    reopened = Event()

    def cooperative_check(*, interval, cancel_event):
        started.set()
        release.wait(timeout=2)
        if not cancel_event.is_set():
            reopened.set()
        finished.set()
        return {"available": False, "motion": None, "posture": None, "error": "cancelled"}

    worker = BackgroundVisionSampler(
        VisionSampler(check_fn=cooperative_check, sample_interval_seconds=30)
    )
    assert worker.poll(
        BASE,
        activity_state="human-active",
        session_id="session-a",
    ) is None
    assert started.wait(timeout=1)

    worker.close()
    release.set()
    assert finished.wait(timeout=1)
    assert reopened.is_set() is False


def test_session_end_cancels_probe_before_it_can_reopen_camera() -> None:
    started = Event()
    release = Event()
    finished = Event()
    reopened = Event()

    def cooperative_check(*, interval, cancel_event):
        started.set()
        release.wait(timeout=2)
        if not cancel_event.is_set():
            reopened.set()
        finished.set()
        return {"available": False, "motion": None, "posture": None, "error": "cancelled"}

    worker = BackgroundVisionSampler(
        VisionSampler(check_fn=cooperative_check, sample_interval_seconds=30)
    )
    assert worker.poll(
        BASE,
        activity_state="human-active",
        session_id="session-a",
    ) is None
    assert started.wait(timeout=1)

    assert worker.poll(
        BASE + timedelta(seconds=1),
        activity_state="idle",
        session_id=None,
    ) is None
    release.set()
    assert finished.wait(timeout=1)
    assert reopened.is_set() is False
    worker.close()


def test_new_session_can_probe_after_cancelled_worker_finishes() -> None:
    first_started = Event()
    release_first = Event()
    first_finished = Event()
    second_finished = Event()
    cancel_state_at_start: list[bool] = []

    def cooperative_check(*, interval, cancel_event):
        cancel_state_at_start.append(cancel_event.is_set())
        if len(cancel_state_at_start) == 1:
            first_started.set()
            release_first.wait(timeout=2)
            first_finished.set()
            return {"available": False, "motion": None, "posture": None, "error": "cancelled"}
        second_finished.set()
        return {"available": True, "motion": 2.0, "posture": None}

    worker = BackgroundVisionSampler(
        VisionSampler(check_fn=cooperative_check, sample_interval_seconds=30)
    )
    worker.poll(BASE, activity_state="human-active", session_id="session-a")
    assert first_started.wait(timeout=1)
    worker.poll(
        BASE + timedelta(seconds=1),
        activity_state="human-active",
        session_id="session-b",
    )
    release_first.set()
    assert first_finished.wait(timeout=1)
    worker.poll(
        BASE + timedelta(seconds=2),
        activity_state="human-active",
        session_id="session-b",
    )

    worker.poll(
        BASE + timedelta(seconds=32),
        activity_state="human-active",
        session_id="session-b",
    )
    assert second_finished.wait(timeout=1)
    observation = worker.poll(
        BASE + timedelta(seconds=33),
        activity_state="human-active",
        session_id="session-b",
    )

    assert cancel_state_at_start == [False, False]
    assert observation is not None
    assert observation.activity == "absent"
    worker.close()


@pytest.mark.parametrize(
    "interval",
    [1, 29.9, 60.1, 999, math.nan, math.inf, -math.inf],
)
def test_privacy_cadence_rejects_values_outside_30_to_60_seconds(interval) -> None:
    with pytest.raises(ValueError, match="30.*60"):
        VisionSampler(sample_interval_seconds=interval)


@pytest.mark.parametrize("interval", [30, 60])
def test_privacy_cadence_accepts_inclusive_endpoints(interval) -> None:
    sampler = VisionSampler(sample_interval_seconds=interval)
    assert sampler.sample_interval_seconds == float(interval)


@pytest.mark.parametrize(
    "interval",
    [-1, 0, 0.09, 5.01, 999, math.nan, math.inf, -math.inf],
)
def test_frame_comparison_interval_rejects_unbounded_values(interval) -> None:
    with pytest.raises(ValueError, match="0.1.*5"):
        VisionSampler(frame_interval_seconds=interval)


@pytest.mark.parametrize("interval", [0.1, 0.4, 5.0])
def test_frame_comparison_interval_accepts_short_finite_values(interval) -> None:
    sampler = VisionSampler(frame_interval_seconds=interval)
    assert sampler.frame_interval_seconds == float(interval)


def test_background_sampler_never_runs_camera_work_on_the_activity_loop() -> None:
    future = Future()

    class FakeExecutor:
        def __init__(self):
            self.submissions = []

        def submit(self, fn, *args, **kwargs):
            self.submissions.append((fn, args, kwargs))
            return future

        def shutdown(self, **kwargs):
            pass

    executor = FakeExecutor()
    sampler = VisionSampler(
        check_fn=lambda **kwargs: {
            "available": True,
            "motion": 2.0,
            "posture": {"fwd_head_deg": 4.0, "visible": True},
            "error": None,
        },
        sample_interval_seconds=60,
    )
    worker = BackgroundVisionSampler(sampler, executor=executor)

    # First poll only submits work; the DWA loop does not execute camera code.
    assert worker.poll(
        BASE,
        activity_state="human-active",
        session_id="session-a",
    ) is None
    assert len(executor.submissions) == 1
    # Re-polling while in flight never starts a duplicate camera probe.
    assert worker.poll(
        BASE + timedelta(seconds=15),
        activity_state="human-active",
        session_id="session-a",
    ) is None
    assert len(executor.submissions) == 1

    fn, args, kwargs = executor.submissions[0]
    future.set_result(fn(*args, **kwargs))
    observation = worker.poll(
        BASE + timedelta(seconds=30),
        activity_state="human-active",
        session_id="session-a",
    )

    assert observation is not None
    assert observation.activity == "reading"
