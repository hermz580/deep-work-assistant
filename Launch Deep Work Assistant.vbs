Option Explicit

Dim shell, fileSystem, repoRoot, scriptPath, command
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

repoRoot = fileSystem.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fileSystem.BuildPath(repoRoot, "scripts\bootstrap_windows.ps1")
command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & Chr(34) & scriptPath & Chr(34)
' "/os" opens the DWA OS shell instead of the classic Command Center
If WScript.Arguments.Count > 0 Then
    If LCase(WScript.Arguments(0)) = "/os" Then command = command & " -OpenOS"
End If

shell.Run command, 0, False
