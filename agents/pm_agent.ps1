param(
    [Parameter(Mandatory=$true)]
    [string]$FeatureRequest,
    [Parameter(Mandatory=$false)]
    [switch]$Interactive
)

# -- Configuration --
$ANTHROPIC_API_KEY = $env:ANTHROPIC_API_KEY
$MCP_SERVER_URL    = "https://ornithologic-wiley-ficklely.ngrok-free.dev/mcp"
$MODEL             = "claude-sonnet-4-5-20250929"

# -- System Prompt --
if ($Interactive) {
$systemPrompt = @"
You are a senior Project Manager overseeing a software development team.
Your team consists of:
  - BE (Backend Engineer): handles APIs, databases, server logic, authentication
  - FE (Frontend Engineer): handles UI components, pages, user interactions, styling

FIRST THING YOU MUST DO:
- Call get_all_status to check existing tasks before proposing anything
- If similar tasks already exist with status 'pending' or 'done', inform the user
- Only propose tasks for genuinely new work

INTERACTIVE APPROVAL MODE:
1. Check existing tasks with get_all_status
2. Prepare task breakdown for NEW work only
3. Present proposed tasks clearly:

PROPOSED TASKS:

BE Task:
[detailed description]

FE Task:
[detailed description]

4. Ask: Do you approve these tasks? (yes / modify / cancel)
5. Do NOT call post_task until user types yes or approve
6. If user says modify, revise and ask again
7. If user says cancel, stop
8. After approval call post_task for each task
9. Then call trigger_agent for each assigned agent

Stack context:
- BE uses FastAPI + MongoDB, Handler/Accessor pattern, Pydantic v2 models
- Always include specific endpoint names, field names, and data structures
"@
} else {
    $systemPrompt = @"
You are a senior Project Manager overseeing a software development team.
Your team consists of:
  - BE (Backend Engineer): handles APIs, databases, server logic, authentication
  - FE (Frontend Engineer): handles UI components, pages, user interactions, styling

Your responsibilities:
1. Receive a feature request
2. Break it down into specific tasks for BE and FE
3. Post each task using post_task
4. Check status with get_all_status
5. Summarize when done
## Communication
- Use send_message to ask BE or FE questions
- Use get_messages to check for replies from BE or FE
- After triggering an agent, wait then check get_messages for their response
- Always relay agent responses back to the user clearly
```

## How to handle questions to BE/FE:
1. Send message to BE/FE using send_message
2. Call trigger_agent to wake them up
3. Wait by calling get_messages("PM") in a loop
4. When a reply arrives, report it to the user
5. NEVER ask the user to wait manually - handle the polling yourself

Rules:
- Always post a BE task AND a FE task
- Be specific - include endpoint names, component names, data structures
"@
}

# -- Function to call Claude API --
function Invoke-Claude($messages) {
    $body = @{
        model       = $MODEL
        max_tokens  = 4096
        system      = $systemPrompt
        messages    = $messages
        mcp_servers = @(
            @{
                type = "url"
                url  = $MCP_SERVER_URL
                name = "agent-system"
            }
        )
        tools       = @(
            @{
                type            = "mcp_toolset"
                mcp_server_name = "agent-system"
            }
        )
    } | ConvertTo-Json -Depth 10

    # Force UTF-8 encoding
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($body)

    try {
        return Invoke-RestMethod `
            -Uri "https://api.anthropic.com/v1/messages" `
            -Method POST `
            -Headers @{
                "x-api-key"         = $ANTHROPIC_API_KEY
                "anthropic-version" = "2023-06-01"
                "content-type"      = "application/json; charset=utf-8"
                "anthropic-beta"    = "mcp-client-2025-11-20"
            } `
            -Body $bodyBytes
    } catch {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        Write-Host "[ERROR] $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "[API ERROR] $($reader.ReadToEnd())" -ForegroundColor Red
        return $null
    }
}
# -- Main --
Write-Host ""
Write-Host "[PM Agent] Starting..." -ForegroundColor Cyan
Write-Host "[PM Agent] Feature: $FeatureRequest" -ForegroundColor Yellow
if ($Interactive) {
    Write-Host "[PM Agent] Interactive mode ON - you approve before tasks are posted" -ForegroundColor Yellow
}
Write-Host ""

# -- Conversation history --
$messages = @(@{ role = "user"; content = $FeatureRequest })

while ($true) {
    $response = Invoke-Claude $messages
    if ($null -eq $response) { break }

    # -- Display response --
    foreach ($block in $response.content) {
        if ($block.type -eq "text") {
            Write-Host $block.text -ForegroundColor White
        } elseif ($block.type -eq "tool_use") {
            Write-Host "[TOOL CALL] $($block.name)" -ForegroundColor Magenta
            Write-Host "  $($block.input | ConvertTo-Json -Compress)" -ForegroundColor Gray
        }
    }

    # -- Add assistant turn to history --
    $messages += @{ role = "assistant"; content = $response.content }

    # -- If Claude finished talking, get user input (interactive) or stop --
    if ($response.stop_reason -eq "end_turn") {
        if ($Interactive) {
            Write-Host ""
            Write-Host "You: " -ForegroundColor Cyan -NoNewline
            $userInput = Read-Host
            if ($userInput -eq "" -or $userInput -eq "exit" -or $userInput -eq "quit") {
                Write-Host "[PM Agent] Exiting." -ForegroundColor Yellow
                break
            }
            $messages += @{ role = "user"; content = $userInput }
        } else {
            break
        }
    } else {
        break
    }
}

Write-Host ""
Write-Host "[PM Agent] Done." -ForegroundColor Green