[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:18080/v1",
    [string]$Model = "hosted_vllm/Qwen3.8-27B-rev-1d4bf0f",
    [string]$RunLabel = "260918_provider_retry_v2",
    [string]$FreezePath = "",
    [string]$ExpectedFreezeHash = "",
    [ValidateSet("full_trajectory", "full_ours", "last_k", "token_length_pruning")]
    [string]$Manager = "full_trajectory",
    [string]$Budget = "none",
    [int]$LastK = 8,
    [double]$AgentTemperature = 1.0,
    [double]$UserTemperature = 0.6,
    [ValidateRange(0, 10)]
    [int]$MaxRetries = 0,
    [ValidateSet("", "retail", "airline")]
    [string]$OnlyDomain = "",
    [string[]]$OnlyTaskIds = @(),
    [switch]$MockOnly,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tauPython = Join-Path $projectRoot "vendor\tau3-bench\.venv\Scripts\python.exe"
$defaultFreezePath = Join-Path $projectRoot "data\external_benchmark_freezes\tau3_260917\canary_task_ids.jsonl"
$resolvedFreezePath = if ($FreezePath) {
    (Resolve-Path -LiteralPath $FreezePath).Path
} else {
    $defaultFreezePath
}
if (-not (Test-Path -LiteralPath $tauPython)) {
    throw "Missing tau3 environment: $tauPython"
}
if (-not (Test-Path -LiteralPath $resolvedFreezePath)) {
    throw "Missing frozen tau3 task list: $resolvedFreezePath"
}

$resolvedExpectedFreezeHash = if ($ExpectedFreezeHash) {
    $ExpectedFreezeHash.ToLowerInvariant()
} else {
    "914bc655d23bf5ff94d54ddb7414a6ff285a1a5348afc128aa79a52f83c37937"
}
$actualFreezeHash = (Get-FileHash -LiteralPath $resolvedFreezePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualFreezeHash -ne $resolvedExpectedFreezeHash) {
    throw "Frozen tau3 canary task list hash mismatch: $actualFreezeHash"
}

$models = Invoke-RestMethod -Uri ($ApiBase.TrimEnd("/") + "/models") -TimeoutSec 15
if ($models.data.id -notcontains "Qwen3.8-27B-rev-1d4bf0f") {
    throw "Qwen3.8-27B endpoint is not ready at $ApiBase"
}

$env:OPENAI_API_BASE = $ApiBase
$env:OPENAI_API_KEY = "EMPTY"
$env:TRACEGRAPH_MANAGER = $Manager
$env:TRACEGRAPH_BUDGET = $Budget
$env:TRACEGRAPH_LAST_K = [string]$LastK
$env:TRACEGRAPH_TOKEN_ACCOUNTING = "content_estimate_v2"
$env:TRACEGRAPH_TAU_NL_EVALUATOR_MODEL = $Model
$managerTag = switch ($Manager) {
    "full_trajectory" { "fh" }
    "full_ours" { "tg" }
    "last_k" { "rk" }
    "token_length_pruning" { "tp" }
}

$agentArgsJson = (@{
    temperature = $AgentTemperature
    top_p = 0.95
    max_tokens = 8192
    api_base = $ApiBase
    api_key = "EMPTY"
    extra_body = @{
        top_k = 20
        min_p = 0
        chat_template_kwargs = @{ enable_thinking = $true }
    }
} | ConvertTo-Json -Compress -Depth 6)

$userArgsJson = (@{
    temperature = $UserTemperature
    top_p = 0.95
    max_tokens = 4096
    api_base = $ApiBase
    api_key = "EMPTY"
    extra_body = @{
        top_k = 20
        min_p = 0
        chat_template_kwargs = @{ enable_thinking = $true }
    }
} | ConvertTo-Json -Compress -Depth 6)

function Invoke-TauRun {
    param(
        [string]$Domain,
        [string[]]$TaskIds,
        [string]$SaveTo,
        [string]$TraceOutput
    )
    $env:TRACEGRAPH_OUTPUT_DIR = $TraceOutput
    $arguments = @(
        (Join-Path $projectRoot "scripts\tau3_cli.py"),
        "run",
        "--domain", $Domain,
        "--task-set-name", $Domain,
        "--task-ids"
    )
    $arguments += $TaskIds
    $arguments += @(
        "--agent", "tracegraph_agent",
        "--agent-llm", $Model,
        "--agent-llm-args", $agentArgsJson,
        "--user", "tracegraph_user_simulator",
        "--user-llm", $Model,
        "--user-llm-args", $userArgsJson,
        "--num-trials", "1",
        "--max-steps", "50",
        "--max-errors", "5",
        "--timeout", "900",
        "--save-to", $SaveTo,
        "--max-concurrency", "1",
        "--seed", "20260917",
        "--max-retries", [string]$MaxRetries,
        "--log-level", "INFO",
        "--verbose-logs",
        "--llm-log-mode", "all",
        "--auto-resume"
    )
    Write-Host "tau3 $Domain tasks=$($TaskIds -join ',') save_to=$SaveTo"
    if ($DryRun) {
        return
    }
    & $tauPython @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "tau3 run failed for $Domain (exit code $LASTEXITCODE)"
    }
}

Push-Location $projectRoot
try {
    Invoke-TauRun `
        -Domain "mock" `
        -TaskIds @("create_task_1") `
        -SaveTo "q38x_mock_${managerTag}_$RunLabel" `
        -TraceOutput "outputs/tau3_external_canary_$RunLabel/mock_$Manager"

    if (-not $DryRun) {
        $mockPath = Join-Path $projectRoot (
            "vendor\tau3-bench\data\simulations\q38x_mock_${managerTag}_$RunLabel\results.json"
        )
        $mock = Get-Content -LiteralPath $mockPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($mock.simulations.Count -ne 1) {
            throw "Qwen3.8 tau3 mock harness gate failed: expected exactly one simulation"
        }
        $mockSimulation = $mock.simulations[0]
        $matchingCall = @(
            $mockSimulation.messages |
                Where-Object { $_.tool_calls } |
                ForEach-Object { $_.tool_calls } |
                Where-Object {
                    $_.name -eq "create_task" -and
                    $_.arguments.user_id -eq "user_1" -and
                    $_.arguments.title -eq "Important Meeting"
                }
        )
        $successfulResult = @(
            $mockSimulation.messages |
                Where-Object { $_.role -eq "tool" -and $_.error -ne $true } |
                Where-Object {
                    try {
                        $payload = $_.content | ConvertFrom-Json
                        $payload.title -eq "Important Meeting" -and $payload.status -eq "pending"
                    } catch {
                        $false
                    }
                }
        )
        if ($matchingCall.Count -eq 0 -or $successfulResult.Count -eq 0) {
            throw "Qwen3.8 tau3 semantic mock harness gate failed; frozen canary tasks remain untouched"
        }
        if ($mockSimulation.reward_info.reward -ne 1.0) {
            Write-Warning (
                "tau3 strict mock reward is 0 although the required create_task call succeeded; " +
                "continuing because the mismatch is limited to optional action arguments"
            )
        }
    }

    if ($MockOnly) {
        return
    }

    $frozen = Get-Content -LiteralPath $resolvedFreezePath -Encoding UTF8 |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        ForEach-Object { $_ | ConvertFrom-Json }
    $domains = if ($OnlyDomain) { @($OnlyDomain) } else { @("retail", "airline") }
    foreach ($domain in $domains) {
        $ids = @($frozen | Where-Object { $_.domain -eq $domain } | ForEach-Object { $_.task_id })
        if ($OnlyTaskIds.Count -gt 0) {
            $unknownIds = @($OnlyTaskIds | Where-Object { $_ -notin $ids })
            if ($unknownIds.Count -gt 0) {
                throw "Requested task IDs are not in the frozen $domain canary: $($unknownIds -join ',')"
            }
            $ids = @($ids | Where-Object { $_ -in $OnlyTaskIds })
        }
        if ($ids.Count -eq 0) {
            throw "No frozen task IDs selected for $domain"
        }
        Invoke-TauRun `
            -Domain $domain `
            -TaskIds $ids `
            -SaveTo "q38x_${domain}_${managerTag}_$RunLabel" `
            -TraceOutput "outputs/tau3_external_canary_$RunLabel/${domain}_$Manager"
    }
} finally {
    Pop-Location
}
