param(
    [int]$PollSeconds = 30,
    [int]$TimeoutHours = 24
)

$ErrorActionPreference = "Stop"

$Workspace = Split-Path -Parent $PSScriptRoot
$RetailResult = Join-Path $Workspace "vendor\tau3-bench\data\simulations\q38x_retail_tg_feasibility20_tg4096_260918_r1\results.json"
$AirlineResult = Join-Path $Workspace "vendor\tau3-bench\data\simulations\q38x_airline_tg_feasibility20_tg4096_260918_r1\results.json"
$AmaRunner = Join-Path $PSScriptRoot "run_qwen38_ama_feasibility_260918.ps1"
$Deadline = (Get-Date).AddHours($TimeoutHours)

function Get-CompletedSimulationCount {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return 0
    }

    try {
        $Payload = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
        if ($null -eq $Payload.simulations) {
            return 0
        }
        return @($Payload.simulations).Count
    }
    catch {
        return 0
    }
}

while ((Get-Date) -lt $Deadline) {
    $RetailCount = Get-CompletedSimulationCount -Path $RetailResult
    $AirlineCount = Get-CompletedSimulationCount -Path $AirlineResult

    Write-Host "[$(Get-Date -Format o)] tau3 progress: retail=$RetailCount/10 airline=$AirlineCount/10"

    if ($RetailCount -ge 10 -and $AirlineCount -ge 10) {
        Write-Host "Tau3 TraceGraph run completed; starting AMA feasibility matrix."
        & $AmaRunner
        exit $LASTEXITCODE
    }

    Start-Sleep -Seconds $PollSeconds
}

throw "Timed out waiting for tau3 TraceGraph results after $TimeoutHours hours."
