param(
    [string]$BootstrapPython = 'C:\Python312\python.exe',
    [string]$ToolRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km')
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
$toolPath = [IO.Path]::GetFullPath($ToolRoot)
if ($toolPath -match '[^\x00-\x7F]|\s') {
    throw 'Python ToolRoot must be a local ASCII path without spaces. The separate Android SDK build root must also be short.'
}
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE : $Executable" }
}
New-Item -ItemType Directory -Force -Path $toolPath | Out-Null
$cachePath = Join-Path $toolPath 'uv-cache'
$pythonPath = Join-Path $toolPath 'python'
$bootstrapPath = Join-Path $projectRoot '.bootstrap'
$venvPath = Join-Path $projectRoot '.venv'
$uvPath = Join-Path $bootstrapPath 'Scripts\uv.exe'
if (-not (Test-Path -LiteralPath $uvPath)) {
    Invoke-Checked $BootstrapPython @('-X','utf8','-m','venv','--without-pip',$bootstrapPath)
    $bootstrapTarget = Join-Path $bootstrapPath 'Scripts\python.exe'
    Invoke-Checked $BootstrapPython @('-X','utf8','-m','pip','--python',$bootstrapTarget,'install','uv==0.13.0','--disable-pip-version-check')
}
Invoke-Checked $uvPath @('python','install','3.13.16','--install-dir',$pythonPath,'--no-bin','--no-registry','--cache-dir',$cachePath)
$managedPython = Join-Path $pythonPath 'cpython-3.13.16-windows-x86_64-none\python.exe'
if (-not (Test-Path -LiteralPath $managedPython)) { throw "Managed interpreter not found: $managedPython" }
if (-not (Test-Path -LiteralPath (Join-Path $venvPath 'Scripts\python.exe'))) {
    Invoke-Checked $uvPath @('venv','--python',$managedPython,'--cache-dir',$cachePath,$venvPath)
}
$targetPython = Join-Path $venvPath 'Scripts\python.exe'
$interpreterCheck = & $targetPython -X utf8 -c 'import sys,json; print(json.dumps({"version":list(sys.version_info[:3]),"base":sys.base_prefix}))'
if ($LASTEXITCODE -ne 0) { throw 'Existing .venv cannot start; preserve it and choose another project environment.' }
$interpreterState = $interpreterCheck | ConvertFrom-Json
if (($interpreterState.version -join '.') -ne '3.13.16') { throw 'Existing .venv is not Python 3.13.16; it was not changed.' }
$expectedBase = [IO.Path]::GetFullPath((Split-Path $managedPython -Parent)).TrimEnd('\')
$actualBase = [IO.Path]::GetFullPath($interpreterState.base).TrimEnd('\')
if ($actualBase -ne $expectedBase) { throw "Existing .venv uses $actualBase. Re-run with the ToolRoot used to create it; it was not replaced." }
$requirements = Join-Path $PSScriptRoot 'windows-py313.lock.txt'
Invoke-Checked $uvPath @('pip','sync','--python',$targetPython,'--cache-dir',$cachePath,'--link-mode','copy',$requirements)
Invoke-Checked $targetPython @('-X','utf8',(Join-Path $projectRoot 'tools/build_environment.py'),'--host')
Invoke-Checked $targetPython @('-X','utf8','-c','import sys,sqlite3,importlib.metadata as m; print(sys.version); print({p:m.version(p) for p in ["briefcase","toga","fsrs","jsonschema"]}); print(sqlite3.sqlite_version)')
Write-Output "Preparation environment ready: $targetPython"
Write-Output 'No machine PATH, registry Python registration or system Java settings were changed.'
