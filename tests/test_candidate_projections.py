"""Small input projections cannot freeze learner decisions or custom content."""
import json
import sqlite3
from types import SimpleNamespace

import pytest

from kanamemory.content import ContentRepository
from kanamemory.domain import StudyStore, _word_contexts
from kanamemory.kana_ability_bridge import BRIDGE_SETTING
from kanamemory.kana_routing import active_units


def routes(identity):
    return {'version':1,'units':{identity:{'unit_id':identity,'status':'active'}}}


def test_committed_route_lookups_read_large_json_once_and_observe_all_writes(tmp_path):
    with StudyStore(tmp_path) as store:
        store.set_setting(BRIDGE_SETTING,routes('old'))
        queries = []
        store._db.set_trace_callback(queries.append)
        for _ in range(30):
            assert set(active_units(store)) == {'old'}
        assert sum('SELECT value_json FROM app_setting' in q for q in queries) == 1
        # A transaction's temporary result must not replace committed state.
        for name in ('temporary','different temporary'):
            store._db.execute('BEGIN')
            store._db.execute('UPDATE app_setting SET value_json=? WHERE key=?',(json.dumps(routes(name)),BRIDGE_SETTING))
            assert set(active_units(store)) == {name}
            store._db.rollback()
            assert set(active_units(store)) == {'old'}
        # Cross-connection changes are independent of total_changes.
        with sqlite3.connect(store.path) as db:
            db.execute('UPDATE app_setting SET value_json=? WHERE key=?',(json.dumps(routes('external')),BRIDGE_SETTING))
        assert set(active_units(store)) == {'external'}
        with sqlite3.connect(store.path) as db:
            db.execute('DELETE FROM app_setting WHERE key=?',(BRIDGE_SETTING,))
        assert active_units(store) == {}
        store.set_setting(BRIDGE_SETTING,routes('new'))
        assert set(active_units(store)) == {'new'}


def content(path):
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE kana(id TEXT,hiragana TEXT,katakana TEXT,payload TEXT)')
        db.executemany('INSERT INTO kana VALUES(?,?,?,?)',[
            ('ki','き','キ','{}'),('kya','きゃ','キャ','{}'),('a','あ','ア','{}'),
            ('dupe1','じ','ジ','{}'),('dupe2','じ','ジ','{}'),
            ('fullwidth','ｶ','カ','{}')])


@pytest.mark.parametrize('script,reading',[('hiragana','きゃあじか'),('katakana','キャアジカ')])
def test_scalar_projection_matches_original_longest_matching_and_ambiguity(tmp_path,script,reading):
    path=tmp_path/'content.sqlite3';content(path)
    with ContentRepository(path) as repo:
        word=[{'id':'word','reading':reading}]
        expected=_word_contexts(SimpleNamespace(kana_list=repo.kana_list),word,script)
        # NFC keeps halfwidth/script distinctions; it does not fold them.
        assert expected == {'word':['kya','a']+(['fullwidth'] if script=='katakana' else [])}
        queries=[];repo._db.set_trace_callback(queries.append)
        for _ in range(20):
            assert _word_contexts(repo,word,script)==expected
        assert sum(q.startswith('SELECT id,') for q in queries)==1
        assert not any('SELECT * FROM kana' in q for q in queries)
        projection=repo._kana_context_symbols(script)
        with pytest.raises(TypeError):
            projection['bad']='caller mutation'


def test_content_projection_observes_external_changes_and_sqlite_transaction_snapshot(tmp_path):
    path=tmp_path/'content.sqlite3';content(path)
    with ContentRepository(path) as repo:
        word=[{'id':'word','reading':'きゃあ'}]
        assert _word_contexts(repo,word,'hiragana')=={'word':['kya','a']}
        repo._db.execute('BEGIN')
        assert _word_contexts(repo,word,'hiragana')=={'word':['kya','a']}
        with sqlite3.connect(path) as other:
            other.execute("UPDATE kana SET hiragana='く' WHERE id='kya'")
        assert _word_contexts(repo,word,'hiragana')=={'word':['kya','a']}
        repo._db.rollback()
        assert _word_contexts(repo,word,'hiragana')=={'word':['ki','a']}


def test_custom_or_forwarded_catalogue_keeps_its_own_semantics(tmp_path,monkeypatch):
    path=tmp_path/'content.sqlite3';content(path)
    word=[{'id':'word','reading':'あ'}]
    class Forward:
        def __init__(self,repo): self.repo=repo
        def __getattr__(self,name): return getattr(self.repo,name)
    class Custom(Forward):
        def kana_list(self): return [{'id':'custom','hiragana':'あ'}]
    with ContentRepository(path) as repo:
        assert _word_contexts(Forward(repo),word,'hiragana')=={'word':['a']}
        assert _word_contexts(Custom(repo),word,'hiragana')=={'word':['custom']}
        monkeypatch.setattr(ContentRepository,'kana_list',lambda self:[{'id':'replaced','hiragana':'あ'}])
        assert _word_contexts(repo,word,'hiragana')=={'word':['replaced']}


def test_minimal_payload_only_catalogue_falls_back(tmp_path):
    path=tmp_path/'content.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE kana(id TEXT,payload TEXT)')
        db.execute('INSERT INTO kana VALUES(?,?)',('from-payload',json.dumps({'hiragana':'あ'})))
    with ContentRepository(path) as repo:
        assert _word_contexts(repo,[{'id':'word','reading':'あ'}],'hiragana')=={'word':['from-payload']}
