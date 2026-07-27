$ErrorActionPreference = "Stop"
$env:AWS_PROFILE = if ($env:AWS_PROFILE) { $env:AWS_PROFILE } else { "mastalcup" }
Set-Location $PSScriptRoot

# Bundle cross-feed signal_matrix into publish_discord Lambda
$src = Join-Path $PSScriptRoot "..\signal_matrix\signal_matrix"
$dest = Join-Path $PSScriptRoot "lambdas\publish_discord\signal_matrix"
if (Test-Path $src) {
    Remove-Item -Recurse -Force $dest -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item -Recurse -Force "$src\*" $dest
}

sam build
sam deploy
