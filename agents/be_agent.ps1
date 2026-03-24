# -- Configuration --
$ANTHROPIC_API_KEY = $env:ANTHROPIC_API_KEY
$MCP_SERVER_URL    = "https://ornithologic-wiley-ficklely.ngrok-free.dev/mcp"
$MODEL = "claude-haiku-4-5-20251001"

# -- System Prompt (trimmed to save tokens) --
$systemPrompt = @"
You are a BE engineer on project BackOfficeBE (FastAPI + MongoDB + SQL Server).

Structure: app/endpoints.py, Handlers/, Accessors/DataConnections/, models/, utils/static_utils.py
Rules: Use MongoDataConnection wrapper, app_logger, Pydantic v2, never hardcode collection names.
Auth exists in authentication_handler.py - check before implementing auth features.

On every wake-up:
1. get_messages("BE") - answer all questions via reply_message
2. get_my_tasks("BE") - implement all pending tasks, call complete_task when done
3. Exit when finished
"@

# -- Build Request --
$body = @{
    model       = $MODEL
    max_tokens  = 2048
    system      = $systemPrompt
    messages    = @(@{ role = "user"; content = "Check messages and tasks. Handle everything." })
    mcp_servers = @(@{ type = "url"; url = $MCP_SERVER_URL; name = "agent-system" })
    tools       = @(@{ type = "mcp_toolset"; mcp_server_name = "agent-system" })
} | ConvertTo-Json -Depth 10

$bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

# -- Call with Retry --
function Invoke-BeAgent {
    $maxRetries = 3
    $retryWait  = 60

    for ($i = 0; $i -lt $maxRetries; $i++) {
        try {
            $response = Invoke-RestMethod `
                -Uri "https://api.anthropic.com/v1/messages" `
                -Method POST `
                -Headers @{
                    "x-api-key"         = $ANTHROPIC_API_KEY
                    "anthropic-version" = "2023-06-01"
                    "content-type"      = "application/json; charset=utf-8"
                    "anthropic-beta"    = "mcp-client-2025-11-20"
                } `
                -Body $bodyBytes
            return $response
        } catch {
            $statusCode = $_.Exception.Response.StatusCode.value__
            if ($statusCode -eq 429) {
                Write-Host "[BE Agent] Rate limited. Waiting $retryWait seconds..." -ForegroundColor Yellow
                Start-Sleep -Seconds $retryWait
            } else {
                $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
                Write-Host "[BE ERROR] $($_.Exception.Message)" -ForegroundColor Red
                Write-Host "[BE ERROR] $($reader.ReadToEnd())" -ForegroundColor Red
                return $null
            }
        }
    }
    Write-Host "[BE Agent] Max retries reached." -ForegroundColor Red
    return $null
}

# -- Run --
Write-Host "[BE Agent] Waking up..." -ForegroundColor Cyan

$response = Invoke-BeAgent

if ($null -ne $response) {
    foreach ($block in $response.content) {
        if ($block.type -eq "text") {
            Write-Host $block.text -ForegroundColor White
        } elseif ($block.type -eq "tool_use") {
            Write-Host "[TOOL] $($block.name): $($block.input | ConvertTo-Json -Compress)" -ForegroundColor Magenta
        }
    }
}

Write-Host "[BE Agent] Done." -ForegroundColor Cyan