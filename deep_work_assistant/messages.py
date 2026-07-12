"""Motivational message banks, stretch suggestions, and time-of-day awareness."""

from __future__ import annotations

import random
from datetime import datetime, timezone


# ── Time-of-day helpers ──────────────────────────────────────────────────────

def time_of_day_label(now: datetime | None = None) -> str:
    """Return 'morning', 'afternoon', 'evening', or 'night' for UTC."""
    now = now or datetime.now(timezone.utc)
    hour = now.hour
    if 5 <= hour < 12:
        return 'morning'
    if 12 <= hour < 17:
        return 'afternoon'
    if 17 <= hour < 22:
        return 'evening'
    return 'night'


# ── Motivational message banks ───────────────────────────────────────────────

HYDRATION_MESSAGES: dict[str, list[str]] = {
    'morning': [
        "Rise and shine! Your brain is 75% water — give it a drink.",
        "Morning hydration kickstarts your metabolism. Sip up!",
        "You've been in flow since the morning — top up that water glass.",
        "Good morning, focus machine! Time for a water refill.",
    ],
    'afternoon': [
        "Afternoon slump buster: water + stand up = instant reset.",
        "You're deep in the zone. Water keeps the gears turning.",
        "Hydrate now — future you will thank you for skipping the 3pm crash.",
        "Your focus is sharp, but your water bottle is probably empty.",
    ],
    'evening': [
        "Evening push — don't let dehydration steal your last productive hour.",
        "Wind-down hydration: sip slowly, stay sharp for the finish line.",
        "You've earned this water break. Your body's been working hard.",
        "Evening flow is precious. Protect it with a glass of water.",
    ],
    'night': [
        "Late night grind? Water helps you stay clear-headed.",
        "One last water top-up before you wrap up. Your body will thank you.",
        "Night owl mode: hydrate to keep the brain fog away.",
        "Don't let the late hour trick you into skipping water.",
    ],
}

STRETCH_MESSAGES: dict[str, list[str]] = {
    'morning': [
        "Morning stiffness is real. 60 seconds of stretching resets your posture.",
        "Start the session loose — roll your shoulders and tilt your neck.",
        "A quick morning stretch wakes up your circulation and your focus.",
    ],
    'afternoon': [
        "Midday reset: stand up, shake out your hands, roll your neck.",
        "Two hours of sitting is the new smoking. Stand up and stretch!",
        "Your chair has been your best friend. Now give your back a break.",
    ],
    'evening': [
        "Evening wind-down stretch: your muscles have been holding tension all day.",
        "One last stretch push before you finish. Your body has earned it.",
        "Evening stretch = releasing the day's stress. 90 seconds, tops.",
    ],
    'night': [
        "Late session stretch: prevent tomorrow's stiffness tonight.",
        "Your shoulders are probably up by your ears. Drop them. Breathe. Stretch.",
        "Night stretch: quick reset before the next deep focus block.",
    ],
}

EAT_MESSAGES: dict[str, list[str]] = {
    'morning': [
        "Fuel up! A quick breakfast or snack keeps the brain running.",
        "Morning fuel = afternoon energy. Don't skip it.",
        "Your brain is running on empty. Time for some real fuel.",
    ],
    'afternoon': [
        "Lunch fuel is burning off. Grab something light to sustain the flow.",
        "Don't let hunger sabotage your deep work. 5 minutes to eat.",
        "Your body needs calories to keep up with your brain. Time to eat!",
    ],
    'evening': [
        "Evening meal: you've been running a marathon of focus. Refuel.",
        "Dinner time! Your brain has been working overtime — feed it.",
        "You've pushed hard today. That deserves a proper meal.",
    ],
    'night': [
        "Late night fuel — keep it light, keep it going.",
        "If you're still going, grab a small snack to sustain the focus.",
        "Night session fuel: something light to keep the engine running.",
    ],
}

# ── Activity-specific stretch suggestions ─────────────────────────────────────

STRETCH_SUGGESTIONS: dict[str, list[dict[str, str]]] = {
    'coding': [
        {'name': 'Wrist Flexor Stretch', 'duration': '30s per hand',
         'instruction': 'Extend your arm, palm up. Use your other hand to gently pull fingers back. Feel the stretch in your forearm.'},
        {'name': 'Neck Side Bend', 'duration': '20s per side',
         'instruction': 'Sit up straight, slowly tilt your head toward your shoulder. Hold. Repeat on the other side.'},
        {'name': 'Finger Spread & Clench', 'duration': '10 reps',
         'instruction': 'Spread your fingers wide as possible, hold 3s, then make a fist. Repeat. Great for typing hands.'},
        {'name': 'Shoulder Rolls', 'duration': '10 rolls each way',
         'instruction': 'Roll your shoulders forward 5 times, then backward 5 times. Release the hunch.'},
        {'name': 'Eye Palming', 'duration': '30s',
         'instruction': 'Rub your palms together to warm them, then gently cup over your closed eyes. Breathe deeply.'},
    ],
    'writing-admin': [
        {'name': 'Shoulder Shrug Release', 'duration': '5 reps',
         'instruction': 'Lift your shoulders toward your ears, hold 5s, then drop. Feel the tension release.'},
        {'name': 'Seated Spinal Twist', 'duration': '30s per side',
         'instruction': 'Sit sideways in your chair, grip the backrest, and gently twist your torso. Switch sides.'},
        {'name': 'Neck Half-Circles', 'duration': '5 per side',
         'instruction': 'Slowly roll your head from center to the right, down, across, and up the left side. Reverse.'},
        {'name': 'Wrist Circles', 'duration': '10 per wrist',
         'instruction': 'Extend your arms and make slow circles with your wrists. Great after long typing sessions.'},
    ],
    'browser-research': [
        {'name': '20-20-20 Eye Reset', 'duration': '20s',
         'instruction': 'Every 20 minutes, look at something 20 feet away for 20 seconds. Do this now.'},
        {'name': 'Upper Trap Stretch', 'duration': '30s per side',
         'instruction': 'Sit tall, reach one arm behind your back, and gently pull your head to the opposite side.'},
        {'name': 'Chest Opener', 'duration': '30s',
         'instruction': 'Clasp your hands behind your back, straighten your arms, and open your chest. Hold.'},
    ],
    'communication': [
        {'name': 'Jaw Release', 'duration': '5 reps',
         'instruction': 'Open your mouth wide, then close slowly. Massage your jaw muscles gently.'},
        {'name': 'Upper Back Stretch', 'duration': '30s',
         'instruction': 'Hug yourself tightly, rounding your upper back. Hold and breathe.'},
        {'name': 'Side Stretch', 'duration': '30s per side',
         'instruction': 'Reach one arm overhead and lean to the opposite side. Keep your hips square.'},
    ],
    'creative': [
        {'name': 'Hand & Finger Stretch', 'duration': '30s per hand',
         'instruction': 'Gently pull each finger back one at a time. Great for artists and designers.'},
        {'name': 'Cat-Cow Spine', 'duration': '5 breaths',
         'instruction': 'Sit or stand: arch your spine (cow), then round it (cat). Move with your breath.'},
        {'name': 'Hamstring Stretch', 'duration': '30s per leg',
         'instruction': 'Extend one leg forward, heel on floor, and gently lean forward. Switch legs.'},
    ],
    'general': [
        {'name': 'Stand & Reach', 'duration': '15s',
         'instruction': 'Stand up, reach your arms toward the ceiling, and take a deep breath.'},
        {'name': 'Side Bends', 'duration': '30s per side',
         'instruction': 'Stand with feet hip-width apart, raise one arm overhead, and lean to the side.'},
        {'name': 'Forward Fold', 'duration': '30s',
         'instruction': 'Stand and slowly fold forward, letting your head and arms hang heavy.'},
    ],
}


def get_stretch_suggestions(category: str = 'general', count: int = 2) -> list[dict[str, str]]:
    """Return a random selection of stretch suggestions for the given category."""
    exercises = STRETCH_SUGGESTIONS.get(category, STRETCH_SUGGESTIONS['general'])
    return random.sample(exercises, min(count, len(exercises)))


# ── Motivational boosters (daily streak, celebration, etc.) ──────────────────

STREAK_MESSAGES: list[str] = [
    "You're on a roll! {streak} days of focused work in a row. Keep it going!",
    "Day {streak} of the streak — consistency is the superpower nobody talks about.",
    "Look at you! {streak} days deep. Your discipline is inspiring.",
    "Streak alert: {streak} days! Momentum is building. Don't break the chain.",
    "Day {streak} ✅. Every session compounds. Future you is unstoppable.",
]

LEGACY_MESSAGES: list[str] = [
    "You've been doing deep work for a while now. Your brain is getting stronger every session.",
    "Every deep work session rewires your brain for focus. This one counts.",
    "You showed up again. That's the hardest part. Everything else is just reps.",
    "Deep work is a skill, and you're building it one session at a time.",
]


# ── Main message builder ─────────────────────────────────────────────────────

def build_reminder_message(
    stage: str,
    profile_category: str = 'general',
    now: datetime | None = None,
    streak_days: int = 0,
) -> str:
    """Build a context-aware, motivational reminder message."""
    tod = time_of_day_label(now)

    # Choose the right message bank
    if stage == 'hydration':
        bank = HYDRATION_MESSAGES
    elif stage == 'stretch':
        bank = STRETCH_MESSAGES
    elif stage == 'eat':
        bank = EAT_MESSAGES
    else:
        bank = {'general': ['Check in with your body.']}

    # Get time-of-day messages, fallback to general
    tod_messages = bank.get(tod, bank.get('general', []))

    # Pick a random message
    if tod_messages:
        msg = random.choice(tod_messages)
    else:
        msg = 'Time to check in with your body.'

    # Add stretch suggestions for stretch stage
    if stage == 'stretch':
        suggestions = get_stretch_suggestions(profile_category, count=2)
        if suggestions:
            stretch_lines = []
            for s in suggestions:
                stretch_lines.append(f"🧘 {s['name']} ({s['duration']})")
            msg += '\n\nTry:\n' + '\n'.join(stretch_lines)

    # Add streak celebration
    if streak_days >= 3 and random.random() < 0.5:
        celebrate = random.choice(STREAK_MESSAGES).format(streak=streak_days)
        msg += f'\n\n🌟 {celebrate}'

    return msg


def build_session_start_message(
    primary_app: str,
    profile_category: str = 'general',
    streak_days: int = 0,
) -> str:
    """Build a motivational message when a session starts."""
    category_labels = {
        'coding': 'building something',
        'writing-admin': 'organizing ideas',
        'browser-research': 'exploring and learning',
        'communication': 'connecting with people',
        'creative': 'creating',
        'general': 'focusing',
    }
    label = category_labels.get(profile_category, 'focusing')

    msg = random.choice([
        f"Deep work session started. You're {label} — let's get in the zone.",
        f"Focus mode activated. You're {label}. Time to disappear into the work.",
        f"Session started. You're {label} — every minute of deep focus counts.",
        f"Deep work time. You're {label}. I'll remind you to hydrate and move.",
    ])

    if streak_days >= 2:
        msg += f"\n\n🔥 Day {streak_days} of your focus streak. You're on fire!"

    return msg