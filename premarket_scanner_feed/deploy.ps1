# Deploy premarket_scanner_feed SAM stack (Windows / PowerShell).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}
Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"

$StackName = "premarket-scanner-feed"

Push-Location $PSScriptRoot
try {
  sam build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  if (-not (Test-Path "samconfig.toml")) {
    sam deploy --guided
  } else {
    sam deploy --no-confirm-changeset
  }
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  Write-Host ""
  aws cloudformation describe-stacks --stack-name $StackName --query "Stacks[0].Outputs" --output table
  Write-Host ""
  Write-Host "Sync Discord bot token:"
  Write-Host "  python tools/sync_discord_bot.py"
} finally {
  Pop-Location
}
