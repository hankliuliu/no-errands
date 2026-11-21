# Invoke the first UIA element under HWND whose Name matches one of the |-separated names (ASCII only).
# Used for: menu item by name, and Cancel/No on dialogs (never a confirm button).
param([long]$Hwnd, [string]$Names)
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$want = $Names -split '\|'
try {
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]$Hwnd)
    $all = $root.FindAll([System.Windows.Automation.TreeScope]::Subtree, [System.Windows.Automation.Condition]::TrueCondition)
    foreach ($e in $all) {
        if ($want -contains $e.Current.Name) {
            $p = $null
            if ($e.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$p)) {
                $p.Invoke(); ConvertTo-Json -Compress @{ ok = $true; invoked = $e.Current.Name; via = 'Invoke' }; exit
            }
            if ($e.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern, [ref]$p)) {
                $p.Expand(); ConvertTo-Json -Compress @{ ok = $true; invoked = $e.Current.Name; via = 'Expand' }; exit
            }
        }
    }
    ConvertTo-Json -Compress @{ ok = $false; error = 'no matching invokable element' }
} catch {
    ConvertTo-Json -Compress @{ ok = $false; error = $_.Exception.Message }
}
