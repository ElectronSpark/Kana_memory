"""Export and independently verify the demo distribution, without Android builds."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import subprocess
import sys
import tomllib
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def verify(run_id):
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}', run_id):
        raise ValueError('Use a short alphanumeric run identity')
    sys.path.insert(0, str(ROOT / 'tools'))
    from app_build_identity import load_identity
    identity = load_identity(ROOT / 'app')
    DESTINATION = ROOT / '.test_runs' / (run_id + '-clean')
    ARCHIVE = ROOT / 'environment/artifacts' / ('KanaMemory-clean-source-' + identity['version'] + '-' + run_id + '.zip')
    REPORT = ROOT / 'environment' / (run_id + '-clean-source.json')
    LOG = ROOT / 'environment/logs' / (run_id + '-clean-source.log')
    test_root = ROOT / '.test_runs' / (run_id + '-clean-tests')
    if any(path.exists() for path in (DESTINATION, ARCHIVE, REPORT, LOG, test_root)):
        raise FileExistsError('This verification identity already has outputs; use a fresh identity')
    LOG.parent.mkdir(parents=True, exist_ok=True)
    report = {'format': 'kanamemory-clean-source-audit', 'version': 1,
              'created_at_utc': datetime.now(timezone.utc).isoformat(), 'commands': [],
              'run_id': run_id, 'identity': identity,
              'scope': 'Allowlisted source, original demo and local tests only; no Android build or learner database.'}
    log = []
    environment = dict(os.environ)
    environment.pop('KANAMEMORY_TEACHING_PACK', None)
    environment.pop('PYTHONPATH', None)
    environment['PYTHONUTF8'] = '1'

    def run(arguments, cwd):
        command = [sys.executable, *arguments]
        result = subprocess.run(command, cwd=cwd, env=environment, text=True,
                                encoding='utf-8', capture_output=True)
        log.extend(['COMMAND ' + subprocess.list2cmdline(command), 'CWD ' + str(cwd),
                    result.stdout, result.stderr, 'EXIT ' + str(result.returncode), ''])
        LOG.write_text('\n'.join(log), encoding='utf-8')
        report['commands'].append({'arguments': arguments,
            'working_directory': str(cwd.relative_to(ROOT)) or '.', 'exit_code': result.returncode})
        if result.returncode:
            raise RuntimeError('Verification command failed; inspect its log and exit code.')
        return result.stdout

    try:
        run(['tools/export_clean_source.py', str(DESTINATION.relative_to(ROOT)),
             '--archive', str(ARCHIVE.relative_to(ROOT))], ROOT)
        manifest = json.loads((DESTINATION / 'clean-source-manifest.json').read_text('utf-8'))
        sys.path.insert(0, str(ROOT / 'tools'))
        from export_clean_source import selected_files, _public_file, SECRET, PRIVATE_PATH
        expected = {name: _public_file(name, raw) for name, raw in selected_files(ROOT)}
        with zipfile.ZipFile(ARCHIVE) as archive:
            members = archive.namelist()
            assert len(members) == len(set(members))
            assert all(name.startswith('kana-memory-demo/') and '..' not in PurePosixPath(name).parts for name in members)
            contents = {name.removeprefix('kana-memory-demo/'): archive.read(name) for name in members}
        assert set(contents) == set(manifest['files']) | {'clean-source-manifest.json'}
        mismatches = [name for name, expected_hash in manifest['files'].items()
                      if digest(contents[name]) != expected_hash
                      or contents[name] != (DESTINATION / name).read_bytes()]
        workspace_mismatches = [name for name, raw in expected.items() if contents.get(name) != raw]
        forbidden = {'.git', '.codex', '.agents', '.venv', 'data', 'resources', 'research', 'user-data', 'artifacts'}
        asset_suffixes = {'.db', '.sqlite', '.sqlite3', '.mp3', '.wav', '.ogg', '.m4a', '.ttf', '.otf', '.keystore', '.jks'}
        private_paths = [name for name in contents if set(PurePosixPath(name).parts) & forbidden
                         or PurePosixPath(name).suffix.lower() in asset_suffixes
                         or PurePosixPath(name).name.startswith('.env')]
        key_header = re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----')
        secret_paths = [name for name, raw in contents.items() if SECRET.search(raw) or key_header.search(raw)]
        personal_paths = [name for name, raw in contents.items() if PRIVATE_PATH.search(raw)]
        assert not (mismatches or workspace_mismatches or private_paths or secret_paths or personal_paths)
        briefcase = tomllib.loads(contents['app/pyproject.toml'].decode('utf-8'))['tool']['briefcase']
        assert briefcase['license'] == 'MIT' and briefcase['version'] == identity['version']
        assert briefcase['bundle'] == 'org.example.demo'
        assert briefcase['app']['kanamemory']['formal_name'] == 'KanaMemoryDemo'
        assert b'MIT License' in contents['LICENSE']
        assert b'original demonstration material' in contents['LICENSE-SCOPE.txt']
        assert manifest['profile'] == 'demo' and manifest['contains_git_history'] is False
        report['source_archive'] = {'path': str(ARCHIVE.relative_to(ROOT)), 'sha256': digest(ARCHIVE.read_bytes()),
            'size_bytes': ARCHIVE.stat().st_size, 'archive_files': len(contents), 'manifest_files': len(manifest['files']),
            'manifest_hash_mismatches': mismatches, 'workspace_source_mismatches': workspace_mismatches,
            'private_asset_paths': private_paths, 'credential_pattern_paths': secret_paths,
            'personal_path_matches': personal_paths, 'license': 'MIT', 'demo_package_id': manifest['package_id']}
        run(['tools/build_demo_content.py'], DESTINATION)
        run(['tools/check_public_content.py', 'app/src/kanamemory/resources', '--source', 'demo/content.json'], DESTINATION)
        resources = DESTINATION / 'app/src/kanamemory/resources'
        with sqlite3.connect((resources / 'content.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            counts = {table: db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
                      for table in ('kana', 'word', 'example', 'source')}
            integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
            fk_errors = db.execute('PRAGMA foreign_key_check').fetchall()
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            learner_tables = sorted(tables & {'study_card', 'study_session', 'learning_content', 'learning_unit',
                                              'review_event', 'learning_event', 'app_setting'})
        with sqlite3.connect((resources / 'teaching_content.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
            teaching_profile = dict(db.execute('SELECT key,value FROM metadata'))['profile']
            teaching_integrity = db.execute('PRAGMA integrity_check').fetchone()[0]
        assert counts['kana'] == 3 and counts['word'] == 1
        assert integrity == teaching_integrity == 'ok' and teaching_profile == 'demo' and not fk_errors and not learner_tables
        report['demo_build'] = {'counts': counts, 'lexical_integrity': integrity,
            'teaching_integrity': teaching_integrity, 'teaching_profile': teaching_profile,
            'foreign_key_errors': fk_errors, 'learner_tables': learner_tables,
            'resource_sha256': {file.name: digest(file.read_bytes()) for file in sorted(resources.iterdir()) if file.is_file()}}
        stdout = run(['-m', 'pytest', 'tests/test_public_distribution.py', '-q',
                      '--basetemp=' + str(test_root), '--tb=short'], DESTINATION)
        report['public_boundary_tests'] = {'summary': next(line for line in reversed(stdout.splitlines()) if 'passed' in line),
            'includes_independent_runtime_and_questions': True,
            'includes_rejecting_private_profiles_extra_assets_and_embedded_keys': True,
            'android_build_performed': False}
        # Runtime/demo generation happens after archiving; no derived files enter the source zip.
        assert digest(ARCHIVE.read_bytes()) == report['source_archive']['sha256']
        report['passed'] = True
    except Exception as error:
        report.update(passed=False, error_type=type(error).__name__, error=str(error))
        log.append(type(error).__name__ + ': ' + str(error))
        LOG.write_text('\n'.join(log), encoding='utf-8')
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    result = verify(parser.parse_args().run_id)
    print(json.dumps({'passed': result['passed'], 'run_id': result['run_id']}, ensure_ascii=True))
    raise SystemExit(0 if result['passed'] else 1)
