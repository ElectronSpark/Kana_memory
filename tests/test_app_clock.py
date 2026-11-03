"""The business calendar moves without changing OS time, durations or records."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import time

import pytest

from kanamemory import app_clock, domain
from kanamemory.app_clock import FrozenClock, SystemClock, using_clock
from kanamemory.domain import Question, StudyStore


START = datetime(2034, 11, 5, 5, 59, 30, tzinfo=timezone.utc)


def test_default_provider_is_real_and_returned_dates_are_aware():
    before = datetime.now(timezone.utc)
    actual = app_clock.utc_now()
    after = datetime.now(timezone.utc)
    assert before <= actual <= after
    assert actual.utcoffset() == timedelta(0)
    assert app_clock.local_now().utcoffset() is not None
    assert app_clock.localize(actual, 'Asia/Tokyo').hour == (actual.hour + 9) % 24


def test_utc_test_clock_does_not_require_an_iana_database(monkeypatch):
    def unavailable(_):
        raise AssertionError('UTC must not consult the optional IANA database')
    monkeypatch.setattr(app_clock, 'ZoneInfo', unavailable)
    with using_clock(FrozenClock(START, 'UTC')):
        assert app_clock.local_now() == START
        assert app_clock.localize(START, 'UTC') == START


def test_imported_alias_and_all_threads_share_forward_calendar_without_waiting():
    # A previously imported function must resolve the provider on every call.
    imported_before_install = domain.utc_now
    clock = FrozenClock(START, time_zone='America/Toronto')
    real_before = datetime.now(timezone.utc)
    monotonic_function, monotonic_before = time.monotonic, time.monotonic()
    with using_clock(clock):
        assert imported_before_install() == START
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert set(pool.map(lambda _: imported_before_install(), range(16))) == {START}
            list(pool.map(lambda _: clock.advance(1), range(200)))
        assert app_clock.utc_now() == START + timedelta(seconds=200)
        clock.advance(timedelta(days=1461))
        assert domain._utc(None) == START + timedelta(days=1461, seconds=200)
        assert time.monotonic is monotonic_function
        assert time.monotonic() >= monotonic_before
        assert datetime.now(timezone.utc).year == real_before.year


def test_frozen_zone_localizes_due_dates_independently_of_system_zone_and_dst():
    # Toronto's 2026 fall-back repeats 01:xx with a different UTC offset.
    clock = FrozenClock('2026-11-01T05:59:30+00:00', 'America/Toronto')
    with using_clock(clock):
        first = app_clock.local_now()
        assert first.isoformat() == '2026-11-01T01:59:30-04:00'
        clock.advance(60)
        second = app_clock.local_now()
        assert second.isoformat() == '2026-11-01T01:00:30-05:00'
        assert second.fold == 1
        january_due = datetime(2027, 1, 1, 2, tzinfo=timezone.utc)
        assert app_clock.localize(january_due).isoformat() == '2026-12-31T21:00:00-05:00'
        assert app_clock.localize(january_due, 'Asia/Tokyo').isoformat() == '2027-01-01T11:00:00+09:00'


@pytest.mark.parametrize('value', [datetime(2026, 1, 1), '2026-01-01T00:00:00', 'bad', None, 5])
def test_naive_or_invalid_clock_initialization_is_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        FrozenClock(value)


@pytest.mark.parametrize('value', [-1, timedelta(seconds=-1), math.inf, -math.inf, math.nan, True, '30', None])
def test_invalid_advance_preserves_previous_instant(value):
    clock = FrozenClock(START)
    with pytest.raises(ValueError):
        clock.advance(value)
    assert clock.now == START


def test_backward_change_is_explicit_and_overflow_does_not_half_apply():
    clock = FrozenClock(START)
    with pytest.raises(ValueError):
        clock.now = START - timedelta(days=1)
    with pytest.raises(ValueError):
        clock.set(START, allow_backwards='yes')
    assert clock.now == START
    clock.set(START - timedelta(days=1), allow_backwards=True)
    assert clock.now == START - timedelta(days=1)
    clock.set(datetime.max.replace(tzinfo=timezone.utc))
    with pytest.raises(OverflowError):
        clock.advance(1)
    assert clock.now == datetime.max.replace(tzinfo=timezone.utc)


def test_scopes_restore_on_exceptions_and_nested_installations():
    outer = FrozenClock(START)
    inner = FrozenClock(START + timedelta(days=3))
    with using_clock(outer):
        with pytest.raises(RuntimeError, match='test abort'):
            with using_clock(inner):
                assert app_clock.utc_now() == inner.now
                raise RuntimeError('test abort')
        assert app_clock.utc_now() == outer.now
        previous = app_clock.install_clock(inner)
        assert previous is outer
        displaced = app_clock.restore_clock(previous)
        assert displaced is inner
        assert app_clock.utc_now() == outer.now
    assert abs((app_clock.utc_now() - datetime.now(timezone.utc)).total_seconds()) < 5


def test_invalid_provider_does_not_replace_active_clock():
    with using_clock(FrozenClock(START)):
        with pytest.raises(TypeError):
            app_clock.install_clock(object())
        class Naive(SystemClock):
            def utc_now(self):
                return START.replace(tzinfo=None)
        with pytest.raises(ValueError):
            app_clock.install_clock(Naive())
        assert app_clock.utc_now() == START


def test_explicit_now_wins_in_learning_evidence_and_tutor_paths():
    from kanamemory import learning_evidence, kana_rule_evidence, tutor_memory, tutor_store
    explicit = START - timedelta(days=4)
    with using_clock(FrozenClock(START)):
        assert domain._utc(explicit) == explicit
        for module in (learning_evidence, kana_rule_evidence, tutor_memory, tutor_store):
            assert module._now() == START
            assert module._now(explicit) == explicit


def test_late_imported_modules_see_provider_without_scanning_sys_modules():
    root = Path(__file__).resolve().parents[1] / 'app/src'
    code = '''
import json, sys
sys.path.insert(0, sys.argv[1])
from kanamemory.app_clock import FrozenClock, install_clock
install_clock(FrozenClock('2040-05-06T07:08:09+00:00'))
from kanamemory import domain, learning_store, learning_evidence, tutor_store, ai_practice_store
print(json.dumps([domain.utc_now().isoformat(), learning_store._utc(None).isoformat(),
learning_evidence._now().isoformat(), tutor_store._now().isoformat(), ai_practice_store._now()]))
'''
    completed = subprocess.run([sys.executable, '-X', 'utf8', '-c', code, str(root)],
                               text=True, encoding='utf-8', capture_output=True, check=True, timeout=20)
    assert json.loads(completed.stdout) == ['2040-05-06T07:08:09+00:00'] * 5


def test_fast_forward_changes_due_visibility_not_saved_history_queue_or_draft(tmp_path):
    clock = FrozenClock('2031-01-01T12:00:00+00:00')
    with using_clock(clock), StudyStore(tmp_path/'synthetic', time_zone='UTC') as store:
        store.record_review('kana-3042', 'kana_choice', 'hiragana', 1)
        due = datetime.fromisoformat(store.get_card('kana-3042', 'kana_choice', 'hiragana')['due_at_utc'])
        sid = store.start_session([Question('kana-3044', 'kana_romaji', 'hiragana', 'い', 'i', [])])
        store.save_session_progress(sid, 0, state={'romaji_answer': 'i'})
        before = tuple(store._db.iterdump())
        assert store.stats()['due'] == 0
        clock.set(due)
        assert store.stats()['due'] == 1
        assert store.load_session_progress()['state']['romaji_answer'] == 'i'
        assert tuple(store._db.iterdump()) == before
        assert store.stats(now=clock.now - timedelta(days=1))['due'] == 0


def test_business_modules_do_not_add_hidden_wall_clock_bypasses():
    # Real diagnostics/export filenames and device-local zone discovery remain
    # real by design. Adding a scheduling bypass elsewhere is a regression.
    allowed = {
        ('app_clock.py', 'utc_now'), ('app.py', 'screen'), ('domain.py', '__init__'),
        ('backup_ui.py', 'export'),
        # Export/recovery filenames use wall time, not the learning calendar.
        ('backup_ui.py', 'convert_legacy'), ('record_schema.py', '_recovery_copy'),
    }
    diagnostic_files = {'feedback_context.py', 'feedback_store.py', 'feedback_ui.py'}
    failures = []
    for path in (Path(__file__).resolve().parents[1]/'app/src/kanamemory').glob('*.py'):
        tree = ast.parse(path.read_text('utf-8-sig'))
        class Scan(ast.NodeVisitor):
            scope = ''
            def visit_FunctionDef(self, node):
                previous, self.scope = self.scope, node.name
                self.generic_visit(node)
                self.scope = previous
            visit_AsyncFunctionDef = visit_FunctionDef
            def visit_Call(self, node):
                name = ast.unparse(node.func)
                if name in {'datetime.now', 'date.today', 'datetime.today', 'time.time'}:
                    if path.name not in diagnostic_files and (path.name, self.scope) not in allowed:
                        failures.append((path.name, self.scope, node.lineno, name))
                self.generic_visit(node)
        Scan().visit(tree)
    assert not failures
