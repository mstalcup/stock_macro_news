# SAM build only (Windows). Uses same default AWS profile as deploy.ps1 when unset.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}
Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"

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
  exit $LASTEXITCODE
} finally {
  Pop-Location
}
