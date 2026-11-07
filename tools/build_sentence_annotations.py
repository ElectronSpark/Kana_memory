"""Build exact-text, offline sentence annotations from public corpus only.

Build dependency: pyopenjtalk-plus==0.4.1.post10. It is not an Android runtime
dependency. Generated accent positions are explicitly distinguished from exact
Kanjium citation entries. No learner database or API credential is read.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from functools import lru_cache
from hashlib import sha256
import json
from importlib.metadata import distribution
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app/src"))
from kanamemory.pitch_accent import _positions, _reading_key, morae
from kanamemory.sentence_annotations import FORMAT, VERSION, KANJI, KANJI_RUN, KANA, _validated, validated_accent_phrases


def dictionary_entries(path):
    entries = defaultdict(list)
    for line in Path(path).read_text("utf-8-sig").splitlines():
        fields = line.split("\t")
        if len(fields) < 3:
            continue
        surface, reading, raw = fields[:3]
        reading = _reading_key(reading)
        positions = [n for n, qualifier in _positions({"raw": raw})
                     if 0 <= n <= len(morae(reading))]
        if positions and morae(reading):
            entries[(surface, reading)].extend(positions)
    return {key: list(dict.fromkeys(values)) for key, values in entries.items()}


def _valid_mapping(text, mappings):
    """Merge numeric normalization nodes sharing one original source range."""
    nodes = []
    for mapping in mappings:
        start, end = mapping.get("char_span", (0, 0))
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            # Expanding 1990 to 千九百九十 inserts 百 without an additional
            # source character. It belongs to the preceding numeric digit.
            if (start == end == 0 and nodes and mapping.get("pos_group1") == "数"
                    and nodes[-1].get("pos_group1") == "数"):
                previous = nodes[-1]
                previous["pron"] = previous.get("pron", previous.get("read", "")) + mapping.get("pron", mapping.get("read", ""))
                previous["read"] += mapping.get("read", "")
                previous["merged_normalization"] = True
                continue
            if KANJI.search(mapping.get("surface", "")):
                raise ValueError("Unaligned kanji token")
            continue
        node = dict(mapping, start=start, end=end)
        if nodes and start < nodes[-1]["end"]:
            previous = nodes[-1]
            if start < previous["start"]:
                raise ValueError("Nonmonotone source alignment")
            previous["end"] = max(end, previous["end"])
            previous["pron"] = previous.get("pron", previous.get("read", "")) + mapping.get("pron", mapping.get("read", ""))
            previous["read"] += mapping.get("read", "")
            previous["is_unknown"] |= mapping.get("is_unknown", False)
            previous["merged_normalization"] = True
            continue
        nodes.append(node)
    return nodes


def _joins(group, candidate, text):
    previous, first = group[-1], group[0]
    if previous["end"] != candidate["start"]:
        return False
    pos, sub = candidate.get("pos"), candidate.get("pos_group1")
    chain = candidate.get("chain_flag") == 1
    verb = any(n.get("pos") in {"動詞", "形容詞"} for n in group)
    if verb:
        return chain and (pos == "助動詞" or pos in {"動詞", "形容詞"} and sub in {"非自立", "接尾"}
                          or pos == "動詞" and candidate.get("orig") in {"する", "さす", "させる"}
                          or pos == "名詞" and sub == "接尾" and text[candidate["start"]:candidate["end"]] in {"さ", "そう"}
                          or pos == "助詞" and sub == "接続助詞"
                          and text[candidate["start"]:candidate["end"]] in {"て", "で", "ば", "たり", "だり"})
    if first.get("pos") == "接頭詞" and pos == "名詞":
        return chain
    if previous.get("pos") == "名詞" and pos == "名詞":
        if text[candidate["start"]:candidate["end"]] == "そう":
            return False
        return chain or previous.get("pos_group1") == sub == "数"
    if previous.get("pos_group1") == "サ変接続" and pos == "動詞" and candidate.get("orig") == "する":
        return chain
    return False


def _node_reading(node):
    # read is pre-sound-change dictionary kana: 一分 would become いちふん.
    # pron includes counter assimilation; the analyzer restores written long
    # vowels separately so 学校 remains がっこう, not a TTS phonetic spelling.
    reading = _reading_key(node.get("pron", node.get("read", "")).replace("’", "").replace("'", ""))
    # Dictionary historical orthography and normalized numerals must not leak
    # into modern furigana. These do not alter the spoken mora sequence.
    if str(node.get("orig", "")).startswith(("預け", "預か")) and reading.startswith("あづ"):
        reading = "あず" + reading[2:]
    if node.get("surface") == "十日" and reading == "とうか":
        reading = "とおか"
    return reading


class Annotator:
    def __init__(self, dictionary, analyzer=None, lexical_analyzer=None):
        self.dictionary = dictionary
        if analyzer is None:
            import pyopenjtalk
            self.analyzer = lambda text: pyopenjtalk.g2p_mapping(text, predict_nani=False, normalize_mode="None", revert_long_vowels=True, revert_yotsugana=True)
            from sudachipy import dictionary as sudachi_dictionary, tokenizer
            lexical = sudachi_dictionary.Dictionary().create()
            self.lexical_analyzer = lambda text: lexical.tokenize(text, tokenizer.Tokenizer.SplitMode.B)
        else:
            self.analyzer = analyzer
            self.lexical_analyzer = lexical_analyzer

    @lru_cache(maxsize=8192)
    def _isolated_accent(self, text, expected_reading):
        """Only accept an accent matching this entire current surface unit."""
        try:
            nodes = _valid_mapping(text, self.analyzer(text))
        except (ValueError, RuntimeError):
            return None
        relevant = [n for n in nodes if morae(_node_reading(n))]
        if not relevant or any(n.get("is_unknown") for n in relevant):
            return None
        reading = "".join(_node_reading(n) for n in relevant)
        if reading != expected_reading:
            return None
        # A single displayed unit cannot inherit the number of another accent
        # phrase. Compound numerals can be split by the analyzer; keep unknown.
        if any(n.get("chain_flag") != 1 for n in relevant[1:]):
            return None
        accent = relevant[0].get("accent_nucleus")
        return accent if type(accent) is int and 0 <= accent <= len(morae(reading)) else None

    def annotate(self, text):
        nodes = _valid_mapping(text, self.analyzer(text))
        lexical = self.lexical_analyzer(text) if self.lexical_analyzer else []
        lexical_boundaries = {m.begin() for m in lexical}
        groups = []
        for node in nodes:
            join = bool(groups) and _joins(groups[-1], node, text)
            if (join and lexical_boundaries and node["start"] in lexical_boundaries
                    and node.get("pos") == "名詞" and node.get("pos_group1") not in {"接尾", "数"}
                    and groups[-1][0].get("pos") == "名詞"):
                combined = text[groups[-1][0]["start"]:node["end"]]
                combined_reading = "".join(_node_reading(n) for n in groups[-1] + [node])
                if (combined, combined_reading) not in self.dictionary:
                    join = False
            if join:
                groups[-1].append(node)
            else:
                groups.append([node])
        segments = []
        for group_index, group in enumerate(groups):
            start, end = group[0]["start"], group[-1]["end"]
            surface = text[start:end]
            if not KANJI.search(surface):
                continue
            reading = "".join(_node_reading(n) for n in group)
            if not KANA.fullmatch(reading) or not morae(reading):
                continue
            inflected = any(n.get("pos") in {"動詞", "形容詞"} and
                (n.get("cform") != "基本形" or len(group) > 1) for n in group)
            # Do not use lemma dictionary entries for inflected units, even if
            # a truncated stem happens to be the spelling of an unrelated noun.
            nominalized = group[-1].get("pos") == "名詞" and group[-1].get("pos_group1") == "接尾"
            variants = self.dictionary.get((surface, reading), []) if not inflected or nominalized else []
            accent = variants[0] if variants else self._isolated_accent(surface, reading)
            # Homographs can be disambiguated in the sentence but not alone
            # (通っています -> かよっています / とおっています). Retain its
            # context accent only when this is the complete accent phrase.
            following = groups[group_index + 1][0] if group_index + 1 < len(groups) else None
            context_accent = group[0].get("accent_nucleus")
            if (accent is None and group[0].get("chain_flag") != 1
                    and all(n.get("chain_flag") == 1 for n in group[1:])
                    and (following is None or following.get("chain_flag") != 1)
                    and not any(n.get("is_unknown") for n in group)
                    and type(context_accent) is int and 0 <= context_accent <= len(morae(reading))):
                accent = context_accent
            source = "kanjium-pitch-accent" if variants else "openjtalk-surface-accent"
            item = {"start": start, "end": end, "surface": surface, "reading": reading,
                    "reading_source_id": "openjtalk-context-reading", "accent_number": accent,
                    "accent_source_id": source if accent is not None else None,
                    "accent_scope": "inflected_unit" if inflected else "word",
                    "accent_surface": surface, "accent_reading": reading,
                    "derivation": "dictionary" if variants else "context_rules",
                    "reading_derivation": "context_rules" if any(n.get("reading_source") == "Rule" for n in group) else "dictionary",
                    "accent_alternatives": variants,
                    "unknown_token": any(n.get("is_unknown") for n in group)}
            segments.append(item)
        segments = self._reconcile_lexical_readings(text, segments, lexical)
        record = {"version": VERSION, "text": text, "segments": segments, "status": "precomputed"}
        result = _validated(text, record)
        if result is None:
            raise ValueError("Invalid generated source alignment")
        return result

    def _reconcile_lexical_readings(self, text, segments, lexical):
        """Use independent lexical boundaries plus exact dictionary evidence.

        Open JTalk's speech-oriented compounds may incorrectly join 少し体 or
        apply rendaku to a new word (部屋 -> べや). Only a complete overlapping
        group covered by exact Kanjium spellings may replace those readings;
        inflected units and numeric sound changes are never split this way.
        """
        if self.lexical_analyzer is None:
            return segments
        result = []
        for item in segments:
            if item["accent_scope"] != "word" or (item["surface"],item["reading"]) in self.dictionary:
                result.append(item)
                continue
            parts = [m for m in lexical if m.begin() >= item["start"] and m.end() <= item["end"]]
            if (not parts or parts[0].begin() != item["start"] or parts[-1].end() != item["end"]
                    or any(m.part_of_speech()[0] in {"動詞", "形容詞", "接尾辞"}
                           or m.part_of_speech()[1] == "数詞" for m in parts)):
                result.append(item)
                continue
            combined = "".join(_reading_key(m.reading_form()) for m in parts)
            if combined == item["reading"]:
                result.append(item)
                continue
            replacements = []
            for part in parts:
                surface, reading = part.surface(), _reading_key(part.reading_form())
                if not KANJI.search(surface):
                    continue
                positions = self.dictionary.get((surface, reading), [])
                if not positions:
                    break
                replacements.append(dict(start=part.begin(), end=part.end(), surface=surface,
                    reading=reading, accent_number=positions[0], accent_alternatives=positions,
                    reading_source_id="sudachi-context-reading", accent_source_id="kanjium-pitch-accent",
                    accent_scope="word", accent_surface=surface, accent_reading=reading,
                    derivation="dictionary", reading_derivation="dictionary_context_reconciled"))
            else:
                if replacements:
                    result.extend(replacements)
                    continue
            result.append(item)
        return result


def fallback_entries(dictionary):
    forms = defaultdict(list)
    for (surface, reading), positions in dictionary.items():
        if len(surface) >= 2 and KANJI_RUN.fullmatch(surface):
            forms[surface].append((reading, positions))
    for surface, entries in forms.items():
        readings = {r for r, _ in entries}
        accents = {n for _, positions in entries for n in positions}
        if len(readings) != 1 or len(accents) != 1:
            continue
        reading, accent = next(iter(readings)), next(iter(accents))
        yield surface, {"reading": reading, "accent_number": accent,
            "reading_source_id": "kanjium-pitch-accent", "accent_source_id": "kanjium-pitch-accent",
            "accent_surface": surface, "accent_reading": reading, "accent_scope": "word",
            "derivation": "dictionary", "accent_alternatives": [accent]}


def apply_editorial_records(text, plan, overrides=(), exclusions=()):
    """Corrections are anchored to exact public text, never loose substrings."""
    segments = list(plan["segments"])
    for entry in overrides:
        start, end = entry["start"], entry["end"]
        if entry["surface"] != text[start:end]:
            raise ValueError("Stale editorial annotation anchor")
        segments = [s for s in segments if s["end"] <= start or s["start"] >= end]
        reading, accent = entry["reading"], entry["accent_number"]
        segments.append(dict(start=start, end=end, surface=entry["surface"], reading=reading,
            accent_number=accent, reading_source_id=entry.get("reading_source_id", entry["source_id"]),
            accent_source_id=entry.get("accent_source_id", entry["source_id"]) if accent is not None or entry.get("accent_phrases") else None,
            accent_scope="phrase_sequence" if entry.get("accent_phrases") else entry.get('accent_scope', 'word'), accent_surface=entry["surface"], accent_reading=reading,
            derivation=entry.get("derivation", "dictionary"), reading_derivation="exact_context_correction",
            review_note=entry["reason"], source_url=entry.get("source_url", ""),
            review_reason=entry.get("review_reason", ""),
            accent_alternatives=entry.get("accent_alternatives", []),
            accent_phrases=entry.get("accent_phrases", [])))
    for entry in exclusions:
        segments = [s for s in segments if s["end"] <= entry["start"] or s["start"] >= entry["end"]]
    result = _validated(text, dict(plan, segments=sorted(segments,key=lambda s:s["start"]), excluded=list(exclusions)))
    if result is None:
        raise ValueError("Invalid editorial annotations")
    return result


def write_notices(path):
    """Ship verbatim installed licenses with the derived offline sidecar."""
    import pyopenjtalk
    parts = ["Kana Memory — offline sentence reading and accent annotations\n\n"
        "Build-time tools are not included in the Android application. The derived\n"
        "annotations use exact Kanjium entries, Open JTalk contextual pronunciation\n"
        "and surface accent rules, and SudachiDict lexical boundary verification.\n"
        "Context corrections and exclusions are recorded in the build report.\n"
        "Exact-context follow-up facts cite the Japan Foundation Irodori wordlist\n"
        "(https://www.irodori.jpf.go.jp/assets/data/wordlist_X.pdf), OJAD\n"
        "(https://www.gavo.t.u-tokyo.ac.jp/ojad/) and Tatoeba direct translations\n"
        "(https://tatoeba.org/en/sentences/show/236155 and /75878).\n"
        "Only scoped annotation facts and references are included; the source\n"
        "PDF, web pages and recordings are not bundled. Disputed accents remain\n"
        "unknown. Tatoeba sentence attribution is retained in corpus notices.\n"
        "Kanjium-derived annotation data remains under Attribution-ShareAlike 4.0\n"
        "International: https://creativecommons.org/licenses/by-sa/4.0/\n"
        "The application code and third-party tools retain their own licenses.\n"]
    licenses = [("Kanjium", "https://github.com/mifunetoshiro/kanjium", ROOT/"data/enrichment/raw/kanjium_LICENSE.txt"),
        ("Open JTalk bundled dictionary", "https://github.com/tsukumijima/pyopenjtalk-plus", Path(pyopenjtalk.__file__).parent/"dictionary/COPYING")]
    for name in ["pyopenjtalk-plus", "sudachipy", "sudachidict-core"]:
        package = distribution(name)
        for relative in package.files or []:
            if "/licenses/" in str(relative).replace("\\", "/"):
                licenses.append((f"{name} {package.version}", package.metadata.get("Home-page", ""), package.locate_file(relative)))
    for title, url, source in licenses:
        parts.append(f"\n{'='*72}\n{title}\n{url}\nSource: {source.name}\n\n{source.read_text('utf-8-sig')}\n")
    Path(path).write_text("\n".join(parts),encoding="utf-8")


def build(output, report_path, *, limit=None):
    from annotation_followup_review import apply_reviews, load_reviews
    from sentence_annotation_corpus import collect_sentence_records
    from sentence_annotation_overrides import EXACT_OVERRIDES
    try:
        from sentence_annotation_overrides import EXACT_EXCLUSIONS
    except ImportError:
        EXACT_EXCLUSIONS = {}
    import pyopenjtalk
    dictionary = dictionary_entries(ROOT / "data/enrichment/raw/kanjium_accents.txt")
    annotator = Annotator(dictionary)
    records = collect_sentence_records()
    reviews = load_reviews()
    if not set(reviews) <= {record["text"] for record in records}:
        raise ValueError("Review ledger references absent public text")
    if limit is not None:
        records = records[:limit]
    output, report_path = Path(output), Path(report_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".building.sqlite3")
    if temporary.exists():
        temporary.unlink()
    totals, categories, failures, unresolved, unresolved_words = Counter(), defaultdict(Counter), [], [], []
    with closing(sqlite3.connect(temporary)) as db:
        db.executescript("PRAGMA user_version=1; CREATE TABLE sentence(fingerprint TEXT PRIMARY KEY,text TEXT NOT NULL,record TEXT NOT NULL); CREATE TABLE fallback(surface TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);")
        db.executemany("INSERT INTO meta VALUES (?,?)", [("format", FORMAT), ("version", str(VERSION)), ("engine", pyopenjtalk.__version__)])
        for index, record in enumerate(records):
            text = record["text"]
            try:
                plan = annotator.annotate(text)
            except (ValueError, RuntimeError) as exc:
                failures.append({"text": text, "error": type(exc).__name__, "message": str(exc)[:160]})
                plan = {"version": VERSION, "text": text, "segments": [], "status": "alignment_failed"}
                plan = _validated(text, plan)
            plan = apply_editorial_records(text, plan, EXACT_OVERRIDES.get(text, ()), EXACT_EXCLUSIONS.get(text, ()))
            plan = apply_reviews(text, plan, reviews.get(text, ()))
            db.execute("INSERT INTO sentence VALUES (?,?,?)", (sha256(text.encode()).hexdigest(), text,
                json.dumps(plan, ensure_ascii=False, separators=(",", ":"))))
            counts = {"sentences": 1, "kanji_characters": len(KANJI.findall(text)),
                      "annotated_words": len(plan["segments"]),
                      "words_with_accent": sum(s["accent_number"] is not None or bool(validated_accent_phrases(s)) for s in plan["segments"]),
                      "words_with_phrase_accent": sum(bool(validated_accent_phrases(s)) for s in plan["segments"]),
                      "kanji_with_reading": len({i for s in plan["segments"] if s["reading"] for i in range(s["start"],s["end"]) if KANJI.match(text[i])}),
                      "kanji_with_accent": len({i for s in plan["segments"] if s["accent_number"] is not None or validated_accent_phrases(s) for i in range(s["start"],s["end"]) if KANJI.match(text[i])}),
                      "excluded_kanji_characters": len({i for s in plan.get("excluded",[]) for i in range(s["start"],s["end"]) if KANJI.match(text[i])}),
                      "excluded_non_japanese": sum(s["category"] == "non_japanese" for s in plan.get("excluded",[])),
                      "excluded_historical": sum(s["category"] == "historical" for s in plan.get("excluded",[])),
                      "editorial_corrections": len(EXACT_OVERRIDES.get(text, ())),
                      "followup_reviews": len(reviews.get(text, ())),
                      "unresolved_runs": len(plan["unresolved"]), "fully_annotated_sentences": int(not plan["unresolved"])}
            totals.update(counts)
            for category in {s["category"] for s in record["sources"]}:
                categories[category].update(counts)
            for span in plan["unresolved"]:
                unresolved.append({"text": text, **span, "categories": sorted({s["category"] for s in record["sources"]})})
            for span in plan["segments"]:
                if span["reading"] is None or span["accent_number"] is None and not validated_accent_phrases(span):
                    unresolved_words.append({"text": text, **span, "categories": sorted({s["category"] for s in record["sources"]})})
            if index and index % 1000 == 0:
                print(f"Annotated {index}/{len(records)} public texts", flush=True)
        fallback = list(fallback_entries(dictionary))
        db.executemany("INSERT INTO fallback VALUES (?,?)", [(surface, json.dumps(record,ensure_ascii=False,separators=(",", ":"))) for surface, record in fallback])
        db.commit()
    temporary.replace(output)
    write_notices(output.with_name("sentence_annotation_notices.txt"))
    report = {"format": FORMAT, "version": VERSION, "engine": pyopenjtalk.__version__,
        "totals": dict(totals), "categories": {k:dict(v) for k,v in sorted(categories.items())},
        "fallback_words": len(fallback), "alignment_failures": failures, "unresolved": unresolved,
        "unresolved_word_occurrences": len(unresolved_words),
        "unresolved_unique_word_readings": len({(s["surface"],s["reading"]) for s in unresolved_words}),
        "sidecar_sha256": sha256(output.read_bytes()).hexdigest(), "sidecar_bytes": output.stat().st_size,
        "build_dependencies": {name: distribution(name).version for name in ["pyopenjtalk-plus", "sudachipy", "sudachidict-core"]},
        "limits": ["Dictionary context selection and inflected accent rules are automatic annotations, not a claim of manual semantic certification.", "Unknown accents remain null, never zero.", "New text fallback only matches whole multi-kanji runs with a unique exact dictionary reading and accent."]}
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n", encoding="utf-8")
    report_path.with_name("sentence_annotation_unresolved_words.json").write_text(json.dumps(unresolved_words,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"totals":dict(totals),"fallback_words":len(fallback),"bytes":output.stat().st_size},ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"app/src/kanamemory/resources/sentence_annotations.sqlite3")
    parser.add_argument("--report",type=Path,default=ROOT/"environment/sentence_annotation_coverage.json")
    parser.add_argument("--limit",type=int)
    args=parser.parse_args()
    build(args.output,args.report,limit=args.limit)
