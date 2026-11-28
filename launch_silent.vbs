Option Explicit
Dim shell, fso, repoDir, psScript, cmd
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
repoDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = repoDir & "\scripts\launch_dev.ps1"
cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & psScript & """"
shell.Run cmd, 0, False
