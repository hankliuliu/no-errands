# B3(ii) common dialogs, one after another (ASCII only). Each result is appended to $Out as one JSON line.
param([string]$Out)
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form
$owner.Text = 'NE-DIALOGS'; $owner.Width = 300; $owner.Height = 120
$owner.Add_Shown({
    function Log($kind, $res) { [IO.File]::AppendAllText($Out, ((ConvertTo-Json -Compress @{ kind = $kind; result = "$res" }) + "`n")) }
    Log 'msgbox' ([System.Windows.Forms.MessageBox]::Show($owner, 'Operation finished.', 'NE Info', 'OK'))
    $o = New-Object System.Windows.Forms.OpenFileDialog; $o.Title = 'NE Open'
    Log 'open' ($o.ShowDialog($owner))
    $s = New-Object System.Windows.Forms.SaveFileDialog; $s.Title = 'NE Save'
    Log 'save' ($s.ShowDialog($owner))
    Log 'overwrite' ([System.Windows.Forms.MessageBox]::Show($owner, 'File exists. Replace it?', 'NE Confirm Replace', 'YesNo'))
    $p = New-Object System.Windows.Forms.PrintDialog; $p.UseEXDialog = $true
    Log 'print' ($p.ShowDialog($owner))
    $owner.Close()
})
[System.Windows.Forms.Application]::Run($owner)
