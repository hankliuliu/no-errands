# B3(ii) common dialogs, one after another (ASCII only). Each result is appended to $Out as one JSON line.
# -PrintEx 0 uses the classic print dialog: on some machines (user's Win11 Home zh-TW) PrintDlgEx blocks
# without ever showing a window, while the classic one shows fine. -Only print runs just the print dialog.
param([string]$Out, [int]$PrintEx = 1, [string]$Only = '')
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'NE-DIALOGS'; $owner.Width = 300; $owner.Height = 120
$owner.Add_Shown({
    function Log($kind, $res) { [IO.File]::AppendAllText($Out, ((ConvertTo-Json -Compress @{ kind = $kind; result = "$res" }) + "`n")) }
    if ($Only -eq '' -or $Only -eq 'msgbox') {
        Log 'msgbox' ([System.Windows.Forms.MessageBox]::Show($owner, 'Operation finished.', 'NE Info', 'OK'))
    }
    if ($Only -eq '' -or $Only -eq 'open') {
        $o = New-Object System.Windows.Forms.OpenFileDialog; $o.Title = 'NE Open'
        Log 'open' ($o.ShowDialog($owner))
    }
    if ($Only -eq '' -or $Only -eq 'save') {
        $s = New-Object System.Windows.Forms.SaveFileDialog; $s.Title = 'NE Save'
        Log 'save' ($s.ShowDialog($owner))
    }
    if ($Only -eq '' -or $Only -eq 'overwrite') {
        Log 'overwrite' ([System.Windows.Forms.MessageBox]::Show($owner, 'File exists. Replace it?', 'NE Confirm Replace', 'YesNo'))
    }
    if ($Only -eq '' -or $Only -eq 'print') {
        $p = New-Object System.Windows.Forms.PrintDialog; $p.UseEXDialog = ($PrintEx -eq 1)
        Log 'print' ($p.ShowDialog($owner))
    }
    $owner.Close()
})
[System.Windows.Forms.Application]::Run($owner)
