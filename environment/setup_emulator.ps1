[CmdletBinding()]
param(
    [string]$ToolchainRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km'),
    [switch]$Start,
    [switch]$Visible,
    [ValidateRange(5554,5680)][int]$Port = 5580
)
$ErrorActionPreference = 'Stop'
$ToolchainRoot = [IO.Path]::GetFullPath($ToolchainRoot)
if ($ToolchainRoot -match '[^\x00-\x7F]|\s' -or $ToolchainRoot.Length -gt 65) {
    throw 'Choose a short ASCII ToolchainRoot without spaces.'
}
if ($Port % 2 -ne 0) { throw 'Emulator console port must be even.' }
$sdkRoot = Join-Path $ToolchainRoot 'briefcase\tools\android_sdk'
$sdkManager = Join-Path $sdkRoot 'cmdline-tools\19.0\bin\sdkmanager.bat'
$avdManager = Join-Path $sdkRoot 'cmdline-tools\19.0\bin\avdmanager.bat'
$emulator = Join-Path $sdkRoot 'emulator\emulator.exe'
$avdHome = Join-Path $ToolchainRoot 'avd'
$avdName = 'KanaMemory_API36'
$image = 'system-images;android-36;google_apis;x86_64'
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logs,$avdHome | Out-Null
$originalJava = $env:JAVA_HOME
$originalSdk = $env:ANDROID_HOME
$originalAvd = $env:ANDROID_AVD_HOME
try {
    $env:JAVA_HOME = Join-Path $ToolchainRoot 'briefcase\tools\java17'
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_AVD_HOME = $avdHome
    if (-not (Test-Path -LiteralPath (Join-Path $sdkRoot 'licenses\android-sdk-license'))) {
        throw 'Run prepare_android_sdk.ps1 to review and record the standard SDK license first.'
    }
    $imageMetadata = Join-Path $sdkRoot 'system-images\android-36\google_apis\x86_64\source.properties'
    $imageDirectory = Split-Path -Parent $imageMetadata
    if (-not (Test-Path -LiteralPath $imageMetadata) -and
            (Test-Path -LiteralPath (Join-Path $imageDirectory 'package.xml')) -and
            (Test-Path -LiteralPath (Join-Path $imageDirectory 'system.img')) -and
            (Test-Path -LiteralPath (Join-Path $imageDirectory 'kernel-ranchu'))) {
        $imageMetadata = Join-Path $imageDirectory 'package.xml'
    }
    if (-not (Test-Path -LiteralPath $imageMetadata)) {
        # Existing standard SDK license is accepted; decline any additional terms.
        'n' | & $sdkManager $image *> (Join-Path $logs 'emulator-image-install.txt')
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $imageMetadata)) {
            throw 'Image installation incomplete. Inspect logs/emulator-image-install.txt.'
        }
    }
    $avdIni = Join-Path $avdHome "$avdName.ini"
    if (-not (Test-Path -LiteralPath $avdIni)) {
        'no' | & $avdManager create avd --name $avdName --package $image --path (Join-Path $avdHome "$avdName.avd") --device 'pixel_7' *> (Join-Path $logs 'emulator-avd-create.txt')
        if ($LASTEXITCODE -ne 0) { throw 'AVD creation failed; inspect logs/emulator-avd-create.txt.' }
    }
    if ($Start) {
        $adb = Join-Path $sdkRoot 'platform-tools\adb.exe'
        $devices = & $adb devices
        if ($devices -match "^emulator-$Port\s") {
            throw "Port $Port is already occupied by an emulator. Use that device or choose another port."
        }
        $arguments = @('-avd',$avdName,'-port',"$Port",'-no-snapshot','-no-boot-anim','-gpu','swiftshader','-memory','3072')
        if (-not $Visible) { $arguments += '-no-window' }
        $process = Start-Process -FilePath $emulator -ArgumentList $arguments -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logs 'emulator-stdout.txt') -RedirectStandardError (Join-Path $logs 'emulator-stderr.txt')
        Write-Output "Emulator process $($process.Id), serial emulator-$Port. Check sys.boot_completed before installing an APK."
    }
    Write-Output "AVD ready: $avdName; files: $avdHome; image: $image"
} finally {
    $env:JAVA_HOME = $originalJava
    $env:ANDROID_HOME = $originalSdk
    $env:ANDROID_AVD_HOME = $originalAvd
}
