"""Small run manifests: source/input identity, command outcomes, selected evidence."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def source_identity(root: Path) -> dict:
    manifest = root / 'clean-source-manifest.json'
    if manifest.is_file():
        changed = []
        for relative, expected in json.loads(manifest.read_text('utf8'))['files'].items():
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError('Export manifest path escapes source')
            if not path.is_file() or digest(path) != expected:
                changed.append(relative)
        return {'kind': 'clean_export', 'manifest_sha256': digest(manifest), 'changed_files': changed}
    try:
        top = subprocess.check_output(['git', 'rev-parse', '--show-toplevel'], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
        if Path(top).resolve() != root.resolve():
            return {'kind': 'unknown', 'source_commit': None}
        head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, stderr=subprocess.DEVNULL).decode().strip()
        diff = subprocess.check_output(['git', 'diff', '--binary', 'HEAD', '--'], cwd=root, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return {'kind': 'unknown', 'source_commit': None}
    return {'kind': 'git', 'source_commit': head, 'tracked_dirty': bool(diff),
            'tracked_diff_sha256': hashlib.sha256(diff).hexdigest(),
            'untracked_files': 'not inferred; selected inputs hashed separately'}


class RunEvidence:
    """The caller owns execution; only explicitly selected evidence is indexed."""
    def __init__(self, root: Path, run: Path, *, inputs: tuple[str, ...] = ()):
        self.root, self.run = root.resolve(), run.resolve()
        if not self.run.is_relative_to(self.root / '.test_runs'):
            raise ValueError('Run directory must be inside .test_runs')
        self.run.mkdir(parents=True, exist_ok=True)
        self.path = self.run / 'run.json'
        if self.path.exists():
            raise FileExistsError('A run identity cannot overwrite another run')
        self.value = {'format': 'kanamemory-run-evidence-v1', 'run_id': self.run.relative_to(self.root).as_posix(),
            'started_at': timestamp(), 'finished_at': None, 'status': 'running',
            'source': source_identity(self.root), 'inputs': {}, 'commands': [], 'evidence': {}}
        for relative in inputs:
            path = (self.root / relative).resolve()
            if not path.is_relative_to(self.root):
                raise ValueError('Input escapes source root')
            self.value['inputs'][relative] = digest(path)
        self._write()

    def _write(self) -> None:
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.value, ensure_ascii=False, indent=2) + '\n', 'utf8')
        # Windows indexers can briefly hold the previous manifest without
        # delete sharing. Keep the previous complete file and retry finitely.
        for attempt in range(6):
            try:
                temporary.replace(self.path)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(.02 * 2 ** attempt)

    def begin_command(self, arguments: list[str]) -> int:
        row = {'arguments': [arg.replace(str(self.root), '.') for arg in arguments],
               'started_at': timestamp(), 'finished_at': None, 'exit_code': None}
        self.value['commands'].append(row)
        self._write()
        return len(self.value['commands']) - 1

    def end_command(self, index: int, exit_code: int) -> None:
        self.value['commands'][index].update(finished_at=timestamp(), exit_code=exit_code)
        self._write()

    def finish(self, status: str, *, evidence: tuple[str, ...] = ()) -> None:
        if status not in {'passed', 'failed', 'interrupted'}:
            raise ValueError('Unknown run outcome')
        if status == 'passed' and (not self.value['commands'] or any(row['exit_code'] != 0 for row in self.value['commands'])):
            raise ValueError('A passed run requires completed successful commands')
        for relative in evidence:
            path = (self.run / relative).resolve()
            if not path.is_relative_to(self.run):
                raise ValueError('Evidence escapes run directory')
            if path.is_file():
                self.value['evidence'][relative] = {'sha256': digest(path), 'bytes': path.stat().st_size}
        self.value.update(status=status, finished_at=timestamp())
        self._write()
