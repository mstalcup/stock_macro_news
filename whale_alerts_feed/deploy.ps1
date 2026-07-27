Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}
Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"

Push-Location $PSScriptRoot
try {
  sam build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  sam deploy --no-confirm-changeset
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  aws cloudformation describe-stacks --stack-name whale-alerts-feed --query "Stacks[0].Outputs" --output table
} finally {
  Pop-Location
}
