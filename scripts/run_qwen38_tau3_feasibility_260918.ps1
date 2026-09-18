[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:18081/v1"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $PSScriptRoot "run_qwen38_tau3_canary_260917.ps1"
$freeze = Join-Path $projectRoot "data\external_benchmark_freezes\feasibility_260918\tau3\development_feasibility_ids.jsonl"
$freezeHash = "acf30dad801576da8fed52a5f8fcbd511ea4f3e73fe51b02d7d4408d269a64c0"

& $runner `
    -ApiBase $ApiBase `
    -RunLabel "feasibility20_fh_260918_r1" `
    -Manager "full_trajectory" `
    -Budget "none" `
    -FreezePath $freeze `
    -ExpectedFreezeHash $freezeHash
if ($LASTEXITCODE -ne 0) {
    throw "tau3 Full History feasibility run failed with exit code $LASTEXITCODE"
}

& $runner `
    -ApiBase $ApiBase `
    -RunLabel "feasibility20_tg4096_260918_r1" `
    -Manager "full_ours" `
    -Budget "4096" `
    -FreezePath $freeze `
    -ExpectedFreezeHash $freezeHash
if ($LASTEXITCODE -ne 0) {
    throw "tau3 TraceGraph feasibility run failed with exit code $LASTEXITCODE"
}

& (Join-Path $PSScriptRoot "run_qwen38_ama_feasibility_260918.ps1") `
    -ApiBase $ApiBase
if ($LASTEXITCODE -ne 0) {
    throw "AMA feasibility matrix failed with exit code $LASTEXITCODE"
}
