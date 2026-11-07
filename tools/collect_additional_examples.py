"""Offline, conservative surface matches for words still lacking examples.

Only already cached Tatoeba/JMdict data are read. No network and no app database
writes. Long exact spellings are word-level research candidates, never inferred
sense links. Short kana/kanji substrings and attached compounds are excluded.
"""
from __future__ import annotations

import bz2
from collections import Counter, defaultdict, deque
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from collect_examples_enrichment import example_record, sentence_meta
from collect_lexicon import entries

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "app/src/kanamemory/resources/content.sqlite3"
REPORT = ROOT / "research/enrichment_integration_report.json"
RAW = ROOT / "data/raw"

# Context screening removes cases a spelling-only rule cannot resolve. These
# decisions do not promote the other matches to an editor-approved sense link.
CONTEXT_EXCLUSIONS = {
    ("jmdict:1029190", "778280"): "Edyta is a person's name here, not the editor lexeme.",
    ("jmdict:1065130", "786164"): "Jaguar is a car brand here; this JMdict entry only defines the animal.",
    ("jmdict:1030430", "1127681"): "Music-genre usage is unclear and does not substantiate the dictionary's electric adjective.",
}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def kanji(char):
    return "\u3400" <= char <= "\u9fff" or char in "々〆"


def katakana(char):
    return "ァ" <= char <= "ヺ" or char in "ー・"


def eligible(form):
    if len(form) >= 4 and all(katakana(char) for char in form):
        return "long_katakana_exact_spelling"
    if len(form) >= 3 and all(kanji(char) for char in form):
        return "long_kanji_exact_spelling"
    if len(form) >= 4 and any(kanji(char) for char in form) and all(kanji(char) or "ぁ" <= char <= "ゖ" for char in form):
        return "long_mixed_kanji_kana_exact_spelling"
    return None


def attached(char):
    return bool(char) and (kanji(char) or katakana(char) or "A" <= char <= "Z" or "a" <= char <= "z" or char.isdigit() or "Ａ" <= char <= "Ｚ" or "ａ" <= char <= "ｚ")


def dictionary_readings(word, form):
    """Compatible lexicon readings, never an asserted reading of the sentence."""
    readings = word.get("readings", [])
    direct = [row["text"] for row in readings if row["text"] == form]
    if direct:
        return direct
    return list(dict.fromkeys(row["text"] for row in readings
                             if not row.get("no_kanji") and
                             (not row.get("applies_to_kanji") or form in row["applies_to_kanji"])))


class ExactMatcher:
    """Small Aho-Corasick matcher; finds overlaps without giant corpus regexes."""
    def __init__(self, forms):
        self.next, self.fail, self.out = [{}], [0], [[]]
        for form in forms:
            state = 0
            for char in form:
                if char not in self.next[state]:
                    self.next[state][char] = len(self.next)
                    self.next.append({}); self.fail.append(0); self.out.append([])
                state = self.next[state][char]
            self.out[state].append(form)
        queue = deque(self.next[0].values())
        while queue:
            state = queue.popleft()
            for char, child in self.next[state].items():
                queue.append(child)
                fallback = self.fail[state]
                while fallback and char not in self.next[fallback]:
                    fallback = self.fail[fallback]
                self.fail[child] = self.next[fallback].get(char, 0)
                self.out[child].extend(self.out[self.fail[child]])

    def matches(self, text):
        state = 0
        for end, char in enumerate(text, 1):
            while state and char not in self.next[state]:
                state = self.fail[state]
            state = self.next[state].get(char, 0)
            for form in self.out[state]:
                yield form, end - len(form), end


def main():
    baseline = json.loads(REPORT.read_text(encoding="utf-8"))
    missing = {record["id"] for record in baseline["gaps"]["without_examples"]}
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as db:
        words = {wid: {**json.loads(payload), "id":wid, "form":form, "reading":reading}
                 for wid, form, reading, payload in db.execute("SELECT id,form,reading,payload FROM word")}
        current_missing = {row[0] for row in db.execute("SELECT w.id FROM word w WHERE NOT EXISTS (SELECT 1 FROM word_example we WHERE we.word_id=w.id)")}
    # A new app build may already contain this overlay; use the pinned report's
    # missing IDs, but never invent an ID absent from the current dictionary.
    assert missing <= set(words)
    forms, kinds, ignored = defaultdict(set), {}, Counter()
    for wid in sorted(missing):
        word = words[wid]
        all_forms = [word["form"], word["reading"]] + [k["text"] for k in word.get("kanji", [])] + [r["text"] for r in word.get("readings", [])]
        for form in dict.fromkeys(all_forms):
            kind = eligible(form)
            if not kind:
                ignored["short_or_pure_hiragana_form"] += 1
                continue
            forms[form].add(wid)
            kinds[form] = kind
    dictionary_ids = defaultdict(set)
    for entry in entries(RAW / "JMdict_e.gz"):
        wid = "jmdict:" + entry.findtext("ent_seq")
        for element in entry.findall("k_ele/keb") + entry.findall("r_ele/reb"):
            if element.text in forms:
                dictionary_ids[element.text].add(wid)
    ambiguous = {form: sorted(ids) for form, ids in dictionary_ids.items() if len(ids) != 1}
    safe = {form: next(iter(wids)) for form, wids in forms.items()
            if len(wids) == 1 and dictionary_ids.get(form) == wids}
    matcher = ExactMatcher(safe)
    japanese = sentence_meta(RAW / "jpn_sentences_detailed.tsv.bz2")
    candidates, rejected, scanned = defaultdict(dict), Counter(), 0
    for sid, sentence in japanese.items():
        text = sentence["text"]
        if not 6 <= len(text) <= 120 or "\n" in text or "\t" in text:
            continue
        scanned += 1
        for form, start, end in matcher.matches(text):
            # Allow particles and conjugational kana around long exact forms,
            # but exclude a word embedded in a longer written kanji/loanword.
            if attached(text[start - 1] if start else "") or attached(text[end] if end < len(text) else ""):
                rejected["attached_compound_boundary"] += 1
                continue
            wid = safe[form]
            if (wid, sid) in CONTEXT_EXCLUSIONS:
                rejected["context_screened_false_lexeme"] += 1
                continue
            candidate = {"entry_id": wid, "sense_id": "", "alignment_source": "Tatoeba_cached_long_exact_surface_candidate", "sense_alignment_status": "not_aligned_to_current_JMdict", "indexed_text": form, "surface_match": form, "surface_offsets": [start, end], "match_kind": kinds[form], "dictionary_identity_evidence": "Exact written form belongs to only this entry in the cached full JMdict_e snapshot.", "boundary_rule": "No adjacent Han, katakana, Latin letter or digit; hiragana particles/inflection allowed", "curation_status": "word_level_candidate_not_editor_approved"}
            candidate.update({"matched_dictionary_readings": dictionary_readings(words[wid], form),
                              "sentence_reading": None,
                              "reading_alignment_status": "dictionary_readings_only_not_attested_in_sentence",
                              "source_ids": [wid, "tatoeba-enrichment"]})
            previous = candidates[wid].get(sid)
            if previous is None or len(previous["surface_match"]) < len(form):
                candidates[wid][sid] = candidate
    print(json.dumps({"baseline_missing_words":len(missing), "eligible_unambiguous_spellings":len(safe), "ambiguous_spellings_excluded":len(ambiguous), "words_with_surface_candidates":len(candidates), "sentence_word_candidates":sum(len(v) for v in candidates.values()), "scanned_attributed_sentences":scanned}), flush=True)

    # Prefer short attributable sentences with an existing Chinese translation.
    candidate_sids = {sid for choices in candidates.values() for sid in choices}
    translation_ids = defaultdict(list)
    with bz2.open(RAW / "enrichment/jpn-cmn_links.tsv.bz2", "rt", encoding="utf-8") as stream:
        for line in stream:
            source, translated = line.rstrip("\r\n").split("\t")
            if source in candidate_sids:
                translation_ids[source].append(translated)
    chinese = sentence_meta(RAW / "enrichment/cmn_sentences_detailed.tsv.bz2")
    examples, associations = {}, []
    for wid, choices in sorted(candidates.items()):
        ranked = sorted(choices, key=lambda sid: (not any(tid in chinese for tid in translation_ids[sid]), len(japanese[sid]["text"]), int(sid)))
        for sid in ranked[:2]:
            record = examples.setdefault(sid, example_record(sid, japanese[sid]))
            record["curation_status"] = "long_exact_surface_word_candidate_not_editor_approved"
            record["associations"].append(choices[sid])
            record["translations"] = [example_record(tid, chinese[tid], "cmn") for tid in sorted(set(translation_ids[sid]), key=int) if tid in chinese][:2]
            record["audio_candidates"] = []
            associations.append(choices[sid])
    records = [examples[sid] for sid in sorted(examples, key=int)]
    report = {"baseline_report":str(REPORT.relative_to(ROOT)).replace("\\", "/"), "baseline_report_sha256":hashlib.sha256(REPORT.read_bytes()).hexdigest(), "baseline_missing_count":len(missing), "current_missing_count":len(current_missing), "additional_words":len(candidates), "additional_sentences":len(records), "additional_word_sentence_links":len(associations), "selected_examples_with_Chinese":sum(bool(record["translations"]) for record in records), "match_kind_counts":dict(Counter(a["match_kind"] for a in associations)), "ambiguous_dictionary_spellings_excluded":ambiguous, "boundary_rejections":dict(rejected), "remaining_missing_words":[{"id":wid,"form":words[wid]["form"]} for wid in sorted(missing - set(candidates))], "covered_words":[{"id":wid,"form":words[wid]["form"],"candidate_sentence_count":len(candidates[wid])} for wid in sorted(candidates)], "source_id":"tatoeba-enrichment", "licensing":"Same per-sentence owner, history URL and CC-BY-2.0-FR notice as existing cached Tatoeba enrichment; no audio files fetched.", "limitations":["Surface matching is lexical evidence only; no JMdict sense number is inferred.", "No 1–2-character kanji, short katakana, pure-hiragana spelling, or ambiguous full-dictionary spelling is admitted.", "Hiragana surrounding the exact spelling is allowed; human review of morphology, naturalness and meaning remains necessary.", "Chinese translations are existing Tatoeba links, not independently checked or newly generated."]}
    assert all(a["sense_id"] == "" and a["sense_alignment_status"] == "not_aligned_to_current_JMdict" for a in associations)
    assert all(a["matched_dictionary_readings"] and a["sentence_reading"] is None for a in associations)
    assert all(record["owner_at_export"] and record["source_ids"] == ["tatoeba-enrichment"] for record in records)
    report["context_exclusions"] = [{"word_id": wid, "sentence_id": sid, "reason": reason}
                                    for (wid, sid), reason in CONTEXT_EXCLUSIONS.items()]
    report["limitations"].extend([
        "Dictionary readings respect JMdict written-form restrictions; sentence readings are not attested or synthesized.",
        "Dictionary spelling uniqueness cannot by itself rule out names or semantic homonyms; context exclusions remove observed clear false matches, while all surviving associations remain research candidates.",
    ])
    write(ROOT / "data/enrichment/additional_examples_enrichment.json", {"schema_version":1,"examples":records,"collection_method":"offline_long_exact_surface_candidates","baseline_missing_words":len(missing)})
    write(ROOT / "research/additional_examples_enrichment_audit.json", report)
    print(json.dumps({key:report[key] for key in ("additional_words", "additional_sentences", "additional_word_sentence_links", "selected_examples_with_Chinese", "match_kind_counts")}), flush=True)


if __name__ == "__main__":
    main()
