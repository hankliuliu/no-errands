# Capture ONE window (by HWND) with PrintWindow into a PNG (ASCII only). Not the screen.
param([long]$Hwnd, [string]$Out)
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public class NeCap {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
}
'@
$r = New-Object NeCap+RECT
[void][NeCap]::GetWindowRect([IntPtr]$Hwnd, [ref]$r)
$w = $r.R - $r.L; $h = $r.B - $r.T
if ($w -le 0 -or $h -le 0) { ConvertTo-Json -Compress @{ ok = $false; error = 'empty rect' }; exit }
$bmp = New-Object System.Drawing.Bitmap $w, $h
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [NeCap]::PrintWindow([IntPtr]$Hwnd, $hdc, 2)   # PW_RENDERFULLCONTENT
$g.ReleaseHdc($hdc); $g.Dispose()
$bmp.Save($Out, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
ConvertTo-Json -Compress @{ ok = [bool]$ok; w = $w; h = $h }
