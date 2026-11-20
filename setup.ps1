# no-errands stage-0 (Windows PowerShell 5.1+)
# Sole job: find a Python >= 3.9 that ACTUALLY RUNS, then hand off to setup.py.
# If none exists, fetch the single-file uv binary (URL+sha256 read from versions.json, verified).
# NOTE: keep this file ASCII-only -- PS 5.1 reads BOM-less files as ANSI and corrupts UTF-8.
param([Parameter(ValueFromRemainingArguments = $true)]$Rest)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-Python($exe) {
    try {
        $out = & $exe --version 2>&1   # lesson R1: 'python' on PATH may be the MS Store stub; must execute it
        if ("$out" -match 'Python (\d+)\.(\d+)') {
            if ([int]$Matches[1] -gt 3 -or ([int]$Matches[1] -eq 3 -and [int]$Matches[2] -ge 9)) { return $true }
        }
    } catch {}
    return $false
}

foreach ($cand in @('python', 'python3', 'py')) {
    if (Test-Python $cand) {
        & $cand "$repo\setup.py" @Rest
        exit $LASTEXITCODE
    }
}

Write-Host 'No usable Python >= 3.9 found. Fetching single-file uv (user-scope, no system changes)...'
$v = (Get-Content "$repo\versions.json" -Raw -Encoding UTF8 | ConvertFrom-Json).uv
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'windows-arm64' } else { 'windows-x86_64' }
$t = $v.targets.$arch
if (-not $t) { Write-Error "versions.json has no uv entry for $arch"; exit 1 }
$homeDir = if ($Rest -contains '--home') { $Rest[([array]::IndexOf($Rest, '--home') + 1)] } else { Join-Path $env:USERPROFILE '.no-errands' }
$bin = Join-Path $homeDir 'bin'
New-Item -ItemType Directory -Force $bin | Out-Null
$zip = Join-Path $env:TEMP 'ne-uv.zip'
Invoke-WebRequest -Uri $t.url -OutFile $zip -UseBasicParsing
$got = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
if ($got -ne $t.sha256) { Write-Error "uv sha256 mismatch (got $got). Aborting; verify versions.json."; exit 1 }
Expand-Archive -Path $zip -DestinationPath $bin -Force
Remove-Item $zip
& (Join-Path $bin 'uv.exe') run --no-project "$repo\setup.py" @Rest
exit $LASTEXITCODE
