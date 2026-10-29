# Puts an "NTG Genomics Pipeline" shortcut on the Windows desktop that starts
# the UI inside WSL and opens it in the browser.
#   powershell -ExecutionPolicy Bypass -File windows\create-desktop-shortcut.ps1 [-Distro Ubuntu-24.04]
param(
    [string]$Distro = "Ubuntu-24.04",
    [string]$RepoWslPath = ""
)
$ErrorActionPreference = "Stop"

if (-not $RepoWslPath) {
    # This script's repo, as a WSL path (e.g. /mnt/c/Users/...)
    $repo = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
    $RepoWslPath = (& wsl.exe -d $Distro wslpath -a ($repo -replace '\\', '/')).Trim()
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnk = Join-Path $desktop "NTG Genomics Pipeline.lnk"
$shell = New-Object -ComObject WScript.Shell
$s = $shell.CreateShortcut($lnk)
$s.TargetPath = "$env:SystemRoot\System32\wsl.exe"
$s.Arguments = "-d $Distro --cd `"$RepoWslPath`" -- ./ntg ui"
$s.WorkingDirectory = $env:USERPROFILE
$s.IconLocation = "$env:SystemRoot\System32\imageres.dll,144"
$s.Description = "Open the NTG pathology genomics pipeline UI (keep the small window open while you use it; closing it stops the UI)"
$s.WindowStyle = 7  # start minimized
$s.Save()
Write-Host "Created $lnk"

# "Stop" shortcut: closes the UI, running pipelines, Docker and WSL (asks first if a run is going)
$stop = Join-Path $desktop "Stop NTG Genomics Pipeline.lnk"
$t = $shell.CreateShortcut($stop)
$t.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$t.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$(Join-Path $PSScriptRoot 'stop-pipeline.ps1')`""
$t.WorkingDirectory = $env:USERPROFILE
$t.IconLocation = "$env:SystemRoot\System32\imageres.dll,93"
$t.Description = "Stop the NTG pathology genomics pipeline: UI, running pipelines, Docker and WSL"
$t.WindowStyle = 7
$t.Save()
Write-Host "Created $stop"
