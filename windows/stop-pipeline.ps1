# Stops everything: the UI, any running pipeline, Docker and WSL (wsl --shutdown).
# Asks first if a pipeline run is still going.
#   powershell -ExecutionPolicy Bypass -File windows\stop-pipeline.ps1
$ErrorActionPreference = "SilentlyContinue"
Add-Type -AssemblyName PresentationFramework

$running = @()
try {
    $runs = Invoke-RestMethod -Uri "http://localhost:8642/api/runs" -TimeoutSec 5
    $running = @($runs | Where-Object { $_.status -in @("running", "starting") })
} catch { }  # UI not running: nothing to ask about

if ($running.Count -gt 0) {
    $names = ($running | ForEach-Object { "  - $($_.name)" }) -join "`n"
    $answer = [System.Windows.MessageBox]::Show(
        "These pipeline runs are still going:`n`n$names`n`nStopping now cancels them (their results so far are kept, but the run is not finished).`n`nStop everything anyway?",
        "NTG Genomics Pipeline", "YesNo", "Warning")
    if ($answer -ne "Yes") { exit 0 }
}

& "$env:SystemRoot\System32\wsl.exe" --shutdown
[System.Windows.MessageBox]::Show("NTG Genomics Pipeline stopped (UI, pipeline runs, Docker and WSL).", "NTG Genomics Pipeline", "OK", "Information") | Out-Null
