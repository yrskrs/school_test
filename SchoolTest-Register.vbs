Set WshShell = CreateObject("WScript.Shell")
WshShell.Run chr(34) & "start_register.bat" & Chr(34), 0
Set WshShell = Nothing
