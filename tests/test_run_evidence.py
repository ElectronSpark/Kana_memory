import json
from pathlib import Path

import pytest

from tools.run_evidence import RunEvidence, digest, source_identity


def test_run_records_inputs_commands_and_selected_outputs(tmp_path):
    (tmp_path / 'input.txt').write_text('stable', 'utf8')
    run = tmp_path / '.test_runs/one'
    record = RunEvidence(tmp_path, run, inputs=('input.txt',))
    index = record.begin_command(['python', '-m', 'pytest'])
    record.end_command(index, 0)
    (run / 'results.xml').write_text('<testsuite/>', 'utf8')
    (run / 'private-draft.txt').write_text('not selected', 'utf8')
    record.finish('passed', evidence=('results.xml',))
    value = json.loads((run / 'run.json').read_text('utf8'))
    assert value['status'] == 'passed' and value['finished_at']
    assert value['inputs']['input.txt'] == digest(tmp_path / 'input.txt')
    assert value['commands'][0]['exit_code'] == 0
    assert set(value['evidence']) == {'results.xml'}
    with pytest.raises(FileExistsError): RunEvidence(tmp_path, run)


def test_interrupted_command_is_not_reported_as_passed(tmp_path):
    run = tmp_path / '.test_runs/one'
    record = RunEvidence(tmp_path, run)
    record.begin_command(['python', '-m', 'pytest'])
    record.finish('interrupted')
    value = json.loads(record.path.read_text('utf8'))
    assert value['status'] == 'interrupted' and value['commands'][0]['exit_code'] is None
    with pytest.raises(ValueError): record.finish('passed', evidence=('../../outside.txt',))
    with pytest.raises(ValueError): RunEvidence(tmp_path, tmp_path / 'outside')


def test_export_source_identity_detects_changed_files(tmp_path):
    source = tmp_path / 'source.py'; source.write_text('one', 'utf8')
    (tmp_path / 'clean-source-manifest.json').write_text(json.dumps({'files': {'source.py': digest(source)}}), 'utf8')
    assert source_identity(tmp_path)['changed_files'] == []
    source.write_text('two', 'utf8')
    assert source_identity(tmp_path)['changed_files'] == ['source.py']


def test_manifest_retries_transient_windows_replace_lock_without_losing_commands(tmp_path, monkeypatch):
    original = Path.replace
    count = [0]
    def replace(path, target):
        count[0] += 1
        if count[0] < 3: raise PermissionError('transient sharing lock')
        return original(path, target)
    monkeypatch.setattr(Path, 'replace', replace)
    monkeypatch.setattr('tools.run_evidence.time.sleep', lambda seconds: None)
    record = RunEvidence(tmp_path, tmp_path / '.test_runs/locked')
    index = record.begin_command(['python', '-m', 'pytest'])
    record.end_command(index, 0); record.finish('passed')
    assert json.loads(record.path.read_text('utf8'))['commands'][0]['exit_code'] == 0
