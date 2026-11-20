# NE-TYPEPAD: "window A" for non-interference tests (ASCII only).
# Shows without activation. A built-in timer posts one WM_CHAR per IntervalMs into its own TextBox
# (PostMessage to the control handle: no foreground or keyboard focus needed, nothing reaches other
# windows). When done, writes {sent, received} to OutFile and exits.
param([string]$OutFile, [int]$Count = 200, [int]$IntervalMs = 100)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -ReferencedAssemblies System.Windows.Forms -TypeDefinition @'
using System; using System.Runtime.InteropServices;
public class NeTypeForm : System.Windows.Forms.Form {
    protected override bool ShowWithoutActivation { get { return true; } }
}
public class NeMsg {
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
}
'@
$f = New-Object NeTypeForm
$f.Text = 'NE-TYPEPAD'; $f.Width = 420; $f.Height = 160; $f.StartPosition = 'Manual'; $f.Left = 900; $f.Top = 80
$tb = New-Object System.Windows.Forms.TextBox
$tb.Multiline = $true; $tb.Left = 10; $tb.Top = 10; $tb.Width = 380; $tb.Height = 90
$f.Controls.Add($tb)
$alphabet = 'abcdefghijklmnopqrstuvwxyz0123456789'
$script:sent = New-Object System.Text.StringBuilder
$script:i = 0
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = $IntervalMs
$timer.Add_Tick({
    if ($script:i -ge $Count) {
        $timer.Stop()
        $res = @{ sent = $script:sent.ToString(); received = $tb.Text; count = $Count }
        [IO.File]::WriteAllText($OutFile, (ConvertTo-Json $res -Compress))
        $f.Close(); return
    }
    $c = $alphabet[$script:i % $alphabet.Length]
    [void]$script:sent.Append($c)
    [void][NeMsg]::PostMessage($tb.Handle, 0x0102, [IntPtr][int][char]$c, [IntPtr]1)   # WM_CHAR
    $script:i++
})
$f.Add_Shown({ $timer.Start() })
[System.Windows.Forms.Application]::Run($f)
