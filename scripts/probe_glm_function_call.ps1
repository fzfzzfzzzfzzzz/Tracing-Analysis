param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Model,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing local .env."
}
git -C $projectRoot -c "safe.directory=$($projectRoot.Replace('\', '/'))" check-ignore -q .env
if ($LASTEXITCODE -ne 0) {
    throw ".env is not ignored by Git; refusing to load credentials."
}

$localVariables = @{}
Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^([^#][^=]*)=(.*)$') {
        $localVariables[$matches[1].Trim()] = $matches[2].Trim()
    }
}
$apiKey = $localVariables["ZAI_API_KEY"]
$apiBase = $localVariables["ZAI_API_BASE"]
if ([string]::IsNullOrWhiteSpace($apiKey)) {
    throw "ZAI_API_KEY is empty in .env."
}
if ([string]::IsNullOrWhiteSpace($apiBase)) {
    $apiBase = "https://open.bigmodel.cn/api/paas/v4"
}
$endpoint = "$($apiBase.TrimEnd('/'))/chat/completions"

$body = @{
    model = $Model
    messages = @(
        @{
            role = "system"
            content = "This is a deterministic API capability check. Call the required function exactly once."
        },
        @{
            role = "user"
            content = "Report probe status ok using the required function."
        }
    )
    tools = @(
        @{
            type = "function"
            function = @{
                name = "tracegraph_probe"
                description = "Report deterministic function-call availability."
                parameters = @{
                    type = "object"
                    properties = @{
                        status = @{ type = "string"; enum = @("ok") }
                    }
                    required = @("status")
                    additionalProperties = $false
                }
            }
        }
    )
    tool_choice = @{
        type = "function"
        function = @{ name = "tracegraph_probe" }
    }
    temperature = 0.0
    max_tokens = 64
    stream = $false
    thinking = @{ type = "disabled" }
}

if ($DryRun) {
    [pscustomobject]@{
        model = $Model
        endpoint = $endpoint
        credential_loaded = $true
        api_call = $false
        expected_tool = "tracegraph_probe"
    }
    return
}

try {
    $response = Invoke-RestMethod `
        -Method Post `
        -Uri $endpoint `
        -Headers @{ Authorization = "Bearer $apiKey" } `
        -ContentType "application/json; charset=utf-8" `
        -Body ($body | ConvertTo-Json -Compress -Depth 12)
    $choice = $response.choices[0]
    $calls = @($choice.message.tool_calls)
    $matchingCalls = @($calls | Where-Object { $_.function.name -eq "tracegraph_probe" })
    $usage = $response.usage
    [pscustomobject]@{
        model = $Model
        available = $true
        expected_tool_called = ($matchingCalls.Count -eq 1)
        tool_call_count = $calls.Count
        finish_reason = $choice.finish_reason
        prompt_tokens = $usage.prompt_tokens
        completion_tokens = $usage.completion_tokens
        total_tokens = $usage.total_tokens
    }
    if ($matchingCalls.Count -ne 1) {
        exit 3
    }
} catch {
    $caught = $_
    $statusCode = $null
    $rawError = $null
    if ($null -ne $caught.Exception.Response) {
        $statusCode = [int]$caught.Exception.Response.StatusCode
        try {
            $stream = $caught.Exception.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $rawError = $reader.ReadToEnd()
            $reader.Dispose()
        } catch {
            $rawError = $null
        }
    }
    $providerCode = $null
    $providerMessage = "request rejected"
    if ([string]::IsNullOrWhiteSpace($rawError)) {
        $rawError = $caught.ErrorDetails.Message
    }
    if (-not [string]::IsNullOrWhiteSpace($rawError)) {
        try {
            $details = $rawError | ConvertFrom-Json
            if ($null -ne $details.error) {
                $providerCode = $details.error.code
                $providerMessage = $details.error.message
            } else {
                $providerCode = $details.code
                $providerMessage = $details.message
            }
        } catch {
            $providerMessage = "provider returned a non-JSON error"
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($providerMessage)) {
        $providerMessage = $providerMessage.Replace($apiKey, "[REDACTED]")
    }
    [pscustomobject]@{
        model = $Model
        available = $false
        http_status = $statusCode
        provider_code = $providerCode
        provider_message = $providerMessage
    }
    exit 2
}
