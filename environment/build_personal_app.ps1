[CmdletBinding()]
param(
    [ValidateSet('create','build','package','install','launch')]
    [string]$Stage = 'build',
    [Parameter(Mandatory)][string]$IdentityFile,
    [Parameter(Mandatory)][string]$ContentRoot,
    [string]$ToolchainRoot = (Join-Path $env:LOCALAPPDATA 'km'),
    [string]$Serial = 'emulator-5580',
    [string]$Python = ''
)
# A personal package from a clean public tree: the public code, the private content laid over it and the personal
# identity given as data. Both stay outside the tree; run_app.ps1 applies them to the build copy in the toolchain.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'clean-source-manifest.json'))) {
    throw 'Build from a clean public tree: the public checkout, or a fresh export of a development tree (tools/export_clean_source.py <new directory>).'
}
& (Join-Path $PSScriptRoot 'run_app.ps1') -Stage $Stage -ToolchainRoot $ToolchainRoot -Serial $Serial -Python $Python `
    -IdentityFile $IdentityFile -ContentRoot $ContentRoot
if ($LASTEXITCODE -ne 0) { throw 'Personal app build failed.' }
