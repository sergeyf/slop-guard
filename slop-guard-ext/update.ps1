# Pull the latest slop-guard source and regenerate the browser bundle.
# Usage: .\update.ps1 [-Repo <path>]

param(
    [string]$Repo = ""
)

$ErrorActionPreference = "Stop"

if (-not $Repo) {
    $Repo = Join-Path $env:TEMP "slop-guard-latest"
    Write-Host "Cloning latest slop-guard into $Repo ..."
    if (Test-Path $Repo) { Remove-Item -Recurse -Force $Repo }
    git clone --depth 1 https://github.com/eric-tramel/slop-guard.git $Repo
    if ($LASTEXITCODE -ne 0) {
        Write-Error "git clone failed. Is git installed and on PATH?"
        exit 1
    }
}

uv run "$PSScriptRoot\bundle.py" $Repo
if ($LASTEXITCODE -ne 0) {
    Write-Error "bundle.py failed. Is uv installed and on PATH?"
    exit 1
}

Write-Host ""
Write-Host "Reload the extension in your browser:"
Write-Host "  Chrome:  chrome://extensions -> reload Slop Guard"
Write-Host "  Firefox: about:debugging -> This Firefox -> Reload"
