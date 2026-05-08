# Deploy influencer_feed SAM stack (Windows / PowerShell).
# Requires: AWS SAM CLI, AWS credentials configured.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Personal default profile (only if not already set in this session).
if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}
Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"

$StackName = "influencer-feed"

Push-Location $PSScriptRoot
try {
  try {
    sam --version | Out-Null
  } catch {
    Write-Error "Install AWS SAM CLI: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html"
  }

  $vendorDir = Join-Path $PSScriptRoot "lambdas\ingest_source\vendor"
  if (Test-Path $vendorDir) {
    Remove-Item -Recurse -Force $vendorDir
  }
  New-Item -ItemType Directory -Path $vendorDir | Out-Null
  py -m pip install youtube-transcript-api --target $vendorDir --upgrade --quiet
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  $vendorDir2 = Join-Path $PSScriptRoot "lambdas\fetch_source_transcripts\vendor"
  if (Test-Path $vendorDir2) {
    Remove-Item -Recurse -Force $vendorDir2
  }
  New-Item -ItemType Directory -Path $vendorDir2 | Out-Null
  py -m pip install youtube-transcript-api --target $vendorDir2 --upgrade --quiet
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  sam build
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  if (-not (Test-Path "samconfig.toml")) {
    Write-Host "First deploy: running sam deploy --guided (creates samconfig.toml)..."
    sam deploy --guided
  } else {
    sam deploy --no-confirm-changeset
  }

  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

  Write-Host ""
  Write-Host "Stack outputs:"
  aws cloudformation describe-stacks --stack-name $StackName --query "Stacks[0].Outputs" --output table
} finally {
  Pop-Location
}
