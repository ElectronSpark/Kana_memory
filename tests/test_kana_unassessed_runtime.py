"""Actual unified UI uses the same exact new-direction target after preview."""
from datetime import timedelta

from support.study_path_runtime_support import path_app
from support.study_path_runtime_support import history
from support.learning_fixtures import learning_app
from support.app_fixtures import question_app, setup_app
from support.first_teaching_dispatch_runtime_support import controller_clock
from support.new_dispatch_candidate_support import all_kinds
from support.new_dispatch_candidate_support import queued
from support.new_dispatch_candidate_support import reopen
from support.new_dispatch_candidate_support import rate_current
from support.first_teaching_backlog_support import serve
from kanamemory.new_dispatch import SETTING
from kanamemory.first_teaching_dispatch import _teaching_service
from kanamemory.first_teaching_kana import direction_service


def test_unified_ui_commits_direction_and_preserves_draft_after_restart(path_app):
    app=path_app;all_kinds(app);now=app._test_clock[0]
    sid,qs=queued(app,[('う','kana_choice'),('あ','kana_romaji'),('い','kana_choice')])
    target=qs[1]
    app.store.record_review(qs[0].content_id,qs[0].kind,qs[0].script,1,now=now-timedelta(days=10))
    for age in (5,3):
        app.store.record_review(target.content_id,'kana_choice',target.script,3,outcome='correct',now=now-timedelta(days=age))
    serve(app.store,64,now-timedelta(minutes=3))
    app.store.set_setting(SETTING,{'version':1,'last_lane':'course','last_kana_band':0})
    before=history(app)
    app.continue_study_path()
    assert app.page=='question' and app.question==target
    assert history(app)==before
    pending=app.store.load_session_progress(lazy_questions=True)
    state={'page':'question','romaji_answer':'a','question_audio_ok':True}
    app.store.save_session_progress(sid,pending['question_index'],pending['session_right'],pending['assisted_indices'],state)
    reopen(app);app.continue_study_path()
    assert app.page=='question' and app.question==target
    assert app.store.load_session_progress(lazy_questions=True)['state']==state
    rate_current(app)
    # The two earlier kana_choice reviews and this answer share one reading carrier (2c44345).
    assert app.store.get_card(target.content_id,target.kind,target.script)['review_count']==3
    service=_teaching_service(app.store,now+timedelta(seconds=1))
    assert direction_service(app.store,now+timedelta(seconds=1),service['start'],service['end'])['latest']==now
