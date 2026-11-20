"""Build contracts fail before using drifted dependencies or generated tools."""
from copy import deepcopy
import shutil
import re

import pytest

from tools.build_environment import ROOT, android_errors, contract, declarations, host_errors, pins, sdk_errors


@pytest.fixture
def source(tmp_path):
    for relative in ['config/build_environment.json', 'app/pyproject.toml',
                     'environment/windows-py313.lock.txt', 'environment/android-runtime.lock.txt',
                     'environment/requirements-public.txt', 'environment/requirements-quality.txt']:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, path)
    return tmp_path


def test_current_declared_dependencies_are_complete_and_exact():
    value = declarations()
    assert value['host']['python'] == '3.13.16'
    assert len(value['android']['abis']) == 2


@pytest.mark.parametrize('broken', ['cryptography==50.0.2', 'ruff==0.14.3'])
def test_missing_lock_pin_is_rejected(source, broken):
    path = source / 'environment/windows-py313.lock.txt'
    path.write_text(path.read_text('utf8').replace(broken, ''), 'utf8')
    with pytest.raises(ValueError, match='lock'): declarations(source)


@pytest.mark.parametrize('broken', ['numpy>=2', 'fsrs==6.3.2\nFSRS==6.3.2', '-r indirect.txt'])
def test_unpinned_or_ambiguous_dependency_is_rejected(broken):
    with pytest.raises(ValueError): pins(broken)


def test_qa_addition_is_explicit_and_normal_app_cannot_acquire_it(source):
    path = source / 'app/pyproject.toml'
    content = path.read_text('utf8').replace('requires = ["fsrs==6.3.2"]', 'requires = ["fsrs==6.3.2", "tzdata==2026.5"]')
    path.write_text(content, 'utf8')
    with pytest.raises(ValueError, match='Android Python'): declarations(source)
    # The same contract test runs from both personal and clean demo exports.
    path.write_text(re.sub(r'^bundle = "[^"]+"$', 'bundle = "org.example.qa"', content, flags=re.M), 'utf8')
    declarations(source)


def test_pinned_template_hash_cannot_silently_track_a_branch(source):
    path = source / 'app/pyproject.toml'
    path.write_text(path.read_text('utf8').replace('template_hash = "sha1:', 'template_hash = "unverified:'), 'utf8')
    with pytest.raises(ValueError, match='template_hash'): declarations(source)


def test_host_platform_version_and_missing_package_are_explicit(monkeypatch):
    from importlib.metadata import PackageNotFoundError
    value = contract()
    assert not host_errors(value, system='win32', machine='AMD64', version='3.13.16')
    assert len(host_errors(value, system='linux', machine='arm64', version='3.14.0')) == 3
    def missing(name): raise PackageNotFoundError(name)
    monkeypatch.setattr('tools.build_environment.metadata.version', missing)
    assert 'missing' in host_errors(value, installed=[('fsrs', '6.3.2')])[0]


@pytest.fixture
def generated(tmp_path):
    value = declarations()
    files = {
        'build.gradle': "classpath 'com.android.tools.build:gradle:9.3.1'\nclasspath 'com.chaquo.python:gradle:17.0.0'",
        'app/build.gradle': 'minSdkVersion 26\ncompileSdkVersion 36\ntargetSdkVersion 36\nversion "3.13"\nabiFilters "arm64-v8a", "x86_64"\n'
            + '\n'.join('implementation "' + d + '"' for d in value['android']['java_dependencies']),
        'briefcase.toml': '[briefcase]\ntarget_version="0.3.27"',
        'app/requirements.txt': '\n'.join(name+'=='+version for name, version in value['resolved_android_pins'].items()),
        'gradle/wrapper/gradle-wrapper.properties': 'distributionUrl=https://services.gradle.org/distributions/gradle-9.5.0-bin.zip',
        **{name: 'generated' for name in value['android']['generated_files']},
    }
    for name, text in files.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(text, 'utf8')
    return tmp_path


def test_generated_android_settings_match_without_executing_gradle(generated):
    value = declarations()
    assert not android_errors(generated, value)
    for key, changed in [('gradle', '9.6.0'), ('agp', '9.4.0'), ('chaquopy', '18.0.0'),
                         ('target_sdk', 37), ('abis', ['x86_64']), ('template_format', '9.0.0')]:
        expected = deepcopy(value); expected['android'][key] = changed
        assert android_errors(generated, expected)
    path = generated / 'app/requirements.txt'
    path.write_text('fsrs==6.3.2\ntoga-android==0.5.7\n', 'utf8')
    assert 'dependencies differ' in android_errors(generated, value)[0]


def test_a_generated_project_that_lost_a_source_file_is_reported(generated):
    value = declarations()
    names = value['android']['generated_files']
    assert 'app/src/main/java/org/beeware/android/MainActivity.java' in names and len(names) >= 6
    assert not android_errors(generated, value)
    for name in names:  # each one alone: Gradle would still build what is left
        path = generated / name
        kept = path.read_bytes()
        path.unlink()
        assert android_errors(generated, value) == ['Generated Android project is incomplete, re-create it: missing ' + name]
        path.write_bytes(kept)
    assert not android_errors(generated, value)


def test_the_toolchain_is_never_kept_in_the_temporary_directory():
    # The system deletes old files there; a generated project lost its sources that way (2026-11-14).
    scripts = sorted((ROOT / 'environment').glob('*.ps1'))
    assert len(scripts) >= 6
    for script in scripts:
        text = script.read_text('utf8')
        for line in text.splitlines():
            if 'Root' in line and '=' in line and line.lstrip().startswith('['):
                assert 'GetTempPath' not in line, (script.name, line.strip())
    runner = (ROOT / 'environment/run_app.ps1').read_text('utf8')
    assert "[string]$ToolchainRoot = (Join-Path $env:LOCALAPPDATA 'km')" in runner
    assert 'must not be inside the temporary directory' in runner
    assert runner.index('must not be inside the temporary directory') < runner.index('briefcase create android')


def test_sdk_package_identity_and_revision_are_checked(tmp_path):
    value = contract()
    assert len(sdk_errors(tmp_path, value)) == 3
    for package, version in value['android']['sdk_packages'].items():
        path = tmp_path.joinpath(*package.split(';'), 'package.xml'); path.parent.mkdir(parents=True)
        major, minor, micro = version.split('.')
        path.write_text(f'<repository><localPackage path="{package}"><revision><major>{major}</major><minor>{minor}</minor><micro>{micro}</micro></revision></localPackage></repository>', 'utf8')
    assert not sdk_errors(tmp_path, value)
    (tmp_path / 'platform-tools/package.xml').write_text('<repository/>', 'utf8')
    assert sdk_errors(tmp_path, value) == ['SDK package mismatch: platform-tools']


def test_public_build_carries_contract_and_lock():
    value = contract()
    public = set((ROOT / 'tools/public_source_files.txt').read_text('utf8').splitlines())
    assert {value['host']['lock'], value['android']['python_lock'], 'tools/build_environment.py',
            'config/build_environment.json', *value['host']['direct_requirements']} <= public
    assert 'environment/bootstrap_windows.ps1' in public
    runner = (ROOT / 'environment/run_app.ps1').read_text('utf8')
    assert '$environmentTool --host' in runner and 'Assert-AndroidEnvironment -Tools' in runner
    for variable in ('ANDROID_HOME', 'ANDROID_SDK_ROOT'):
        assert f'$env:{variable} = $sdkRoot' in runner


GENERATED_MANIFEST = '''<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application android:allowBackup="true" android:label="@string/formal_name" {app_attrs}>
        <activity
            android:configChanges="orientation|screenSize|screenLayout|keyboardHidden"
            android:name="org.beeware.android.MainActivity"
            android:exported="true" {activity_attrs}>
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
'''
PATCH_DRIVER = r'''param($Runner, $In, $Out)
$ErrorActionPreference = 'Stop'
$ast = [System.Management.Automation.Language.Parser]::ParseFile($Runner, [ref]$null, [ref]$null)
$fn = $ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Set-KanaAndroidManifest' }, $true)
if (-not $fn) { throw 'Set-KanaAndroidManifest missing from run_app.ps1' }
. ([scriptblock]::Create($fn.Extent.Text))
[xml]$manifest = Get-Content -LiteralPath $In -Raw -Encoding UTF8
Set-KanaAndroidManifest -Manifest $manifest
$manifest.Save($Out)
'''


def _patch_manifest(tmp_path, shell, text, name):
    import subprocess
    source, target, driver = tmp_path / f'{name}.in.xml', tmp_path / f'{name}.out.xml', tmp_path / 'driver.ps1'
    source.write_text(text, 'utf8')
    driver.write_text(PATCH_DRIVER, 'utf8')
    subprocess.run([shell, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(driver),
                    str(ROOT / 'environment/run_app.ps1'), str(source), str(target)], check=True, timeout=120,
                   capture_output=True)
    return target.read_text('utf-8-sig')


@pytest.mark.parametrize('activity_attrs', ['', 'android:launchMode="singleTask"'])
def test_manifest_patch_keeps_one_activity_across_relaunch_and_launcher_intents(tmp_path, activity_attrs):
    import xml.etree.ElementTree as ET
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if shell is None:
        pytest.skip('PowerShell is required to execute the run_app.ps1 manifest patch')
    android = '{http://schemas.android.com/apk/res/android}'
    first = _patch_manifest(tmp_path, shell, GENERATED_MANIFEST.format(app_attrs='', activity_attrs=activity_attrs), 'first')
    second = _patch_manifest(tmp_path, shell, first, 'second')
    assert second == first  # idempotent: build re-runs the patch on an already patched project
    application = ET.fromstring(first.encode('utf8')).find('application')
    assert application.get(f'{android}allowBackup') == 'false'
    assert application.get(f'{android}fullBackupContent') == 'false'
    main, = [a for a in application.findall('activity') if a.get(f'{android}name') == 'org.beeware.android.MainActivity']
    assert main.get(f'{android}configChanges').split('|') == [
        'orientation', 'screenSize', 'screenLayout', 'keyboardHidden', 'assetsPaths']
    assert main.get(f'{android}launchMode') == 'singleTask'
    assert first.count('launchMode') == 1 and first.count('KanaTextShareActivity') == 1


@pytest.mark.parametrize('rendered', [False, True])
def test_manifest_patch_adds_the_library_file_filters_once(tmp_path, rendered):
    """A project generated before the .kmlib association gets pyproject's VIEW filters; a fresh create that
    rendered them (android_manifest_activity_extra_content) keeps them without a second copy (design 08 16.8)."""
    import tomllib
    import xml.etree.ElementTree as ET
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if shell is None:
        pytest.skip('PowerShell is required to execute the run_app.ps1 manifest patch')
    android = '{http://schemas.android.com/apk/res/android}'
    extra = tomllib.loads((ROOT / 'app/pyproject.toml').read_text('utf8'))['tool']['briefcase']['app']['kanamemory'][
        'android']['android_manifest_activity_extra_content']
    text = GENERATED_MANIFEST.format(app_attrs='', activity_attrs='android:launchMode="singleTask"')
    if rendered:
        text = text.replace('</intent-filter>\n        </activity>', '</intent-filter>\n' + extra + '        </activity>')
    first = _patch_manifest(tmp_path, shell, text, 'first')
    assert _patch_manifest(tmp_path, shell, first, 'second') == first

    def filters(activity):
        return [(tuple(a.get(f'{android}name') for a in node.findall('action')),
                 tuple(c.get(f'{android}name') for c in node.findall('category')),
                 tuple(tuple(sorted(d.items())) for d in node.findall('data')))
                for node in activity.findall('intent-filter')]

    main, = [a for a in ET.fromstring(first.encode('utf8')).find('application').findall('activity')
             if a.get(f'{android}name') == 'org.beeware.android.MainActivity']
    expected = filters(ET.fromstring(f'<activity xmlns:android="{android[1:-1]}">{extra}</activity>'))
    assert len(expected) == 4 and filters(main)[1:] == expected
    assert filters(main)[0][0] == ('android.intent.action.MAIN',)
    assert 'application/zip' not in first


needs_git = pytest.mark.skipif(shutil.which('git') is None, reason='the pinned template is rolled out with Git')


def _git(cwd, *arguments):
    import subprocess
    return subprocess.run(['git', '-C', str(cwd), *arguments], check=True, capture_output=True, text=True).stdout.strip()


def _template_origin(root):
    """A small cookiecutter template repository with two commits; returns (origin, first, second)."""
    origin = root / 'origin'
    (origin / '{{ cookiecutter.format }}').mkdir(parents=True)
    _git(origin, 'init', '--quiet')
    for key, value in (('user.email', 'test@example.org'), ('user.name', 'test'), ('core.autocrlf', 'false')):
        _git(origin, 'config', key, value)
    (origin / 'cookiecutter.json').write_text('{"format": "gradle"}\n', 'utf8')
    (origin / '{{ cookiecutter.format }}/build.gradle').write_text('first\n', 'utf8')
    _git(origin, 'add', '-A'); _git(origin, 'commit', '--quiet', '-m', 'first')
    first = _git(origin, 'rev-parse', 'HEAD')
    (origin / '{{ cookiecutter.format }}/build.gradle').write_text('second\n', 'utf8')
    _git(origin, 'commit', '--quiet', '-am', 'second')
    return origin, first, _git(origin, 'rev-parse', 'HEAD')


@needs_git
def test_the_pinned_commit_is_rolled_out_from_the_cache_without_the_network(tmp_path):
    from tools.build_environment import TEMPLATE_MARKER, pinned_template
    origin, first, _ = _template_origin(tmp_path)
    url = origin.as_uri()
    cache = tmp_path / 'cache'
    _git(tmp_path, 'clone', '--quiet', url, str(cache))
    origin.rename(origin.with_name('moved-away'))  # no remote any more: the cache alone must do
    target = tmp_path / 'km/briefcase/pinned-template/t'
    info = pinned_template(target, cache, url, first)
    assert (info['commit'], info['fetched'], info['files']) == (first, False, 2)
    assert (target / '{{ cookiecutter.format }}/build.gradle').read_text('utf8') == 'first\n'
    assert (target / TEMPLATE_MARKER).is_file()
    assert pinned_template(target, cache, url, first)['fetched'] is False  # a second create rolls it out again


@needs_git
def test_a_commit_missing_from_the_cache_is_fetched_by_its_hash_and_a_wrong_one_stops_create(tmp_path):
    from tools.build_environment import pinned_template
    origin, first, second = _template_origin(tmp_path)
    url = origin.as_uri()
    cache = tmp_path / 'cache'
    _git(tmp_path, 'clone', '--quiet', '--no-checkout', url, str(cache))
    _git(origin, 'reset', '--quiet', '--hard', first)  # the newer commit is not on any branch any more
    _git(cache, 'fetch', '--quiet', '--prune', 'origin')
    _git(cache, 'update-ref', '-d', 'refs/heads/' + _git(cache, 'symbolic-ref', '--short', 'HEAD'))
    _git(cache, 'reflog', 'expire', '--expire=now', '--all'); _git(cache, 'gc', '--quiet', '--prune=now')
    assert second not in _git(cache, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname)').split()
    _git(origin, 'config', 'uploadpack.allowAnySHA1InWant', 'true')
    info = pinned_template(tmp_path / 'out', cache, url, second)
    assert info['fetched'] is True
    assert (tmp_path / 'out/{{ cookiecutter.format }}/build.gradle').read_text('utf8') == 'second\n'
    wrong = 'f' * 40
    with pytest.raises(ValueError, match='could not be fetched'):
        pinned_template(tmp_path / 'out', cache, url, wrong)
    assert (tmp_path / 'out/{{ cookiecutter.format }}/build.gradle').read_text('utf8') == 'second\n'  # untouched
    with pytest.raises(ValueError, match='40-digit'):
        pinned_template(tmp_path / 'out', cache, url, 'main')


@needs_git
def test_a_foreign_cache_or_an_unknown_directory_is_refused(tmp_path):
    from tools.build_environment import pinned_template
    origin, first, _ = _template_origin(tmp_path)
    cache = tmp_path / 'cache'
    _git(tmp_path, 'clone', '--quiet', origin.as_uri(), str(cache))
    with pytest.raises(ValueError, match='does not come from'):
        pinned_template(tmp_path / 'out', cache, 'https://example.org/other-template.git', first)
    kept = tmp_path / 'kept'
    kept.mkdir(); (kept / 'note.txt').write_text('not a template', 'utf8')
    with pytest.raises(ValueError, match='was not written as a pinned template'):
        pinned_template(kept, cache, origin.as_uri(), first)
    assert (kept / 'note.txt').read_text('utf8') == 'not a template'


@needs_git
def test_the_command_line_takes_the_commit_from_the_declaration_and_fails_with_its_exit_code(source, tmp_path):
    import subprocess, sys
    origin, first, _ = _template_origin(tmp_path / 'repo')
    cache = tmp_path / 'cache'
    _git(tmp_path, 'clone', '--quiet', origin.as_uri(), str(cache))
    declared = contract()['android']
    for relative, old, new in (('config/build_environment.json', declared['template'], origin.as_uri()),
                               ('app/pyproject.toml', declared['template'], origin.as_uri())):
        path = source / relative
        path.write_text(path.read_text('utf8').replace(old, new), 'utf8')
    def run(commit):
        for relative in ('config/build_environment.json', 'app/pyproject.toml'):
            path = source / relative
            path.write_text(re.sub(r'[0-9a-f]{40}', commit, path.read_text('utf8')), 'utf8')
        return subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'tools/build_environment.py'), '--root',
                               str(source), '--pinned-template', str(tmp_path / 'out'), '--template-cache', str(cache)],
                              capture_output=True, text=True, encoding='utf-8', timeout=120)
    done = run(first)
    assert done.returncode == 0 and first in done.stdout and '"fetched": false' in done.stdout
    failed = run('e' * 40)
    assert failed.returncode == 1 and 'could not be fetched' in failed.stderr


def test_create_gives_briefcase_the_rolled_out_commit_as_a_local_template():
    runner = (ROOT / 'environment/run_app.ps1').read_text('utf8')
    prepare = runner.index('$environmentTool --pinned-template $pinnedTemplate --template-cache $templateCache')
    create = runner.index('briefcase create android --no-input --log -C "template=\'$pinnedTemplate\'"')
    assert prepare < runner.index("throw 'The pinned Android template could not be prepared.'") < create
    assert runner.count('briefcase create android') == 1


def test_pyproject_renders_single_task_on_fresh_create():
    import tomllib
    android = tomllib.loads((ROOT / 'app/pyproject.toml').read_text('utf8'))['tool']['briefcase']['app']['kanamemory']['android']
    assert android['android_manifest_activity_attrs_extra_content'] == 'android:launchMode="singleTask"'
