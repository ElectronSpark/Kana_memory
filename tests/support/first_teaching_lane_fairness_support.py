"""Shared first teaching lane fairness fixtures and assertions; no collected scenarios."""
from support.first_teaching_dispatch_core_support import NOW
from kanamemory.new_dispatch import SETTING
from support.first_teaching_backlog_support import backlog
from support.first_teaching_dispatch_core_support import course
from support.first_teaching_dispatch_core_support import install_queue
from support.first_teaching_dispatch_core_support import kana
from support.first_teaching_backlog_support import serve
from datetime import timedelta


def both_fresh(store, learning, *, last_lane='course', blocked=False):
    backlog(store)
    fresh = kana('fresh-in-other-lane', 'い', 'i')
    sid = install_queue(store, [kana('old-waiting', 'あ', 'a'), fresh])
    course(learning, 'fresh-course', **({'depends_on': ['missing-prerequisite']} if blocked else {}))
    serve(store, 64, NOW-timedelta(minutes=3))
    store.set_setting(SETTING, {'version': 1, 'last_lane': last_lane, 'last_kana_band': 0})
    return sid, fresh
