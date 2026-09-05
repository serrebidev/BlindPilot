<#
.SYNOPSIS
  Bring a process's main window to the foreground and optionally send keystrokes (SendKeys syntax).
.EXAMPLE
  pwsh -NoProfile -File sendkeys.ps1 -ProcessId 51852 -Keys "%o"          # Alt+O opens the Options menu
  pwsh -NoProfile -File sendkeys.ps1 -ProcessId 51852 -Keys "^+h"         # Ctrl+Shift+H
  pwsh -NoProfile -File sendkeys.ps1 -ProcessId 51852 -Keys "{ESC}"
  pwsh -NoProfile -File sendkeys.ps1 -ProcessId 51852                      # focus only
.NOTES
  SendKeys: ^ = Ctrl, % = Alt, + = Shift, {ESC} {ENTER} {TAB} {DOWN} {F10} etc.
  Always target the AUDIT instance's PID, never another BlindPilot window.
  -Wait is milliseconds to sleep after sending (default 700) so the UI can settle before a capture.
#>
param(
  [Parameter(Mandatory)][int]$ProcessId,
  [string]$Keys = "",
  [int]$Wait = 700
)
Add-Type -AssemblyName System.Windows.Forms
$sig = @"
using System; using System.Runtime.InteropServices;
public static class F {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@
if (-not ([System.Management.Automation.PSTypeName]'F').Type) { Add-Type -TypeDefinition $sig }
$p = Get-Process -Id $ProcessId
$h = $p.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { throw "Process $ProcessId has no main window" }
if ([F]::IsIconic($h)) { [F]::ShowWindow($h, 9) | Out-Null }
[F]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 400
# Verify the foreground window belongs to the target process (a modal dialog of that process also counts).
$fg = [F]::GetForegroundWindow(); $fgPid = 0; [F]::GetWindowThreadProcessId($fg, [ref]$fgPid) | Out-Null
if ($fgPid -ne $ProcessId) { throw "Foreground window belongs to pid $fgPid, not $ProcessId. Refusing to send keys." }
if ($Keys) { [System.Windows.Forms.SendKeys]::SendWait($Keys) }
Start-Sleep -Milliseconds $Wait
"focused pid $ProcessId" + $(if ($Keys) { ", sent: $Keys" } else { "" })
