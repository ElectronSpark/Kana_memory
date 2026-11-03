"""Read-only access to the bundled, attributed content database."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Any

from .word_search import normalize_search


def _json(value: str | None, fallback=None):
    if value is None:
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value if fallback is None else fallback


def _payload_copier(value):
    """Compile copies of private parsed JSON containers; share scalar leaves.

    Each call still owns every mutable child. The cached source never escapes,
    and flattening the copied payload retains the public record's old aliasing
    between nested source fields and ``record['payload']``.
    """
    if not isinstance(value, (dict, list)):
        return lambda: value
    children = value.items() if isinstance(value, dict) else enumerate(value)
    nested = [(key, _payload_copier(item)) for key, item in children
              if isinstance(item, (dict, list))]
    if not nested:
        return value.copy

    def clone():
        result = value.copy()
        for key, copy_child in nested:
            result[key] = copy_child()
        return result
    return clone


class ContentRepository:
    """Never copies learner progress into, or writes to, packaged content.

    Public records flatten their full source payload and add the stable query
    columns. Missing editorial fields remain missing/None, never fabricated.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"Content database not found: {self.path}")
        self._db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA query_only = ON")
        self._practice_word_candidates_cache = {}
        self._kana_records_cache = None
        self._kana_context_symbols_cache = {}
        from .lexical_review import LexicalReview, RESOURCE
        review_path = self.path.with_name(RESOURCE)
        self.lexical_review = LexicalReview(review_path, self._db) if review_path.is_file() else None

    def close(self):
        self._practice_word_candidates_cache.clear()
        self._kana_records_cache = None
        self._kana_context_symbols_cache.clear()
        if self.lexical_review:
            self.lexical_review.close()
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _record(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        columns = dict(row)
        payload = _json(columns.pop("payload", None), {})
        record = dict(payload) if isinstance(payload, dict) else {"payload": payload}
        record.update(columns)
        record["payload"] = payload
        return record

    def metadata(self) -> dict:
        return {r["key"]: _json(r["value"]) for r in self._db.execute("SELECT key,value FROM metadata")}

    def kana_headers(self) -> list[dict]:
        """Insertion-ordered catalogue columns only, without decoding rich payloads."""
        return [dict(row) for row in self._db.execute(
            "SELECT id,hiragana,katakana,romaji,category FROM kana ORDER BY rowid")]

    def kana_list(self, category: str | None = None, tier: str | None = None) -> list[dict]:
        # One small immutable catalogue serves many queue/prerequisite visits.
        # External content replacement is observed on the next call; learner
        # state, eligibility and questions are never stored in this cache.
        version = self._db.execute("PRAGMA data_version").fetchone()[0]
        cached = self._kana_records_cache
        if cached is None or cached[0] != version:
            records = []
            for row in self._db.execute("SELECT * FROM kana ORDER BY rowid"):
                columns = dict(row)
                payload = _json(columns.pop("payload", None), {})
                records.append((columns, _payload_copier(payload)))
            cached = (version, tuple(records))
            if not self._db.in_transaction:
                self._kana_records_cache = cached
        results = []
        for columns, copy_payload in cached[1]:
            if category and columns.get("category") != category or tier and columns.get("tier") != tier:
                continue
            payload = copy_payload()
            record = dict(payload) if isinstance(payload, dict) else {"payload": payload}
            record.update(columns)
            record["payload"] = payload
            results.append(record)
        return results

    _bundled_kana_list = kana_list

    def get_kana(self, content_id: str) -> dict | None:
        result = self._record(self._db.execute("SELECT * FROM kana WHERE id=?", (content_id,)).fetchone())
        if result is not None:
            result["origins"] = [self._record(r) for r in self._db.execute("SELECT * FROM origin WHERE kana_id=? ORDER BY id", (content_id,))]
            pronunciation_ids = {pid for origin in result["origins"] for pid in origin.get("modern_dialect_pronunciation_ids", [])}
            pronunciation_ids.update(result.get("enrichment", {}).get("modern_dialect_reading_ids", []))
            result["dialects"] = [reading for pid in sorted(pronunciation_ids) if (reading := self.get_dialect(pid)) is not None]
            result["example_words"] = self.kana_words(content_id, limit=12)
            result["words"] = result["example_words"]
        return result

    def _kana_context_symbols(self, script):
        """Read-only scalar projection; no examples or payload copies needed.

        Custom catalogue implementations retain the ordinary kana_list path.
        A transaction uses its own SQLite view without publishing cache state.
        """
        if (script not in {'hiragana', 'katakana'} or
                getattr(self.kana_list, '__func__', None) is not ContentRepository._bundled_kana_list):
            return None
        db = self._db
        version = (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0])
        cached = self._kana_context_symbols_cache.get(script)
        if not db.in_transaction and cached is not None and cached[0] == version:
            return cached[1]
        columns = {r[1] for r in db.execute('PRAGMA table_info(kana)')}
        if not {'id', script} <= columns:
            return None  # Minimal/custom stores may carry these in payload.
        from .domain import normalize_answer
        by_symbol = {}
        for identity, symbol in db.execute('SELECT id,'+script+' FROM kana ORDER BY rowid'):
            if identity and symbol:
                by_symbol.setdefault(normalize_answer(symbol), set()).add(identity)
        symbols = MappingProxyType({symbol: next(iter(ids)) for symbol, ids in by_symbol.items() if len(ids) == 1})
        if not db.in_transaction and version == (db.total_changes, db.execute('PRAGMA data_version').fetchone()[0]):
            self._kana_context_symbols_cache[script] = version, symbols
        return symbols

    def get_dialect(self, reading_id: str) -> dict | None:
        result = self._record(self._db.execute("SELECT * FROM dialect WHERE id=?", (reading_id,)).fetchone())
        if result:
            result["locality"] = self._record(self._db.execute("SELECT * FROM locality WHERE id=?", (result["locality_id"],)).fetchone())
            result["locality_label"] = (result["locality"] or {}).get("label", result["locality_id"])
        return result

    def dialects_for_hanzi(self, hanzi: str) -> list[dict]:
        return [self.get_dialect(r[0]) for r in self._db.execute("SELECT id FROM dialect WHERE hanzi=? ORDER BY locality_id,id", (hanzi,))]

    def historical_for_hanzi(self, hanzi: str) -> list[dict]:
        return [self._record(r) for r in self._db.execute("SELECT * FROM historical WHERE hanzi=? ORDER BY id", (hanzi,))]

    def character_synthesis_for_hanzi(self, character: str) -> list[dict]:
        """Unreviewed character-input demos; never a phonetic reading attachment."""
        return [self._record(r) for r in self._db.execute(
            "SELECT * FROM character_synthesis WHERE hanzi=? ORDER BY id", (character,))]

    def historical_characters(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT DISTINCT hanzi FROM historical WHERE hanzi IS NOT NULL AND hanzi<>'' ORDER BY hanzi")]

    def variants_for_hanzi(self, hanzi: str) -> list[dict]:
        return [self._record(r) for r in self._db.execute("SELECT * FROM orthographic_relation WHERE character=? OR related_character=?", (hanzi,hanzi))]

    def localities(self) -> list[dict]:
        return [self._record(r) for r in self._db.execute("SELECT * FROM locality ORDER BY id")]

    def source(self, source_id: str) -> dict | None:
        return self._record(self._db.execute("SELECT * FROM source WHERE id=?", (source_id,)).fetchone())

    def sources(self, include_citations: bool = False) -> list[dict]:
        result = [self._record(r) for r in self._db.execute("SELECT * FROM source ORDER BY id")]
        return result if include_citations else [r for r in result if r.get("record_kind") != "citation"]

    def search_words(self, query: str = "", limit: int = 50, collection: str | None = None, offset: int = 0) -> list[dict]:
        return [self._record(r) for r in self._word_rows(query, limit, collection, offset, "w.*")]

    def word_list_page(self, query: str = "", limit: int = 30, collection: str | None = None, offset: int = 0) -> dict:
        """Small display projection plus reliable last-page information."""
        limit = max(1, min(100, int(limit)))
        columns = """w.id,w.form,w.reading,w.gloss,w.rank,w.collection,
            json_extract(w.payload,'$.display_reading') AS display_reading,
            json_extract(w.payload,'$.chinese_gloss') AS chinese_gloss,
            json_extract(w.payload,'$.pitch_accent') AS pitch_accent"""
        rows = self._word_rows(query, limit + 1, collection, offset, columns)
        return {"words": [{**dict(r), "pitch_accent": _json(r["pitch_accent"], [])} for r in rows[:limit]], "has_next": len(rows) > limit}

    def _word_rows(self, query, limit, collection, offset, columns):
        limit = max(1, min(1000, int(limit)))
        clauses, params = [], []
        query = normalize_search(query)
        join = ""
        order = "w.rank,w.id"
        if query:
            literal = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            # Group by id before joining, so several matching spellings cannot
            # duplicate a word or consume result-page slots. Exact matches win
            # over prefixes and substrings, then primary forms over aliases.
            join = """ JOIN (SELECT word_id,MIN((CASE WHEN term=? THEN 0
                WHEN term LIKE ? ESCAPE '\\' THEN 10 ELSE 20 END)+priority) AS score
                FROM word_search WHERE term LIKE ? ESCAPE '\\' GROUP BY word_id) hit
                ON hit.word_id=w.id"""
            params.extend([query, literal + "%", "%" + literal + "%"])
            order = "hit.score,w.rank,w.id"
        if collection:
            clauses.append("EXISTS (SELECT 1 FROM word_collection wc WHERE wc.word_id=w.id AND wc.collection=?)")
            params.append(collection)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        params.extend([limit, max(0, int(offset))])
        return self._db.execute("SELECT " + columns + " FROM word w" + join + where + " ORDER BY " + order + " LIMIT ? OFFSET ?", params).fetchall()

    def get_word(self, word_id: str) -> dict | None:
        result = self._record(self._db.execute("SELECT * FROM word WHERE id=?", (word_id,)).fetchone())
        if result is not None:
            result["collections"] = [r[0] for r in self._db.execute("SELECT collection FROM word_collection WHERE word_id=? ORDER BY collection", (word_id,))]
            result["examples"] = self.word_examples(word_id)
            result["sources"] = [item for sid in result.get("source_ids", []) if (item := self.source(sid)) is not None]
        return result

    def word_examples(self, word_id: str, limit: int = 10) -> list[dict]:
        limit = max(1, min(100, int(limit)))
        reviewed = self.lexical_review
        results = []
        if reviewed:
            for decision in reviewed.word_examples(word_id, limit):
                original = self._record(self._db.execute("SELECT * FROM example WHERE id=?", (decision["id"],)).fetchone())
                results.append(reviewed.project(decision, original))
        if len(results) < limit:
            seen = {x["id"] for x in results}
            rows = self._db.execute("SELECT e.*,we.sense_id AS matched_sense_id FROM example e JOIN word_example we ON we.example_id=e.id WHERE we.word_id=? ORDER BY e.id,we.sense_id", (word_id,))
            for row in rows:
                if row["id"] in seen or reviewed and reviewed.get(row["id"]) is not None:
                    # Accepted and uncertain links already came from the review;
                    # an explicitly rejected original must never reappear here.
                    continue
                results.append(self._record(row))
                seen.add(row["id"])
                if len(results) >= limit:
                    break
        return results

    def get_example(self, identity: str) -> dict | None:
        original = self._record(self._db.execute("SELECT * FROM example WHERE id=?", (identity,)).fetchone())
        if original and self.lexical_review and (review := self.lexical_review.get(identity)):
            return self.lexical_review.project(review, original)
        return original

    def example_links(self, identity: str) -> list[dict]:
        """Complete reverse identities for impact review, never a display limit."""
        links = [{'word_id': r['word_id'], 'sense_id': r['sense_id'], 'origin': 'dictionary', 'active': None}
                 for r in self._db.execute('SELECT word_id,sense_id FROM word_example WHERE example_id=? ORDER BY word_id,sense_id', (identity,))]
        reviewed = self.lexical_review.get(identity) if self.lexical_review else None
        for link in (reviewed or {}).get('associations', []):
            for sense in link.get('sense_ids', []) or ['']:
                links.append({'word_id': link['entry_id'], 'sense_id': sense, 'origin': 'lexical_review',
                              'active': bool(link.get('active')), 'status': link.get('status', 'uncertain')})
        return links

    def reviewed_example_page(self, query="", state="all", offset=0, limit=10, *, additional_ids=(), display_overrides=None):
        if not self.lexical_review:
            return {"total": 0, "rows": []}
        page = self.lexical_review.candidates(query, state, offset, limit,
            additional_ids=additional_ids, display_overrides=display_overrides)
        return {"total": page["total"], "rows": [self.get_example(r["id"]) for r in page["rows"]]}

    def kana_words(self, kana_id: str, limit: int = 20) -> list[dict]:
        rows = self._db.execute("SELECT w.*,kw.reading AS matched_reading,kw.match_kind AS kana_match_kind FROM kana_word kw JOIN word w ON w.id=kw.word_id WHERE kw.kana_id=? ORDER BY CASE kw.match_kind WHEN 'whole_token' THEN 0 ELSE 1 END,kw.teaching_rank,w.rank,w.id", (kana_id,))
        found, results = set(), []
        for row in rows:
            if row["id"] in found:
                continue
            found.add(row["id"])
            results.append(self._record(row))
            if len(results) >= max(1, min(100, int(limit))):
                break
        return results

    def practice_words(self, include_research: bool = False) -> list[dict]:
        """Research words require a separate, explicit caller opt-in."""
        words = [self._record(r) for r in self._db.execute("SELECT * FROM word ORDER BY rank,id")]
        return [w for w in words if include_research or w.get("practice_approved") is True]

    def approved_word_count(self) -> int:
        return self._db.execute("SELECT COUNT(*) FROM word WHERE json_type(payload,'$.practice_approved')='true'").fetchone()[0]

    def practice_word_candidates(self, include_research: bool = False, *, collection: str | None = None) -> list[dict]:
        """Project only quiz fields; keep full attributed payloads in practice_words.

        json_type preserves strict editorial approval: numeric 1 and the string
        "true" do not grant the permission represented by the JSON boolean true.
        The packaged catalog is immutable. Cache only this projection, never
        learner eligibility, chosen questions, or randomized answer options.
        """
        if collection is not None and (not isinstance(collection, str) or not collection):
            raise ValueError("collection must be a nonempty string")
        key = (bool(include_research), collection)
        cached = self._practice_word_candidates_cache.get(key)
        if cached is not None:
            return [dict(record) for record in cached]
        clauses = [] if include_research else ["json_type(payload,'$.practice_approved')='true'"]
        params = []
        if collection is not None:
            clauses.append("EXISTS (SELECT 1 FROM word_collection wc WHERE wc.word_id=word.id AND wc.collection=?)")
            params.append(collection)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._db.execute("""SELECT id,reading,gloss,
            json_extract(payload,'$.display_reading') AS display_reading,
            json_extract(payload,'$.chinese_gloss') AS chinese_gloss,
            json_extract(payload,'$.practice_gloss_zh') AS practice_gloss_zh,
            json_type(payload,'$.practice_approved')='true' AS practice_approved
            FROM word""" + where + " ORDER BY rank,id", params)
        result = []
        for row in rows:
            record = dict(row)
            record["practice_approved"] = record["practice_approved"] == 1
            result.append(record)
        self._practice_word_candidates_cache[key] = tuple(dict(record) for record in result)
        return result

    def all_kana_symbols(self, script: str = "hiragana") -> list[str]:
        if script not in {"hiragana", "katakana"}:
            raise ValueError("script must be hiragana or katakana")
        return list(dict.fromkeys(row[0] for row in self._db.execute(
            "SELECT " + script + " FROM kana ORDER BY rowid") if row[0]))
