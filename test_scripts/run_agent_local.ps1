# PowerShell script to run the AI agent
# API keys are loaded from .env file

Write-Host "Running Carbon-Aware Serverless Scheduler..."
Write-Host "API keys loaded from .env file"
Write-Host ""

$scriptPath = Join-Path $PSScriptRoot "run_agent.py"
python "$scriptPath"
