[CmdletBinding()]
param(
    [string]$ToolchainRoot = (Join-Path $env:LOCALAPPDATA 'Temp/km')
)
$ErrorActionPreference = 'Stop'
$sdkPath = Join-Path $ToolchainRoot 'briefcase/tools/android_sdk'
$javaPath = Join-Path $ToolchainRoot 'briefcase/tools/java17/bin'
$platformPath = Join-Path $sdkPath 'platforms/android-36'
$androidJar = Join-Path $platformPath 'android.jar'
$automationJar = Join-Path $platformPath 'uiautomator.jar'
$testBaseJar = Join-Path $platformPath 'optional/android.test.base.jar'
$dexCompiler = Join-Path $sdkPath 'build-tools/36.0.0/lib/d8.jar'
$sourcePath = Join-Path $PSScriptRoot 'src/qa/UnicodeInput.java'
$buildPath = Join-Path $PSScriptRoot 'build'
$classesPath = Join-Path $buildPath 'classes'
$dexPath = Join-Path $buildPath 'dex'
$temporaryPath = Join-Path $buildPath 'tmp'
$classJar = Join-Path $buildPath 'unicode-input-classes.jar'
$outputJar = Join-Path $buildPath 'unicode-input.jar'
foreach ($dependency in @($androidJar, $automationJar, $testBaseJar, $dexCompiler, $sourcePath)) {
    if (-not (Test-Path -LiteralPath $dependency -PathType Leaf)) {
        throw "Required existing toolchain input is missing: $dependency"
    }
}
New-Item -ItemType Directory -Force -Path $buildPath,$classesPath,$dexPath,$temporaryPath | Out-Null
$classPath = @($androidJar,$automationJar,$testBaseJar) -join [IO.Path]::PathSeparator
# Relative ASCII arguments avoid the Windows Java launcher losing non-ASCII
# workspace path characters when the active command code page is not UTF-8.
Push-Location -LiteralPath $PSScriptRoot
try {
    & (Join-Path $javaPath 'javac.exe') -encoding UTF-8 --release 8 -classpath $classPath -d 'build/classes' 'src/qa/UnicodeInput.java'
    if ($LASTEXITCODE -ne 0) { throw 'Java compilation failed.' }
    & (Join-Path $javaPath 'jar.exe') '-J-Djava.io.tmpdir=build/tmp' cf 'build/unicode-input-classes.jar' -C 'build/classes' qa
    if ($LASTEXITCODE -ne 0) { throw 'Class packaging failed.' }
    & (Join-Path $javaPath 'java.exe') '-Djava.io.tmpdir=build/tmp' -cp $dexCompiler com.android.tools.r8.D8 --min-api 26 --lib $androidJar --classpath $automationJar --classpath $testBaseJar --output 'build/dex' 'build/unicode-input-classes.jar'
    if ($LASTEXITCODE -ne 0) { throw 'Dex compilation failed.' }
    & (Join-Path $javaPath 'jar.exe') '-J-Djava.io.tmpdir=build/tmp' cf 'build/unicode-input.jar' -C 'build/dex' classes.dex
    if ($LASTEXITCODE -ne 0) { throw 'Dex packaging failed.' }
} finally {
    Pop-Location
}
Get-FileHash -LiteralPath $outputJar -Algorithm SHA256
Write-Output $outputJar
