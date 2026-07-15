param(
    [string]$Python = "python",
    [string]$Venv = ".venv"
)

$ErrorActionPreference = "Stop"
& $Python -m venv $Venv
$venvPython = Join-Path $Venv "Scripts\python.exe"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -e ".[dev,analysis]"
& $venvPython -m unittest discover -s tests -v
