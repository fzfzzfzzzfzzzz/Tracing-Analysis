[CmdletBinding()]
param(
    [string]$Destination = "vendor/acon-main"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$vendorRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "vendor"))
$destinationPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Destination))
if (-not $destinationPath.StartsWith($vendorRoot + [System.IO.Path]::DirectorySeparatorChar)) {
    throw "Destination must remain inside $vendorRoot"
}
if (Test-Path -LiteralPath $destinationPath) {
    throw "Destination already exists; refusing to replace it: $destinationPath"
}

$sha = "d63f9ae18959dc7215ff62899c94c5e8c56847ae"
$download = Join-Path $vendorRoot "acon-$sha.zip"
$expandRoot = Join-Path $vendorRoot "acon-$sha-extracted"
if (Test-Path -LiteralPath $expandRoot) {
    throw "Temporary extraction path already exists: $expandRoot"
}

New-Item -ItemType Directory -Force -Path $vendorRoot | Out-Null
$downloadArgs = @{
    Uri = "https://codeload.github.com/microsoft/acon/zip/$sha"
    OutFile = $download
}
Invoke-WebRequest @downloadArgs
Expand-Archive -LiteralPath $download -DestinationPath $expandRoot
$expanded = Get-ChildItem -LiteralPath $expandRoot -Directory
if ($expanded.Count -ne 1) {
    throw "Expected one source directory in the official archive"
}
Move-Item -LiteralPath $expanded[0].FullName -Destination $destinationPath

$python = Join-Path $repoRoot "vendor/tau3-bench/.venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}
$env:PYTHONPATH = Join-Path $repoRoot "src"
@"
from pathlib import Path
from tracegraph.integrations.acon import load_official_acon_adapter
adapter = load_official_acon_adapter(
    config_path=Path(r'$repoRoot') / 'configs' / 'acon_tau3.json',
    source_root=Path(r'$destinationPath'),
)
assert adapter.provenance['source_manifest_verified'] is True
print(adapter.provenance['source_snapshot_sha'])
"@ | & $python -

Write-Host "Verified official ACON snapshot at $destinationPath"
