# Start FindContent state machine using seed/run-input.json, then poll until done.
param(
  [string]$Region = "us-east-1",
  [string]$StackName = "influencer-feed",
  [string]$InputPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}

if ([string]::IsNullOrWhiteSpace($InputPath)) {
  $InputPath = Join-Path $PSScriptRoot "seed\run-input.json"
}
if (-not (Test-Path $InputPath)) {
  Write-Error "Missing input file: $InputPath"
}

$smArn = aws cloudformation describe-stacks --stack-name $StackName --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='FindContentStateMachineArn'].OutputValue" --output text
if ([string]::IsNullOrWhiteSpace($smArn)) {
  Write-Error "Could not resolve FindContentStateMachineArn from stack $StackName"
}

$filePath = (Resolve-Path $InputPath).Path -replace "\\", "/"
$inputUri = "file://$filePath"
$name = "manual-" + (Get-Date -Format "yyyyMMdd-HHmmss")

Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"
Write-Host "State machine: $smArn"
Write-Host "Input: $InputPath"
Write-Host ""

$start = aws stepfunctions start-execution --state-machine-arn $smArn --region $Region `
  --name $name --input $inputUri --output json | ConvertFrom-Json
$execArn = $start.executionArn
Write-Host "Started: $execArn"
Write-Host ""

$status = "RUNNING"
$n = 0
while ($status -eq "RUNNING" -and $n -lt 120) {
  Start-Sleep -Seconds 2
  $n++
  $d = aws stepfunctions describe-execution --execution-arn $execArn --region $Region --output json | ConvertFrom-Json
  $status = $d.status
  Write-Host "[$n] $status"
}

$d = aws stepfunctions describe-execution --execution-arn $execArn --region $Region --output json | ConvertFrom-Json
Write-Host ""
Write-Host "Final: $($d.status)"
if ($d.PSObject.Properties.Name -contains "output" -and $d.output) { Write-Host "Output: $($d.output)" }
if ($d.PSObject.Properties.Name -contains "error" -and $d.error) { Write-Host "Error: $($d.error)"; Write-Host $d.cause }

if ($d.status -ne "SUCCEEDED") { exit 1 }
