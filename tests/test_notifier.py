from types import SimpleNamespace

import deep_work_assistant.notifier as notifier_module
from deep_work_assistant.notifier import DesktopNotifier


class StrictCp1252Console:
    encoding = 'cp1252'

    def __init__(self):
        self.output = ''

    def write(self, value):
        value.encode(self.encoding, errors='strict')
        self.output += value
        return len(value)

    def flush(self):
        return None


def test_notify_uses_popup_sound_and_toast(monkeypatch):
    calls = {}

    def fake_run(*args, **kwargs):
        calls['run_args'] = args
        calls['run_kwargs'] = kwargs
        return SimpleNamespace(returncode=0, stderr='')

    def fake_popen(args, **kwargs):
        calls['popen_args'] = args
        calls['popen_kwargs'] = kwargs
        return SimpleNamespace(pid=12345)

    class FakeWinsound:
        MB_ICONEXCLAMATION = 0

        def MessageBeep(self, code):
            calls['beep_code'] = code

    monkeypatch.setattr(notifier_module.subprocess, 'run', fake_run)
    monkeypatch.setattr(notifier_module.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(notifier_module, 'winsound', FakeWinsound())

    notifier = DesktopNotifier(dry_run=False)
    assert notifier.notify('Stretch time', 'Please stand up and move around') is True

    assert 'run_args' in calls
    assert 'popen_args' in calls
    assert 'beep_code' in calls


def test_dry_run_reminder_survives_legacy_windows_console(monkeypatch):
    console = StrictCp1252Console()
    monkeypatch.setattr(notifier_module.sys, 'stdout', console)

    notifier = DesktopNotifier(dry_run=True)
    assert notifier.notify_reminder('stretch', 'Stretch', '🧘 Stand and reset') is True

    assert '[interactive-reminder] stretch: Stretch' in console.output
    assert '? Stand and reset' in console.output
