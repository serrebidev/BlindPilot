# Visual audit kit

How to look at BlindPilot the way a sighted user does, without touching the user's live window or settings.

## Launch an audit copy

Run from the repo root in PowerShell. It starts a second instance with its own config folder and a distinct window title.

```powershell
$sb = "$PWD\docs\visual-audit\sandbox"
New-Item -ItemType Directory -Force "$sb\Roaming\BlindPilot", "$sb\Local" | Out-Null
Copy-Item "$env:APPDATA\BlindPilot\config.json" "$sb\Roaming\BlindPilot\" -Force
$psi = New-Object System.Diagnostics.ProcessStartInfo "C:\Python313\pythonw.exe", "blind_pilot.py"
$psi.WorkingDirectory = $PWD; $psi.UseShellExecute = $false
$psi.EnvironmentVariables["APPDATA"] = "$sb\Roaming"
$psi.EnvironmentVariables["LOCALAPPDATA"] = "$sb\Local"
# wx lives in the user site-packages, which Python finds via APPDATA. Point at it explicitly.
$psi.EnvironmentVariables["PYTHONPATH"] = "$env:APPDATA\Python\Python313\site-packages"
$p = [System.Diagnostics.Process]::Start($psi); Start-Sleep 7
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class T { [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern bool SetWindowText(IntPtr h, string s); }'
[T]::SetWindowText($p.MainWindowHandle, "BlindPilot AUDIT COPY - ignore this window") | Out-Null
$p.Id
```

There is no single-instance guard, so this runs beside the real app. The retitled window lets a screen-reader user tell the two apart when focus jumps.

## Capture and drive

- `tools\capture.ps1 -ProcessId <pid> -NoFocus -Out shots\x.png` screenshots the window with PrintWindow, so it works while covered and never steals focus. Drop `-NoFocus` for a normal screen copy. `-Title "Preferences"` captures a dialog. `-Screen` captures the whole display (needed for open menus).
- `tools\sendkeys.ps1 -ProcessId <pid> -Keys "%o"` focuses the window, checks the foreground process really is that pid, then sends SendKeys text. It refuses to type into any other process.

## Rules learned the hard way

- Never send Enter to the prompt box of the audit copy. The first run did, the Claude CLI started in the repo folder and installed MCP servers into the sandbox.
- Do not change Windows theme, DPI, or contrast on the user's machine. Test those from code review instead.
- The crash log entry `Windows fatal exception: code 0x8001010d` is faulthandler noise from a COM callback, not a real crash.

## Reports

`01-screenshot-audit.md`, `02-code-layout-audit.md`, `03-reference-checklist.md`, `04-responses-list-design.md`, `05-responses-list-plan.md`.

Screenshots go to `shots\`, which is ignored by git. They are evidence for whoever is writing or checking a report at the time; every report describes what its screenshots show, because the people this app is for cannot see them, and a clone does not need three megabytes of pictures.
