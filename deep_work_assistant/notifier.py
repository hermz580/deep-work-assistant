from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None


class DesktopNotifier:
    def __init__(self, app_id: str = 'Deep Work Assistant', dry_run: bool = False) -> None:
        self.app_id = app_id
        self.dry_run = dry_run

    def notify(self, title: str, message: str) -> bool:
        if self.dry_run:
            print(f'[notification] {title} — {message}')
            return True

        popup_started = self._spawn_popup(title, message)
        self._play_attention_sound()

        script = self._build_powershell_script(title, message, self.app_id)
        try:
            completed = subprocess.run(
                [
                    'powershell.exe',
                    '-NoProfile',
                    '-ExecutionPolicy',
                    'Bypass',
                    '-Command',
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except FileNotFoundError:
            print(f'[notification-fallback] {title} — {message}')
            return popup_started
        except subprocess.TimeoutExpired:
            print(f'[notification-timeout] {title} — {message}')
            return popup_started

        if completed.returncode != 0:
            stderr = completed.stderr.strip()
            if stderr:
                print(f'[notification-error] {stderr}')
            print(f'[notification-fallback] {title} — {message}')
            return popup_started
        return True

    def _play_attention_sound(self) -> None:
        if winsound is None:
            return
        try:
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    def _spawn_popup(self, title: str, message: str) -> bool:
        script = self._build_popup_script(title, message)
        pythonw = Path(sys.executable).with_name('pythonw.exe')
        interpreter = str(pythonw if pythonw.exists() else Path(sys.executable))
        creationflags = 0
        for flag_name in ('CREATE_NO_WINDOW', 'DETACHED_PROCESS', 'CREATE_NEW_PROCESS_GROUP'):
            creationflags |= int(getattr(subprocess, flag_name, 0))
        kwargs = {
            'stdout': subprocess.DEVNULL,
            'stderr': subprocess.DEVNULL,
        }
        if creationflags:
            kwargs['creationflags'] = creationflags
        try:
            subprocess.Popen([interpreter, '-c', script], **kwargs)
            return True
        except Exception as exc:
            print(f'[notification-popup-fallback] {exc}')
            return False

    @staticmethod
    def _build_popup_script(title: str, message: str) -> str:
        title_repr = repr(title)
        message_repr = repr(message)
        return f"""
import tkinter as tk

TITLE = {title_repr}
MESSAGE = {message_repr}

root = tk.Tk()
root.title(TITLE)
root.attributes('-topmost', True)
root.resizable(False, False)
root.configure(bg='#111827')

width = 380
height = 150
root.update_idletasks()
try:
    screen_width = root.winfo_screenwidth()
    screen_height = root.winfo_screenheight()
except Exception:
    screen_width = 1280
    screen_height = 720
x = max(20, screen_width - width - 24)
y = max(20, screen_height - height - 80)
root.geometry('%dx%d+%d+%d' % (width, height, x, y))

frame = tk.Frame(root, bg='#111827', padx=18, pady=16)
frame.pack(fill='both', expand=True)

tk.Label(
    frame,
    text=TITLE,
    font=('Segoe UI', 13, 'bold'),
    fg='white',
    bg='#111827',
).pack(anchor='w')

tk.Label(
    frame,
    text=MESSAGE,
    font=('Segoe UI', 10),
    fg='white',
    bg='#111827',
    wraplength=330,
    justify='left',
).pack(anchor='w', pady=(10, 12))

tk.Button(frame, text='Dismiss', command=root.destroy).pack(anchor='e')
root.after(15000, root.destroy)
root.mainloop()
""".strip()

    @staticmethod
    def _build_powershell_script(title: str, message: str, app_id: str) -> str:
        title_xml = escape(title)
        message_xml = escape(message)
        app_xml = escape(app_id)
        return f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(@'
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{title_xml}</text>
      <text>{message_xml}</text>
    </binding>
  </visual>
</toast>
'@)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{app_xml}')
$notifier.Show($toast)
""".strip()
