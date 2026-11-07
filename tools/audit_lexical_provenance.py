"""Check every bundled dictionary record against its pinned primary data.

This is provenance/restriction validation, not a claim that a source sentence
or translation has received a linguistic review. The report keeps those two
statuses separate, including word-only links with no adjudicated sense.
"""
from collections import Counter
import bz2
import hashlib
import json
from pathlib import Path
import sqlite3

from collect_lexicon import entries, parse_entry, LANG

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/linguistic_audit/lexical_provenance.json"


def audit():
    db_path = ROOT / "app/src/kanamemory/resources/content.sqlite3"
    with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        words = {wid: {"record": json.loads(payload), "form": form, "reading": reading}
                 for wid, form, reading, payload in db.execute("SELECT id,form,reading,payload FROM word")}
        examples = {eid: {"text": text, **json.loads(payload)}
                    for eid, text, payload in db.execute("SELECT id,text,payload FROM example")}
        links = list(db.execute("SELECT word_id,example_id,sense_id FROM word_example"))
        foreign_keys = list(db.execute("PRAGMA foreign_key_check"))

    dictionary, source_links = {}, set()
    for filename in ("JMdict_e.gz", "JMdict_e_examp.gz"):
        for node in entries(ROOT / "data/raw" / filename):
            wid = "jmdict:" + node.findtext("ent_seq")
            if wid not in words:
                continue
            if filename == "JMdict_e.gz":
                dictionary[wid] = parse_entry(node)
            else:
                for number, sense in enumerate(node.findall("sense"), 1):
                    for ex in sense.findall("example"):
                        src = ex.find("ex_srce")
                        if src is None or src.get("exsrc_type") != "tat":
                            continue
                        text = next((s.text for s in ex.findall("ex_sent") if s.get(LANG) == "jpn"), None)
                        source_links.add((wid, "tatoeba:" + src.text, f"{wid}:sense:{number}", text))

    word_results, issues = [], []
    for wid, word in words.items():
        record, original = word["record"], dictionary.get(wid)
        differences = []
        if original:
            for field in ("kanji", "readings", "senses"):
                if record.get(field) != original[field]:
                    differences.append(field)
        reading = next((r for r in record["readings"] if r["text"] == word["reading"]), None)
        form_valid = reading is not None and (
            word["form"] == word["reading"] or (
                not reading.get("no_kanji")
                and word["form"] in {k["text"] for k in record["kanji"]}
                and (not reading.get("applies_to_kanji") or word["form"] in reading["applies_to_kanji"])))
        row = {"id": wid, "source_equal": original is not None and not differences,
               "differing_fields": differences, "selected_form_reading_compatible": bool(form_valid),
               "semantic_review": "not_performed_by_this_provenance_check"}
        if not original:
            row["note"] = "Not in the base pinned JMdict snapshot; inspect supplemental source."
        if not form_valid or differences:
            issues.append(row)
        word_results.append(row)

    tatoeba = {}
    with bz2.open(ROOT / "data/raw/jpn_sentences_detailed.tsv.bz2", "rt", encoding="utf-8") as stream:
        for line in stream:
            fields = line.rstrip("\r\n").split("\t")
            eid = "tatoeba:" + fields[0]
            if eid in examples:
                tatoeba[eid] = fields[2]
    example_results = []
    for eid, row in examples.items():
        source_equal = tatoeba.get(eid) == row["text"] if eid.startswith("tatoeba:") else None
        if source_equal is False:
            issues.append({"id": eid, "kind": "tatoeba_source_text_mismatch"})
        example_results.append({"id": eid, "source_text_equal": source_equal,
                                "curation_status": row.get("curation_status"),
                                "semantic_review": "not_performed_by_this_provenance_check"})
    link_results = []
    for wid, eid, sense in links:
        row = examples[eid]
        assoc = next((a for a in row.get("associations", [])
                      if a.get("entry_id") == wid and a.get("sense_id", "") == sense), None)
        source = (assoc or {}).get("alignment_source", "supplemental_authored")
        source_equal = (wid, eid, sense, row["text"]) in source_links if source == "JMdict_e_examp" else None
        sense_exists = not sense or sense in {s["sense_id"] for s in words[wid]["record"]["senses"]}
        if not sense_exists or source_equal is False or assoc is None:
            issues.append({"word_id": wid, "example_id": eid, "sense_id": sense,
                           "kind": "source_link_or_sense_mismatch", "source_equal": source_equal,
                           "sense_exists": sense_exists, "payload_association_present": assoc is not None})
        link_results.append({"word_id": wid, "example_id": eid, "sense_id": sense,
                             "method": source, "source_equal": source_equal,
                             "association_status": "word_only_not_sense_verified" if not sense else
                             "primary_source_indexed" if source_equal else "authored_or_other_source_not_adjudicated"})
    result = {
        "format": "kanamemory-lexical-provenance-audit", "version": 1,
        "database_sha256": hashlib.sha256(db_path.read_bytes()).hexdigest(),
        "sources": ["https://www.edrdg.org/wiki/JMdict-EDICT_Dictionary_Project.html",
                    "https://www.edrdg.org/wiki/Sentence-Dictionary_Linking.html"],
        "scope": "All bundled dictionary rows and dictionary/example links, distinct from the reviewed course example pack.",
        "counts": {"words": len(words), "examples": len(examples), "links": len(links),
                   "link_status": dict(Counter(r["association_status"] for r in link_results)),
                   "example_status": dict(Counter(r["curation_status"] for r in example_results))},
        "foreign_key_issues": foreign_keys, "issues": issues,
        "words": word_results, "examples": example_results, "links": link_results,
        "limitations": ["Source equality does not prove semantic correctness or translation quality.",
                        "Word-only matches are not promoted to sense-specific lesson references.",
                        "This report does not claim human/native-speaker approval."]}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": result["counts"], "issues": len(issues),
                      "missing_base_entries": [r["id"] for r in word_results if "note" in r]}, ensure_ascii=False))
    return result


if __name__ == "__main__":
    audit()
