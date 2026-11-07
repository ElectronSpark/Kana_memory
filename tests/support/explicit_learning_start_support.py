"""Shared explicit learning start fixtures and assertions; no collected scenarios."""
from kanamemory.app_clock import FrozenClock
from support.study_path_service_support import NOW
from support.first_teaching_dispatch_core_support import kana
import pytest
from support.learning_store_fixtures import question
from datetime import timedelta
from kanamemory.app_clock import using_clock


@pytest.fixture(autouse=True)
def clock():
    with using_clock(FrozenClock(NOW)) as value:
        yield value


def append(learning, cid):
    learning.append_questions({**question(cid, qid='explicit-'+cid),
                               'revision': learning.get_item(cid)['revision']}, now=NOW)


def backlog(store):
    old = kana('explicit-old', 'あ', 'a')
    store.record_review(old.content_id, old.kind, old.script, 1, now=NOW-timedelta(days=60))
    sid = store.start_session([old], now=NOW)
    store.save_session_progress(sid, 0, state={})
    return sid


def finish_first(learning, cid, clock):
    item = learning.get_item(cid)
    learning.set_initial_familiarity(cid, {a:'new' for a in item['abilities']}, now=clock.now)
    learning.mark_instruction_exposed(cid, now=clock.now)
    learning.advance_instruction(cid, 'intro_done', now=clock.now)
    learning.advance_instruction(cid, 'guide_correct', now=clock.now)
    clock.advance(61)
    q = learning.get_question('explicit-'+cid)
    learning.submit(q['id'], q['answer'], True, now=clock.now)
    clock.advance(4)
    learning.finalize(q['id'], 3, now=clock.now)
