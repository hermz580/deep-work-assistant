"""Voice and motivational reminder output via TTS (text-to-speech).

Uses `edge-tts` when available (cross-platform, natural voices).
Falls back silently if the package isn't installed.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


# Try to import edge-tts; if not available, voice is disabled.
try:
    import edge_tts

    EDGE_AVAILABLE = True
except ImportError:
    EDGE_AVAILABLE = False


DEFAULT_VOICE = 'en-US-JennyNeural'       # warm, clear female voice
ALT_VOICE = 'en-US-GuyNeural'             # male voice for variety
RATE = '+0%'                               # speech rate (can be '-10%' etc.)


def _speak(path: str, text: str) -> bool:
    """Play an audio file cross-platform. Returns True on playback attempt."""
    try:
        if sys.platform == 'win32':
            try:
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return True
            except Exception:
                ps_script = (
                    "$p = New-Object System.Media.MediaPlayer; "
                    "$p.Open('" + str(path).replace("'", "''") + "'); "
                    "$p.Play(); "
                    "while($p.Position -lt $p.NaturalDuration) { Start-Sleep -Milliseconds 200 }; "
                    "$p.Close()"
                )
                subprocess.Popen(
                    ['powershell.exe', '-NoProfile', '-Command', ps_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True

        if sys.platform == 'darwin':
            subprocess.Popen(['afplay', path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True

        for player in ('paplay', 'aplay', 'ffplay', 'mpg123', 'mpv'):
            try:
                subprocess.Popen([player, path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except FileNotFoundError:
                continue
        return False
    except Exception:
        return False


async def _speak_async(text: str, voice: str, rate: str) -> bool:
    """Generate TTS audio and return True when playback is attempted."""
    tmp_path: str | None = None
    try:
        communicate = edge_tts.Communicate(text, voice, rate=rate)
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tmp:
            tmp_path = tmp.name
        await communicate.save(tmp_path)
        return _speak(tmp_path, text)
    except Exception:
        return False
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


def _run_async(coro) -> None:
    """Run an async coroutine in a new event loop on a background thread."""
    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(coro)
        finally:
            loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()


class VoiceNotifier:
    """Optional TTS voice companion for desktop notifications.

    When `enabled=True` and edge-tts is installed, each reminder is
    spoken aloud.  On platforms without edge-tts the notifier stays
    silent without raising.
    """

    def __init__(
        self,
        enabled: bool = True,
        voice: str = DEFAULT_VOICE,
        rate: str = RATE,
        dry_run: bool = False,
    ) -> None:
        self.enabled = enabled and EDGE_AVAILABLE
        self.voice = voice
        self.rate = rate
        self.dry_run = dry_run

    def speak(self, text: str, voice: str | None = None) -> bool:
        """Speak *text* using TTS. Returns True if speech was attempted."""
        if not self.enabled:
            return False
        if self.dry_run:
            print(f'[voice] {text[:120]}...')
            return True

        use_voice = voice or self.voice

        async def _speak() -> bool:
            try:
                return await _speak_async(text, use_voice, self.rate)
            except Exception:
                return False

        _run_async(_speak())
        return True

    def speak_async(self, text: str, voice: str | None = None) -> bool:
        """Non-blocking speak. Returns immediately; audio plays in background."""
        return self.speak(text, voice)


class ChainedNotifier:
    """Wraps both a DesktopNotifier and a VoiceNotifier so one call does both."""

    def __init__(
        self,
        desktop_notifier,
        voice_notifier: VoiceNotifier | None = None,
        pre_announce: bool = False,
    ) -> None:
        self.desktop = desktop_notifier
        self.voice = voice_notifier or VoiceNotifier()
        self.pre_announce = pre_announce  # Voice first, then popup?

    def notify(self, title: str, message: str) -> bool:
        """Send both desktop notification and optional voice."""
        if self.pre_announce:
            self.voice.speak(f'{title}. {message.split(chr(10))[0]}')
        desktop_ok = self.desktop.notify(title, message)
        if not self.pre_announce:
            self.voice.speak(f'{title}. {message.split(chr(10))[0]}')
        return desktop_ok

    def speak_session_start(self, message: str) -> None:
        """Speak the session-start motivational message."""
        self.voice.speak(message)

    def speak_session_end(self, summary_message: str) -> None:
        """Speak a session-wrap-up message."""
        self.voice.speak(summary_message)