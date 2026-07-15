param(
    [string]$Target = "vendor\tau3-bench"
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is required by current tau3-bench. Install it from https://docs.astral.sh/uv/."
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not (Test-Path -LiteralPath $Target)) {
    git clone https://github.com/sierra-research/tau2-bench.git $Target
}
uv sync --project $Target
$tauPython = (uv run --project $Target python -c "import sys; print(sys.executable)").Trim()
uv pip install --python $tauPython -e $projectRoot
uv run --project $Target tau2 check-data
