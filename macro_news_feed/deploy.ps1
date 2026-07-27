# Deploy macro-news-feed SAM stack (us-east-1, profile mastalcup by default)
$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { "mastalcup" }
Set-Location $PSScriptRoot
sam build
sam deploy
