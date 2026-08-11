"""Phase 2 runtime coordinator for privacy-gated DWA camera sampling.

The coordinator has no OpenCV/MediaPipe imports.  It calls Phase 1's guarded
``run_check`` function only when a live DWA session is human-active and the
sampling interval is due.  Frames remain inside ``vision.run_check`` and are
discarded there; this module handles metrics only.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import inspect
import math
from threading import Event
from typing import Any, Callable

from .vision import DEFAULT_WINDOW, classify_activity, posture_alert, run_check


@dataclass(frozen=True)
class VisionObservation:
    captured_at: datetime
    activity: str
    motion: float | None
    posture: dict[str, Any] | None
    posture_alert: bool = False
    error: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "captured_at": self.captured_at.isoformat(),
            "activity": self.activity,
            "motion": self.motion,
            "posture": self.posture,
            "posture_alert": self.posture_alert,
            "error": self.error,
        }


class VisionSampler:
    """Run brief, local camera probes only during active human sessions."""

    def __init__(
        self,
        *,
        check_fn: Callable[..., dict[str, Any]] = run_check,
        sample_interval_seconds: float = 60.0,
        frame_interval_seconds: float = 0.4,
    ) -> None:
        self._check_fn = check_fn
        self._cancel_event = Event()
        try:
            parameters = inspect.signature(check_fn).parameters.values()
            self._check_accepts_cancel = any(
                parameter.name == "cancel_event"
                or parameter.kind is inspect.Parameter.VAR_KEYWORD
                for parameter in parameters
            )
        except (TypeError, ValueError):
            self._check_accepts_cancel = False
        sample_interval = float(sample_interval_seconds)
        if not math.isfinite(sample_interval) or not 30.0 <= sample_interval <= 60.0:
            raise ValueError("vision sample interval must be between 30 and 60 seconds")
        self.sample_interval_seconds = sample_interval
        frame_interval = float(frame_interval_seconds)
        if not math.isfinite(frame_interval) or not 0.1 <= frame_interval <= 5.0:
            raise ValueError("vision frame interval must be between 0.1 and 5 seconds")
        self.frame_interval_seconds = frame_interval
        self._last_attempt_at: datetime | None = None
        self._posture_window: deque[float | None] = deque(maxlen=DEFAULT_WINDOW)
        self._posture_alert_latched = False

    def maybe_sample(
        self,
        captured_at: datetime,
        *,
        activity_state: str,
        session_active: bool,
    ) -> VisionObservation | None:
        if not session_active:
            self.reset_session()
            return None
        if not self.is_due(
            captured_at,
            activity_state=activity_state,
            session_active=session_active,
        ):
            return None

        self._last_attempt_at = captured_at
        try:
            if self._cancel_event.is_set():
                return self._unavailable(captured_at, "vision probe cancelled")
            kwargs = {"interval": self.frame_interval_seconds}
            if self._check_accepts_cancel:
                kwargs["cancel_event"] = self._cancel_event
            result = self._check_fn(**kwargs)
        except Exception as exc:
            return VisionObservation(
                captured_at=captured_at,
                activity="unavailable",
                motion=None,
                posture=None,
                error=str(exc),
            )
        if not isinstance(result, dict):
            return self._unavailable(captured_at, "invalid vision result")

        motion = result.get("motion")
        posture_raw = result.get("posture")
        available = bool(result.get("available"))
        if not available:
            return self._unavailable(
                captured_at,
                str(result.get("error") or "vision unavailable"),
            )

        try:
            if isinstance(motion, bool):
                raise ValueError
            normalized_motion = float(motion or 0.0)
        except (TypeError, ValueError):
            return self._unavailable(captured_at, "invalid motion metric")
        if not math.isfinite(normalized_motion) or not 0.0 <= normalized_motion <= 255.0:
            return self._unavailable(captured_at, "invalid motion metric")
        if posture_raw is not None and not isinstance(posture_raw, dict):
            return self._unavailable(captured_at, "invalid posture metric")
        posture = None
        if posture_raw is not None:
            posture = {}
            metric_ranges = {
                "fwd_head_deg": (0.0, 180.0),
                "shoulder_tilt_deg": (-90.0, 90.0),
                "nose_y_frac": (0.0, 1.0),
            }
            for key, (minimum, maximum) in metric_ranges.items():
                value = posture_raw.get(key)
                if value is None:
                    posture[key] = None
                    continue
                if isinstance(value, bool):
                    return self._unavailable(captured_at, "invalid posture metric")
                try:
                    normalized_value = float(value)
                except (TypeError, ValueError):
                    return self._unavailable(captured_at, "invalid posture metric")
                if (
                    not math.isfinite(normalized_value)
                    or not minimum <= normalized_value <= maximum
                ):
                    return self._unavailable(captured_at, "invalid posture metric")
                posture[key] = normalized_value
            visible = posture_raw.get("visible")
            if not isinstance(visible, bool):
                return self._unavailable(captured_at, "invalid posture metric")
            posture["visible"] = visible

        activity = classify_activity(
            present=posture is not None,
            human_active=True,
            motion=normalized_motion,
        )
        if posture is None:
            self._posture_window.clear()
            self._posture_alert_latched = False
        fwd_head = posture.get("fwd_head_deg") if posture else None
        self._posture_window.append(fwd_head)
        sustained_bad_posture = posture_alert(list(self._posture_window))
        should_alert = sustained_bad_posture and not self._posture_alert_latched
        self._posture_alert_latched = sustained_bad_posture
        return VisionObservation(
            captured_at=captured_at,
            activity=activity,
            motion=normalized_motion,
            posture=posture,
            posture_alert=should_alert,
            error=str(result["error"]) if result.get("error") else None,
        )

    def reset_session(self) -> None:
        """Clear cadence and sustained-posture state at a session boundary."""
        self._last_attempt_at = None
        self._posture_window.clear()
        self._posture_alert_latched = False

    def cancel(self) -> None:
        """Cooperatively stop an in-flight production camera probe."""
        self._cancel_event.set()

    def reset_cancellation(self) -> None:
        """Allow probes again only after the cancelled worker has finished."""
        self._cancel_event.clear()

    def is_due(
        self,
        captured_at: datetime,
        *,
        activity_state: str,
        session_active: bool,
    ) -> bool:
        if not session_active or activity_state != "human-active":
            return False
        if self._last_attempt_at is None:
            return True
        elapsed = (captured_at - self._last_attempt_at).total_seconds()
        return elapsed >= self.sample_interval_seconds

    def _unavailable(self, captured_at: datetime, error: str) -> VisionObservation:
        self._posture_window.clear()
        self._posture_alert_latched = False
        return VisionObservation(
            captured_at=captured_at,
            activity="unavailable",
            motion=None,
            posture=None,
            error=error,
        )


class BackgroundVisionSampler:
    """Run due camera probes off the activity/reminder loop.

    ``poll`` is non-blocking. Completed metrics are returned on a later poll;
    results from an ended or replaced session are discarded.
    """

    def __init__(
        self,
        sampler: VisionSampler,
        *,
        executor: Executor | None = None,
    ) -> None:
        self.sampler = sampler
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="dwa-vision",
        )
        self._future: Future | None = None
        self._future_session_id: str | None = None

    def poll(
        self,
        captured_at: datetime,
        *,
        activity_state: str,
        session_id: str | None,
    ) -> VisionObservation | None:
        if self._future is not None:
            if not self._future.done():
                if session_id != self._future_session_id:
                    self.sampler.cancel()
                    self.sampler.reset_session()
                return None
            future = self._future
            future_session_id = self._future_session_id
            self._future = None
            self._future_session_id = None
            try:
                observation = future.result()
            except Exception as exc:
                observation = VisionObservation(
                    captured_at=captured_at,
                    activity="unavailable",
                    motion=None,
                    posture=None,
                    error=str(exc),
                )
            if session_id is None or future_session_id != session_id:
                self.sampler.reset_session()
                self.sampler.reset_cancellation()
                return None
            return observation

        if session_id is None:
            self.sampler.reset_session()
            return None
        if not self.sampler.is_due(
            captured_at,
            activity_state=activity_state,
            session_active=True,
        ):
            return None

        self._future_session_id = session_id
        self._future = self._executor.submit(
            self.sampler.maybe_sample,
            captured_at,
            activity_state=activity_state,
            session_active=True,
        )
        return None

    def close(self) -> None:
        self.sampler.cancel()
        if self._future is not None:
            self._future.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)
