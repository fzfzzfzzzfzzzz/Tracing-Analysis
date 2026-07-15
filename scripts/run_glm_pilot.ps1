param(
    [ValidateSet("mock", "airline", "retail", "telecom", "telecom-workflow", "banking_knowledge")]
    [string]$Domain = "mock",
    [string]$TaskSetName = "",
    [string]$TaskId = "create_task_1",
    [ValidateSet(
        "full_trajectory",
        "last_k",
        "token_length_pruning",
        "summary_only",
        "llm_only_pruning",
        "agentdiet_style",
        "acon_style",
        "ours_without_graph_edges",
        "ours_without_lifecycle_states",
        "ours_without_failure_retention",
        "ours_without_constraint_retention",
        "full_ours"
    )]
    [string]$Manager = "full_trajectory",
    [string]$Budget = "none",
    [string]$AgentModel = "",
    [string]$UserModel = "",
    [int]$AgentMaxTokens = 512,
    [int]$UserMaxTokens = 256,
    [int]$NumTrials = 1,
    [int]$MaxSteps = 30,
    [int]$Seed = 300,
    [int]$TimeoutSeconds = 600,
    [string]$SaveTo = "",
    [string]$TraceOutputDir = "",
    [switch]$VerboseLogs,
    [switch]$NormalizeUserStop,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$envFile = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envFile)) {
    throw "Missing local .env. Copy .env.example and set ZAI_API_KEY locally."
}

git -C $projectRoot -c "safe.directory=$($projectRoot.Replace('\', '/'))" check-ignore -q .env
if ($LASTEXITCODE -ne 0) {
    throw ".env is not ignored by Git; refusing to load credentials."
}

Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    if ($_ -match '^([^#][^=]*)=(.*)$') {
        $name = $matches[1].Trim()
        $value = $matches[2].Trim()
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

if ([string]::IsNullOrWhiteSpace($env:ZAI_API_KEY)) {
    throw "ZAI_API_KEY is empty in .env."
}
if ([string]::IsNullOrWhiteSpace($env:ZAI_API_BASE)) {
    $env:ZAI_API_BASE = "https://open.bigmodel.cn/api/paas/v4"
}
if ([string]::IsNullOrWhiteSpace($AgentModel)) {
    $AgentModel = $env:TRACEGRAPH_AGENT_MODEL
}
if ([string]::IsNullOrWhiteSpace($UserModel)) {
    $UserModel = $env:TRACEGRAPH_USER_MODEL
}
if ([string]::IsNullOrWhiteSpace($AgentModel) -or [string]::IsNullOrWhiteSpace($UserModel)) {
    throw "Set AgentModel/UserModel or TRACEGRAPH_AGENT_MODEL/TRACEGRAPH_USER_MODEL."
}
if ([string]::IsNullOrWhiteSpace($TaskSetName)) {
    $TaskSetName = $Domain
}

$tauPython = Join-Path $projectRoot "vendor\tau3-bench\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $tauPython)) {
    throw "Missing official tau3 environment. Run scripts/setup_tau3.ps1 first."
}

$safeTaskId = $TaskId -replace '[^A-Za-z0-9_-]', '_'
if ([string]::IsNullOrWhiteSpace($SaveTo)) {
    $SaveTo = "tracegraph_glm_${Domain}_${safeTaskId}_${Manager}"
}
if ([string]::IsNullOrWhiteSpace($TraceOutputDir)) {
    $TraceOutputDir = "outputs/tau3_live/${Domain}_${safeTaskId}_${Manager}"
}
$env:TRACEGRAPH_MANAGER = $Manager
$env:TRACEGRAPH_BUDGET = $Budget
$env:TRACEGRAPH_OUTPUT_DIR = $TraceOutputDir
$userImplementation = if ($NormalizeUserStop) {
    "tracegraph_user_simulator"
} else {
    "user_simulator"
}

# LiteLLM 1.81.11 does not expose GLM's `thinking` as a top-level OpenAI
# parameter. `extra_body` is the compatible route and prevents reasoning tokens
# from exhausting the output cap before a tool call is emitted.
$agentArgsJson = (@{
    temperature = 0.0
    max_tokens = $AgentMaxTokens
    extra_body = @{ thinking = @{ type = "disabled" } }
} | ConvertTo-Json -Compress -Depth 5).Replace('"', '\"')
$userArgsJson = (@{
    temperature = 0.0
    max_tokens = $UserMaxTokens
    extra_body = @{ thinking = @{ type = "disabled" } }
} | ConvertTo-Json -Compress -Depth 5).Replace('"', '\"')

$arguments = @(
    (Join-Path $projectRoot "scripts\tau3_cli.py"),
    "run",
    "--domain", $Domain,
    "--task-set-name", $TaskSetName,
    "--task-ids", $TaskId,
    "--agent", "tracegraph_agent",
    "--agent-llm", $AgentModel,
    "--agent-llm-args", $agentArgsJson,
    "--user", $userImplementation,
    "--user-llm", $UserModel,
    "--user-llm-args", $userArgsJson,
    "--num-trials", $NumTrials.ToString(),
    "--max-steps", $MaxSteps.ToString(),
    "--max-errors", "5",
    "--timeout", $TimeoutSeconds.ToString(),
    "--save-to", $SaveTo,
    "--max-concurrency", "1",
    "--seed", $Seed.ToString(),
    "--max-retries", "0",
    "--log-level", "INFO"
)
if ($VerboseLogs) {
    $arguments += @("--verbose-logs", "--llm-log-mode", "all")
}

$runConfig = [pscustomobject]@{
    domain = $Domain
    task_set = $TaskSetName
    task_id = $TaskId
    manager = $Manager
    budget = $Budget
    agent_model = $AgentModel
    user_model = $UserModel
    user_implementation = $userImplementation
    seed = $Seed
    trials = $NumTrials
    max_steps = $MaxSteps
    save_to = $SaveTo
    trace_output = $TraceOutputDir
}
$runConfig
if ($DryRun) {
    return
}

Push-Location $projectRoot
try {
    & $tauPython @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "tau3 GLM pilot failed (exit code $LASTEXITCODE)."
    }
} finally {
    Pop-Location
}
