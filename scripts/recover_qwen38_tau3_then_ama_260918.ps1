[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:18081/v1"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$tauRunner = Join-Path $PSScriptRoot "run_qwen38_tau3_canary_260917.ps1"
$amaRunner = Join-Path $PSScriptRoot "run_qwen38_ama_feasibility_260918.ps1"
$freeze = Join-Path $projectRoot "data\external_benchmark_freezes\feasibility_260918\tau3\development_feasibility_ids.jsonl"
$freezeHash = "acf30dad801576da8fed52a5f8fcbd511ea4f3e73fe51b02d7d4408d269a64c0"

# The first run produced valid Full History results for retail tasks
# 9, 12, 42, 50, and 65. Only retry the five retail infrastructure failures.
& $tauRunner `
    -ApiBase $ApiBase `
    -RunLabel "feasibility20_fh_260918_r2_recovery" `
    -Manager "full_trajectory" `
    -Budget "none" `
    -FreezePath $freeze `
    -ExpectedFreezeHash $freezeHash `
    -OnlyDomain "retail" `
    -OnlyTaskIds @("16", "43", "54", "67", "85") `
    -MaxRetries 2
if ($LASTEXITCODE -ne 0) {
    throw "tau3 Full History retail recovery failed with exit code $LASTEXITCODE"
}

# All ten airline Full History tasks were infrastructure failures.
& $tauRunner `
    -ApiBase $ApiBase `
    -RunLabel "feasibility20_fh_260918_r2_recovery" `
    -Manager "full_trajectory" `
    -Budget "none" `
    -FreezePath $freeze `
    -ExpectedFreezeHash $freezeHash `
    -OnlyDomain "airline" `
    -MaxRetries 2
if ($LASTEXITCODE -ne 0) {
    throw "tau3 Full History airline recovery failed with exit code $LASTEXITCODE"
}

# TraceGraph did not start in the interrupted run, so run all frozen tasks.
& $tauRunner `
    -ApiBase $ApiBase `
    -RunLabel "feasibility20_tg4096_260918_r2" `
    -Manager "full_ours" `
    -Budget "4096" `
    -FreezePath $freeze `
    -ExpectedFreezeHash $freezeHash `
    -MaxRetries 2
if ($LASTEXITCODE -ne 0) {
    throw "tau3 TraceGraph feasibility run failed with exit code $LASTEXITCODE"
}

& $amaRunner -ApiBase $ApiBase
if ($LASTEXITCODE -ne 0) {
    throw "AMA feasibility matrix failed with exit code $LASTEXITCODE"
}
