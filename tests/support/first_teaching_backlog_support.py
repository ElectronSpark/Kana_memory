"""Shared first teaching backlog fixtures and assertions; no collected scenarios."""
from support.first_teaching_dispatch_core_support import NOW
from support.first_teaching_dispatch_core_support import install_queue
from support.first_teaching_dispatch_core_support import kana
from datetime import timedelta


def backlog(store):
    old = kana('old-waiting', 'あ', 'a')
    store.record_review(old.content_id, old.kind, old.script, 1, now=NOW-timedelta(days=10))
    sid = install_queue(store, [old])
    return sid


def serve(store, n, at, *, prefix='served'):
    for i in range(n):
        cid = f'{prefix}-{i}'
        store.record_review(cid, 'kana_choice', 'hiragana', 1, now=at-timedelta(days=4))
        store.record_review(cid, 'kana_choice', 'hiragana', 3, now=at+timedelta(seconds=i))
