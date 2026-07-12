"""Tests for the guided mindfulness module."""

from datetime import datetime, timedelta, timezone

from deep_work_assistant.mindfulness import (
    BODY_SCAN_PARTS,
    BREATHING_PATTERNS,
    GRATITUDE_PROMPTS,
    MindfulnessCoach,
    MindfulnessType,
)

BASE = datetime(2026, 7, 12, 9, 0, tzinfo=timezone.utc)


class TestBreathingExercises:
    def test_start_creates_active_session(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BREATHING, duration_minutes=1, now=BASE)
        status = coach.status()
        assert status["active"] is True
        assert status["session_type"] == "breathing"

    def test_box_breathing_has_four_phases(self):
        pattern = BREATHING_PATTERNS["box"]
        assert len(pattern) == 4
        names = [p[0] for p in pattern]
        assert names == ["Inhale", "Hold", "Exhale", "Hold"]

    def test_4_7_8_breathing_has_three_phases(self):
        pattern = BREATHING_PATTERNS["4-7-8"]
        assert len(pattern) == 3
        names = [p[0] for p in pattern]
        assert names == ["Inhale", "Hold", "Exhale"]

    def test_simple_breathing_has_two_phases(self):
        pattern = BREATHING_PATTERNS["simple"]
        assert len(pattern) == 2
        names = [p[0] for p in pattern]
        assert names == ["Inhale", "Exhale"]

    def test_breathing_phase_changes_at_correct_time(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BREATHING, duration_minutes=1, now=BASE)
        # Initial guidance shows phase 0 (Inhale, 4s)
        assert "Inhale" in coach.get_guidance()

        # Tick exactly 4s — should advance to phase 1 (Hold)
        events = coach.tick(now=BASE + timedelta(seconds=4))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["phase"] == "Hold"
        assert events[0].data["phase_index"] == 1

        # Tick another 4s — should advance to phase 2 (Exhale)
        events = coach.tick(now=BASE + timedelta(seconds=8))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["phase"] == "Exhale"
        assert events[0].data["phase_index"] == 2

    def test_breathing_guidance_shows_inhale_countdown(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BREATHING, duration_minutes=1, now=BASE)
        # Initial guidance: "Inhale... N... N-1... 1" with N countdown
        guidance = coach.get_guidance()
        assert "Inhale" in guidance
        assert "..." in guidance

        # After 2s within the 4s inhale phase, countdown shrinks
        coach.tick(now=BASE + timedelta(seconds=2))
        guidance = coach.get_guidance()
        # phase_elapsed=2, phase_duration=4, remaining=int(4-2+1)=3 → "Inhale... 3... 2... 1"
        assert guidance == "Inhale... 3... 2... 1"

    def test_breathing_cycles_through_all_phases(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BREATHING, duration_minutes=1, now=BASE)
        # Box breathing has 4 phases of 4s each
        for i in range(4):
            events = coach.tick(now=BASE + timedelta(seconds=(i + 1) * 4))
            assert len(events) == 1
            assert events[0].kind == "phase_updated"

        # After 4 ticks of 4s (16s total) we should be back at phase 0 (Inhale)
        coach.tick(now=BASE + timedelta(seconds=16))
        # Phase is still index 3 after 16s — extra tick brings it back around
        # Let's tick through exactly: at 4s→phase 1, 8s→phase 2, 12s→phase 3, 16s→phase 0
        # Actually 4 ticks of 4s each: tick #1 at +4s → phase 1, #2 at +8s → phase 2,
        # #3 at +12s → phase 3, #4 at +16s → phase 0 (wraps around)
        # Then guidance should show Inhale again
        guidance = coach.get_guidance()
        assert "Inhale" in guidance


class TestBodyScan:
    def test_body_scan_has_10_zones(self):
        assert len(BODY_SCAN_PARTS) == 10
        assert BODY_SCAN_PARTS[0] == "feet"
        assert BODY_SCAN_PARTS[-1] == "face"

    def test_body_scan_advances_through_zones(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BODY_SCAN, duration_minutes=1, now=BASE)
        # 60s / 10 zones = 6s per zone
        # Initial zone is index 0 (feet)
        assert "feet" in coach.get_guidance()

        # Tick 6s — should advance to zone 1 (calves)
        events = coach.tick(now=BASE + timedelta(seconds=6))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["zone"] == "calves"
        assert events[0].data["zone_index"] == 1

        # Tick another 6s — should advance to zone 2 (thighs)
        events = coach.tick(now=BASE + timedelta(seconds=12))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["zone"] == "thighs"
        assert events[0].data["zone_index"] == 2

    def test_body_scan_guidance_mentions_body_part(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BODY_SCAN, duration_minutes=1, now=BASE)
        guidance = coach.get_guidance()
        assert "feet" in guidance
        assert "attention" in guidance
        assert "Relax" in guidance or "relax" in guidance.lower()


class TestGratitude:
    def test_gratitude_has_three_prompts(self):
        assert len(GRATITUDE_PROMPTS) == 3
        assert "grateful" in GRATITUDE_PROMPTS[0].lower()

    def test_gratitude_advances_at_correct_time(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.GRATITUDE, duration_minutes=1, now=BASE)
        # 60s / 3 prompts = 20s per prompt
        assert coach.get_guidance() == GRATITUDE_PROMPTS[0]

        # Tick 20s — should advance to prompt 1
        events = coach.tick(now=BASE + timedelta(seconds=20))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["prompt"] == GRATITUDE_PROMPTS[1]
        assert events[0].data["prompt_index"] == 1

        # Tick another 20s — should advance to prompt 2
        events = coach.tick(now=BASE + timedelta(seconds=40))
        assert len(events) == 1
        assert events[0].kind == "phase_updated"
        assert events[0].data["prompt"] == GRATITUDE_PROMPTS[2]
        assert events[0].data["prompt_index"] == 2

    def test_gratitude_guidance_returns_prompt_text(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.GRATITUDE, duration_minutes=1, now=BASE)
        guidance = coach.get_guidance()
        assert guidance == GRATITUDE_PROMPTS[0]


class TestCountdown:
    def test_countdown_shows_remaining_time(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=2, now=BASE)
        guidance = coach.get_guidance()
        assert guidance == "[02:00] remaining"

        coach.tick(now=BASE + timedelta(seconds=30))
        guidance = coach.get_guidance()
        assert guidance == "[01:30] remaining"

        coach.tick(now=BASE + timedelta(seconds=60))
        guidance = coach.get_guidance()
        assert guidance == "[01:00] remaining"

        coach.tick(now=BASE + timedelta(seconds=90))
        guidance = coach.get_guidance()
        assert guidance == "[00:30] remaining"

    def test_countdown_reaches_zero(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=1, now=BASE)
        # Advance 30s (delta clamped to 30s max)
        coach.tick(now=BASE + timedelta(seconds=30))
        guidance = coach.get_guidance()
        assert "[00:30]" in guidance

        # Advance to 60s — session should complete
        events = coach.tick(now=BASE + timedelta(seconds=60))
        assert any(e.kind == "session_completed" for e in events)
        # Guidance should be empty after completion
        assert coach.get_guidance() == ""


class TestCoachLifecycle:
    def test_session_completes_after_duration(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=1, now=BASE)
        # Need two ticks (max delta 30s each) to reach 60s total
        coach.tick(now=BASE + timedelta(seconds=30))
        events = coach.tick(now=BASE + timedelta(seconds=60))
        assert len(events) == 1
        assert events[0].kind == "session_completed"
        assert events[0].data["session_type"] == "countdown"

        # Session should now be inactive
        status = coach.status()
        assert status["active"] is False

    def test_interrupt_ends_session_early(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=5, now=BASE)
        coach.tick(now=BASE + timedelta(seconds=30))
        events = coach.interrupt()
        assert len(events) == 1
        assert events[0].kind == "interrupted"

        # Session should now be inactive
        status = coach.status()
        assert status["active"] is False

        # Subsequent ticks should produce no events
        events = coach.tick(now=BASE + timedelta(seconds=45))
        assert events == []

    def test_tick_before_start_returns_empty(self):
        coach = MindfulnessCoach()
        events = coach.tick(now=BASE)
        assert events == []

    def test_get_guidance_before_start_returns_empty(self):
        coach = MindfulnessCoach()
        assert coach.get_guidance() == ""

    def test_interrupt_before_start_returns_empty(self):
        coach = MindfulnessCoach()
        events = coach.interrupt()
        assert events == []

    def test_status_shows_active_when_running(self):
        coach = MindfulnessCoach()
        coach.start(MindfulnessType.BREATHING, duration_minutes=1, now=BASE)
        status = coach.status()
        assert status["active"] is True
        assert status["session_type"] == "breathing"
        assert "remaining_seconds" in status
        assert "elapsed_seconds" in status

    def test_status_shows_inactive_when_stopped(self):
        coach = MindfulnessCoach()
        # Before any session
        status = coach.status()
        assert status["active"] is False

        # After a completed session (need two ticks due to 30s clamp)
        coach.start(MindfulnessType.COUNTDOWN, duration_minutes=1, now=BASE)
        coach.tick(now=BASE + timedelta(seconds=30))
        coach.tick(now=BASE + timedelta(seconds=60))
        status = coach.status()
        assert status["active"] is False