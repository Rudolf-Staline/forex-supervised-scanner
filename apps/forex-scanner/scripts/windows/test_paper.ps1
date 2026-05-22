$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$env:EXECUTION_MODE = "paper"
$env:ALLOW_LIVE_TRADING = "false"
$env:BROKER_MODE = "paper"
$env:AUTO_BOT_ENABLED = "false"

python scripts\health_check.py
python scripts\run_one_cycle.py --provider synthetic --broker paper
