[CmdletBinding()]
param(
    [ValidateSet('create','build','package','install','launch','desktop')]
    [string]$Stage = 'build',
    [string]$ToolchainRoot = (Join-Path ([IO.Path]::GetTempPath()) 'km'),
    [string]$Serial = 'emulator-5580',
    [switch]$PublicBuild,
    [string]$Python = ''
)
$ErrorActionPreference = 'Stop'

function Get-PersonalAudioPolicy {
    param([string]$Python, [string]$Database, [string]$AppBuildRoot)
    $readPolicy = @'
import json, sqlite3, sys
from pathlib import Path
database = Path(sys.argv[1]).resolve()
if len(sys.argv) > 2 and sys.argv[2] and Path(sys.argv[2]).resolve() == database.parents[3]:
    raise SystemExit('Generated app directory resolves to the source app directory.')
with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
    row = db.execute("SELECT value FROM metadata WHERE key='audio_policy'").fetchone()
    policy = json.loads(row[0]) if row else {}
print('true' if policy.get('personal_audio_included') is True else 'false')
'@
    $policyText = & $Python -X utf8 -c $readPolicy $Database $AppBuildRoot
    if ($LASTEXITCODE -ne 0 -or "$policyText" -notin @('true', 'false')) {
        throw 'Cannot read source content audio policy. Run tools/build_app_content.py first.'
    }
    return "$policyText" -eq 'true'
}

function Assert-SafeGeneratedPath {
    param([string]$Path, [string]$AppRoot)
    $rootPath = [IO.Path]::GetFullPath($AppRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)
    $fullPath = [IO.Path]::GetFullPath($Path)
    if ($fullPath -ne $rootPath -and -not $fullPath.StartsWith(
            $rootPath + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Generated path escapes the exact app build root: $fullPath"
    }
    # AppRoot is the explicitly selected generated checkout boundary. Check it
    # and every relative component for reparse points, never following a link
    # out of that root. Its already-selected ancestors may include OneDrive's
    # normal Documents junction; they are not relative cleanup targets.
    $cursorPath = $fullPath
    while ($cursorPath) {
        $entry = Get-Item -LiteralPath $cursorPath -Force -ErrorAction SilentlyContinue
        if ($entry -and ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Generated resource path contains a link or junction: $cursorPath"
        }
        if ($cursorPath -eq $rootPath) { break }
        $cursorPath = [IO.Path]::GetDirectoryName($cursorPath)
    }
    return $fullPath
}

function Remove-PublicBuildPersonalAudio {
    param([string]$AppBuildRoot, [bool]$PersonalAudioIncluded)
    if ($PersonalAudioIncluded) { return 0 }
    $rootPath = Assert-SafeGeneratedPath -Path $AppBuildRoot -AppRoot $AppBuildRoot
    $relativeMedia = @(
        'src/kanamemory/resources/media',
        'build/kanamemory/android/gradle/app/src/main/python/kanamemory/resources/media',
        'build/kanamemory/android/gradle/app/build/python/sources/debug/kanamemory/resources/media',
        'build/kanamemory/android/gradle/app/build/python/sources/release/kanamemory/resources/media'
    )
    $pending = @()
    foreach ($relative in $relativeMedia) {
        $mediaPath = Assert-SafeGeneratedPath -Path (Join-Path $rootPath $relative) -AppRoot $rootPath
        if (-not (Test-Path -LiteralPath $mediaPath)) { continue }
        if (-not (Test-Path -LiteralPath $mediaPath -PathType Container)) { throw "Media directory is not a directory: $mediaPath" }
        # No recursion and no SDK-wide search: only these four generated copies.
        foreach ($entry in @(Get-ChildItem -LiteralPath $mediaPath -Filter 'chinese-qieyun-*.mp3' -Force)) {
            $targetPath = Assert-SafeGeneratedPath -Path $entry.FullName -AppRoot $rootPath
            if ($entry.PSIsContainer -or [IO.Path]::GetDirectoryName($targetPath) -ne $mediaPath) {
                throw "Unsafe personal audio cleanup target: $targetPath"
            }
            $pending += $targetPath
        }
    }
    # Validate the complete set before deleting the first file, then recheck at use.
    foreach ($targetPath in $pending) {
        $checkedPath = Assert-SafeGeneratedPath -Path $targetPath -AppRoot $rootPath
        Remove-Item -LiteralPath $checkedPath -Force
    }
    return $pending.Count
}

function Get-SafeResourceFiles {
    param([string]$ResourceRoot, [string]$Boundary)
    $resourcePath = Assert-SafeGeneratedPath -Path $ResourceRoot -AppRoot $Boundary
    $files = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    if (-not (Test-Path -LiteralPath $resourcePath)) { return ,$files }
    if (-not (Test-Path -LiteralPath $resourcePath -PathType Container)) {
        throw "Resource root is not a directory: $resourcePath"
    }
    $directories = [Collections.Generic.Queue[string]]::new()
    $directories.Enqueue($resourcePath)
    while ($directories.Count -gt 0) {
        $directory = $directories.Dequeue()
        [void](Assert-SafeGeneratedPath -Path $directory -AppRoot $Boundary)
        # Do not use -Recurse: validate every child before entering a directory.
        foreach ($entry in @(Get-ChildItem -LiteralPath $directory -Force)) {
            $checked = [IO.Path]::GetFullPath($entry.FullName)
            if ([IO.Path]::GetDirectoryName($checked) -ne $directory -or -not $checked.StartsWith(
                    $resourcePath.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar,
                    [StringComparison]::OrdinalIgnoreCase)) {
                throw "Resource child escapes the validated directory: $checked"
            }
            # Ancestors were just checked at dequeue. Inspect each child once,
            # rather than re-reading the same deep Gradle ancestors per asset.
            $actual = Get-Item -LiteralPath $checked -Force -ErrorAction Stop
            if ($actual.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Resource path contains a link or junction: $checked"
            }
            if ($actual.PSIsContainer) {
                $directories.Enqueue($checked)
            } else {
                $relative = $checked.Substring($resourcePath.TrimEnd([IO.Path]::DirectorySeparatorChar).Length + 1)
                $files.Add($relative, $checked)
            }
        }
    }
    return ,$files
}

function Remove-StaleGeneratedResources {
    param([string]$AppBuildRoot, [string]$SourceResources)
    $rootPath = Assert-SafeGeneratedPath -Path $AppBuildRoot -AppRoot $AppBuildRoot
    $sourcePath = Assert-SafeGeneratedPath -Path $SourceResources -AppRoot $SourceResources
    $separator = [IO.Path]::DirectorySeparatorChar
    $rootPath = $rootPath.TrimEnd($separator)
    $sourcePath = $sourcePath.TrimEnd($separator)
    if ($rootPath -eq $sourcePath -or $sourcePath.StartsWith($rootPath + $separator, [StringComparison]::OrdinalIgnoreCase) -or
            $rootPath.StartsWith($sourcePath + $separator, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source resources and generated app root must be separate, non-overlapping directories.'
    }
    if (-not (Test-Path -LiteralPath $sourcePath -PathType Container)) {
        throw 'Source resources are missing; refusing to remove generated resources.'
    }
    $sourceFiles = Get-SafeResourceFiles -ResourceRoot $sourcePath -Boundary $sourcePath
    $relativeResources = @(
        'src/kanamemory/resources',
        'build/kanamemory/android/gradle/app/src/main/python/kanamemory/resources',
        'build/kanamemory/android/gradle/app/build/python/sources/debug/kanamemory/resources',
        'build/kanamemory/android/gradle/app/build/python/sources/release/kanamemory/resources'
    )
    $pending = [Collections.Generic.List[string]]::new()
    foreach ($relative in $relativeResources) {
        $resourcePath = Assert-SafeGeneratedPath -Path (Join-Path $rootPath $relative) -AppRoot $rootPath
        $generatedFiles = Get-SafeResourceFiles -ResourceRoot $resourcePath -Boundary $rootPath
        foreach ($entry in $generatedFiles.GetEnumerator()) {
            if (-not $sourceFiles.ContainsKey($entry.Key)) { $pending.Add($entry.Value) }
        }
    }
    # All four inventories must pass before deleting anything. Retain empty
    # directories and remove only individually checked files, never subtrees.
    foreach ($targetPath in $pending) {
        $checkedPath = Assert-SafeGeneratedPath -Path $targetPath -AppRoot $rootPath
        if (-not (Test-Path -LiteralPath $checkedPath -PathType Leaf)) {
            throw "Resource cleanup target changed after validation: $checkedPath"
        }
        Remove-Item -LiteralPath $checkedPath -Force
    }
    return $pending.Count
}

function Get-SafeModuleFiles {
    param([string]$PackageRoot, [string]$Boundary)
    $packagePath = Assert-SafeGeneratedPath -Path $PackageRoot -AppRoot $Boundary
    $files = [Collections.Generic.Dictionary[string,string]]::new([StringComparer]::OrdinalIgnoreCase)
    if (-not (Test-Path -LiteralPath $packagePath)) { return ,$files }
    if (-not (Test-Path -LiteralPath $packagePath -PathType Container)) { throw "Python package is not a directory: $packagePath" }
    # Runtime modules are flat. Inspect only the package and its Python cache;
    # never enter resources, native code, dependencies or arbitrary subtrees.
    foreach ($directory in @($packagePath, (Join-Path $packagePath '__pycache__'))) {
        $checkedDirectory = Assert-SafeGeneratedPath -Path $directory -AppRoot $Boundary
        if (-not (Test-Path -LiteralPath $checkedDirectory)) { continue }
        if (-not (Test-Path -LiteralPath $checkedDirectory -PathType Container)) { throw "Python cache is not a directory: $checkedDirectory" }
        foreach ($entry in @(Get-ChildItem -LiteralPath $checkedDirectory -Force)) {
            if ($entry.Extension -notin @('.py', '.pyc')) { continue }
            $checked = Assert-SafeGeneratedPath -Path $entry.FullName -AppRoot $Boundary
            if ($entry.PSIsContainer -or [IO.Path]::GetDirectoryName($checked) -ne $checkedDirectory) {
                throw "Unsafe Python module cleanup target: $checked"
            }
            $relative = $checked.Substring($packagePath.TrimEnd([IO.Path]::DirectorySeparatorChar).Length + 1)
            $files.Add($relative, $checked)
        }
    }
    return ,$files
}

function Remove-StaleGeneratedModules {
    param([string]$AppBuildRoot, [string]$SourcePackage)
    $rootPath = Assert-SafeGeneratedPath -Path $AppBuildRoot -AppRoot $AppBuildRoot
    $sourcePath = Assert-SafeGeneratedPath -Path $SourcePackage -AppRoot $SourcePackage
    $separator = [IO.Path]::DirectorySeparatorChar
    $rootPath = $rootPath.TrimEnd($separator)
    $sourcePath = $sourcePath.TrimEnd($separator)
    if ($rootPath -eq $sourcePath -or $sourcePath.StartsWith($rootPath + $separator, [StringComparison]::OrdinalIgnoreCase) -or
            $rootPath.StartsWith($sourcePath + $separator, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source Python package and generated app root must be separate, non-overlapping directories.'
    }
    $sourceFiles = Get-SafeModuleFiles -PackageRoot $sourcePath -Boundary $sourcePath
    if (-not $sourceFiles.ContainsKey('__init__.py')) {
        throw 'Source Python package is missing or has no __init__.py; refusing module cleanup.'
    }
    $relativePackages = @(
        'src/kanamemory',
        'build/kanamemory/android/gradle/app/src/main/python/kanamemory',
        'build/kanamemory/android/gradle/app/build/python/sources/debug/kanamemory',
        'build/kanamemory/android/gradle/app/build/python/sources/release/kanamemory'
    )
    $pending = [Collections.Generic.List[string]]::new()
    foreach ($relative in $relativePackages) {
        $packagePath = Assert-SafeGeneratedPath -Path (Join-Path $rootPath $relative) -AppRoot $rootPath
        $generatedFiles = Get-SafeModuleFiles -PackageRoot $packagePath -Boundary $rootPath
        foreach ($entry in $generatedFiles.GetEnumerator()) {
            $sourceRelative = $entry.Key
            if ([IO.Path]::GetExtension($sourceRelative) -eq '.pyc') {
                if ([IO.Path]::GetDirectoryName($sourceRelative) -eq '__pycache__') {
                    # PEP 3147/488 cache files map to their source module, not
                    # to possibly retired cache files in the source checkout.
                    $stem = [IO.Path]::GetFileNameWithoutExtension($sourceRelative)
                    $stem = $stem -replace '\.opt-\d+$', '' -replace '\.[^.]+$', ''
                    $sourceRelative = $stem + '.py'
                } else { $sourceRelative = [IO.Path]::ChangeExtension($sourceRelative, '.py') }
            }
            if (-not $sourceFiles.ContainsKey($sourceRelative)) { $pending.Add($entry.Value) }
        }
    }
    # Validate all four inventories before the first deletion. Delete files
    # individually, rechecking links at use; retain even empty directories.
    foreach ($targetPath in $pending) {
        $checkedPath = Assert-SafeGeneratedPath -Path $targetPath -AppRoot $rootPath
        if (-not (Test-Path -LiteralPath $checkedPath -PathType Leaf)) {
            throw "Module cleanup target changed after validation: $checkedPath"
        }
        Remove-Item -LiteralPath $checkedPath -Force
    }
    return $pending.Count
}

function Assert-PackagedAudioPolicy {
    param([string]$Python, [string]$Apk, [string]$SourceDatabase, [bool]$PersonalAudioIncluded)
    $checkPackage = @'
import hashlib, io, json, sqlite3, sys, zipfile
from pathlib import PurePosixPath
apk, database, private = sys.argv[1:]
with zipfile.ZipFile(apk) as outer:
    with zipfile.ZipFile(io.BytesIO(outer.read('assets/chaquopy/app.imy'))) as inner:
        raw = inner.read('kanamemory/resources/content.sqlite3')
        if hashlib.sha256(raw).digest() != hashlib.sha256(open(database, 'rb').read()).digest():
            raise SystemExit('APK content database is stale; rebuild before packaging/installing.')
        if private != 'true':
            stale = [n for n in inner.namelist() if PurePosixPath(n).name.lower().startswith('chinese-qieyun-')
                     and PurePosixPath(n).suffix.lower() == '.mp3']
            if stale:
                raise SystemExit('Public APK contains leftover personal audio: ' + ', '.join(stale[:3]))
            db = sqlite3.connect(':memory:')
            db.deserialize(raw)
            row = db.execute("SELECT value FROM metadata WHERE key='audio_policy'").fetchone()
            policy = json.loads(row[0]) if row else {}
            db.close()
            if policy.get('personal_audio_included') or policy.get('contains_audio_without_public_redistribution_clearance'):
                raise SystemExit('Public APK contains a personal-use audio database.')
'@
    $personalText = if ($PersonalAudioIncluded) { 'true' } else { 'false' }
    & $Python -X utf8 -c $checkPackage $Apk $SourceDatabase $personalText
    if ($LASTEXITCODE -ne 0) {
        throw 'APK audio policy check failed. Run run_app.ps1 -Stage build, then -Stage package with the same ToolchainRoot; do not reuse the old APK.'
    }
}

function Assert-PackagedBuildIdentity {
    param([string]$Python, [string]$Apk, [string]$AppSource, [string]$IdentityTool, [string]$SdkRoot)
    $buildTools = Join-Path $SdkRoot 'build-tools'
    $aapt = Get-ChildItem -LiteralPath $buildTools -Directory -ErrorAction Stop |
        Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending |
        ForEach-Object { Join-Path $_.FullName 'aapt.exe' } |
        Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $aapt) { throw 'Android aapt missing; cannot verify APK build identity.' }
    & $Python -X utf8 $IdentityTool verify-apk $AppSource --apk $Apk --aapt $aapt | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'APK build identity check failed; rebuild the current app version.' }
}

$ToolchainRoot = [IO.Path]::GetFullPath($ToolchainRoot)
if ($ToolchainRoot -match '[^\x00-\x7F]|\s' -or $ToolchainRoot.Length -gt 65) {
    throw 'Android ToolchainRoot must be a short ASCII path without spaces.'
}
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = if ($Python) { [IO.Path]::GetFullPath($Python) } else { Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (Test-Path -LiteralPath (Join-Path $projectRoot 'clean-source-manifest.json')) { $PublicBuild = $true }
$appSource = Join-Path $projectRoot 'app'
$buildDirectory = if ($PublicBuild) { 'app-public' } else { 'app' }
$appBuild = Join-Path $ToolchainRoot $buildDirectory
$identityTool = Join-Path $projectRoot 'tools\app_build_identity.py'
$environmentTool = Join-Path $projectRoot 'tools\build_environment.py'
$sourceDatabase = Join-Path $appSource 'src/kanamemory/resources/content.sqlite3'
$publicCheck = Join-Path $projectRoot 'tools\check_public_content.py'
$demoSource = Join-Path $projectRoot 'demo\content.json'
function Assert-PublicContent {
    param([string]$Apk = '')
    if (-not $PublicBuild) { return }
    $arguments = @('-X', 'utf8', $publicCheck, (Split-Path -Parent $sourceDatabase), '--source', $demoSource)
    if ($Apk) { $arguments += @('--apk', $Apk) }
    & $projectPython @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Public content verification failed. Build the original demo pack in an empty resources directory.' }
}
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project Python missing. Run the desktop bootstrap first.' }
if (-not (Test-Path -LiteralPath (Join-Path $appSource 'pyproject.toml'))) { throw 'App pyproject.toml missing.' }
& $projectPython -X utf8 $environmentTool --host
if ($LASTEXITCODE -ne 0) { throw 'Build environment differs from the declared lock; see docs/build-environment.txt.' }
Assert-PublicContent
if (-not $PublicBuild) {
    $teachingCheck = 'import sys; sys.path.insert(0, sys.argv[1]); from kanamemory.content_pack import validate_pack; m = validate_pack(sys.argv[2]); assert m["profile"] == "personal", "Personal build requires its complete teaching pack"'
    & $projectPython -X utf8 -c $teachingCheck (Join-Path $appSource 'src') (Join-Path (Split-Path -Parent $sourceDatabase) 'teaching_content.sqlite3')
    if ($LASTEXITCODE -ne 0) { throw 'Personal teaching pack is missing, incomplete, or corrupt. Rebuild it before packaging.' }
}
$identityJson = & $projectPython -X utf8 $identityTool read $appSource
if ($LASTEXITCODE -ne 0) { throw 'App build identity is inconsistent.' }
$buildIdentity = $identityJson | ConvertFrom-Json
if ($PublicBuild -and $buildIdentity.package_id -ne 'org.example.demo.kanamemory') {
    throw 'Public demo requires its separate org.example.demo.kanamemory package ID. Use the clean source export.'
}
$packagedName = $buildIdentity.apk_name
$sdkRoot = Join-Path $ToolchainRoot 'briefcase\tools\android_sdk'
if ($Stage -in @('install','launch') -and $Serial -notmatch '^emulator-\d+$') {
    throw 'This development script only installs/launches on explicitly selected emulators, never a phone.'
}
$personalAudioIncluded = Get-PersonalAudioPolicy -Python $projectPython -Database $sourceDatabase -AppBuildRoot $appBuild
if ([IO.Path]::GetFullPath($appBuild) -eq [IO.Path]::GetFullPath($appSource)) {
    throw 'Toolchain app directory must be a generated copy, not the source app directory.'
}
[void](Assert-SafeGeneratedPath -Path $appBuild -AppRoot $appBuild)
[void](Remove-StaleGeneratedModules -AppBuildRoot $appBuild -SourcePackage (Join-Path $appSource 'src/kanamemory'))
[void](Remove-StaleGeneratedResources -AppBuildRoot $appBuild -SourceResources (Split-Path -Parent $sourceDatabase))
[void](Remove-PublicBuildPersonalAudio -AppBuildRoot $appBuild -PersonalAudioIncluded $personalAudioIncluded)
New-Item -ItemType Directory -Force -Path $appBuild | Out-Null
Copy-Item -LiteralPath (Join-Path $appSource 'pyproject.toml') -Destination $appBuild -Force
Copy-Item -LiteralPath (Join-Path $appSource 'src') -Destination $appBuild -Recurse -Force
[void](Remove-StaleGeneratedModules -AppBuildRoot $appBuild -SourcePackage (Join-Path $appSource 'src/kanamemory'))
[void](Remove-PublicBuildPersonalAudio -AppBuildRoot $appBuild -PersonalAudioIncluded $personalAudioIncluded)
$oldBriefcase = $env:BRIEFCASE_HOME
$oldGradle = $env:GRADLE_USER_HOME
$oldPython = $env:KANA_BUILD_PYTHON
$oldUtf8 = $env:PYTHONUTF8
$oldIo = $env:PYTHONIOENCODING
$oldJava = $env:JAVA_HOME
$oldAndroidHome = $env:ANDROID_HOME
$oldAndroidSdkRoot = $env:ANDROID_SDK_ROOT
try {
    $env:BRIEFCASE_HOME = Join-Path $ToolchainRoot 'briefcase'
    $env:GRADLE_USER_HOME = Join-Path $ToolchainRoot 'gradle'
    $env:KANA_BUILD_PYTHON = $projectPython
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $declaredJava = Join-Path $ToolchainRoot 'briefcase\tools\java17'
    if (Test-Path -LiteralPath (Join-Path $declaredJava 'bin\java.exe')) { $env:JAVA_HOME = $declaredJava }
    Push-Location -LiteralPath $appBuild
    try {
        $gradleFile = Join-Path $appBuild 'build\kanamemory\android\gradle\app\build.gradle'
        $nativeJava = Join-Path $appSource 'native\java'
        function Assert-AndroidEnvironment {
            param([switch]$Tools)
            $arguments = @('-X', 'utf8', $environmentTool, '--android-project', (Split-Path (Split-Path $gradleFile -Parent) -Parent))
            if ($Tools) {
                $arguments += @('--sdk-root', $sdkRoot)
                $contract = Get-Content -LiteralPath (Join-Path $projectRoot 'config\build_environment.json') -Raw | ConvertFrom-Json
                $java = Get-Item -LiteralPath (Join-Path $declaredJava 'bin\java.exe') -ErrorAction Stop
                if ($java.VersionInfo.FileVersion -ne $contract.android.jdk_pe_version) {
                    throw 'JDK differs from the declared version; see docs/build-environment.txt.'
                }
            }
            & $projectPython @arguments
            if ($LASTEXITCODE -ne 0) { throw 'Generated Android project or tools differ from the declared environment.' }
        }
        function Copy-NativeBridge {
            [void](Assert-SafeGeneratedPath -Path $gradleFile -AppRoot $appBuild)
            & $projectPython -X utf8 $identityTool sync-gradle $appSource --gradle $gradleFile | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not synchronize generated Android version.' }
            $targetJava = Join-Path $appBuild 'build\kanamemory\android\gradle\app\src\main\java'
            if ((Test-Path -LiteralPath $nativeJava) -and (Test-Path -LiteralPath $targetJava)) {
                Copy-Item -LiteralPath (Join-Path $nativeJava 'org') -Destination $targetJava -Recurse -Force
            }
            $targetRes = Join-Path $appBuild 'build\kanamemory\android\gradle\app\src\main\res'
            Copy-Item -LiteralPath (Join-Path $appSource 'native\res\xml') -Destination $targetRes -Recurse -Force
            $manifestFile = Join-Path $appBuild 'build\kanamemory\android\gradle\app\src\main\AndroidManifest.xml'
            [xml]$manifest = Get-Content -LiteralPath $manifestFile -Raw -Encoding UTF8
            $androidNamespace = 'http://schemas.android.com/apk/res/android'
            [void]$manifest.manifest.application.SetAttribute('allowBackup', $androidNamespace, 'false')
            [void]$manifest.manifest.application.SetAttribute('fullBackupContent', $androidNamespace, 'false')
            [void]$manifest.manifest.application.SetAttribute('dataExtractionRules', $androidNamespace, '@xml/kana_data_extraction_rules')
            $shareClass = 'org.example.kanamemory.KanaTextShareActivity'
            $shareActivity = @($manifest.manifest.application.ChildNodes | Where-Object {
                $_.LocalName -eq 'activity' -and $_.GetAttribute('name', $androidNamespace) -eq $shareClass
            })
            if ($shareActivity.Count -eq 0) {
                $share = $manifest.CreateElement('activity')
                [void]$share.SetAttribute('name', $androidNamespace, $shareClass)
                [void]$share.SetAttribute('exported', $androidNamespace, 'true')
                [void]$share.SetAttribute('theme', $androidNamespace, '@android:style/Theme.NoDisplay')
                [void]$share.SetAttribute('excludeFromRecents', $androidNamespace, 'true')
                [void]$share.SetAttribute('taskAffinity', $androidNamespace, '')
                $shareFilter = $manifest.CreateElement('intent-filter')
                foreach ($shareSpec in @(@('action', 'name', 'android.intent.action.SEND'), @('category', 'name', 'android.intent.category.DEFAULT'), @('data', 'mimeType', 'text/plain'))) {
                    $shareElement = $manifest.CreateElement($shareSpec[0])
                    [void]$shareElement.SetAttribute($shareSpec[1], $androidNamespace, $shareSpec[2])
                    [void]$shareFilter.AppendChild($shareElement)
                }
                [void]$share.AppendChild($shareFilter)
                [void]$manifest.manifest.application.AppendChild($share)
            }
            $manifest.Save($manifestFile)
        }
        switch ($Stage) {
            'desktop' { & $projectPython -X utf8 -m briefcase dev --no-input }
            'create' {
                & $projectPython -X utf8 -m briefcase create android --no-input --log
                if ($LASTEXITCODE -ne 0) { throw 'Briefcase create failed.' }
                [void](Remove-StaleGeneratedModules -AppBuildRoot $appBuild -SourcePackage (Join-Path $appSource 'src/kanamemory'))
                [void](Remove-StaleGeneratedResources -AppBuildRoot $appBuild -SourceResources (Split-Path -Parent $sourceDatabase))
                [void](Remove-PublicBuildPersonalAudio -AppBuildRoot $appBuild -PersonalAudioIncluded $personalAudioIncluded)
                Copy-NativeBridge
                Assert-AndroidEnvironment
            }
            'build' {
                if (-not (Test-Path -LiteralPath $gradleFile)) { throw 'Run create first.' }
                # Refresh Python sources/dependencies in an existing generated project.
                & $projectPython -X utf8 -m briefcase update android -r --no-input --log
                if ($LASTEXITCODE -ne 0) { throw 'Briefcase update failed.' }
                [void](Remove-StaleGeneratedModules -AppBuildRoot $appBuild -SourcePackage (Join-Path $appSource 'src/kanamemory'))
                [void](Remove-StaleGeneratedResources -AppBuildRoot $appBuild -SourceResources (Split-Path -Parent $sourceDatabase))
                [void](Remove-PublicBuildPersonalAudio -AppBuildRoot $appBuild -PersonalAudioIncluded $personalAudioIncluded)
                Copy-NativeBridge
                Assert-AndroidEnvironment -Tools
                & $projectPython -X utf8 -m briefcase build android --no-input --log
            }
            'package' {
                Assert-AndroidEnvironment -Tools
                & $projectPython -X utf8 -m briefcase package android -p debug-apk --no-input --log
                if ($LASTEXITCODE -ne 0) { throw 'Briefcase packaging failed.' }
                $artifacts = Join-Path $PSScriptRoot 'artifacts'
                New-Item -ItemType Directory -Force -Path $artifacts | Out-Null
                $apk = Join-Path (Join-Path $appBuild 'dist') $packagedName
                if (-not (Test-Path -LiteralPath $apk -PathType Leaf)) { throw "Current-version packaged APK missing: $packagedName" }
                Assert-PackagedBuildIdentity -Python $projectPython -Apk $apk -AppSource $appSource -IdentityTool $identityTool -SdkRoot $sdkRoot
                Assert-PackagedAudioPolicy -Python $projectPython -Apk $apk -SourceDatabase $sourceDatabase -PersonalAudioIncluded $personalAudioIncluded
                Assert-PublicContent -Apk $apk
                Copy-Item -LiteralPath $apk -Destination $artifacts -Force
            }
            'install' {
                $apk = Join-Path $appBuild 'build\kanamemory\android\gradle\app\build\outputs\apk\debug\app-debug.apk'
                # Briefcase package moves the built APK into dist.
                if (-not (Test-Path -LiteralPath $apk)) {
                    $apk = Join-Path (Join-Path $appBuild 'dist') $packagedName
                }
                if (-not (Test-Path -LiteralPath $apk)) { throw 'Build APK first.' }
                Assert-PackagedBuildIdentity -Python $projectPython -Apk $apk -AppSource $appSource -IdentityTool $identityTool -SdkRoot $sdkRoot
                Assert-PackagedAudioPolicy -Python $projectPython -Apk $apk -SourceDatabase $sourceDatabase -PersonalAudioIncluded $personalAudioIncluded
                Assert-PublicContent -Apk $apk
                & (Join-Path $ToolchainRoot 'briefcase\tools\android_sdk\platform-tools\adb.exe') -s $Serial install -r $apk
            }
            'launch' {
                $activity = $buildIdentity.package_id + '/org.beeware.android.MainActivity'
                & (Join-Path $ToolchainRoot 'briefcase\tools\android_sdk\platform-tools\adb.exe') -s $Serial shell am start -n $activity
            }
        }
        if ($LASTEXITCODE -ne 0) { throw "Stage $Stage failed with exit code $LASTEXITCODE" }
    } finally { Pop-Location }
} finally {
    $env:BRIEFCASE_HOME = $oldBriefcase
    $env:GRADLE_USER_HOME = $oldGradle
    $env:KANA_BUILD_PYTHON = $oldPython
    $env:PYTHONUTF8 = $oldUtf8
    $env:PYTHONIOENCODING = $oldIo
    $env:JAVA_HOME = $oldJava
    $env:ANDROID_HOME = $oldAndroidHome
    $env:ANDROID_SDK_ROOT = $oldAndroidSdkRoot
}
