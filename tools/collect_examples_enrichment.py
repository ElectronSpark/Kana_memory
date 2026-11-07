"""Reproducible Tatoeba/JMdict examples, translations and licensed kana recordings.

Reads pinned source exports; only --download fetches recordings. Keeps sentence
and recording licenses separate and never turns a word match into a sense match.
"""
from __future__ import annotations

import argparse
import bz2
from collections import Counter, defaultdict
import hashlib
import html
import json
from pathlib import Path
import re
import tarfile
import time
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from collect_lexicon import entries, LANG

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw"
EXTRA = RAW / "enrichment"
OUT = ROOT / "data/enrichment"
MEDIA = OUT / "media"
TOKEN = re.compile(r"([^()[\]{}~|]+)(?:\(([^()]*)\))?(?:\[(\d+)\])?(?:\{([^{}]*)\})?(~)?(?:\|\d+)?$")
UA = "KanaMemoryResearch/0.1 (personal language-learning data research)"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sentence_meta(path):
    result = {}
    with bz2.open(path, "rt", encoding="utf-8") as stream:
        for line in stream:
            f = line.rstrip("\n").split("\t")
            if len(f) >= 6 and f[3] not in {"", "\\N"}:
                result[f[0]] = {"text": f[2], "owner_at_export": f[3], "modified": f[5]}
    return result


def corpus_lines(path):
    with tarfile.open(path) as archive:
        member, = archive.getmembers()
        for line in archive.extractfile(member):
            yield line.decode("utf-8").rstrip("\r\n").split("\t")


def example_record(sid, details, language="jpn"):
    return {"example_id": f"tatoeba:{sid}", "language": language,
            "text": details["text"], "license": "CC-BY-2.0-FR",
            "license_url": "https://creativecommons.org/licenses/by/2.0/fr/",
            "source_ids": ["tatoeba-enrichment"],
            "attribution_url": f"https://tatoeba.org/en/sentences/show/{sid}",
            "owner_at_export": details["owner_at_export"],
            "attribution": f"Tatoeba #{sid}; owner at export: {details['owner_at_export']}; contribution history at source URL",
            "modified_by_this_project": False, "source_modified": details["modified"],
            "curation_status": "source_indexed_candidate_not_editor_approved",
            "translations": [], "audio": [], "associations": []}


def collect_examples():
    words = {}
    for line in (ROOT / "data/candidates/japanese_core_2000.jsonl").read_text(encoding="utf-8").splitlines():
        r = json.loads(line); words[r["entry_id"]] = r
    for line in (ROOT / "data/staging/english_loanwords_1000_candidates.jsonl").read_text(encoding="utf-8").splitlines():
        for r in json.loads(line)["japanese_entries"]:
            words[r["entry_id"]] = r
    assert len(words) == 3000
    japanese = sentence_meta(RAW / "jpn_sentences_detailed.tsv.bz2")
    chinese = sentence_meta(EXTRA / "cmn_sentences_detailed.tsv.bz2")
    translations = defaultdict(list)
    with bz2.open(EXTRA / "jpn-cmn_links.tsv.bz2", "rt", encoding="utf-8") as stream:
        for line in stream:
            a, b = line.strip().split("\t")
            if a in japanese and b in chinese:
                translations[a].append(b)
    examples, links = {}, defaultdict(dict)
    stats = Counter()

    def include(wid, sid, sense_id, method, indexed_text, **extra):
        if sid not in japanese:
            return
        e = examples.setdefault(sid, example_record(sid, japanese[sid]))
        assoc = {"entry_id": wid, "sense_id": sense_id, "alignment_source": method,
                 "indexed_text": indexed_text, **extra}
        key = (sid, sense_id)
        links[wid][key] = assoc
        if assoc not in e["associations"]:
            e["associations"].append(assoc)

    # Retain all prior examples; do not silently remove previous teaching content.
    for line in (ROOT / "data/candidates/japanese_examples.jsonl").read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        sid = e["example_id"].split(":")[1]
        if sid not in japanese or japanese[sid]["text"] != e["text"]:
            continue
        for a in e["associations"]:
            if a["entry_id"] in words:
                include(a["entry_id"], sid, a["sense_id"], "JMdict_e_examp", a.get("indexed_text"))
    for node in entries(RAW / "JMdict_e_examp.gz"):
        wid = "jmdict:" + node.findtext("ent_seq")
        if wid not in words:
            continue
        word = words[wid]
        for i, sense in enumerate(node.findall("sense"), 1):
            match = next((s for s in word["senses"] if s["sense_number"] == i and
                          [g["text"] for g in s["japanese_meaning_english_glosses"]] ==
                          ["".join(g.itertext()) for g in sense.findall("gloss")]), None)
            if not match:
                continue
            for ex in sense.findall("example"):
                if len({sid for sid, _ in links[wid]}) >= 3:
                    break
                src = ex.find("ex_srce")
                if src is None or src.get("exsrc_type") != "tat":
                    continue
                sid = src.text
                text = next((x.text for x in ex.findall("ex_sent") if x.get(LANG) == "jpn"), None)
                if sid in japanese and japanese[sid]["text"] == text:
                    include(wid, sid, match["sense_id"], "JMdict_e_examp", ex.findtext("ex_text"))

    # Index-only matches remain explicitly unaligned to current JMdict senses.
    forms = defaultdict(set)
    for wid, w in words.items():
        for f in [k["text"] for k in w["kanji"]] + [r["text"] for r in w["readings"]]:
            forms[f].add(wid)
    candidates = defaultdict(dict)
    for row in corpus_lines(EXTRA / "jpn_indices.tar.bz2"):
        if len(row) < 3 or row[0] not in japanese:
            continue
        sid, _, indices = row[:3]
        text = japanese[sid]["text"]
        if not 4 <= len(text) <= 120:
            continue
        for token in indices.split():
            match = TOKEN.fullmatch(token)
            if not match:
                continue
            head, reading, sense, surface, checked = match.groups()
            choices = forms.get(head, set())
            if reading and reading.startswith("#"):
                choices = choices & {"jmdict:" + reading[1:]}
            elif reading:
                choices = {wid for wid in choices if reading in {r["text"] for r in words[wid]["readings"]}}
            if len(choices) != 1:
                continue
            wid, = choices
            if (surface or head) not in text and not (reading and reading in text):
                continue
            score = (not bool(checked), not bool(translations[sid]), len(text), int(sid))
            candidates[wid][sid] = (score, token, sense)
    for wid, choices in candidates.items():
        for sid, (_, token, source_sense) in sorted(choices.items(), key=lambda item: item[1][0]):
            if len({s for s, _ in links[wid]}) >= 3:
                break
            if any(s == sid for s, _ in links[wid]):
                continue
            include(wid, sid, "", "Tatoeba_jpn_indices_word_only", token,
                    source_sense_number=source_sense, sense_alignment_status="not_aligned_to_current_JMdict")
    selected = set(sid for associations in links.values() for sid, _ in associations)
    for sid in selected:
        examples[sid]["translations"] = [example_record(tid, chinese[tid], "cmn")
                                        for tid in sorted(set(translations[sid]), key=int)[:2]]
    reusable_audio = defaultdict(list)
    for f in corpus_lines(EXTRA / "sentences_with_audio.tar.bz2"):
        if len(f) >= 5 and f[0] in selected and f[3] in {"CC BY 4.0", "CC BY-SA 4.0", "CC0 1.0"}:
            reusable_audio[f[0]].append({"id": "tatoeba-audio:" + f[1], "audio_id": f[1], "author": f[2],
                "license": f[3], "attribution_url": f[4], "url": "https://tatoeba.org/audio/download/" + f[1]})
    for sid in selected:
        examples[sid]["audio_candidates"] = reusable_audio[sid]
    result = [examples[s] for s in sorted(selected, key=int)]
    covered = sum(bool(v) for v in links.values())
    stats.update(examples=len(result), words_with_examples=covered, words_without_examples=3000-covered,
                 examples_with_chinese=sum(bool(e["translations"]) for e in result),
                 examples_with_open_audio=sum(bool(e["audio_candidates"]) for e in result),
                 word_example_links=sum(len(v) for v in links.values()))
    missing = [{"word_id":wid, "form":w["display_kanji"] or w["display_reading"],
                "reason":"No attributable unambiguous dictionary/index-linked example found in checked exports"}
               for wid,w in words.items() if not links[wid]]
    save(OUT / "examples_enrichment.json", {"schema_version":1, "examples":result, "missing_words":missing})
    return dict(stats)


def download(url, path):
    if path.exists():
        return
    error = None
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent":UA}), timeout=45) as response:
                data = response.read()
            if not data.startswith((b"OggS", b"ID3", b"\xff")):
                raise ValueError("Response is not an expected audio file")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return
        except Exception as exc:
            if isinstance(exc, HTTPError) and exc.code in (403, 429):
                raise
            error = exc
            time.sleep(attempt + 1)
    raise error


def collect_kana_audio(do_download):
    doc = json.loads((EXTRA / "commons_kana_audio.json").read_text(encoding="utf-8"))
    inventory = json.loads((ROOT / "data/curated/kana_inventory.json").read_text(encoding="utf-8"))["records"]
    forms = {r["hiragana"]: r["id"] for r in inventory if r.get("hiragana")}
    records, failures = [], []
    network_available = do_download
    for p in doc["query"]["pages"].values():
        info = p["imageinfo"][0]; meta = info["extmetadata"]
        if meta.get("LicenseShortName",{}).get("value") != "Public domain":
            continue
        description = html.unescape(re.sub("<[^>]+>", "", meta["ImageDescription"]["value"]))
        match = re.search("「([^」]+)」", description)
        if not match or match[1] not in forms:
            failures.append({"title":p["title"],"reason":"Description has no exact indexed hiragana spelling"})
            continue
        url = urlunsplit((*urlsplit(info["url"])[:3], "", ""))
        filename = "commons-" + str(p["pageid"]) + Path(urlsplit(url).path).suffix
        path = MEDIA / filename
        if network_available and not path.exists():
            try:
                download(url, path)
            except Exception as exc:
                if isinstance(exc, HTTPError) and exc.code in (403, 429):
                    network_available = False
                failures.append({"title":p["title"],"reason":str(exc)}); continue
            time.sleep(2)
        if not path.exists():
            failures.append({"title":p["title"],"reason":"not_downloaded_remote_rate_limit_or_download_disabled"})
            continue
        records.append({"id":"commons-audio:"+str(p["pageid"]), "kana_id":forms[match[1]],
            "text":match[1], "language":"ja", "kind":"human_recording", "local_path":"media/"+filename,
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(), "bytes":path.stat().st_size,
            "author":html.unescape(re.sub("<[^>]+>", "", meta["Artist"]["value"])),
            "license":"Public Domain (PD-self)", "source_url":info["descriptionurl"], "download_url":url,
            "description":description, "source_ids":["commons-kana-recordings"],
            "note_zh":"单独假名示范；助词、拨音及连续语流读音仍需结合语境。"})
    save(OUT / "audio_enrichment.json", {"schema_version":1, "recordings":records, "unavailable":failures})
    return {"kana_recordings":len(records),"kana_recording_bytes":sum(r["bytes"] for r in records),"audio_failed":len(failures)}


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--download",action="store_true");args=parser.parse_args()
    counts=collect_examples();print(json.dumps(counts),flush=True)
    counts.update(collect_kana_audio(args.download))
    sources=[
      {"id":"tatoeba-enrichment","title":"Tatoeba 例句、词形索引与普通话译文","url":"https://tatoeba.org/en/downloads",
       "license":"CC-BY-2.0-FR","attribution":"Tatoeba contributors; per-sentence owner and history links retained",
       "changes":"Subset selection; added dictionary/index associations; sentence and translation text unchanged.",
       "license_url":"https://creativecommons.org/licenses/by/2.0/fr/"},
      {"id":"commons-kana-recordings","title":"Hakatanoshio117117 假名录音（Wikimedia Commons）",
       "url":"https://commons.wikimedia.org/wiki/Category:Audio_files_of_hiragana_(set_by_Hakatanoshio117117)",
       "license":"Public Domain (PD-self)","attribution":"Hakatanoshio117117; individual file URLs retained",
       "changes":"Original Ogg files unchanged; associated with exact kana spelling from file descriptions."}]
    save(OUT/'sources_examples_audio.json',{'sources':sources})
    manifest=[]
    for path in [RAW/'JMdict_e_examp.gz', RAW/'jpn_sentences_detailed.tsv.bz2', *sorted(EXTRA.iterdir())]:
        if path.is_file():manifest.append({'path':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    save(ROOT/'research/examples_audio_enrichment_audit.json',{'counts':counts,'input_manifest':manifest,
        'limitations':['Index-only matches are word-level candidates, not automatically aligned to dictionary senses.',
        'Tatoeba sentences and Chinese translations remain community candidates, not native-editor-approved curriculum.',
        'Empty/NC-only sentence-audio licenses are not redistributed in this open-data bundle.']})
    print(json.dumps(counts,ensure_ascii=False),flush=True)


if __name__ == '__main__':main()
