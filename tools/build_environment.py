"""Verify the declared Windows build inputs without installing or changing them.

--pinned-template DIR is the one writing mode: it rolls out the pinned template commit into DIR for the create stage.
"""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def pins(text: str) -> dict[str, str]:
    result = {}
    for raw in text.splitlines():
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)', line)
        if not match:
            raise ValueError('Dependencies must be exact name==version pins')
        name = re.sub(r'[-_.]+', '-', match[1]).lower()
        if name in result:
            raise ValueError('Duplicate dependency: ' + name)
        result[name] = match[2]
    return result


def contract(root: Path = ROOT) -> dict:
    value = json.loads((root / 'config/build_environment.json').read_text('utf8'))
    if value.get('format') != 'kanamemory-build-environment-v1':
        raise ValueError('Unsupported build environment contract')
    return value


def declarations(root: Path = ROOT) -> dict:
    value = contract(root)
    host, android = value['host'], value['android']
    locked = pins((root / host['lock']).read_text('utf8'))
    for relative in host['direct_requirements']:
        for name, version in pins((root / relative).read_text('utf8')).items():
            if locked.get(name) != version:
                raise ValueError(f'{relative}: missing or inconsistent lock for {name}')
    config = tomllib.loads((root / 'app/pyproject.toml').read_text('utf8'))['tool']['briefcase']
    app = config['app']['kanamemory']
    target = app['android']
    expected = pins((root / android['python_lock']).read_text('utf8'))
    if config['bundle'] == 'org.example.qa':
        expected.update(android['qa_extra'])
    if pins('\n'.join(app['requires'] + target['requires'])) != expected:
        raise ValueError('Android Python dependencies differ from the declared lock')
    value['resolved_android_pins'] = expected
    for key, expected_value in {'template': android['template'], 'template_branch': android['template_commit'],
                                'template_hash': 'sha1:' + android['template_commit'],
                                'min_os_version': str(android['min_sdk']),
                                'target_os_version': str(android['target_sdk']), 'android_abis': android['abis']}.items():
        if target.get(key) != expected_value:
            raise ValueError('Android declaration differs: ' + key)
    return value


def host_errors(value: dict, *, system=None, machine=None, version=None, installed=None) -> list[str]:
    expected = value['host']
    errors = []
    actual = {'platform': system or sys.platform, 'machine': (machine or platform.machine()).lower(),
              'python': version or platform.python_version()}
    for key in actual:
        if actual[key] != expected[key]:
            errors.append(f'{key}: expected {expected[key]}, found {actual[key]}')
    for name, wanted in installed or ():
        try:
            found = metadata.version(name)
        except metadata.PackageNotFoundError:
            found = 'missing'
        if found != wanted:
            errors.append(f'{name}: expected {wanted}, found {found}')
    return errors


def android_errors(project: Path, value: dict) -> list[str]:
    expected = value['android']
    errors = []
    top = (project / 'build.gradle').read_text('utf8')
    app = (project / 'app/build.gradle').read_text('utf8')
    wrapper = (project / 'gradle/wrapper/gradle-wrapper.properties').read_text('utf8')
    info = tomllib.loads((project / 'briefcase.toml').read_text('utf8'))
    if pins((project / 'app/requirements.txt').read_text('utf8')) != value['resolved_android_pins']:
        errors.append('Generated Android dependencies differ; run the build/update stage before package')
    checks = {
        'gradle': f"gradle-{expected['gradle']}-" in wrapper,
        'agp': f"com.android.tools.build:gradle:{expected['agp']}'" in top,
        'chaquopy': f"com.chaquo.python:gradle:{expected['chaquopy']}'" in top,
        'template_format': info['briefcase']['target_version'] == expected['template_format'],
        'python_series': f'version "{expected["python_series"]}"' in app,
    }
    for key, keyword in [('min_sdk', 'minSdkVersion'), ('compile_sdk', 'compileSdkVersion'), ('target_sdk', 'targetSdkVersion')]:
        match = re.search(r'\b' + keyword + r'\s+(\d+)\b', app)
        checks[key] = bool(match and int(match[1]) == expected[key])
    abis = re.search(r'abiFilters([^\r\n]+)', app)
    checks['abis'] = bool(abis and re.findall(r'"([^"]+)"', abis[1]) == expected['abis'])
    for dependency in expected['java_dependencies']:
        checks[dependency] = f'"{dependency}"' in app
    errors.extend('Generated Android mismatch: ' + key for key, passed in checks.items() if not passed)
    # Files that create writes once and no later stage touches. When one is gone (a clean-up of temporary files
    # removed them from a toolchain kept in the temporary directory, 2026-11-14) Gradle still builds what is left,
    # and the app fails only when it starts.
    errors.extend('Generated Android project is incomplete, re-create it: missing ' + name
                  for name in expected.get('generated_files', ()) if not (project / name).is_file())
    return errors


def sdk_errors(root: Path, value: dict) -> list[str]:
    errors = []
    for package, expected in value['android']['sdk_packages'].items():
        path = root.joinpath(*package.split(';'), 'package.xml')
        if not path.is_file():
            errors.append('Missing SDK package metadata: ' + package)
            continue
        tree = ET.parse(path)
        node = next((n for n in tree.iter() if n.tag.rsplit('}', 1)[-1] == 'localPackage'), None)
        revision = None if node is None else next((n for n in node if n.tag.rsplit('}', 1)[-1] == 'revision'), None)
        parts = {} if revision is None else {n.tag.rsplit('}', 1)[-1]: n.text for n in revision}
        found = '.'.join(parts.get(key, '0') for key in ('major', 'minor', 'micro'))
        if node is None or node.get('path') != package or found != expected:
            errors.append('SDK package mismatch: ' + package)
    return errors


# Briefcase 0.4.5 resolves template_branch only as a branch of the cached clone (remote.refs[branch]), so a commit
# pin fails there, and it ignores template_hash for a local template directory. The create stage therefore rolls
# out the pinned commit from Briefcase's own template cache into a directory and hands Briefcase that directory
# (briefcase create -C template='DIR'); the commit check happens here. The network is used only when the cache
# lacks the commit or one of its files.
TEMPLATE_MARKER = '.kanamemory-pinned-template.json'


def _git(git: str, repository: Path, *arguments: str, offline: bool = True) -> subprocess.CompletedProcess:
    environment = dict(os.environ, GIT_TERMINAL_PROMPT='0')
    if offline:
        environment['GIT_NO_LAZY_FETCH'] = '1'  # Git 2.44+; with older Git the object check below decides first
    return subprocess.run([git, '-C', str(repository), *arguments], capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=environment)


def _last_line(result: subprocess.CompletedProcess) -> str:
    lines = [line for line in (result.stderr or result.stdout).splitlines() if line.strip()]
    return lines[-1].strip() if lines else f'exit code {result.returncode}'


def _template_presence(git: str, cache: Path, commit: str) -> str:
    """'complete', 'partial' (commit there, some files not) or 'absent'; reads local objects only."""
    listed = _git(git, cache, 'cat-file', '--batch-all-objects', '--batch-check=%(objectname) %(objecttype)')
    if listed.returncode != 0 or f'{commit} commit' not in listed.stdout.splitlines():
        return 'absent'
    tree = _git(git, cache, 'rev-list', '--objects', '--missing=print', f'{commit}^{{tree}}')
    if tree.returncode != 0 or any(line.startswith('?') for line in tree.stdout.splitlines()):
        return 'partial'
    return 'complete'


def pinned_template(destination: Path, cache: Path, template: str, commit: str, *, git: str = 'git') -> dict:
    """Write exactly the pinned template commit to destination and return what was done."""
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('The template pin must be a full 40-digit commit hash: ' + commit)
    destination, cache = Path(destination), Path(cache)
    if destination.exists() and not (destination / TEMPLATE_MARKER).is_file():
        raise ValueError(f'{destination} exists and was not written as a pinned template; it is left as it is')
    if not (cache / '.git').exists():
        cache.mkdir(parents=True, exist_ok=True)
        for arguments in (('init', '--quiet'), ('remote', 'add', 'origin', template)):
            result = _git(git, cache, *arguments)
            if result.returncode != 0:
                raise ValueError(f'Could not prepare the template cache {cache}: {_last_line(result)}')
    origin = _git(git, cache, 'remote', 'get-url', 'origin')
    if origin.returncode != 0 or origin.stdout.strip() != template:
        raise ValueError(f'The template cache {cache} does not come from {template}')
    presence = _template_presence(git, cache, commit)
    fetched = False
    if presence == 'absent':
        result = _git(git, cache, 'fetch', '--quiet', '--no-tags', 'origin', commit, offline=False)
        fetched = True
        if result.returncode != 0 or _template_presence(git, cache, commit) == 'absent':
            raise ValueError(f'The pinned template commit {commit} is not in the cache {cache} and could not be '
                             f'fetched from {template}: {_last_line(result)}')
    resolved = _git(git, cache, 'rev-parse', '--verify', '--quiet', f'{commit}^{{commit}}')
    if resolved.returncode != 0 or resolved.stdout.strip() != commit:
        raise ValueError(f'The template cache {cache} does not resolve {commit} to that commit')
    # Missing files of a blobless cache come from its promisor remote while the archive is written.
    online = _template_presence(git, cache, commit) != 'complete'
    fetched = fetched or online
    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=destination.name + '.', dir=destination.parent))
    try:
        archive = work / 'template.zip'
        result = _git(git, cache, 'archive', '--format=zip', '-o', str(archive), commit, offline=not online)
        if result.returncode != 0:
            raise ValueError(f'Could not write the pinned template commit {commit}: {_last_line(result)}')
        tree = work / 'tree'
        with zipfile.ZipFile(archive) as bundle:
            for name in bundle.namelist():
                if name.startswith(('/', '\\')) or '..' in Path(name).parts or ':' in name:
                    raise ValueError('Unsafe path in the template archive: ' + name)
            bundle.extractall(tree)
            files = sum(1 for info in bundle.infolist() if not info.is_dir())
        if not (tree / 'cookiecutter.json').is_file():
            raise ValueError(f'The commit {commit} is not a cookiecutter template')
        info = {'template': template, 'commit': commit, 'files': files, 'fetched': fetched}
        (tree / TEMPLATE_MARKER).write_text(json.dumps(info, sort_keys=True) + '\n', 'utf8')
        if destination.exists():
            shutil.rmtree(destination)
        tree.rename(destination)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {**info, 'path': str(destination)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--host', action='store_true')
    parser.add_argument('--android-project', type=Path)
    parser.add_argument('--sdk-root', type=Path)
    parser.add_argument('--pinned-template', type=Path, help='write the pinned template commit into this directory')
    parser.add_argument('--template-cache', type=Path, help="Briefcase's clone of the template (with --pinned-template)")
    args = parser.parse_args()
    if bool(args.pinned_template) != bool(args.template_cache):
        parser.error('--pinned-template and --template-cache go together')
    try:
        value = declarations(args.root)
        if args.pinned_template:
            android = value['android']
            info = pinned_template(args.pinned_template, args.template_cache, android['template'],
                                   android['template_commit'])
            print('Pinned template ' + json.dumps(info, sort_keys=True))
            return 0
        errors = []
        if args.host:
            locked = pins((args.root / value['host']['lock']).read_text('utf8'))
            errors += host_errors(value, installed=locked.items())
        if args.android_project:
            errors += android_errors(args.android_project, value)
        if args.sdk_root:
            errors += sdk_errors(args.sdk_root, value)
    except (ValueError, KeyError, OSError, ET.ParseError) as exc:
        print('Build environment check failed: ' + str(exc), file=sys.stderr)
        return 1
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print('Declared build environment verified for the requested checks.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
