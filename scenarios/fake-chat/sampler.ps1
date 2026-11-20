# Foreground/cursor sampler for non-interference tests (ASCII only).
# Every IntervalMs writes {t, hwnd, pid, ours, x, y} as one JSON line. Window titles are NOT logged;
# "ours" = foreground title starts with NE- or equals a dialog title we open (privacy: only a boolean).
param([string]$Out, [int]$Seconds = 60, [int]$IntervalMs = 50, [string]$StopFile = '')
Add-Type -TypeDefinition @'
using System; using System.Runtime.InteropServices; using System.Text;
public class NeSmp {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X; public int Y; }
  [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
}
'@
$utf8 = New-Object System.Text.UTF8Encoding($false)
$sw = New-Object IO.StreamWriter($Out, $false, $utf8)
$end = (Get-Date).AddSeconds($Seconds)
$sb = New-Object System.Text.StringBuilder 256
while ((Get-Date) -lt $end) {
    if ($StopFile -and (Test-Path $StopFile)) { break }
    $h = [NeSmp]::GetForegroundWindow()
    [void]$sb.Clear(); [void][NeSmp]::GetWindowText($h, $sb, 256)
    $t = $sb.ToString()
    [uint32]$procId = 0; [void][NeSmp]::GetWindowThreadProcessId($h, [ref]$procId)
    $p = New-Object NeSmp+POINT; [void][NeSmp]::GetCursorPos([ref]$p)
    $ours = $t.StartsWith('NE-') -or ($t -eq 'Save chat history')
    $line = '{{"t":{0},"hwnd":{1},"pid":{2},"ours":{3},"x":{4},"y":{5}}}' -f `
        [DateTimeOffset]::Now.ToUnixTimeMilliseconds(), $h.ToInt64(), $procId, $ours.ToString().ToLower(), $p.X, $p.Y
    $sw.WriteLine($line); $sw.Flush()
    Start-Sleep -Milliseconds $IntervalMs
}
$sw.Close()
