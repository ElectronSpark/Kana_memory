[CmdletBinding()]
param([string]$ToolchainRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km'))
$ErrorActionPreference = 'Stop'
$sdkRoot = Join-Path $ToolchainRoot 'briefcase\tools\android_sdk'
$sdkManager = Join-Path $sdkRoot 'cmdline-tools\19.0\bin\sdkmanager.bat'
$javaRoot = Join-Path $ToolchainRoot 'briefcase\tools\java17'
$logs = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force -Path $logs | Out-Null
$review = Join-Path $logs 'sdk-license-review.txt'
$install = Join-Path $logs 'sdk-platform-tools-install.txt'
$oldJavaHome = $env:JAVA_HOME
try {
    $env:JAVA_HOME = $javaRoot
    if (-not (Test-Path -LiteralPath $sdkManager -PathType Leaf)) { throw 'Run the create stage once to install SDK command-line tools.' }
    $licenseFile = Join-Path $sdkRoot 'licenses\android-sdk-license'
    if (-not (Test-Path -LiteralPath $licenseFile -PathType Leaf)) {
        # Request only a normal SDK component, not all optional SDK licenses.
        'n' | & $sdkManager 'platform-tools' *> $review
        $reviewText = Get-Content -LiteralPath $review -Raw
        $licenseNames = @([regex]::Matches($reviewText, 'License\s+([a-z][a-z0-9-]+):') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
        if ($licenseNames.Count -ne 1 -or $licenseNames[0] -ne 'android-sdk-license') {
            throw 'Unexpected or unreadable license request; inspect sdk-license-review.txt before proceeding.'
        }
        'y' | & $sdkManager 'platform-tools' *> $install
        if ($LASTEXITCODE -ne 0) { throw 'SDK installation failed; inspect sdk-platform-tools-install.txt.' }
    }
    if (-not (Test-Path -LiteralPath $licenseFile -PathType Leaf)) { throw 'Android SDK license acceptance was not confirmed.' }
    [PSCustomObject]@{
        license_name = 'android-sdk-license'
        terms_url = 'https://developer.android.com/studio/terms'
        license_file = $licenseFile
        license_file_sha256 = (Get-FileHash -LiteralPath $licenseFile -Algorithm SHA256).Hash.ToLowerInvariant()
        review_file = $review
        review_sha256 = $(if (Test-Path -LiteralPath $review) { (Get-FileHash -LiteralPath $review -Algorithm SHA256).Hash.ToLowerInvariant() } else { $null })
        requested_component = 'platform-tools'
        accepted_other_sdk_licenses = $false
        scope = 'Standard Android SDK license for the user-authorized local environment setup.'
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'sdk_license_record.json') -Encoding utf8
    Write-Output 'Standard Android SDK license recorded; platform-tools requested. No other SDK licenses accepted.'
} finally { $env:JAVA_HOME = $oldJavaHome }
