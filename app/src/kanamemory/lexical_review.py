"""Read-only, fingerprinted sentence decisions, separate from learner state.

The dictionary database remains the source snapshot. A review never changes a
word, memory unit, deadline or queued question. Shared references require an
explicit accepted sense and a resolved dictionary reading.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata

FORMAT = "kanamemory-lexical-sentence-review"
VERSION = 1
RESOURCE = "lexical_sentence_review.sqlite3"


def fingerprint(identity, text, payload):
    raw = json.dumps({"id": identity, "text": text, "payload": payload},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def word_fingerprint(word):
    value = {key: word.get(key) for key in ("senses", "readings", "kanji")}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def reading_key(value):
    """Script/width variants are equivalent; different pronunciations are not."""
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c
                   for c in unicodedata.normalize("NFKC", value))


class LexicalReview:
    def __init__(self, path, source_db):
        self.path = Path(path).resolve()
        self.source_db = source_db
        self.db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA query_only=ON")
        self.meta = {r[0]: json.loads(r[1]) for r in self.db.execute("SELECT key,value FROM metadata")}
        if (self.meta.get("format") != FORMAT or self.meta.get("version") != VERSION
                or self.meta.get("complete") is not True):
            self.db.close()
            raise ValueError("例句审校资料不完整或版本不支持。")
        self._valid = {}
        self._valid_words = {}
        self._candidate_states = {}
        self._provenance = {}

    def close(self):
        self.db.close()
        self._valid.clear()
        self._valid_words.clear()
        self._candidate_states.clear()
        self._provenance.clear()

    def _matches(self, row):
        identity = row["id"]
        if identity not in self._valid:
            original = self.source_db.execute("SELECT text,payload FROM example WHERE id=?", (identity,)).fetchone()
            self._valid[identity] = bool(original and fingerprint(identity, original[0], json.loads(original[1])) == row["fingerprint"])
        return self._valid[identity]

    def get(self, identity):
        row = self.db.execute("SELECT * FROM review WHERE id=?", (identity,)).fetchone()
        return self._decision(row) if row and self._matches(row) else None

    def _word_matches(self, identity):
        if identity not in self._valid_words:
            original = self.source_db.execute("SELECT payload FROM word WHERE id=?", (identity,)).fetchone()
            expected = self.db.execute("SELECT fingerprint FROM reviewed_word WHERE id=?", (identity,)).fetchone()
            self._valid_words[identity] = bool(original and expected and word_fingerprint(json.loads(original[0])) == expected[0])
        return self._valid_words[identity]

    def _decision(self, row):
        decision = json.loads(row["payload"])
        for link in decision["associations"]:
            if not self._word_matches(link["entry_id"]):
                link.update(active=False, resolved_reading="", status="uncertain",
                            reason="词典义项或读法已变更，原关联需要重新核对。")
                decision["state"] = "uncertain"
        return decision

    def word_examples(self, word_id, limit=10):
        """One sentence per card; rejected links never consume result slots."""
        rows = self.db.execute("""SELECT DISTINCT r.* FROM review r
            JOIN association a ON a.example_id=r.id
            WHERE a.entry_id=? AND a.status != 'rejected'
            ORDER BY CASE WHEN r.state='uncertain' THEN 1 ELSE 0 END,r.id""", (word_id,))
        result = []
        for row in rows:
            if not self._matches(row):
                continue
            review = self._decision(row)
            links = [a for a in review["associations"] if a["entry_id"] == word_id and a["status"] != "rejected"]
            review["matched_associations"] = links
            result.append(review)
            if len(result) >= max(1, min(100, int(limit))):
                break
        return result

    def has_original(self, identity):
        return self.db.execute("SELECT 1 FROM review WHERE id=?", (identity,)).fetchone() is not None

    def candidates(self, query="", state="all", offset=0, limit=10, *, additional_ids=(), display_overrides=None):
        display_overrides = display_overrides or {}
        needle = query.strip().casefold()
        additional_ids = set(additional_ids) | {cid for cid, row in display_overrides.items()
            if needle in row['search_text'].casefold()}
        if state not in {"all", "reviewed", "corrected", "uncertain"}:
            raise ValueError("不支持的审校状态。")
        params, clauses = [], []
        if query.strip():
            literal = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("search_text LIKE ? ESCAPE '\\'")
            params.append("%" + literal + "%")
            if additional_ids:
                # Teaching search can include current personal translations;
                # original audit text and fingerprint/state filtering remain.
                clauses[-1] = '(' + clauses[-1] + ' OR id IN (SELECT value FROM json_each(?)))'
                params.append(json.dumps(sorted(set(additional_ids))))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        # Counts and pagination use the same fingerprint-valid set; stale review
        # files must not advertise unavailable rows or silently bless new text.
        ids = []
        # Pagination must not reload every sentence's full audit payload. Like
        # source fingerprints, effective states belong to this read-only bundle
        # and are checked once for this repository connection.
        for row in self.db.execute("SELECT id,fingerprint FROM review" + where + " ORDER BY id", params):
            if not self._matches(row):
                continue
            identity = row["id"]
            current = display_overrides.get(identity)
            if current is not None and needle and needle not in current['search_text'].casefold(): continue
            if state != "all":
                if identity not in self._candidate_states:
                    self._candidate_states[identity] = self.get(identity)["state"]
                if (current['state'] if current is not None else self._candidate_states[identity]) != state:
                    continue
            ids.append(identity)
        offset, limit = max(0, int(offset)), max(1, min(100, int(limit)))
        return {"total": len(ids), "rows": [self.get(eid) for eid in ids[offset:offset + limit]]}

    @staticmethod
    def project(review, original):
        """Flatten corrected display fields without relabeling the source text."""
        result = deepcopy(original)
        result["text"] = review["text"]
        result["review"] = deepcopy(review)
        result["chinese_translation"] = review["chinese_translation"]["text"]
        result["review_state"] = review["state"]
        links = review.get("matched_associations", [])
        result["matched_sense_ids"] = sorted({s for a in links for s in a.get("sense_ids", [])})
        result["matched_sense_id"] = next(iter(result["matched_sense_ids"]), "")
        return result

    def _shared_rows(self, items, *, target_id=None, example_id=None):
        """Read only the relevant reviewed links, never all 8,823 at startup."""
        values = items.values() if isinstance(items, dict) else items
        for item in values:
            if target_id is not None and item["id"] != target_id:
                continue
            word_id, sense_id = item.get("word_id"), item.get("sense_id")
            if item["id"] == "word:tegami:letter" and item.get("reading") == "てがみ":
                word_id, sense_id = "jmdict:1327720", "jmdict:1327720:sense:1"
            if not word_id or not sense_id or item.get("family") != "word":
                continue
            # Reviewed foundation mappings are explicit, not title matching.
            rows = self.db.execute("""SELECT DISTINCT r.* FROM review r JOIN association a ON a.example_id=r.id
                WHERE a.entry_id=? AND a.sense_id=? AND a.reading=? AND a.active=1 ORDER BY r.id""",
                (word_id, sense_id, reading_key(item.get("reading", ""))))
            for row in rows:
                if not self._matches(row):
                    continue
                review = self._decision(row)
                from .example_store import example_id_for, reference_id_for
                eid = example_id_for(review["text"])
                if example_id is not None and eid != example_id:
                    continue
                example = {"id": eid, "revision": "1", "text": review["text"],
                    "translation": review["chinese_translation"]["text"], "reading": "",
                    "source_ids": [review["id"], "kanamemory-lexical-review-v1"],
                    "lexical_review_ids": [review["id"]],
                    "lexical_provenance": [self._source_snapshot(review)]}
                for link in review["associations"]:
                    if (not link.get("active") or link["entry_id"] != word_id or sense_id not in link["sense_ids"]
                            or reading_key(link.get("resolved_reading", "")) != reading_key(item.get("reading", ""))):
                        continue
                    ref = {"example_id": eid, "example_revision": "1", "target_id": item["id"],
                        "spans": deepcopy(link["spans"]), "hint": {"meaning": item.get("meaning", ""),
                            "reading": item["reading"]}, "reading_scope": "lemma",
                        "explanation": "", "weight": 1.0}
                    ref["id"] = reference_id_for(ref)
                    yield example, ref

    def _source_snapshot(self, review):
        """Attribution travels with exported text, independently of this database."""
        identity = review["id"]
        if identity not in self._provenance:
            row = self.source_db.execute("SELECT payload FROM example WHERE id=?", (identity,)).fetchone()
            original = json.loads(row[0])
            fields = ("text", "language", "example_id", "attribution", "attribution_url", "license",
                      "license_url", "owner_at_export", "source_modified", "content_origin", "source_ids")
            def credit(source):
                return {key: deepcopy(source[key]) for key in fields if source.get(key) is not None}
            source = credit(original)
            source["translations"] = [credit(t) for t in original.get("translations", [])]
            self._provenance[identity] = {
                "review_id": identity, "original": source, "reviewed_text": review["text"],
                "chinese_translation": review["chinese_translation"]["text"],
                "review_method": review.get("review_method", ""),
                "notes": review.get("notes", ""), "sources": deepcopy(review.get("sources", []))}
        return deepcopy(self._provenance[identity])

    def shared_pack(self, items, *, target_id=None, example_id=None):
        from .example_store import empty_pack, merge_example_metadata
        examples, refs = {}, {}
        for example, ref in self._shared_rows(items, target_id=target_id, example_id=example_id):
            existing = examples.get(example["id"])
            if existing:
                examples[example["id"]] = merge_example_metadata(existing, example)
            else:
                examples[example["id"]] = example
            refs[ref["id"]] = ref
        return {**empty_pack(), "examples": list(examples.values()), "references": list(refs.values())}
