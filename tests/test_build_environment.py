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
