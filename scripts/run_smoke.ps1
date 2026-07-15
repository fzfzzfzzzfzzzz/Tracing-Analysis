param(
    [string]$Python = "python",
    [string]$Root = "outputs\smoke",
    [int]$Budget = 100
)

$ErrorActionPreference = "Stop"
$env:PYTHONPATH = "src"
& $Python -m tracegraph make-synthetic `
    --output (Join-Path $Root "graphs\synthetic.json") `
    --archive (Join-Path $Root "archive")
& $Python -m tracegraph validate-trace (Join-Path $Root "graphs\synthetic.json")
& $Python -m tracegraph run-offline `
    --input (Join-Path $Root "graphs") `
    --output (Join-Path $Root "results") `
    --archive (Join-Path $Root "archive") `
    --budget $Budget `
    --provenance synthetic_cli_smoke
& $Python -m tracegraph verify-archive (Join-Path $Root "archive")
