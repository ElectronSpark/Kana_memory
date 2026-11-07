"""Collect traceable vocabulary candidates, not a reviewed learning curriculum.

Python standard library only. Run from any directory:
    python kana_memory/tools/collect_lexicon.py
    python kana_memory/tools/collect_lexicon.py --download

Existing raw snapshots make selection deterministic. --download updates snapshots;
use --expected-sha256 to pin a JMdict snapshot when rebuilding a release.
No Android/business logic is implemented here.
"""
from __future__ import annotations

import argparse
import bz2
from collections import Counter
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "candidates"
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
COMMON = {"news1", "ichi1", "spec1", "spec2", "gai1"}
SOURCES = {
    "JMdict_e.gz": "http://ftp.edrdg.org/pub/Nihongo/JMdict_e.gz",
    "JMdict_e_examp.gz": "http://ftp.edrdg.org/pub/Nihongo/JMdict_e_examp.gz",
    "jpn_sentences_detailed.tsv.bz2": "https://downloads.tatoeba.org/exports/per_language/jpn/jpn_sentences_detailed.tsv.bz2",
    "edrdg_licence.html": "https://www.edrdg.org/edrdg/licence.html",
    "edrdg_jmdict_documentation.html": "https://www.edrdg.org/wiki/JMdict-EDICT_Dictionary_Project.html",
    "edrdg_jmdict_dtd.html": "https://www.edrdg.org/jmdict/jmdict_dtd_h.html",
    "tatoeba_downloads.html": "https://tatoeba.org/en/downloads",
    "Tatoeba_terms.html": "https://tatoeba.org/en/terms_of_use",
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def save_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")


def download() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES.items():
        temp = RAW / (name + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": "KanaMemory-research/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, temp.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                stream.write(chunk)
        temp.replace(RAW / name)


def texts(node, tag: str) -> list[str]:
    return [x.text or "" for x in node.findall(tag)]


def entries(path: Path):
    # ElementTree expands JMdict's internal descriptive entities; it does not
    # retrieve external entities. Preserve the descriptions plus raw XML hash.
    with gzip.open(path, "rb") as stream:
        iterator = ET.iterparse(stream, events=("start", "end"))
        _, root = next(iterator)
        for event, node in iterator:
            if event == "end" and node.tag == "entry":
                yield node
                root.clear()


def parse_entry(node) -> dict:
    seq = node.findtext("ent_seq")
    kanji = [{"text": k.findtext("keb"), "info": texts(k, "ke_inf"), "priority": texts(k, "ke_pri")} for k in node.findall("k_ele")]
    readings = [{"text": r.findtext("reb"), "info": texts(r, "re_inf"), "priority": texts(r, "re_pri"), "applies_to_kanji": texts(r, "re_restr"), "no_kanji": r.find("re_nokanji") is not None} for r in node.findall("r_ele")]
    senses, inherited_pos = [], []
    for number, sense in enumerate(node.findall("sense"), 1):
        explicit_pos = texts(sense, "pos")
        inherited_pos = explicit_pos or inherited_pos
        lsources = [{"language": x.get(LANG, "eng"), "source_expression": x.text, "type": x.get("ls_type", "full"), "wasei": x.get("ls_wasei") == "y"} for x in sense.findall("lsource")]
        senses.append({
            "sense_id": f"jmdict:{seq}:sense:{number}", "sense_number": number,
            "kanji_restrictions": texts(sense, "stagk"), "reading_restrictions": texts(sense, "stagr"),
            "pos_explicit": explicit_pos, "pos_effective": inherited_pos[:],
            "field": texts(sense, "field"), "misc": texts(sense, "misc"), "dialect_tags": texts(sense, "dial"),
            "notes": texts(sense, "s_inf"), "cross_references": texts(sense, "xref"), "antonyms": texts(sense, "ant"),
            "source_languages": lsources,
            "japanese_meaning_english_glosses": [{"text": "".join(x.itertext()), "language": x.get(LANG, "eng"), "type": x.get("g_type"), "gender": x.get("g_gend")} for x in sense.findall("gloss")],
        })
    priority = sorted({p for f in kanji + readings for p in f["priority"]})
    preferred_reading = next((r for r in readings if COMMON.intersection(r["priority"])), readings[0])
    display_reading = preferred_reading["text"]
    compatible_kanji = [] if preferred_reading["no_kanji"] else [k for k in kanji if not preferred_reading["applies_to_kanji"] or k["text"] in preferred_reading["applies_to_kanji"]]
    display_kanji = next((k["text"] for k in compatible_kanji if COMMON.intersection(k["priority"])), compatible_kanji[0]["text"] if compatible_kanji else None)
    return {
        "entry_id": f"jmdict:{seq}", "source_ids": [f"jmdict:{seq}"], "source_snapshot": "JMdict_e.gz",
        "license": "CC-BY-SA-4.0", "copyright": "James William Breen and the Electronic Dictionary Research and Development Group",
        "source_url": f"https://www.edrdg.org/jmdictdb/cgi-bin/entr.py?svc=jmdict&sid=&q={seq}",
        "display_kanji": display_kanji, "display_reading": display_reading, "kanji": kanji, "readings": readings,
        "senses": senses, "priority": priority,
        "curation_status": "candidate_not_pedagogically_reviewed",
        "romanization": None, "pitch_accent": None, "audio": [], "example_ids": [],
        "mandarin_equivalents": {"status": "unreviewed", "semantic_translations": [], "attested_transliterations": [], "note": "Do not create a Mandarin transliteration from the Japanese reading. A normal semantic translation is not a transliteration."},
        "chinese_dialect_comparisons": [],
    }


def rank(row: dict) -> tuple:
    pri = set(row["priority"])
    bucket = min([int(p[2:]) for p in pri if re.fullmatch(r"nf\d+", p)] or [999])
    weight = sum({"ichi1": 8, "news1": 6, "spec1": 5, "spec2": 3, "gai1": 6}.get(p, 0) for p in pri)
    return (not bool(pri & COMMON), -weight, bucket, int(row["entry_id"].split(":")[1]))


def rank_metadata(row: dict, position: int, collection: str) -> None:
    row["candidate_rank"] = position
    row["collection"] = collection
    row["ranking_basis"] = {
        "method": "jmdict_priority_heuristic_v1",
        "rule": "common-tag first; weighted ichi1=8, news1=6, gai1=6, spec1=5, spec2=3 descending; lowest nfxx bucket; ent_seq tie-break",
        "has_common_priority_tag": bool(set(row["priority"]) & COMMON),
        "not_a_frequency_top_n": True,
        "limitations": "Historical dictionary priority proxy; the entry-id tie-break has no linguistic meaning. No balanced modern spoken corpus or learner-level review has been applied.",
    }


def origin(row: dict) -> dict:
    lsources = [dict(x, sense_id=s["sense_id"]) for s in row["senses"] for x in s["source_languages"]]
    english = [x for x in lsources if x["language"] == "eng"]
    if english:
        labels = ["explicit_english_source_tag"]
        if any(x["wasei"] for x in english):
            labels.append("wasei_eigo_in_at_least_one_sense")
        if any(x["type"] == "part" for x in english):
            labels.append("partial_english_component")
        if any(x["language"] != "eng" for x in lsources):
            labels.append("other_source_languages_in_entry_review_sense_alignment")
        return {"status": "dictionary_tagged", "labels": labels, "source_expressions": lsources,
                "english_lemma": None, "english_dictionary_definition": None,
                "note": "Source expressions can be abbreviations, components, or Japanese coinages; they are not automatically attested English lemmas. English glosses define the Japanese sense. Loan pathway still requires review."}
    if lsources:
        return {"status": "dictionary_tagged", "labels": ["source_language_tagged"], "source_expressions": lsources}
    return {"status": "unknown", "labels": [], "source_expressions": [], "note": "Kanji spelling does not establish a Chinese etymology; a kana spelling does not establish native Japanese origin."}


def eligible_core(row: dict) -> bool:
    if not set(row["priority"]) & COMMON:
        return False
    if any(x["language"] not in {"chi", "zho"} for s in row["senses"] for x in s["source_languages"]):
        return False
    # Do not mistake untagged katakana loans for native vocabulary.
    if not any(re.search(r"[ぁ-ゖ]", r["text"]) and not re.search(r"[ァ-ヺ]", r["text"]) for r in row["readings"]):
        return False
    def usable(s):
        archaic = {"archaism", "obsolete term", "rare term", "rare", "historical term"}
        return not (archaic & set(s["misc"])) and not (s["pos_effective"] and all("proper noun" in p for p in s["pos_effective"]))
    return any(usable(s) for s in row["senses"])


def english_queue(row: dict) -> str:
    sources = row["origin"]["source_expressions"]
    if any(x["wasei"] for x in sources):
        return "wasei_or_wasei_in_some_senses"
    if any(x["type"] == "part" or x["language"] != "eng" for x in sources):
        return "mixed_partial_or_multiple_language_sources"
    if any(not x["source_expression"] for x in sources):
        return "english_tag_but_source_expression_missing"
    if set(row["priority"]) & COMMON:
        return "priority_english_borrowing_candidate"
    return "english_source_commonness_unverified"


def collect_examples(selected: list[dict]) -> tuple[list[dict], dict]:
    paths = (RAW / "JMdict_e_examp.gz", RAW / "jpn_sentences_detailed.tsv.bz2")
    if not all(p.exists() for p in paths):
        return [], {"status": "raw_files_missing"}
    meta = {}
    with bz2.open(paths[1], "rt", encoding="utf-8") as stream:
        for line in stream:
            f = line.rstrip("\n").split("\t")
            if len(f) >= 6:
                meta[f[0]] = {"text": f[2], "owner_at_export": f[3], "created": f[4], "modified": f[5]}
    wanted = {r["entry_id"].split(":")[1]: r for r in selected}
    examples, counts = {}, Counter()
    for node in entries(paths[0]):
        seq = node.findtext("ent_seq")
        if seq not in wanted:
            continue
        row = wanted[seq]
        for index, sense in enumerate(node.findall("sense"), 1):
            if len(row["example_ids"]) >= 2:
                break
            # Match the sense gloss between the two official snapshots.
            sense_match = next((s for s in row["senses"] if s["sense_number"] == index and [g["text"] for g in s["japanese_meaning_english_glosses"]] == ["".join(g.itertext()) for g in sense.findall("gloss")]), None)
            if not sense_match:
                counts["sense_mismatch_skipped"] += 1
                continue
            for ex in sense.findall("example"):
                if len(row["example_ids"]) >= 2:
                    break
                src = ex.find("ex_srce")
                if src is None or src.get("exsrc_type") != "tat":
                    continue
                sid = src.text
                jap = next((x.text for x in ex.findall("ex_sent") if x.get(LANG) == "jpn"), None)
                details = meta.get(sid)
                if not details or details["text"] != jap or details["owner_at_export"] in {"\\N", "", None}:
                    counts["missing_owner_or_text_mismatch_skipped"] += 1
                    continue
                eid = f"tatoeba:{sid}"
                if eid in row["example_ids"]:
                    continue
                if eid not in examples:
                    examples[eid] = {"example_id": eid, "source_ids": [eid], "language": "jpn", "text": jap,
                        "license": "CC-BY-2.0-FR", "license_url": "https://creativecommons.org/licenses/by/2.0/fr/",
                        "attribution_url": f"https://tatoeba.org/en/sentences/show/{sid}",
                        "owner_at_export": details["owner_at_export"], "attribution": f"Tatoeba sentence #{sid}; owner at export: {details['owner_at_export']}; see linked sentence history for contributors",
                        "modified_by_this_project": False, "curation_status": "dictionary_linked_candidate_requires_native_review",
                        "translations": [], "audio": [], "associations": []}
                examples[eid]["associations"].append({"entry_id": row["entry_id"], "sense_id": sense_match["sense_id"], "indexed_text": ex.findtext("ex_text"), "alignment_source": "JMdict_e_examp"})
                row["example_ids"].append(eid)
    counts["examples"] = len(examples)
    counts["entries_with_examples"] = sum(bool(r["example_ids"]) for r in selected)
    counts["english_source_research_entries_with_examples"] = sum(bool(r["example_ids"]) for r in selected if r["collection"] == "english_source_research")
    counts["core_entries_with_examples"] = sum(bool(r["example_ids"]) for r in selected if r["collection"] == "japanese_core")
    return sorted(examples.values(), key=lambda e: int(e["example_id"].split(":")[1])), dict(counts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    if args.download:
        download()
    source = RAW / "JMdict_e.gz"
    if not source.exists():
        parser.error("Raw JMdict_e.gz missing. Run with --download or supply an official snapshot.")
    sha = digest(source)
    if args.expected_sha256 and sha != args.expected_sha256:
        parser.error(f"Snapshot hash mismatch: {sha}")
    with gzip.open(source, "rt", encoding="utf-8") as stream:
        header = stream.read(60000)
    snapshot = re.search(r"JMdict created: ([0-9-]+)", header).group(1)
    english, core, untagged_katakana, counts = [], [], [], Counter()
    for node in entries(source):
        counts["total_entries"] += 1
        row = parse_entry(node)
        row["source_snapshot_date"] = snapshot
        row["origin"] = origin(row)
        if "explicit_english_source_tag" in row["origin"]["labels"]:
            english.append(row)
        elif eligible_core(row):
            core.append(row)
        elif set(row["priority"]) & COMMON and not any(s["source_languages"] for s in row["senses"]) and re.search(r"[ァ-ヺ]", row["display_reading"]):
            untagged_katakana.append(row)
    english.sort(key=rank)
    core.sort(key=rank)
    untagged_katakana.sort(key=rank)
    counts.update({"explicit_english_source_pool": len(english), "explicit_english_source_common_tag_pool": sum(bool(set(r["priority"]) & COMMON) for r in english), "core_priority_pool": len(core), "untagged_katakana_common_pool": len(untagged_katakana)})
    selected_english, selected_core = english[:1000], core[:2000]
    queue_counts = Counter(english_queue(r) for r in english)
    for row in english:
        row["review_queue"] = english_queue(row)
    for name, rows in (("english_source_research", selected_english), ("japanese_core", selected_core), ("untagged_katakana_review_pool", untagged_katakana)):
        for n, row in enumerate(rows, 1):
            rank_metadata(row, n, name)
    examples, example_counts = collect_examples(selected_english + selected_core)
    priority_english = [deepcopy(r) for r in english if english_queue(r) == "priority_english_borrowing_candidate"]
    additional_english = [deepcopy(r) for r in english if english_queue(r) == "english_source_commonness_unverified"]
    for queue_name, rows in (("english_borrowing_priority", priority_english), ("english_source_additional_review", additional_english)):
        for n, row in enumerate(rows, 1):
            rank_metadata(row, n, queue_name)
    for filename, rows in (("source_research_1000.jsonl", selected_english), ("english_borrowing_priority_candidates.jsonl", priority_english), ("english_source_additional_review_pool.jsonl", additional_english), ("japanese_core_2000.jsonl", selected_core), ("untagged_katakana_priority_review_pool.jsonl", untagged_katakana), ("japanese_examples.jsonl", examples)):
        save_jsonl(OUT / filename, rows)
    counts.update({"english_source_research_selected": len(selected_english), "core_selected": len(selected_core), "english_source_research_common_tag": sum(bool(set(r["priority"]) & COMMON) for r in selected_english), "english_source_research_with_wasei_flag": sum("wasei_eigo_in_at_least_one_sense" in r["origin"]["labels"] for r in selected_english), "english_source_research_partial_source": sum("partial_english_component" in r["origin"]["labels"] for r in selected_english)})
    raw_manifest = [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), "url": SOURCES.get(p.name), "bytes": p.stat().st_size, "sha256": digest(p)} for p in sorted(RAW.iterdir()) if p.is_file() and p.name in SOURCES]
    save_json(ROOT / "research" / "lexicon_audit.json", {
        "snapshot_date": snapshot, "jmdict_sha256": sha, "counts": dict(counts), "example_counts": example_counts, "english_source_pool_queues": dict(queue_counts),
        "status": "research_candidates_not_release_curriculum", "raw_manifest": raw_manifest,
        "limitations": ["source_research_1000.jsonl is an English-related etymology research subset, NOT 1000 verified common English loanwords. Numeric size is not a completion metric.", "The explicit-English source pool is strongly biased toward wasei-eigo. Use the separate priority English borrowing queue first; exclude wasei and partial/multi-language entries from that queue.", "JMdict often omits an English lsource when the origin seems obvious. The untagged katakana pool needs independent etymology validation.", "Priority reflects old heterogeneous word lists, not a current balanced frequency ranking or a beginner curriculum.", "The 2000 core candidates do not have validated native-Japanese/Chinese etymology labels. Han characters alone are not evidence of borrowing.", "Examples are dictionary-linked Japanese-only candidates with exact current-export text and attributed sentence URLs. Sense fit and learner suitability require human review.", "Romanization, pitch accent, original English dictionary definitions, Mandarin transliteration and dialect correspondences remain unfilled pending source-based editorial work.", "No audio has been downloaded; sentence-text licensing does not license audio."],
        "release_gate": ["Review corpus commonness and topic balance", "Review origin pathway and native/Sino-Japanese category", "Validate English source expressions as lemmas or coinages", "Native review of example sentences", "Complete required details and legal notices", "Pin input and output SHA-256 hashes"],
    })
    save_json(ROOT / "research" / "lexicon_record_examples.json", {"english_priority_example": priority_english[0] if priority_english else None, "core_example": selected_core[0], "sentence_example": examples[0] if examples else None})
    print(json.dumps({"snapshot_date": snapshot, "counts": dict(counts), "example_counts": example_counts, "english_source_pool_queues": dict(queue_counts)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
