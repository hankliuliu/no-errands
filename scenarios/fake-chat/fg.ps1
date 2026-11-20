# Print foreground window handle + title and cursor position as one JSON line (ASCII only).
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Text;
public class NeFg {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
}
'@
$h = [NeFg]::GetForegroundWindow()
$sb = New-Object System.Text.StringBuilder 256
[void][NeFg]::GetWindowText($h, $sb, 256)
$p = New-Object NeFg+POINT
[void][NeFg]::GetCursorPos([ref]$p)
$isOurs = $sb.ToString().StartsWith('NE-')
ConvertTo-Json -Compress @{ hwnd = $h.ToInt64(); ours = $isOurs; x = $p.X; y = $p.Y }
