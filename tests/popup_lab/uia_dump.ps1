# Dump UIA descendants of one HWND as JSON (ASCII only). Names are app UI text (menu items).
param([long]$Hwnd)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$items = @()
try {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Subtree, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {
        $pats = @($e.GetSupportedPatterns() | ForEach-Object { $_.ProgrammaticName -replace 'PatternIdentifiers.Pattern','' })
        $items += @{ name = $e.Current.Name; type = $e.Current.ControlType.ProgrammaticName -replace 'ControlType.',''; patterns = ($pats -join ',') }
    }
    ConvertTo-Json -Compress -Depth 4 @{ ok = $true; count = $items.Count; items = $items }
} catch {
    ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message }
}
