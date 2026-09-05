param(
    [Parameter(Mandatory = $true)][int]$ProcessId,
    [Parameter(Mandatory = $true)][AllowEmptyString()][string]$Title,
    [Parameter(Mandatory = $true)][string]$Keys
)
# Focus a window of the given process whose title contains $Title, then send keys.
# Unlike sendkeys.ps1 this reaches a modal dialog, which disables the main window.
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Text; using System.Runtime.InteropServices;
public static class WT {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  public static IntPtr Find(uint want, string part) { IntPtr found = IntPtr.Zero;
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid);
      if (pid == want && IsWindowVisible(h) && IsWindowEnabled(h)) { var sb = new StringBuilder(256); GetWindowText(h, sb, 256);
        if (sb.ToString().IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0) { found = h; return false; } }
      return true; }, IntPtr.Zero);
    return found; }
  public static uint PidOf(IntPtr h) { uint pid; GetWindowThreadProcessId(h, out pid); return pid; }
}
"@
$h = [WT]::Find([uint32]$ProcessId, $Title)
if ($h -eq [IntPtr]::Zero) { Write-Output "no enabled visible window of pid $ProcessId with '$Title'"; exit 2 }
[WT]::SetForegroundWindow($h) | Out-Null
Start-Sleep -Milliseconds 300
$fg = [WT]::GetForegroundWindow()
if ([WT]::PidOf($fg) -ne [uint32]$ProcessId) { Write-Output "refusing: foreground is pid $([WT]::PidOf($fg))"; exit 3 }
[System.Windows.Forms.SendKeys]::SendWait($Keys)
Write-Output "focused '$Title' (pid $ProcessId), sent: $Keys"
