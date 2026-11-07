from pathlib import Path
import json
import os
import signal
import subprocess
import sys

import pytest


@pytest.fixture
def stage(monkeypatch):
    from kanamemory import backup_stage
    return backup_stage


def abandoned(stage, folder):
    owner=stage.StageOwner(folder)
    (owner.path/'stage.sqlite3').write_bytes(b'synthetic stage')
    stage._release(owner.handle)
    owner.handle=None
    return owner.path


def child_stage(folder):
    code="""import json,os,sys
from kanamemory.backup_stage import StageOwner
owner=StageOwner(sys.argv[1])
(owner.path/'stage.sqlite3').write_bytes(b'synthetic child' * 1000)
print(json.dumps({'pid':os.getpid(),'path':str(owner.path)}),flush=True)
sys.stdin.readline()
owner.cleanup()
"""
    process=subprocess.Popen([sys.executable,'-X','utf8','-c',code,str(folder)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,
        env={**os.environ,'PYTHONPATH':os.pathsep.join(sys.path)},
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    state=json.loads(process.stdout.readline())
    return process,state


def test_actual_other_process_lock_is_preserved_then_kill_is_reclaimed(tmp_path,stage):
    process,state=child_stage(tmp_path)
    path=Path(state['path'])
    try:
        assert stage.reclaim_orphans(tmp_path)['busy']==1
        assert path.exists() and (path/'stage.sqlite3').exists()
        # Kill only the exact child PID returned by our just-spawned helper;
        # venv launchers can have a different parent PID on Windows.
        os.kill(state['pid'],signal.SIGTERM)
        process.wait(timeout=30)
        assert path.exists()
        assert stage.reclaim_orphans(tmp_path)['reclaimed']==1
        assert not path.exists()
    finally:
        if process.poll() is None:
            process.communicate('close\n',timeout=30)
        process.stdin.close();process.stdout.close();process.stderr.close()


def test_new_stage_reclaims_orphan_but_preserves_other_live_owner(tmp_path,stage):
    orphan=abandoned(stage,tmp_path)
    active=stage.StageOwner(tmp_path)
    assert not orphan.exists()
    (active.path/'stage.sqlite3').write_bytes(b'active')
    other=stage.StageOwner(tmp_path)
    assert active.path.exists() and other.path.exists()
    other.cleanup();active.cleanup()
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('fault',['unknown_file','unknown_directory','no_marker','bad_token','bool_version','invalid_json','wrong_lock_size'])
def test_unrecognized_directory_is_never_deleted(tmp_path,stage,fault):
    path=abandoned(stage,tmp_path)
    if fault=='unknown_file':(path/'keep.txt').write_text('keep')
    elif fault=='unknown_directory':(path/'other').mkdir()
    elif fault=='no_marker':(path/stage.MARKER).unlink()
    elif fault in {'bad_token','bool_version'}:
        marker=json.loads((path/stage.MARKER).read_text())
        marker['token' if fault=='bad_token' else 'version']='0'*32 if fault=='bad_token' else True
        (path/stage.MARKER).write_text(json.dumps(marker))
    elif fault=='invalid_json':(path/stage.MARKER).write_text('broken')
    elif fault=='wrong_lock_size':(path/stage.LOCK).write_bytes(b'two')
    before={entry.name for entry in path.iterdir()}
    assert stage.reclaim_orphans(tmp_path)['unrecognized']==1
    assert path.exists() and {entry.name for entry in path.iterdir()}==before
    assert (path/'stage.sqlite3').read_bytes()==b'synthetic stage'


def test_hardlinked_database_is_not_removed(tmp_path,stage):
    path=abandoned(stage,tmp_path)
    original=tmp_path/'outside.sqlite3'
    original.write_bytes(b'outside sentinel')
    (path/'stage.sqlite3').unlink()
    os.link(original,path/'stage.sqlite3')
    assert stage.reclaim_orphans(tmp_path)['unrecognized']==1
    assert original.read_bytes()==b'outside sentinel' and path.exists()


@pytest.mark.parametrize('kind',['directory','database'])
def test_symbolic_links_are_not_followed(tmp_path,stage,kind):
    path=abandoned(stage,tmp_path)
    outside=tmp_path/'outside'
    outside.mkdir()
    (outside/'keep').write_bytes(b'keep')
    try:
        if kind=='database':
            (path/'stage.sqlite3').unlink()
            os.symlink(outside/'keep',path/'stage.sqlite3')
        else:
            target=tmp_path/(path.name+'target')
            path.rename(target)
            os.symlink(target,path,target_is_directory=True)
    except OSError as exc:
        pytest.skip('Host cannot create symlinks: '+str(exc))
    assert stage.reclaim_orphans(tmp_path)['reclaimed']==0
    assert path.exists() and (outside/'keep').read_bytes()==b'keep'


def test_delete_failure_keeps_owner_marker_for_safe_retry(tmp_path,stage,monkeypatch):
    owner=stage.StageOwner(tmp_path)
    (owner.path/'stage.sqlite3').write_bytes(b'synthetic stage')
    original=Path.unlink
    def fail(path,*args,**kwargs):
        if path==owner.path/'stage.sqlite3':raise PermissionError('temporary failure')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',fail)
    owner.cleanup()
    assert owner.path.exists() and (owner.path/stage.MARKER).exists()
    monkeypatch.setattr(Path,'unlink',original)
    assert stage.reclaim_orphans(tmp_path)['reclaimed']==1
    assert not owner.path.exists()


def test_arbitrary_prefixed_and_parent_directories_are_untouched(tmp_path,stage):
    unknown=tmp_path/'.backup-stage-not-owned'
    unknown.mkdir();(unknown/'user.txt').write_text('keep')
    nested=tmp_path/'child';nested.mkdir()
    owner=stage.StageOwner(nested)
    assert stage.reclaim_orphans(tmp_path)['reclaimed']==0
    assert unknown.exists() and owner.path.exists()
    owner.cleanup()
