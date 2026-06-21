# Enable or disable WhatsApp Web sending at runtime (Redis kill switch).
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_send_kill_switch.ps1 -Enable
#   powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_send_kill_switch.ps1 -Disable
#   powershell -ExecutionPolicy Bypass -File .\scripts\whatsapp_send_kill_switch.ps1 -Status
#
# Also set WHATSAPP_SENDING_DISABLED=true in .env and restart workers for env-level stop.

param(
    [switch]$Enable,
    [switch]$Disable,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$Key = "system:whatsapp_send_disabled"
$BaileysKey = "whatsapp:kill_switch"

if (-not $Enable -and -not $Disable -and -not $Status) {
    Write-Host "Usage: -Enable | -Disable | -Status" -ForegroundColor Yellow
    exit 1
}

if ($Status) {
    $val = docker exec mmp_redis redis-cli GET $Key 2>$null
    $bval = docker exec mmp_redis redis-cli GET $BaileysKey 2>$null
    $on = ($val -match "true") -or ($bval -match "true")
    Write-Host "Redis keys: $Key, $BaileysKey" -ForegroundColor Cyan
    Write-Host "WhatsApp send kill switch: $(if ($on) { 'ON (blocked)' } else { 'OFF (allowed)' })" -ForegroundColor $(if ($on) { 'Red' } else { 'Green' })
    exit 0
}

$value = if ($Enable) { "true" } else { "false" }
docker exec mmp_redis redis-cli SET $Key $value | Out-Null
docker exec mmp_redis redis-cli SET $BaileysKey $value | Out-Null
Write-Host "WhatsApp send kill switch set to: $value" -ForegroundColor $(if ($Enable) { 'Red' } else { 'Green' })
Write-Host "UI live sends and worker/script sends are blocked when true." -ForegroundColor Gray
