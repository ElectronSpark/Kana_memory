"""Validate research inputs and build a local, explicitly non-release preview catalog.

Run from any directory: python -X utf8 tools/prepare_catalog.py
No network; no product UI; no learner progress database is touched.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "staging"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def index_key(text):
    # Search/index normalization ONLY. Never reuse for script-sensitive grading.
    text = unicodedata.normalize("NFKC", text)
    return "".join(chr(ord(c) - 0x60) if 0x30A1 <= ord(c) <= 0x30F6 else c for c in text)


def unique(records, key, label):
    result = {}
    for record in records:
        value = record[key]
        if value in result:
            raise ValueError(f"Duplicate {label}: {value}")
        result[value] = record
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    checks = []
    for filename in ("content_schema.sql", "progress_schema.sql"):
        with closing(sqlite3.connect(":memory:")) as db, db:
            db.executescript((ROOT / "docs" / filename).read_text(encoding="utf-8"))
            assert not db.execute("PRAGMA foreign_key_check").fetchall()
            assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        checks.append(f"{filename}: SQLite DDL executes and integrity checks pass")

    inventory = read_json(ROOT / "data/curated/kana_inventory.json")
    origins_doc = read_json(ROOT / "data/curated/kana_origin_samples.json")
    dialect_doc = read_json(ROOT / "data/curated/dialect_samples.json")
    source_doc = read_json(ROOT / "data/curated/sources_kana.json")
    lexical_source_doc = read_json(ROOT / "data/sources_lexicon.json")
    kana = unique(inventory["records"], "id", "kana")
    origins = unique(origins_doc["records"], "id", "origin")
    dialects = unique(dialect_doc["records"], "id", "dialect reading")
    sources = unique(source_doc["records"], "id", "source")
    localities = unique(dialect_doc["localities"], "id", "locality")
    assert len(kana) == inventory["record_count"]
    assert sum(k["category"] == "basic" for k in kana.values()) == 46
    assert len(origins) == origins_doc["record_count"]
    assert len(dialects) == dialect_doc["record_count"]

    def check_sources(record):
        for sid in record.get("source_ids", []):
            assert sid in sources, f"Unknown source: {sid}"

    for record in kana.values():
        check_sources(record)
        for component in record.get("components", []):
            # Dataset components are stable IDs, not character guesses.
            assert component in kana, f"Unknown kana component: {component}"
        for origin_id in record.get("origin_record_ids", []):
            assert origin_id in origins
        for form in (record.get("hiragana"), record.get("katakana")):
            if form:
                assert unicodedata.normalize("NFC", form) == form
    for record in origins.values():
        assert record["kana_id"] in kana
        for family in ("hiragana_origins", "katakana_origins"):
            assert record.get(family), f"Origin family missing: {record['id']} {family}"
            for claim in record[family]:
                check_sources(claim)
                assert claim.get("source_locator")
        for pronunciation_id in record.get("modern_dialect_pronunciation_ids", []):
            assert pronunciation_id in dialects
    for record in dialects.values():
        check_sources(record)
        assert record["locality_id"] in localities
        assert record.get("source_locator")
    checks.append("Kana IDs, component IDs, origin links, dialect localities and source locators resolve")

    files = sorted((ROOT / "data/candidates").glob("*.jsonl"))
    words, membership, file_counts = {}, defaultdict(set), {}
    extra_evidence = []
    for path in files:
        rows = read_lines(path)
        file_counts[path.name] = len(rows)
        if path.name == "japanese_examples.jsonl":
            continue
        for record in rows:
            if "entry_id" not in record:
                if "jmdict_entry_ids" in record:
                    extra_evidence.append((path.name, record))
                continue
            eid = record["entry_id"]
            if eid in words:
                # Collections may duplicate entries; actual dictionary payload must agree.
                for field in ("kanji", "readings", "senses"):
                    assert words[eid][field] == record[field], f"Conflicting dictionary record: {eid}/{field}"
            else:
                words[eid] = record
            membership[eid].add(path.stem)
            assert record.get("source_url") and record.get("license") and record.get("source_ids")

    examples = unique(read_lines(ROOT / "data/candidates/japanese_examples.jsonl"), "example_id", "example")
    for example in examples.values():
        assert example.get("license") and example.get("owner_at_export") and example.get("attribution")
        for association in example["associations"]:
            eid = association["entry_id"]
            assert eid in words, f"Dangling example word: {eid}"
            assert association["sense_id"] in {s["sense_id"] for s in words[eid]["senses"]}
    for word in words.values():
        for example_id in word.get("example_ids", []):
            assert example_id in examples
            assert any(a["entry_id"] == word["entry_id"] for a in examples[example_id]["associations"])
    checks.append("Lexical IDs, duplicate record agreement, sentence authors/licenses and sense associations resolve")
    lexical_sources = unique(lexical_source_doc['sources'], 'source_id', 'lexical source')
    citations, evidence_records = {}, {}
    for word in words.values():
        for sid in word['source_ids']:
            if sid.startswith('enwiktionary:'):
                continue  # Resolve against the retained revision evidence below.
            assert sid.startswith('jmdict:')
            citations[sid] = {'source_family': 'jmdict', 'url': word['source_url'],
                              'license': word['license'], 'snapshot': word.get('source_snapshot_date'),
                              'attribution': word.get('copyright')}
    for example in examples.values():
        for sid in example['source_ids']:
            assert sid.startswith('tatoeba:')
            citations[sid] = {'source_family': 'tatoeba', 'url': example['attribution_url'],
                              'license': example['license'], 'attribution': example['attribution']}
    for filename, evidence in extra_evidence:
        for eid in evidence['jmdict_entry_ids']:
            assert eid in words, f"Unresolved cross-dictionary evidence: {eid}"
        if evidence.get('wiktionary_revision_id'):
            assert evidence.get('permalink') and evidence.get('license') and evidence.get('attribution')
            assert str(evidence['wiktionary_revision_id']) in evidence['permalink']
            assert evidence.get('source_ids')
            for sid in evidence['source_ids']:
                assert sid.startswith('enwiktionary:')
                citations[sid] = {'source_family': 'enwiktionary', 'url': evidence['permalink'],
                                  'license': evidence['license'], 'attribution': evidence['attribution'],
                                  'revision': evidence['wiktionary_revision_id']}
        else:
            assert not evidence.get('source_expression'), 'No sourced assertion without a source revision'
        rid = hashlib.sha256((filename + json.dumps(evidence, sort_keys=True, ensure_ascii=False)).encode('utf-8')).hexdigest()
        evidence_records[rid] = {'collection_file': filename, 'record': evidence,
                                'record_kind': 'source_evidence' if evidence.get('wiktionary_revision_id') else 'lookup_attempt'}
    for word in words.values():
        assert all(sid in citations for sid in word['source_ids']), f"Missing lexical citation: {word['entry_id']}"
    checks.append("Cross-dictionary entry links, revision URLs and attribution resolve; lookup failures remain distinct")

    # First review batch, not a completed 1000-word curriculum. Avoid duplicate pages/entries.
    english_review_batch, seen_pages, seen_entries = [], set(), set()
    crosscheck_path = ROOT / 'data/candidates/english_borrowing_crosscheck_candidates.jsonl'
    for evidence in read_lines(crosscheck_path) if crosscheck_path.exists() else []:
        page_id = evidence['wiktionary_page_id']
        entry_ids = set(evidence['jmdict_entry_ids'])
        if page_id in seen_pages or entry_ids & seen_entries:
            continue
        assert evidence['source_language'] == 'eng' and evidence['source_expression']
        assert evidence['jmdict_has_common_priority_tag']
        row = {
            'candidate_id': f'english-review:{page_id}', 'review_rank': len(english_review_batch) + 1,
            'headword': evidence['headword'], 'english_source_expression': evidence['source_expression'],
            'english_original_definition': None, 'english_original_pos': None,
            'jmdict_entry_ids': evidence['jmdict_entry_ids'],
            'japanese_entries': [words[eid] for eid in evidence['jmdict_entry_ids']],
            'etymology_evidence': evidence,
            'sense_alignment_status': 'pending_editor_review',
            'mandarin_equivalents_status': 'pending_evidence',
            'release_ready': False,
        }
        english_review_batch.append(row)
        seen_pages.add(page_id)
        seen_entries.update(entry_ids)
        if len(english_review_batch) == 1000:
            break
    (OUT / 'english_loanwords_1000_candidates.jsonl').write_text(
        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in english_review_batch), encoding='utf-8')

    # Separate spelling match from component exposure. Neither says the word is release-ready.
    forms = defaultdict(set)
    for kid, record in kana.items():
        for form in (record.get("hiragana"), record.get("katakana")):
            if form:
                forms[index_key(form)].add(kid)
    tokens_by_first = defaultdict(list)
    for form in sorted(forms, key=lambda x: (-len(x), x)):
        tokens_by_first[form[0]].append(form)
    index = []
    unmatched = Counter()
    for eid, word in sorted(words.items()):
        for reading in word["readings"]:
            original = reading["text"]
            normalized = index_key(original)
            positions, i = defaultdict(list), 0
            while i < len(normalized):
                match = next((f for f in tokens_by_first[normalized[i]] if normalized.startswith(f, i)), None)
                if match is None:
                    unmatched[normalized[i]] += 1
                    i += 1
                    continue
                for kid in forms[match]:
                    positions[(kid, "whole_token")].append(i)
                    for component in kana[kid].get("components", []):
                        positions[(component, "component_of_combination")].append(i)
                i += len(match)
            if reading.get("no_kanji"):
                written_forms = [original]
            elif reading.get("applies_to_kanji"):
                written_forms = reading["applies_to_kanji"]
            else:
                written_forms = [k["text"] for k in word["kanji"]] or [original]
            for (kid, kind), offsets in sorted(positions.items()):
                index.append({
                    "kana_id": kid, "entry_id": eid, "reading": original,
                    "written_forms": written_forms, "match_kind": kind,
                    "normalized_codepoint_offsets": sorted(set(offsets)),
                    "collections": sorted(membership[eid]),
                    "example_ids": word.get("example_ids", []),
                    "review_status": "mechanical_index_requires_teaching_review",
                })
    index_doc = {
        "schema_version": "1.0", "status": "research_index_not_a_release_curriculum",
        "normalization": "NFKC then katakana-to-hiragana for index only; longest spelling token first; original reading retained",
        "limitations": ["Spelling index, not a phonetic syllabifier or pronunciation validator.",
                       "Component matches are separate from whole-token matches.",
                       "A dictionary example sentence may use another inflected form; do not imply it contains every indexed kana.",
                       "Word commonness, each reading variant and suitability need editorial review."],
        "record_count": len(index), "records": index,
    }
    save(OUT / "kana_word_index.json", index_doc)

    dbpath = OUT / "catalog.preview.sqlite3"
    # Replace our generated preview only; never an application content/progress database.
    temp_dbpath = OUT / "catalog.preview.building.sqlite3"
    with closing(sqlite3.connect(temp_dbpath)) as db, db:
        db.executescript("""
        PRAGMA foreign_keys=ON;
        DROP TABLE IF EXISTS kana_word; DROP TABLE IF EXISTS example;
        DROP TABLE IF EXISTS lexeme; DROP TABLE IF EXISTS kana;
        DROP TABLE IF EXISTS research_record; DROP TABLE IF EXISTS metadata;
        CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL CHECK(json_valid(value_json)));
        CREATE TABLE kana(id TEXT PRIMARY KEY,record_json TEXT NOT NULL CHECK(json_valid(record_json)));
        CREATE TABLE lexeme(id TEXT PRIMARY KEY,record_json TEXT NOT NULL CHECK(json_valid(record_json)),collections_json TEXT NOT NULL CHECK(json_valid(collections_json)));
        CREATE TABLE example(id TEXT PRIMARY KEY,record_json TEXT NOT NULL CHECK(json_valid(record_json)));
        CREATE TABLE research_record(kind TEXT NOT NULL,id TEXT NOT NULL,record_json TEXT NOT NULL CHECK(json_valid(record_json)),PRIMARY KEY(kind,id));
        CREATE TABLE kana_word(kana_id TEXT NOT NULL REFERENCES kana,entry_id TEXT NOT NULL REFERENCES lexeme,reading TEXT NOT NULL,match_kind TEXT NOT NULL,record_json TEXT NOT NULL CHECK(json_valid(record_json)),PRIMARY KEY(kana_id,entry_id,reading,match_kind));
        CREATE INDEX word_to_kana ON kana_word(entry_id);
        """)
        pack = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))
        db.execute("INSERT INTO metadata VALUES(?,?)", ("status", pack("research_staging_not_release_data")))
        db.executemany("INSERT INTO kana VALUES(?,?)", [(k, pack(v)) for k, v in kana.items()])
        db.executemany("INSERT INTO lexeme VALUES(?,?,?)", [(k, pack(v), pack(sorted(membership[k]))) for k, v in words.items()])
        db.executemany("INSERT INTO example VALUES(?,?)", [(k, pack(v)) for k, v in examples.items()])
        for kind, records in (("origin", origins), ("dialect", dialects), ("source", sources), ("locality", localities)):
            db.executemany("INSERT INTO research_record VALUES(?,?,?)", [(kind, k, pack(v)) for k, v in records.items()])
        for kind, records in (("lexical_source", lexical_sources), ("citation", citations), ("lexical_evidence", evidence_records)):
            db.executemany("INSERT INTO research_record VALUES(?,?,?)", [(kind, k, pack(v)) for k, v in records.items()])
        db.executemany("INSERT INTO research_record VALUES(?,?,?)", [('english_review_batch', r['candidate_id'], pack(r)) for r in english_review_batch])
        db.executemany("INSERT INTO kana_word VALUES(?,?,?,?,?)", [(r["kana_id"], r["entry_id"], r["reading"], r["match_kind"], pack(r)) for r in index])
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    temp_dbpath.replace(dbpath)
    checks.append("Generated preview SQLite: foreign_key_check and integrity_check pass")

    whole = Counter(r["kana_id"] for r in index if r["match_kind"] == "whole_token")
    counts = {"kana": len(kana), "basic_kana_pairs": sum(k["category"] == "basic" for k in kana.values()),
              "origin_pairs": len(origins), "dialect_readings": len(dialects),
              "unique_research_lexemes": len(words), "japanese_examples": len(examples),
              "lexical_citations": len(citations), "cross_dictionary_evidence_records": len(evidence_records),
              "english_1000_review_batch": len(english_review_batch),
              "kana_word_links": len(index), "kana_with_whole_token_word_links": len(whole),
              "historical_reading_pairs_completed": sum(bool(o.get("historical_chinese_readings")) and bool(o.get("historical_japanese_readings")) for o in origins.values()),
              "licensed_audio_files": 0, "editor_approved_release_words": 0}
    missing = [{"kana_id": k, "hiragana": v.get("hiragana"), "katakana": v.get("katakana"), "tier": v.get("tier")}
               for k, v in kana.items() if not whole[k]]
    manifest_paths = sorted(set([*files, *list((ROOT / "data/curated").glob("*.json")), ROOT / "data/sources_lexicon.json",
                                 ROOT / "docs/content_schema.sql", ROOT / "docs/progress_schema.sql"]))
    manifest = [{"path": str(p.relative_to(ROOT)).replace("\\", "/"),
                 "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in manifest_paths]
    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "phase": "preparation", "release_ready": False, "counts": counts,
        "candidate_file_counts": file_counts, "dialect_locality_counts": dict(Counter(r["locality_id"] for r in dialects.values())),
        "validation_checks": checks, "kana_without_whole_token_word_links": missing,
        "unmatched_reading_characters": dict(unmatched), "input_manifest": manifest,
        "uncompleted_requirements": [
            "Source-critical historical Chinese and Japanese readings at each kana's formation stage",
            "Editorially verified 1000 common English loans, original English details, sense-linked Mandarin equivalents",
            "Editorially verified 2000 native/Sino-Japanese words and their etymology categories",
            "Multiple suitable examples per word; Chinese dialect word-level alignments and audio",
            "Pixel 9 Pro handwriting, Japanese TTS and offline model tests",
        ],
    }
    save(OUT / "preparation_audit.json", audit)
    print(json.dumps({"counts": counts, "checks": checks, "preview_database": str(dbpath)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
