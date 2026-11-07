[CmdletBinding()]
param(
    [ValidateSet('create', 'build', 'package')]
    [string]$Stage = 'create',
    [string]$ToolchainRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km')
)
$ErrorActionPreference = 'Stop'
$ToolchainRoot = [IO.Path]::GetFullPath($ToolchainRoot)
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
$probeSource = Join-Path $PSScriptRoot 'probe_source'
$probeBuild = Join-Path $ToolchainRoot 'probe'
if (-not (Test-Path -LiteralPath $projectPython -PathType Leaf)) {
    throw 'Project Python environment missing; complete bootstrap first.'
}
if ($probeBuild -match '[^\x00-\x7F]|\s' -or $ToolchainRoot.Length -gt 65) {
    throw 'The Android build root must be a short ASCII path without spaces (maximum 65 characters for SDK extraction on this host).'
}
New-Item -ItemType Directory -Force -Path $probeBuild | Out-Null
Copy-Item -LiteralPath (Join-Path $probeSource 'pyproject.toml') -Destination $probeBuild -Force
Copy-Item -LiteralPath (Join-Path $probeSource 'src') -Destination $probeBuild -Recurse -Force

# Process-local environment only. No persistent PATH or Java settings are changed.
$oldBriefcaseHome = $env:BRIEFCASE_HOME
$oldGradleHome = $env:GRADLE_USER_HOME
$oldPythonUtf8 = $env:PYTHONUTF8
$oldPythonIo = $env:PYTHONIOENCODING
$oldBuildPython = $env:KANA_BUILD_PYTHON
try {
    # Briefcase resolves drive aliases; SDK extraction needs a genuinely short
    # physical path on hosts where Windows long paths are disabled.
    $env:BRIEFCASE_HOME = Join-Path $ToolchainRoot 'briefcase'
    $env:GRADLE_USER_HOME = Join-Path $ToolchainRoot 'gradle'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:KANA_BUILD_PYTHON = $projectPython
    Push-Location -LiteralPath $probeBuild
    try {
        if ($Stage -eq 'package') {
            & $projectPython -X utf8 -m briefcase package android --no-input --log -p debug-apk
        } else {
            & $projectPython -X utf8 -m briefcase $Stage android --no-input --log
        }
        if ($LASTEXITCODE -ne 0) { throw "Briefcase $Stage exited with code $LASTEXITCODE" }
        if ($Stage -eq 'package') {
            $artifactDirectory = Join-Path $PSScriptRoot 'artifacts'
            New-Item -ItemType Directory -Force -Path $artifactDirectory | Out-Null
            $builtApk = Join-Path $probeBuild 'dist\KanaProbe-0.0.1.debug.apk'
            Copy-Item -LiteralPath $builtApk -Destination $artifactDirectory -Force
            Get-FileHash -LiteralPath (Join-Path $artifactDirectory 'KanaProbe-0.0.1.debug.apk') -Algorithm SHA256
        }
    } finally { Pop-Location }
} finally {
    $env:BRIEFCASE_HOME = $oldBriefcaseHome
    $env:GRADLE_USER_HOME = $oldGradleHome
    $env:PYTHONUTF8 = $oldPythonUtf8
    $env:PYTHONIOENCODING = $oldPythonIo
    $env:KANA_BUILD_PYTHON = $oldBuildPython
}
