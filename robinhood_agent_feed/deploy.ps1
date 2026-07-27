# Deploy robinhood_agent_feed SAM stack (Windows / PowerShell).
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}
Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"

$StackName = "robinhood-agent-feed"

Push-Location $PSScriptRoot
try {
  Push-Location lambdas/run_agent
  if (-not (Test-Path "node_modules")) {
    npm install
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
  Pop-Location

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
} finally {
  Pop-Location
}
