$ErrorActionPreference = "Stop"
Set-Location (Resolve-Path "$PSScriptRoot\..\..")

if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    . ".\.venv\Scripts\Activate.ps1"
}

$env:EXECUTION_MODE = "paper"
$env:ALLOW_LIVE_TRADING = "false"
$env:BROKER_MODE = "mt5_demo"
$env:AUTO_BOT_ENABLED = "false"
$env:MT5_DEMO_ONLY = "true"
$env:MT5_SERVER = "Deriv-Demo"

python scripts\run_demo_bot.py --provider mt5 --broker mt5_demo
