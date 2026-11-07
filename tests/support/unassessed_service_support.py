"""Shared unassessed service fixtures and assertions; no collected scenarios."""
from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_backlog_support import backlog
from kanamemory.first_teaching_dispatch import choose_protected
from support.first_teaching_dispatch_core_support import course
from support.learning_store_fixtures import item
from support.first_teaching_dispatch_core_support import normal
from support.learning_store_fixtures import question
from support.learning_store_fixtures import ready
from support.first_teaching_dispatch_core_support import rows
from support.first_teaching_backlog_support import serve
from datetime import timedelta


KINDS=['grammar_meaning','grammar_cloze']


def secondary(store, learning, cid='known-content', diagnostic=False):
    at=NOW-timedelta(days=1)
    learning.enroll(item(cid),now=at)
    learning.append_questions([question(cid,qid=cid+'-meaning'),question(cid,qid=cid+'-application',
        ability='application',kind='grammar_cloze')],now=at)
    if diagnostic:
        learning.set_initial_familiarity(cid,{'understanding':'familiar','application':'familiar'},now=at,fair_new=True)
        when=at+timedelta(seconds=61)
    else:
        when=ready(learning,cid,at)
    learning.submit(cid+'-meaning','正在读书',True,now=when)
    learning.finalize(cid+'-meaning',3,now=when+timedelta(seconds=3),fair_new=True)
    return cid+'-application'


def setup(store,learning,*,fresh_course=False,diagnostic=False):
    qid=secondary(store,learning,diagnostic=diagnostic)
    sid=backlog(store)
    if fresh_course:
        course(learning,'fresh-course')
    serve(store,64,NOW-timedelta(minutes=3))
    return sid,qid


def protected(store,learning,sid,now=NOW,kinds=None,priority=None):
    k=normal(store,sid,now)
    if priority is not None:
        k['dispatch_priority']=priority
    enabled=KINDS if kinds is None else kinds
    c=learning.current(enabled,now=now,fair_new=True)
    before=rows(store);writes=store._db.total_changes
    result=choose_protected(store,learning,k,c,enabled_kinds=enabled,kana_kinds=['kana_choice'],now=now)
    assert rows(store)==before and store._db.total_changes==writes
    return result
