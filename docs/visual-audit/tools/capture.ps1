<#
.SYNOPSIS
  Capture a top-level window (or the whole screen) to PNG for visual audits.
.EXAMPLE
  pwsh -NoProfile -File capture.ps1 -ProcessId 1234 -Out shots\01-main.png
  pwsh -NoProfile -File capture.ps1 -Title "Preferences" -Out shots\02-prefs.png
  pwsh -NoProfile -File capture.ps1 -Screen -Out shots\03-screen.png
.NOTES
  -ProcessId : capture the main window of that process id.
  -Title  : capture the first top-level visible window whose title contains this text (any process). Use for dialogs.
  -Screen : capture the entire primary display.
  -NoFocus: do not bring the window to the foreground; uses PrintWindow so a covered window still captures.
  Uses DWM extended-frame bounds so the invisible resize border is excluded.
#>
param(
  [int]$ProcessId = 0,
  [string]$Title = "",
  [switch]$Screen,
  [switch]$NoFocus,
  [Parameter(Mandatory)][string]$Out
)
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms
$sig = @"
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public struct RECT { public int Left, Top, Right, Bottom; }
public static class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr l);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr h, int attr, out RECT r, int size);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  public static List<IntPtr> Find(string part) {
    var found = new List<IntPtr>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(512); GetWindowText(h, sb, 512);
      if (sb.ToString().IndexOf(part, StringComparison.OrdinalIgnoreCase) >= 0) found.Add(h);
      return true; }, IntPtr.Zero);
    return found;
  }
}
"@
if (-not ([System.Management.Automation.PSTypeName]'W').Type) { Add-Type -TypeDefinition $sig }

if ($Screen) {
  $b = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
  $x=$b.X; $y=$b.Y; $w=$b.Width; $h=$b.Height
} else {
  if ($ProcessId) { $hwnd = (Get-Process -Id $ProcessId).MainWindowHandle }
  elseif ($Title) { $list = [W]::Find($Title); if ($list.Count -eq 0) { throw "No visible window with title containing '$Title'" }; $hwnd = $list[0] }
  else { throw "Give -ProcessId, -Title or -Screen" }
  if ([W]::IsIconic($hwnd)) { [W]::ShowWindow($hwnd, 9) | Out-Null }
  if (-not $NoFocus) { [W]::SetForegroundWindow($hwnd) | Out-Null; Start-Sleep -Milliseconds 600 }
  $r = New-Object RECT
  [W]::DwmGetWindowAttribute($hwnd, 9, [ref]$r, 16) | Out-Null
  $x=$r.Left; $y=$r.Top; $w=$r.Right-$r.Left; $h=$r.Bottom-$r.Top
}
$dir = Split-Path -Parent (Resolve-Path -LiteralPath (Split-Path -Parent $Out) -ErrorAction SilentlyContinue)
if (-not (Test-Path (Split-Path -Parent $Out))) { New-Item -ItemType Directory -Force (Split-Path -Parent $Out) | Out-Null }
if ($NoFocus -and -not $Screen) {
  # PrintWindow renders the window even when it is behind other windows. PW_RENDERFULLCONTENT = 2.
  $wr = New-Object RECT; [W]::GetWindowRect($hwnd, [ref]$wr) | Out-Null
  $full = New-Object System.Drawing.Bitmap ($wr.Right-$wr.Left), ($wr.Bottom-$wr.Top)
  $g = [System.Drawing.Graphics]::FromImage($full); $hdc = $g.GetHdc()
  [W]::PrintWindow($hwnd, $hdc, 2) | Out-Null
  $g.ReleaseHdc($hdc); $g.Dispose()
  # Crop the invisible resize border: DWM frame bounds relative to GetWindowRect.
  $crop = New-Object System.Drawing.Rectangle ($x-$wr.Left), ($y-$wr.Top), $w, $h
  $bmp = $full.Clone($crop, $full.PixelFormat); $full.Dispose()
  $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
  "saved $Out ${w}x${h} via PrintWindow (no focus change)"
} else {
  $bmp = New-Object System.Drawing.Bitmap $w, $h
  $g = [System.Drawing.Graphics]::FromImage($bmp)
  $g.CopyFromScreen($x, $y, 0, 0, $bmp.Size)
  $bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png)
  $g.Dispose(); $bmp.Dispose()
  "saved $Out ${w}x${h} at ($x,$y)"
}
