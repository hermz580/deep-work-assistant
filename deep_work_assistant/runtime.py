from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from datetime import datetime, timezone

import psutil

from .engine import ActivitySample


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.UINT),
        ('dwTime', wintypes.DWORD),
    ]


class WindowsActivityProbe:
    def sample(self) -> ActivitySample:
        captured_at = datetime.now(timezone.utc)
        hwnd = user32.GetForegroundWindow()
        process_name = ''
        window_title = ''

        if hwnd:
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value:
                try:
                    process_name = psutil.Process(pid.value).name()
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    process_name = ''

            buffer = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buffer, len(buffer))
            window_title = buffer.value

        idle_seconds = self.get_idle_seconds()
        return ActivitySample(
            captured_at=captured_at,
            process_name=process_name,
            window_title=window_title,
            idle_seconds=idle_seconds,
        )

    @staticmethod
    def get_idle_seconds() -> int:
        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0
        try:
            tick = kernel32.GetTickCount64()
        except AttributeError:
            tick = kernel32.GetTickCount()
        return max(0, int((tick - info.dwTime) / 1000))
