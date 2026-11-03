"""Run bounded, corpus-independent engineering checks from any working directory."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

if __package__:
    from .run_evidence import RunEvidence
else:
    from run_evidence import RunEvidence

ROOT = Path(__file__).resolve().parents[1]
SMOKE_TESTS = (
    "tests/test_architecture.py",
    "tests/test_app_clock.py",
    "tests/test_study_path_selection.py",
    "tests/test_public_distribution.py",
    "tests/test_build_environment.py",
)


def main() -> int:
    """Fail at the first broken gate; never launch native, API or long-term tests."""
    # Keep paths short enough for nested Java-source exports on Windows.
    runs = ROOT / ".test_runs"
    runs.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="q-", dir=runs))
    record = RunEvidence(ROOT, run, inputs=('app/pyproject.toml', 'config/build_environment.json',
        'environment/windows-py313.lock.txt', 'environment/android-runtime.lock.txt', 'tools/architecture_policy.json',
        'tools/check_python.py', 'tools/run_evidence.py', 'tools/build_environment.py'))
    print(f"Test evidence: {run}", flush=True)
    commands = (
        ("ruff", "check", "app/src/kanamemory", "tools", "tests"),
        ("tools.check_architecture",),
        ("tools.build_environment",),
        (
            "pytest",
            *SMOKE_TESTS,
            "-q",
            "--tb=short",
            f"--basetemp={run / 't'}",
            f"--junitxml={run / 'results.xml'}",
        ),
    )
    outcome = 'interrupted'
    try:
        for command in commands:
            print(f"Running: python -m {' '.join(command)}", flush=True)
            index = record.begin_command(['python', '-X', 'utf8', '-m', *command])
            result = subprocess.run(
                [sys.executable, "-X", "utf8", "-m", *command], cwd=ROOT, check=False
            )
            record.end_command(index, result.returncode)
            if result.returncode:
                outcome = 'failed'
                return result.returncode
        outcome = 'passed'
        return 0
    finally:
        record.finish(outcome, evidence=('results.xml',))


if __name__ == "__main__":
    raise SystemExit(main())
