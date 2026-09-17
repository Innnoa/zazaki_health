' launch_hidden.vbs - run a command line with a fully hidden window.
' Usage:  wscript.exe //nologo launch_hidden.vbs "<full command line>"
' Window style 0 = hidden; wait=False = do not block the caller.
Set sh = CreateObject("WScript.Shell")
If WScript.Arguments.Count < 1 Then
    WScript.Quit 2
End If
sh.Run WScript.Arguments(0), 0, False
