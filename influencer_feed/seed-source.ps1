# Put example SOURCE row into DynamoDB (edit seed/example-source.json first).
# Usage: .\seed-source.ps1
#        .\seed-source.ps1 -TableName my-stack-influencer-feed
param(
  [string]$TableName = "influencer-feed-influencer-feed",
  [string]$ItemPath = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($env:AWS_PROFILE)) {
  $env:AWS_PROFILE = "mastalcup"
}

if ([string]::IsNullOrWhiteSpace($ItemPath)) {
  $ItemPath = Join-Path $PSScriptRoot "seed\example-source.json"
}
if (-not (Test-Path $ItemPath)) {
  Write-Error "Missing $ItemPath"
}

Write-Host "AWS_PROFILE=$($env:AWS_PROFILE)"
Write-Host "Table: $TableName"
Write-Host "Item file: $ItemPath"
Write-Host ""

$uri = "file://" + ((Resolve-Path $ItemPath).Path -replace "\\", "/")
aws dynamodb put-item --table-name $TableName --item $uri

Write-Host ""
Write-Host "Done. Edit the row in the console or run this again after changing the item file."
