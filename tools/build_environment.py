"""Verify the declared Windows build inputs without installing or changing them."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
from pathlib import Path
import platform
import re
import sys
import tomllib
import xml.etree.ElementTree as ET

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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--host', action='store_true')
    parser.add_argument('--android-project', type=Path)
    parser.add_argument('--sdk-root', type=Path)
    args = parser.parse_args()
    try:
        value = declarations(args.root)
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
