[CmdletBinding()]
param(
    [string]$ApiBase = "http://127.0.0.1:18081/v1"
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $PSScriptRoot "run_ama_uniform_canary_260918.py"
$python = Join-Path $projectRoot "vendor\tau3-bench\.venv\Scripts\python.exe"
$freeze = Join-Path $projectRoot "data\external_benchmark_freezes\feasibility_260918\ama_bench\development_feasibility_ids.jsonl"
$freezeHash = "5c75c3898dc594b3d1709c9deb701166ae122be6fbcf8ee26fa4ed685afe5230"
$outputRoot = Join-Path $projectRoot "outputs\ama_feasibility_260918"

foreach ($method in @("full_history", "tracegraph_0_4")) {
    $output = Join-Path $outputRoot "${method}_b8192_i4096_q1_r1"
    $arguments = @(
        $runner,
        "--method", $method,
        "--output", $output,
        "--memory-budget", "8192",
        "--ingest-budget", "4096",
        "--freeze-path", $freeze,
        "--expected-freeze-hash", $freezeHash,
        "--expected-episode-count", "20",
        "--base-url", $ApiBase,
        "--continue-on-unsafe"
    )
    if (Test-Path -LiteralPath $output) {
        $arguments += "--resume"
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "AMA $method feasibility run failed with exit code $LASTEXITCODE"
    }
}
