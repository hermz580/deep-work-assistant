' Deep Work Assistant - Auto-start on Windows login
' Launches the assistant silently in the background
Option Explicit
Dim sh, target, vault
target = "C:\Users\HarpStar\AppData\Local\hermes\work\repo-checks\deep-work-assistant\run_deep_work_assistant.bat"
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd.exe /c """ & target & """ run", 0, False
