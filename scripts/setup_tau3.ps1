param(
    [string]$Target = "vendor\tau3-bench",
    [string]$Uv = ""
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
if (-not $Uv) {
    $localUv = Join-Path $PSScriptRoot "..\.venv\Scripts\uv.exe"
    if (Test-Path -LiteralPath $localUv) {
        $Uv = (Resolve-Path -LiteralPath $localUv).Path
    } else {
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
        if (-not $uvCommand) {
            throw "uv is required by current tau3-bench. Install it from https://docs.astral.sh/uv/."
        }
        $Uv = $uvCommand.Source
    }
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath $Target)) {
    git clone https://github.com/sierra-research/tau2-bench.git $Target
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone tau3-bench (exit code $LASTEXITCODE)."
    }
}
& $Uv sync --project $Target
if ($LASTEXITCODE -ne 0) {
    throw "uv sync failed (exit code $LASTEXITCODE)."
}
$resolvedTarget = (Resolve-Path -LiteralPath $Target).Path
$tauPython = Join-Path $resolvedTarget ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $tauPython)) {
    throw "tau3-bench Python was not created at $tauPython."
}
& $Uv pip install --python $tauPython -e $projectRoot
if ($LASTEXITCODE -ne 0) {
    throw "TraceGraph editable install failed (exit code $LASTEXITCODE)."
}
& $Uv run --project $Target tau2 check-data
if ($LASTEXITCODE -ne 0) {
    throw "tau2 check-data failed (exit code $LASTEXITCODE)."
}
