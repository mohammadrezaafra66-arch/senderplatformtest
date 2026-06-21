# Link WhatsApp Web via QR on Windows using REAL Google Chrome (not Playwright).
# Profile is stored under %LOCALAPPDATA% (NOT Desktop/OneDrive) then copied for Docker.
# Usage: .\scripts\whatsapp_web_link_local.ps1 -AccountId 248
param(
    [Parameter(Mandatory = $true)]
    [int] $AccountId,
    [switch] $KeepProfile,
    [switch] $CompleteOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

if (-not (Test-Path ".env")) {
    Write-Error "Missing .env in $Root — copy from .env.example first."
}

$env:DATABASE_URL = "postgresql://mmp_user:mmp_pass@127.0.0.1:5433/mmp_db"
$env:REDIS_URL = "redis://localhost:6379/0"

function Find-ChromeExe {
    foreach ($candidate in @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Stop-ChromeProcesses {
    Get-Process -Name "chrome" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}

function Sync-ProfileDirectory([string]$Source, [string]$Dest) {
    $parent = Split-Path $Dest -Parent
    if (-not (Test-Path $parent)) {
        New-Item -ItemType Directory -Force -Path $parent | Out-Null
    }
    if (Test-Path $Dest) {
        Remove-Item -Recurse -Force $Dest
    }
    New-Item -ItemType Directory -Force -Path $Dest | Out-Null
    robocopy $Source $Dest /E /COPY:DAT /R:2 /W:2 /NFL /NDL /NJH /NJS | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw "Failed to copy profile to Docker storage (robocopy exit $LASTEXITCODE)."
    }
}

$venv = Join-Path $Root ".playwright-venv"
$activate = Join-Path $venv "Scripts\Activate.ps1"

$py = $null
foreach ($candidate in @("py -3.11", "python3.11", "python")) {
    try {
        $ver = Invoke-Expression "$candidate --version" 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) {
            $py = $candidate
            break
        }
    } catch { }
}
if (-not $py) {
    Write-Error "Python 3.11+ not found. Install Python 3.11 from python.org."
}

if (-not (Test-Path $activate)) {
    Write-Host "Creating Python venv at .playwright-venv (using $py) ..."
    Invoke-Expression "$py -m venv `"$venv`""
}
& $activate
python -m pip install --upgrade pip -q
pip install -r requirements.txt -q
playwright install chrome 2>$null | Out-Null

# Local profile: avoid Desktop/OneDrive — WhatsApp IndexedDB breaks there.
$localProfile = Join-Path $env:LOCALAPPDATA "SenderPlatform\mmp-whatsapp\account-$AccountId"
$dockerProfile = Join-Path $Root "storage\browser_profiles\whatsapp\account-$AccountId"

Stop-ChromeProcesses

if (-not $KeepProfile -and -not $CompleteOnly) {
    if (Test-Path $localProfile) {
        Write-Host "Removing old local profile: $localProfile"
        Remove-Item -Recurse -Force $localProfile
    }
    if (Test-Path $dockerProfile) {
        Write-Host "Removing old Docker profile: $dockerProfile"
        Remove-Item -Recurse -Force $dockerProfile
    }
}
New-Item -ItemType Directory -Force -Path $localProfile | Out-Null

$chrome = Find-ChromeExe
if (-not $chrome) {
    Write-Error "Google Chrome not found. Install from https://www.google.com/chrome/"
}

Write-Host ""
Write-Host "=== WhatsApp Web link (real Chrome) ===" -ForegroundColor Cyan
Write-Host "Local profile (QR here): $localProfile" -ForegroundColor Gray
Write-Host "Docker copy target:      $dockerProfile" -ForegroundColor Gray
Write-Host ""
Write-Host "NOTE: Profile is NOT on Desktop — OneDrive breaks WhatsApp storage." -ForegroundColor DarkYellow
Write-Host ""

if (-not $CompleteOnly) {
    Write-Host "Opening Chrome ..."
    Start-Process -FilePath $chrome -ArgumentList @(
        "--user-data-dir=$localProfile",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-features=TranslateUI",
        "https://web.whatsapp.com"
    )

    Write-Host ""
    Write-Host "If page stays on WhatsApp logo / loading bar:" -ForegroundColor DarkYellow
    Write-Host "  - Turn ON VPN/filter-breaker FIRST, then F5 refresh" -ForegroundColor DarkYellow
    Write-Host "  - Wait 1-2 minutes, then press F5 again" -ForegroundColor DarkYellow
    Write-Host "  - Click 'Action required' in Chrome toolbar if shown" -ForegroundColor DarkYellow
    Write-Host "  - Or use 'Link with phone number' on the WhatsApp page" -ForegroundColor DarkYellow
    Write-Host ""
    Write-Host "1) Scan QR OR use 'Link with phone number'" -ForegroundColor Yellow
    Write-Host "2) OK if stuck on 'Loading your chats' — check phone: Linked Devices must show this PC" -ForegroundColor Yellow
    Write-Host "3) Wait at least 3 minutes after scan, then CLOSE all Chrome windows" -ForegroundColor Yellow
    Write-Host "4) Press Enter here" -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter after closing Chrome"
    Stop-ChromeProcesses
} else {
    Write-Host "CompleteOnly: skipping Chrome — using existing profile on disk." -ForegroundColor DarkYellow
    if (-not (Test-Path (Join-Path $localProfile "Default"))) {
        Write-Error "No profile data under $localProfile. Run without -CompleteOnly and scan QR first."
    }
}

Write-Host "Verifying local profile ..."
python -m workers.whatsapp_web_link --account-id $AccountId --profile-dir $localProfile --probe-only
$probeOk = ($LASTEXITCODE -eq 0)
if (-not $probeOk) {
    Write-Host ""
    Write-Host "Playwright probe did not see chat list (common when UI stuck on Loading)." -ForegroundColor DarkYellow
    $answer = Read-Host "Did you scan QR successfully? (y/n)"
    if ($answer -notmatch '^[yY]') {
        Write-Error "Re-run script and complete QR / phone login first."
    }
    Write-Host "Continuing with profile files on disk (--skip-probe) ..." -ForegroundColor DarkYellow
}

Write-Host "Copying profile to Docker storage ..."
Sync-ProfileDirectory -Source $localProfile -Dest $dockerProfile

Write-Host "Registering session in database ..."
$registerArgs = @(
    "-m", "workers.whatsapp_web_link",
    "--account-id", $AccountId,
    "--register-from-profile",
    "--skip-probe"
)
if ($probeOk) {
    Write-Host "Local probe passed — skipping second probe on copied profile." -ForegroundColor DarkGreen
}
python @registerArgs
if ($LASTEXITCODE -ne 0) {
    Write-Error "Database registration failed (exit $LASTEXITCODE)."
}

Write-Host ""
Write-Host "Success. UI -> Accounts -> WhatsApp Web -> Refresh." -ForegroundColor Green
