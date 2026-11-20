# T3 desktop test app (ASCII only; PS 5.1 safe).
# Two-step GUI: click [Unlock] to enable [Issue], click [Issue] -> shows a runtime-random code.
# Ground-truth: code is appended to the file given by -GtFile (set by the test runner).
param([string]$GtFile = "$PSScriptRoot\.code-log")
Add-Type -AssemblyName System.Windows.Forms
$f = New-Object System.Windows.Forms.Form
$f.Text = 'NE-PANEL'
$f.Width = 420; $f.Height = 220; $f.StartPosition = 'Manual'; $f.Left = 60; $f.Top = 60
$lbl = New-Object System.Windows.Forms.Label
$lbl.Text = '(locked)'; $lbl.AccessibleName = 'status'; $lbl.Width = 360; $lbl.Top = 20; $lbl.Left = 20
$b1 = New-Object System.Windows.Forms.Button
$b1.Text = 'Unlock'; $b1.AccessibleName = 'Unlock'; $b1.Top = 60; $b1.Left = 20
$b2 = New-Object System.Windows.Forms.Button
$b2.Text = 'Issue'; $b2.AccessibleName = 'Issue'; $b2.Top = 60; $b2.Left = 120; $b2.Enabled = $false
$b1.Add_Click({ $b2.Enabled = $true; $lbl.Text = '(unlocked - press Issue)' })
$b2.Add_Click({
    $code = -join ((1..6) | ForEach-Object { '{0:X}' -f (Get-Random -Maximum 16) })
    $lbl.Text = 'CODE: ' + $code
    if ($GtFile) { Add-Content -Path $GtFile -Value $code }
})
$f.Controls.AddRange(@($lbl, $b1, $b2))
[System.Windows.Forms.Application]::Run($f)
