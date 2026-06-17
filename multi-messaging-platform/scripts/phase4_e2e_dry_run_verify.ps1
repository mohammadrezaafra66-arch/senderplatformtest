# Phase 4 end-to-end dry-run verification — calls debug endpoints only.
# Does not push to Redis, start workers, or write to DB outside API endpoints.

$ErrorActionPreference = "Stop"
$BaseUrl = if ($env:MMP_BASE_URL) { $env:MMP_BASE_URL } else { "http://localhost:8001" }
$JsonContentType = "application/json; charset=utf-8"

function Write-Section($title) {
    Write-Output ""
    Write-Output "=== $title ==="
}

function Assert-Equal($label, $actual, $expected) {
    if ($actual -eq $expected) {
        Write-Output "OK   $label = $actual"
    } else {
        Write-Output "FAIL $label expected=$expected actual=$actual"
        $script:Failures++
    }
}

function Assert-True($label, $condition) {
    if ($condition) {
        Write-Output "OK   $label"
    } else {
        Write-Output "FAIL $label"
        $script:Failures++
    }
}

$Failures = 0
$Results = @{}

Write-Output "Phase 4 E2E Dry-Run Verification"
Write-Output "Base URL: $BaseUrl"

# 1. Safety
Write-Section "1. Safety check"
$safety = Invoke-RestMethod -Uri "$BaseUrl/debug/safety/status" -Method GET
$safety | ConvertTo-Json -Depth 10
$Results.safety = $safety
Assert-True "phase_4_debug_mode" ($safety.phase_4_debug_mode -eq $true)
Assert-True "real_queue_push_enabled=false" ($safety.real_queue_push_enabled -eq $false)
Assert-True "real_message_sending_enabled=false" ($safety.real_message_sending_enabled -eq $false)
Assert-True "worker_execution_enabled=false" ($safety.worker_execution_enabled -eq $false)
Assert-True "channel_connectors_enabled=false" ($safety.channel_connectors_enabled -eq $false)

# 2. Create campaign
Write-Section "2. Create campaign"
$campaignBody = @{
    name         = "تست نهایی فاز ۴ - واتساپ"
    channel      = "whatsapp"
    intent       = "sales_followup"
    message_goal = "معرفی چند محصول موجود با قیمت روز"
    daily_limit  = 20
    max_contacts = 100
} | ConvertTo-Json -Depth 10

$campaign = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/campaigns/create" `
    -Method POST `
    -ContentType $JsonContentType `
    -Body $campaignBody

$campaignId = $campaign.id
$Results.campaign_id = $campaignId
$campaign | ConvertTo-Json -Depth 20
Assert-True "campaign created" ($campaignId -gt 0)
Assert-Equal "campaign.status" $campaign.status "draft"

# 3. Import contacts
Write-Section "3. Import contacts"
$contactsBody = @{
    campaign_id = $campaignId
    contacts    = @(
        @{
            first_name     = "محمد"
            last_name      = "رضایی"
            phone          = "09120000000"
            consent_status = "allowed"
        },
        @{
            first_name     = "علی"
            last_name      = "احمدی"
            phone          = "09121111111"
            consent_status = "unknown"
        },
        @{
            first_name     = "رضا"
            last_name      = "کریمی"
            phone          = "09122222222"
            consent_status = "blocked"
        },
        @{
            first_name     = "محمد"
            last_name      = "تکراری"
            phone          = "09120000000"
            consent_status = "allowed"
        }
    )
} | ConvertTo-Json -Depth 20

$import = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/contacts/import-json" `
    -Method POST `
    -ContentType $JsonContentType `
    -Body $contactsBody

$Results.import = $import
$import | ConvertTo-Json -Depth 30
Assert-Equal "received_count" $import.received_count 4
Assert-Equal "imported_count" $import.imported_count 3
Assert-Equal "duplicate_count" $import.duplicate_count 1
Assert-Equal "invalid_count" $import.invalid_count 0
Assert-Equal "allowed_count" $import.allowed_count 1
Assert-Equal "unknown_count" $import.unknown_count 1
Assert-Equal "blocked_count (import)" $import.blocked_count 1

# 4. Preview contacts
Write-Section "4. Preview contacts"
$preview = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/contacts/latest?campaign_id=$campaignId&limit=10" `
    -Method GET

$Results.preview = $preview
if ($preview -is [System.Array]) {
    $previewList = @($preview)
} elseif ($preview.value) {
    $previewList = @($preview.value)
} else {
    $previewList = @($preview)
}
$previewList | ConvertTo-Json -Depth 30
Assert-Equal "preview count" $previewList.Count 3
foreach ($contact in $previewList) {
    Assert-True "phone normalized ($($contact.phone))" ($contact.phone -like "+98*")
    Assert-True "Persian name present ($($contact.full_name))" ($contact.full_name -match "[\u0600-\u06FF]")
}

# 5. Prepare messages (first run)
Write-Section "5. Prepare messages (first run)"
$prepareBody = @{
    force_mock_output = $true
    limit             = $null
} | ConvertTo-Json -Depth 10

$prepare1 = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/campaigns/$campaignId/prepare-messages" `
    -Method POST `
    -ContentType $JsonContentType `
    -Body $prepareBody

$Results.prepare1 = $prepare1
$prepare1 | ConvertTo-Json -Depth 40
Assert-Equal "total_contacts" $prepare1.total_contacts 3
Assert-Equal "allowed_contacts" $prepare1.allowed_contacts 1
Assert-Equal "skipped_contacts" $prepare1.skipped_contacts 2
Assert-Equal "staged_count" $prepare1.staged_count 1
Assert-Equal "ready_count" $prepare1.ready_count 1
Assert-True "real_gpt_called=false" ($prepare1.real_gpt_called -eq $false)
Assert-True "redis_queue_pushed=false" ($prepare1.redis_queue_pushed -eq $false)
Assert-True "product_snapshot_valid" ($prepare1.product_snapshot_valid -eq $true)
$item1 = $prepare1.items[0]
Assert-True "final_text Persian" ($item1.final_text -match "[\u0600-\u06FF]")
Assert-True "queue_payload.ready_for_queue" ($item1.queue_payload.ready_for_queue -eq $true)
Assert-True "queue_payload.real_queue_push_enabled=false" ($item1.queue_payload.real_queue_push_enabled -eq $false)
Assert-True "queue_payload.dry_run=true" ($item1.queue_payload.dry_run -eq $true)

# 6. Prepare messages (idempotency)
Write-Section "6. Prepare messages (second run / idempotency)"
$prepare2 = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/campaigns/$campaignId/prepare-messages" `
    -Method POST `
    -ContentType $JsonContentType `
    -Body $prepareBody

$Results.prepare2 = $prepare2
$prepare2 | ConvertTo-Json -Depth 40
Assert-True "already_staged_count >= 1" ($prepare2.already_staged_count -ge 1)
Assert-Equal "ready_count stable" $prepare2.ready_count $prepare1.ready_count
Assert-Equal "staged_count stable" $prepare2.staged_count $prepare1.staged_count

# 7. Staged messages
Write-Section "7. Staged messages listing"
$staged = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/campaigns/$campaignId/staged-messages" `
    -Method GET

$Results.staged = $staged
$staged | ConvertTo-Json -Depth 40
Assert-Equal "staged.total_contacts" $staged.total_contacts 3
Assert-Equal "staged.allowed_contacts" $staged.allowed_contacts 1
Assert-Equal "staged.skipped_contacts" $staged.skipped_contacts 2
Assert-Equal "staged.staged_count" $staged.staged_count 1
Assert-Equal "staged.ready_count" $staged.ready_count 1
Assert-Equal "staged items length" $staged.items.Count 1
$stagedItem = $staged.items[0]
Assert-True "staged final_text Persian" ($stagedItem.final_text -match "[\u0600-\u06FF]")
Assert-True "staged queue_payload.ready_for_queue" ($stagedItem.queue_payload.ready_for_queue -eq $true)
Assert-True "staged queue_payload.real_queue_push_enabled=false" ($stagedItem.queue_payload.real_queue_push_enabled -eq $false)
Assert-True "staged queue_payload.dry_run=true" ($stagedItem.queue_payload.dry_run -eq $true)

# 8. Queue status
Write-Section "8. Queue status"
$queue = Invoke-RestMethod `
    -Uri "$BaseUrl/debug/queue/status" `
    -Method GET

$Results.queue = $queue
$queue | ConvertTo-Json -Depth 30
Assert-True "queue real_queue_push_enabled=false" ($queue.real_queue_push_enabled -eq $false)
Assert-True "staged_items_count >= 1" ($queue.staged_items_count -ge 1)
Assert-True "ready_staged_items_count >= 1" ($queue.ready_staged_items_count -ge 1)

# 9. OpenAPI
Write-Section "9. OpenAPI route verification"
$openapi = Invoke-RestMethod -Uri "$BaseUrl/openapi.json"
$requiredRoutes = @(
    "/debug/safety/status",
    "/debug/campaigns/create",
    "/debug/campaigns/latest",
    "/debug/contacts/import-json",
    "/debug/contacts/latest",
    "/debug/campaigns/{campaign_id}/prepare-messages",
    "/debug/campaigns/{campaign_id}/staged-messages",
    "/debug/queue/status"
)
$missing = @()
foreach ($route in $requiredRoutes) {
    if ($openapi.paths.PSObject.Properties.Name -contains $route) {
        Write-Output "OK: $route"
    } else {
        Write-Output "MISSING: $route"
        $missing += $route
        $script:Failures++
    }
}
$Results.openapi_missing = $missing

# Summary
Write-Section "Summary"
Write-Output "Campaign ID: $campaignId"
Write-Output "Failures: $Failures"
if ($Failures -eq 0) {
    Write-Output "RESULT: PASS — Phase 4 E2E dry-run verification succeeded."
    exit 0
} else {
    Write-Output "RESULT: FAIL — see assertions above."
    exit 1
}
