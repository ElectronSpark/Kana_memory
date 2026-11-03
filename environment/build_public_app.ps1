[CmdletBinding()]
param(
    [ValidateSet('create','build','package','install','launch','desktop')]
    [string]$Stage = 'build',
    [string]$ToolchainRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km'),
    [string]$Serial = 'emulator-5580',
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'clean-source-manifest.json'))) {
    throw 'Build from a fresh clean-source export. Do not package the personal working directory as the public demo.'
}
& (Join-Path $PSScriptRoot 'run_app.ps1') -Stage $Stage -ToolchainRoot $ToolchainRoot -Serial $Serial -Python $Python -PublicBuild
if ($LASTEXITCODE -ne 0) { throw 'Public app build failed.' }
