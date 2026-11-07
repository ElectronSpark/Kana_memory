"""Reproducible English-source enrichment of the packaged 1000 loan candidates.

Offline by default. --download caches official WordNet, CMUdict, CC-CEDICT and
English Wiktionary revisions. Existing research data and the app DB are read
only. This is lexical evidence collection, not automatic Japanese sense review.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sqlite3
import tarfile
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data/raw/english_enrichment"
OUT = ROOT / "data/enrichment"
INPUT = ROOT / "data/staging/english_loanwords_1000_candidates.jsonl"
DB = ROOT / "app/src/kanamemory/resources/content.sqlite3"
AGENT = "KanaMemoryResearch/0.2 (open dictionary enrichment; interactive personal research)"
WN = "wordnet_3_0_english"
CMU = "cmudict_english_pronunciation"
CEDICT = "cc_cedict_2026_10_10"
WIKI = "enwiktionary_english_source_entries"
WIKI_ZH = "enwiktionary_mandarin_loan_evidence"
POS = {"Noun", "Verb", "Adjective", "Adverb", "Proper noun", "Interjection", "Phrase", "Proverb", "Preposition", "Conjunction", "Determiner", "Pronoun", "Article", "Numeral", "Particle", "Prefix", "Suffix", "Abbreviation", "Initialism", "Contraction"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + ".part")
    pending.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(5):
        try:
            pending.replace(path)
            break
        except PermissionError:
            if attempt == 4:
                raise
            # A synced workspace or virus scanner may briefly hold the target.
            time.sleep(0.2 * (attempt + 1))


def download(url, path):
    if path.exists():
        return path.read_bytes()
    for attempt in range(3):
        try:
            with urlopen(Request(url, headers={"User-Agent": AGENT}), timeout=60) as response:
                raw = response.read()
            if url.startswith("https://en.wiktionary.org/w/api.php"):
                parsed = json.loads(raw)
                if "error" in parsed:
                    if parsed["error"].get("code") in {"maxlag", "ratelimited"} and attempt < 2:
                        time.sleep(5 * 2 ** attempt)
                        continue
                    raise RuntimeError(str(parsed["error"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            pending = path.with_suffix(path.suffix + ".part")
            pending.write_bytes(raw)
            pending.replace(path)
            return raw
        except HTTPError as exc:
            if exc.code in {429, 503} and attempt < 2:
                time.sleep(min(30, max(5, int(exc.headers.get("Retry-After", "5")))))
                continue
            raise


def bulk_sources(fetch):
    urls = {
        "WordNet-3.0.tar.gz": "https://wordnetcode.princeton.edu/3.0/WordNet-3.0.tar.gz",
        "cmudict_commit.json": "https://api.github.com/repos/cmusphinx/cmudict/commits/master",
        "cedict_1_0_ts_utf-8_mdbg.txt.gz": "https://www.mdbg.net/chinese/export/cedict/cedict_1_0_ts_utf-8_mdbg.txt.gz",
    }
    if fetch:
        for name, url in urls.items():
            download(url, RAW / name)
    commit = json.loads((RAW / "cmudict_commit.json").read_text(encoding="utf-8"))["sha"]
    for original in ("cmudict.dict", "LICENSE", "README"):
        name = original if original == "cmudict.dict" else "cmudict_" + original
        urls[name] = f"https://raw.githubusercontent.com/cmusphinx/cmudict/{commit}/{original}"
        if fetch:
            download(urls[name], RAW / name)
    return urls, commit


def source_forms(expression):
    """Remove display markup, preserve explicit alternative forms and sense hints."""
    notes = []
    cleaned = expression
    if "[[" in cleaned:
        cleaned = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m.group(2) or m.group(1), cleaned)
        notes.append("Removed Wiktionary display link markup only.")
    hints = re.findall(r"<id:([^>]+)>", cleaned)
    cleaned = re.sub(r"<id:[^>]+>", "", cleaned)
    if "#" in cleaned:
        cleaned, fragment = cleaned.split("#", 1)
        hints.append(fragment)
    pieces = re.split(r"\s*(?:/|,\s+)\s*", cleaned)
    forms = list(dict.fromkeys(piece.strip() for piece in pieces if piece.strip()))
    if len(forms) > 1:
        notes.append("Several source spellings were supplied; their relationship remains unreviewed.")
    return forms, hints, notes


def load_wordnet():
    index, synsets, exceptions = {}, {}, {}
    with tarfile.open(RAW / "WordNet-3.0.tar.gz", "r:gz") as archive:
        names = {Path(member.name).name: member for member in archive.getmembers() if member.isfile()}
        license_text = archive.extractfile(names["LICENSE"]).read().decode("utf-8")
        for part, shortpos in (("noun", "n"), ("verb", "v"), ("adj", "a"), ("adv", "r")):
            data = archive.extractfile(names["data." + part]).read().decode("utf-8")
            for line in data.splitlines():
                if not line or not line[0].isdigit():
                    continue
                body, gloss = line.split("|", 1)
                fields = body.split()
                offset, lexfile, pos = fields[:3]
                count = int(fields[3], 16)
                synonyms = [fields[4 + i * 2].replace("_", " ") for i in range(count)]
                examples = re.findall(r'"([^"\n]+)"', gloss)
                definition = re.sub(r';?\s*"[^"\n]+"', "", gloss).strip().rstrip(";")
                sid = f"wn30:{offset}-{shortpos}"
                synsets[(offset, shortpos)] = {"sense_id": sid, "pos": {"n": "noun", "v": "verb", "a": "adjective", "s": "adjective satellite", "r": "adverb"}[pos], "definition": definition, "examples": examples, "raw_gloss": gloss.strip(), "synonyms": synonyms, "lexicographer_file": lexfile, "source_ids": [WN]}
            rawindex = archive.extractfile(names["index." + part]).read().decode("utf-8")
            for line in rawindex.splitlines():
                if not line or line.startswith(" "):
                    continue
                values = line.split()
                lemma, pos, count, pointers = values[:4]
                offsets = values[6 + int(pointers):]
                index[(lemma, shortpos)] = [(offset, shortpos) for offset in offsets]
            rawexc = archive.extractfile(names[part + ".exc"]).read().decode("utf-8")
            for line in rawexc.splitlines():
                values = line.split()
                exceptions[(values[0], shortpos)] = values[1:]
    return index, synsets, exceptions, license_text


def wordnet_lookup(lemma, data):
    index, synsets, exceptions, _ = data
    key = lemma.lower().replace(" ", "_")
    entries, matched = [], []
    for pos in "nvar":
        forms = [key] if (key, pos) in index else exceptions.get((key, pos), [])
        for form in forms:
            for sense in index.get((form, pos), []):
                entries.append(dict(synsets[sense], matched_lemma=form.replace("_", " "), lookup_method="exact_lemma" if form == key else "WordNet_exception_table"))
            if (form, pos) in index:
                matched.append(form.replace("_", " "))
    return list({entry["sense_id"]: entry for entry in entries}.values()), list(dict.fromkeys(matched))


def load_cmu():
    result = defaultdict(list)
    for number, line in enumerate((RAW / "cmudict.dict").read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith(";;; "):
            continue
        spelling, value = line.split(" ", 1)
        base = re.sub(r"\(\d+\)$", "", spelling)
        result[base].append({"notation": "ARPABET", "value": value, "variety": "US English", "dictionary_spelling": spelling, "source_line": number, "source_ids": [CMU], "status": "dictionary_attested", "note": "CMU vowel suffixes 0/1/2 denote unstressed/primary/secondary stress; alternatives are not aligned to POS or Japanese senses."})
    return result


def normal_gloss(value):
    value = re.sub(r"\([^()]*\)", "", value).strip().lower().replace("_", " ")
    value = re.sub(r"^(?:to |an? |the )", "", value)
    return " ".join(value.split()).rstrip(".,;:")


def load_cedict():
    raw = gzip.decompress((RAW / "cedict_1_0_ts_utf-8_mdbg.txt.gz").read_bytes()).decode("utf-8-sig")
    by_gloss = defaultdict(list)
    headers = []
    for number, line in enumerate(raw.splitlines(), 1):
        if line.startswith("#"):
            headers.append(line)
            continue
        match = re.fullmatch(r"(\S+) (\S+) \[(.+?)\] /(.*)/", line)
        if not match:
            continue
        traditional, simplified, pinyin, definitions = match.groups()
        glosses = definitions.split("/")
        for gloss in glosses:
            key = normal_gloss(gloss)
            if not key:
                continue
            explicit = bool(re.search(r"\btransliteration\b|\bphonetic(?: loan| transcription)\b", gloss, flags=re.I))
            loan = bool(re.search(r"\bloanword\b|\bloan word\b", gloss, flags=re.I))
            by_gloss[key].append({"traditional": traditional, "simplified": simplified, "pinyin": pinyin, "matched_english_gloss": gloss, "dictionary_glosses": glosses, "source_line": number, "source_ids": [CEDICT], "dictionary_record_id": f"cedict-line:{number}", "translation_type": "explicit_phonetic_marker" if explicit else "source_labeled_loanword" if loan else "translation_strategy_unclassified", "match_method": "exact_English_gloss_after_removing_parenthetic_labels_and_initial_article_or_to", "sense_alignment_status": "English_source_lemma_only_not_yet_aligned_to_Japanese_sense"})
    return by_gloss, headers


def wiki_pages(titles, fetch, batch_size, pause, scope="wiktionary_english"):
    folder = RAW / scope
    folder.mkdir(parents=True, exist_ok=True)
    pages, manifest, errors = {}, [], []
    for start in range(0, len(titles), batch_size):
        batch = titles[start:start + batch_size]
        cache = folder / ("batch_" + digest("\n".join(batch).encode())[:16] + ".json")
        url = "https://en.wiktionary.org/w/api.php?" + urlencode({"action": "query", "titles": "|".join(batch), "redirects": 1, "prop": "revisions", "rvprop": "ids|timestamp|content", "rvslots": "main", "format": "json", "formatversion": 2, "maxlag": 5})
        cached = cache.exists()
        if not cached and fetch:
            try:
                download(url, cache)
                time.sleep(pause)
            except Exception as exc:
                errors.append({"batch": start // batch_size + 1, "error": str(exc), "titles": batch})
                print("Wiktionary batch failed; remaining batches stay resumable: " + str(exc), flush=True)
                break
        if not cache.exists():
            continue
        raw = cache.read_bytes()
        query = json.loads(raw).get("query", {})
        mapping = {record["from"]: record["to"] for key in ("normalized", "redirects") for record in query.get(key, [])}
        records = {page["title"]: page for page in query.get("pages", [])}
        for original in batch:
            title, seen = original, set()
            while title in mapping and title not in seen:
                seen.add(title)
                title = mapping[title]
            pages[original] = records.get(title, {"title": title})
        manifest.append({"requested_titles": batch, "api_url": url, "raw_path": str(cache.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(raw), "bytes": len(raw)})
        write(folder / "request_manifest.json", {"batches": manifest, "errors": errors, "selected_title_count": len(titles), "loaded_title_count": len(pages)})
        print(f"{scope} {min(start + batch_size, len(titles))}/{len(titles)}; cache={cached}", flush=True)
    return pages, manifest, errors


def clean_wiki(text):
    """Conservative display cleanup; raw evidence remains alongside extraction."""
    text = re.sub(r"<ref\b[^>]*>.*?</ref>|<ref\b[^>]*/>", "", text, flags=re.S)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    def template(match):
        items = match.group(1).split("|")
        name = items[0].lower()
        values = [value for value in items[1:] if "=" not in value]
        if name in {"senseid", "sid", "rfe", "rfdef", "defdate", "def-date", "attention", "rfv-sense", "rfex", "rfquote"}:
            return ""
        if values and values[0] in {"en", "mul", "la"}:
            values = values[1:]
        if name in {"lb", "label", "lbl", "context", "q", "qualifier", "i", "qual"}:
            values = [value for value in values if value not in {"_", "and"}]
            return "(" + ", ".join(values) + ")" if values else ""
        if name in {"gloss", "sense", "non-gloss definition", "ngd", "n-g"}:
            return " ".join(values)
        if name in {"l", "link", "m", "mention", "w", "wikipedia", "taxlink", "taxon", "vern", "syn", "ux", "uxi"}:
            return values[0] if values else ""
        if " of" in name or name in {"plural of", "infl of", "inflection of", "abbreviation of", "initialism of"}:
            return name + " " + (values[0] if values else "")
        return " ".join(values) if values else ""
    for _ in range(6):
        following = re.sub(r"\{\{([^{}]+)\}\}", template, text)
        if following == text:
            break
        text = following
    text = re.sub(r"\[\[([^]|]+)(?:\|([^]]+))?\]\]", lambda m: m.group(2) or m.group(1).split("#", 1)[0], text)
    text = re.sub(r"<[^>]+>", "", text).replace("'''", "").replace("''", "")
    return " ".join(html.unescape(text).split())


def parse_english_page(page):
    empty = {"senses": [], "pronunciations": [], "mandarin": [], "sources": [], "status": "page_or_revision_missing"}
    if not page or not page.get("revisions"):
        return empty
    revision = page["revisions"][0]
    revid, title = revision["revid"], page["title"]
    sid = f"enwiktionary-english:{page['pageid']}:{revid}"
    source = {"source_id": sid, "title": "English Wiktionary: " + title, "url": f"https://en.wiktionary.org/w/index.php?oldid={revid}", "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "record_kind": "citation", "parent_source_id": WIKI, "revision_id": revid, "revision_timestamp": revision["timestamp"], "attribution": "English Wiktionary contributors", "attribution_history_url": "https://en.wiktionary.org/w/index.php?title=" + quote(title) + "&action=history", "changes": "Extracted English-language senses, explicit pronunciation templates and Mandarin translation templates; raw revision preserved; template display cleanup is partial."}
    raw = revision["slots"]["main"]["content"]
    match = re.search(r"^==\s*English\s*==\s*$", raw, re.M)
    if not match:
        return dict(empty, sources=[source], status="no_English_section")
    english = re.split(r"^==[^=].*?==\s*$", raw[match.end():], maxsplit=1, flags=re.M)[0]
    senses, pronunciations, mandarin = [], [], []
    pos, etymology, translation_sense = None, None, None
    for line in english.splitlines():
        header = re.match(r"^(={3,6})\s*([^=]+?)\s*\1\s*$", line)
        if header:
            name = header[2].strip()
            if name.startswith("Etymology"):
                etymology = name
            pos = name if name in POS else None
        if pos and re.match(r"^#+\s+", line):
            raw_definition = re.sub(r"^#+\s+", "", line)
            definition = clean_wiki(raw_definition)
            if definition and "{{" not in definition:
                senses.append({"sense_id": f"{sid}:definition:{len(senses) + 1}", "pos": pos.lower(), "definition": definition, "examples": [], "etymology_section": etymology, "raw_wikitext": raw_definition, "source_ids": [sid], "extraction_status": "automated_template_cleanup_not_editor_reviewed"})
        for template in re.finditer(r"\{\{(IPA|enPR|en-PR)\|([^{}]+)\}\}", line):
            notation, values = template[1], template[2].split("|")
            labels = {value.split("=", 1)[0]: value.split("=", 1)[1] for value in values if "=" in value}
            if notation == "IPA":
                if not values or values.pop(0) != "en":
                    continue
            for value in values:
                if value and "=" not in value and (notation != "IPA" or value.startswith(("/", "["))):
                    pronunciations.append({"notation": "IPA" if notation == "IPA" else "enPR", "value": value, "variety": labels.get("a", "see original English entry for regional scope"), "template_qualifiers": labels, "raw_wikitext": template[0], "source_ids": [sid], "status": "explicit_dictionary_template"})
        trans = re.search(r"\{\{trans-top\|([^{}]+)\}\}", line)
        if trans:
            translation_sense = clean_wiki(trans[1].split("|id=")[0])
        for template in re.finditer(r"\{\{(?:t\+?|tt\+?|t-check|t\+check)\|cmn\|([^{}]+)\}\}", line):
            values = template[1].split("|")
            term = values[0]
            pinyin = next((v[3:] for v in values[1:] if v.startswith("tr=")), None)
            if term and not term.startswith("-"):
                mandarin.append({"text": term, "pinyin": pinyin, "english_translation_sense": translation_sense, "source_ids": [sid], "raw_wikitext": template[0], "translation_type": "translation_strategy_unclassified", "match_method": "explicit_Mandarin_translation_template_in_English_entry", "sense_alignment_status": "English_source_lemma_only_not_yet_aligned_to_Japanese_sense"})
        if "{{trans-bottom" in line:
            translation_sense = None
    dedup = lambda entries, fields: list({tuple(entry.get(key) for key in fields): entry for entry in entries}.values())
    return {"senses": senses, "pronunciations": dedup(pronunciations, ("notation", "value")), "mandarin": dedup(mandarin, ("text", "pinyin", "english_translation_sense")), "sources": [source], "status": "English_section_found", "resolved_title": title}


class IPAParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.text, self.values = 0, [], []

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class", "").split()
        if self.depth:
            if tag not in {"br", "hr", "img", "input", "meta", "link"}:
                self.depth += 1
        elif tag == "span" and "IPA" in classes:
            self.depth = 1
            self.text = []

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1
            if not self.depth:
                value = "".join(self.text).strip()
                if value.startswith(("/", "[")):
                    self.values.append(value)

    def handle_data(self, text):
        if self.depth:
            self.text.append(text)


def rendered_ipa(page, fetch, pause):
    """Read generated en-IPA from the source revision's official rendered HTML."""
    if not page or not page.get("revisions"):
        return [], None
    revid = page["revisions"][0]["revid"]
    path = RAW / "wiktionary_rendered" / f"revision_{revid}.json"
    url = "https://en.wiktionary.org/w/api.php?" + urlencode({"action": "parse", "oldid": revid, "prop": "text", "format": "json", "formatversion": 2, "maxlag": 5, "disableeditsection": 1})
    if not path.exists() and fetch:
        download(url, path)
        time.sleep(pause)
    if not path.exists():
        return [], None
    raw = path.read_bytes()
    text = json.loads(raw).get("parse", {}).get("text", "")
    if isinstance(text, dict):
        text = text.get("*", "")
    heading = re.search(r'<h2\b[^>]*\bid="English"[^>]*>.*?</h2>', text, flags=re.S)
    if not heading:
        # Older MediaWiki wraps the English id in a child span.
        heading = re.search(r'<h2\b[^>]*>\s*<span[^>]*\bid="English"[^>]*>.*?</h2>', text, flags=re.S)
    if not heading:
        return [], {"revision_id": revid, "status": "English_heading_not_found", "raw_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(raw)}
    english = re.split(r"<h2\b", text[heading.end():], maxsplit=1)[0]
    parser = IPAParser()
    parser.feed(english)
    sid = f"enwiktionary-english:{page['pageid']}:{revid}"
    evidence = {"revision_id": revid, "raw_path": str(path.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(raw), "url": url, "note": "Revision text pinned; template modules are expanded by Wikimedia at fetch time and the full rendered response is cached."}
    return [{"notation": "IPA", "value": value, "variety": "see original English entry for regional scope", "source_ids": [sid], "status": "official_rendered_dictionary_pronunciation", "rendered_evidence": evidence} for value in dict.fromkeys(parser.values)], evidence


def chinese_borrowing_evidence(page, english_lemma):
    """Only an explicit English borrowing template can upgrade a loan candidate."""
    if not page or not page.get("revisions"):
        return None, None
    revision = page["revisions"][0]
    raw = revision["slots"]["main"]["content"]
    match = re.search(r"^==\s*Chinese\s*==\s*$", raw, flags=re.M)
    if not match:
        return None, None
    chinese = re.split(r"^==[^=].*?==\s*$", raw[match.end():], maxsplit=1, flags=re.M)[0]
    headings = list(re.finditer(r"^(={3,6})\s*([^=]+?)\s*\1\s*$", chinese, flags=re.M))
    etymologies = []
    for number, heading in enumerate(headings):
        if not heading[2].strip().startswith("Etymology"):
            continue
        end = next((later.start() for later in headings[number + 1:] if len(later[1]) <= len(heading[1])), len(chinese))
        etymologies.append(chinese[heading.end():end])
    if len(etymologies) != 1:
        return None, None
    claims = []
    for template in re.finditer(r"\{\{(?:bor\+?|borrowed)\|([^{}]+)\}\}", etymologies[0]):
        values = [part for part in template[1].split("|") if "=" not in part]
        if len(values) >= 3 and values[0] in {"zh", "cmn"} and values[1] in {"en", "en-US", "en-GB"}:
            source = re.sub(r"<[^>]+>", "", values[2]).split("#", 1)[0]
            if normal_gloss(source) == normal_gloss(english_lemma):
                claims.append({"recipient_language": values[0], "donor_language": values[1], "english_source_expression": source, "raw_wikitext": template[0]})
    if not claims:
        return None, None
    revid = revision["revid"]
    sid = f"enwiktionary-chinese:{page['pageid']}:{revid}"
    source = {"source_id": sid, "title": "English Wiktionary Chinese entry: " + page["title"], "url": f"https://en.wiktionary.org/w/index.php?oldid={revid}", "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "record_kind": "citation", "parent_source_id": WIKI_ZH, "revision_id": revid, "revision_timestamp": revision["timestamp"], "attribution": "English Wiktionary contributors", "attribution_history_url": "https://en.wiktionary.org/w/index.php?title=" + quote(page["title"]) + "&action=history", "changes": "Extracted one Chinese etymology section's explicit English borrowing template; paired with CC-CEDICT's attested Mandarin form and loanword label."}
    return {"classification": "source_attested_English_borrowing_with_Mandarin_dictionary_loanword_label", "claims": claims, "source_ids": [sid], "scope_note": "Two dictionaries supply a borrowing claim and Mandarin lexical attestation. This does not assert that every Japanese sense shares the same Mandarin usage, nor that the spelling has no semantic component."}, source


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--batch-size", type=int, default=40)
    parser.add_argument("--pause", type=float, default=1.2)
    args = parser.parse_args()
    if not 1 <= args.batch_size <= 50 or args.pause < 1:
        parser.error("Use batches of 1..50 titles and a pause of at least 1 second")
    RAW.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(line) for line in INPUT.read_text(encoding="utf-8").splitlines()]
    with sqlite3.connect(DB.resolve().as_uri() + "?mode=ro", uri=True) as db:
        packaged_ids = {row[0] for row in db.execute("SELECT word_id FROM word_collection WHERE collection='english_loanwords'")}
        existing_source_ids = {row[0] for row in db.execute("SELECT id FROM source")}
    ids = {wid for row in rows for wid in row["jmdict_entry_ids"]}
    if ids != packaged_ids or len(ids) != 1000:
        raise ValueError("Input staging IDs do not match all 1000 packaged English-loanword candidates")
    urls, commit = bulk_sources(args.download)
    titles = list(dict.fromkeys(lemma for row in rows for lemma in source_forms(row["english_source_expression"])[0]))
    pages, wiki_manifest, fetch_errors = wiki_pages(titles, args.download, args.batch_size, args.pause)
    wordnet, cmu, (cedict, cedict_headers) = load_wordnet(), load_cmu(), load_cedict()
    sources = {
        WN: {"source_id": WN, "title": "Princeton WordNet 3.0", "url": "https://wordnet.princeton.edu/", "license": "WordNet-3.0", "license_url": "https://wordnet.princeton.edu/license-and-commercial-use", "license_text": wordnet[3], "attribution": "WordNet 3.0 Copyright 2006 by Princeton University. All rights reserved.", "changes": "Selected independent English lemma senses/examples, retaining synset IDs and exception-table lookup provenance.", "record_kind": "family"},
        CMU: {"source_id": CMU, "title": "CMU Pronouncing Dictionary", "url": f"https://github.com/cmusphinx/cmudict/tree/{commit}", "license": "BSD-2-Clause", "license_text": (RAW / "cmudict_LICENSE").read_text(encoding="utf-8"), "attribution": "Copyright (C) 1993-2015 Carnegie Mellon University. All rights reserved.", "commit": commit, "changes": "Selected exact English pronunciations in original ARPABET notation; component-only readings clearly separated from attested phrase pronunciations.", "record_kind": "family"},
        CEDICT: {"source_id": CEDICT, "title": "CC-CEDICT Chinese-English dictionary (2026-10-10)", "url": "https://www.mdbg.net/chinese/dictionary?page=cc-cedict", "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": "CC-CEDICT contributors; original CEDICT project by Paul Denisowski; distributed by MDBG.", "raw_header_notice": "\n".join(cedict_headers), "changes": "Selected lexical records with original Chinese forms, pinyin, English glosses and snapshot line references; downstream English-lemma or same-spelling matching does not automatically establish Japanese sense or etymological equivalence.", "record_kind": "family"},
        WIKI: {"source_id": WIKI, "title": "English Wiktionary English-source lexical enrichment", "url": "https://en.wiktionary.org/", "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": "English Wiktionary contributors; retain each entry's revision URL and history link.", "changes": "Official API cached English-language revisions; extracted literal English senses, explicit pronunciation and Mandarin-translation templates. Partial wikitext display cleanup; unreviewed Japanese sense alignment.", "record_kind": "family"},
        WIKI_ZH: {"source_id": WIKI_ZH, "title": "English Wiktionary Chinese borrowing evidence", "url": "https://en.wiktionary.org/", "license": "CC-BY-SA-4.0", "license_url": "https://creativecommons.org/licenses/by-sa/4.0/", "attribution": "English Wiktionary contributors; retain each entry's revision URL and history link.", "changes": "Compared Chinese etymology English-borrowing templates against English lemmas and CC-CEDICT Mandarin loanword records. Sound history and Japanese-sense alignment remain unreviewed.", "record_kind": "family"},
    }
    bulk_manifest = [{"path": str((RAW / name).relative_to(ROOT)).replace("\\", "/"), "url": url, "bytes": (RAW / name).stat().st_size, "sha256": digest((RAW / name).read_bytes())} for name, url in urls.items()]
    records, cache, rendered_manifest, supplemental_errors = [], {}, [], []
    for row in rows:
        forms, hints, notes = source_forms(row["english_source_expression"])
        english_entries, counterparts, source_ids = [], [], set()
        for lemma in forms:
            if lemma not in cache:
                wn_senses, matches = wordnet_lookup(lemma, wordnet)
                wiki = parse_english_page(pages.get(lemma))
                for source in wiki["sources"]:
                    sources[source["source_id"]] = source
                pronunciations = wiki["pronunciations"] + cmu.get(lemma.lower(), [])
                if not pronunciations and wiki["status"] == "English_section_found":
                    try:
                        pronunciations, evidence = rendered_ipa(pages.get(lemma), args.download, args.pause)
                        if evidence:
                            rendered_manifest.append(evidence)
                    except Exception as exc:
                        supplemental_errors.append({"kind": "rendered_pronunciation", "lemma": lemma, "error": str(exc)})
                components = []
                if not pronunciations and " " in lemma:
                    components = [{"word": token, "pronunciations": cmu.get(token.lower(), [])} for token in lemma.split()]
                c_entries = [{**entry, "english_lemma": lemma} for entry in cedict.get(normal_gloss(lemma), [])]
                c_entries += [{**entry, "english_lemma": lemma} for entry in wiki["mandarin"]]
                sense_sources = set(sid for sense in wn_senses + wiki["senses"] for sid in sense["source_ids"])
                sense_sources.update(sid for pron in pronunciations for sid in pron["source_ids"])
                cache[lemma] = ({"lemma": lemma, "source_language": "eng", "lookup_status": "independent_English_senses_found" if wn_senses or wiki["senses"] else "English_senses_not_found", "wiktionary_lookup_status": wiki["status"], "wiktionary_resolved_title": wiki.get("resolved_title"), "wordnet_matched_lemmas": matches, "pos": sorted({sense["pos"] for sense in wn_senses + wiki["senses"]}), "senses": wn_senses, "supplemental_wiktionary_senses": wiki["senses"], "pronunciations": pronunciations, "component_pronunciations": components, "component_pronunciation_note": "Individual-word pronunciations only; connected speech and phrase stress are not attested." if components else None, "source_ids": sorted(sense_sources), "definition_scope": "English lemma senses; not definitions copied from the Japanese JMdict entry", "sense_alignment_status": "not_automatically_aligned_to_Japanese_senses"}, c_entries)
            entry, terms = cache[lemma]
            english_entries.append(entry)
            counterparts.extend(terms)
            source_ids.update(entry["source_ids"])
            source_ids.update(sid for term in terms for sid in term["source_ids"])
        dedup = {}
        for counterpart in counterparts:
            key = (counterpart.get("english_lemma"), counterpart.get("traditional") or counterpart.get("text"), counterpart.get("pinyin"), counterpart.get("matched_english_gloss") or counterpart.get("english_translation_sense"))
            dedup.setdefault(key, counterpart)
        counterparts = list(dedup.values())
        phonetic = [c for c in counterparts if c["translation_type"] == "explicit_phonetic_marker"]
        loanwords = [c for c in counterparts if c["translation_type"] == "source_labeled_loanword"]
        translations = [c for c in counterparts if c["translation_type"] == "translation_strategy_unclassified"]
        for word_id in row["jmdict_entry_ids"]:
            missing = []
            if not any(entry["senses"] or entry["supplemental_wiktionary_senses"] for entry in english_entries):
                missing.append("independent_English_definition")
            if not any(entry["pronunciations"] for entry in english_entries):
                missing.append("attested_whole_expression_pronunciation")
            if not counterparts:
                missing.append("Mandarin_lexical_counterpart_evidence")
            records.append({"word_id": word_id, "japanese_form": row["headword"], "source_expression_raw": row["english_source_expression"], "source_expression_cleanup_notes": notes, "source_sense_hints": hints, "english_entries": english_entries, "mandarin_equivalents": {"semantic_translation_candidates": translations, "attested_transliteration_candidates": phonetic, "source_labeled_loanword_candidates": loanwords, "scope": "counterparts_of_English_source_lemma_not_yet_Japanese_sense_translations", "sense_alignment_status": "pending_editor_review", "absence_note": "No found transliteration is not proof that no established transliteration exists; ordinary translation entries do not prove a semantic-versus-phonetic etymology."}, "etymology_evidence_source_ids": row.get("etymology_evidence", {}).get("source_ids", []), "japanese_english_sense_alignment": [], "sense_alignment_status": "pending_editor_review", "review_status": "source_attested_lexical_data_with_automated_matching_not_editor_approved", "source_ids": sorted(source_ids), "missing_fields": missing, "known_remaining_gaps": ["Japanese-to-English sense alignment", "Mandarin-to-Japanese sense alignment", "independent review of the proposed English borrowing pathway"]})
    loan_terms = sorted({term["traditional"] for record in records for term in record["mandarin_equivalents"]["source_labeled_loanword_candidates"]})
    zh_pages, zh_manifest, zh_errors = wiki_pages(loan_terms, args.download, args.batch_size, args.pause, scope="wiktionary_mandarin_loans")
    for record in records:
        equivalents = record["mandarin_equivalents"]
        for term in equivalents["source_labeled_loanword_candidates"]:
            evidence, source = chinese_borrowing_evidence(zh_pages.get(term["traditional"]), term["english_lemma"])
            if evidence:
                sources[source["source_id"]] = source
                equivalents["attested_transliteration_candidates"].append(dict(term, translation_type="source_attested_English_borrowing_candidate", borrowing_evidence=evidence, source_ids=term["source_ids"] + evidence["source_ids"]))
                record["source_ids"] = sorted(set(record["source_ids"] + evidence["source_ids"]))
    generated = datetime.now(timezone.utc).isoformat()
    audit = {"generated_at_utc": generated, "input_file": str(INPUT.relative_to(ROOT)).replace("\\", "/"), "input_sha256": digest(INPUT.read_bytes()), "packaged_word_ids_verified": len(packaged_ids), "record_count": len(records), "unique_lookup_lemmas": len(titles), "wiktionary_lemmas_loaded": len(pages), "bulk_manifest": bulk_manifest, "wiktionary_batch_count": len(wiki_manifest), "fetch_errors": fetch_errors, "coverage": {"independent_English_definition": sum("independent_English_definition" not in r["missing_fields"] for r in records), "attested_whole_expression_pronunciation": sum("attested_whole_expression_pronunciation" not in r["missing_fields"] for r in records), "Mandarin_lexical_counterpart_evidence": sum("Mandarin_lexical_counterpart_evidence" not in r["missing_fields"] for r in records), "source_explicit_phonetic_marker": sum(bool(r["mandarin_equivalents"]["attested_transliteration_candidates"]) for r in records), "source_labeled_loanword": sum(bool(r["mandarin_equivalents"]["source_labeled_loanword_candidates"]) for r in records)}, "missing_by_field": dict(Counter(field for r in records for field in r["missing_fields"])), "status": "all_1000_processed_not_all_gaps_resolved", "limitations": ["An English word can have many senses, only some borrowed into Japanese. No unreviewed automatic sense equivalence is promoted.", "CC-CEDICT exact English-gloss matches and Wiktionary Mandarin templates attest lexical counterparts of the English lemma, not a guaranteed translation of every Japanese usage.", "CMUdict is US English ARPABET; POS and dialect distribution of alternatives are not inferred.", "Wiktionary extraction recognizes explicit templates only; generated en-IPA and /translations subpages may require a later pass.", "No pronunciation recordings, automatic Mandarin transliterations, or machine-translated English definitions were manufactured."]}
    audit["coverage"]["corroborated_English_loan_counterpart"] = audit["coverage"].pop("source_explicit_phonetic_marker")
    audit["supplemental_rendered_pronunciations"] = rendered_manifest
    audit["Mandarin_loan_evidence_titles"] = len(loan_terms)
    audit["Mandarin_loan_evidence_loaded"] = len(zh_pages)
    audit["Mandarin_loan_evidence_batches"] = len(zh_manifest)
    audit["fetch_errors"].extend(zh_errors + supplemental_errors)
    used_ids = set()
    def check_sources(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"source_ids", "etymology_evidence_source_ids"}:
                    used_ids.update(child)
                else:
                    check_sources(child)
        elif isinstance(value, list):
            for child in value:
                check_sources(child)
    check_sources(records)
    unresolved = sorted(used_ids - set(sources) - existing_source_ids)
    if unresolved:
        raise ValueError("Unresolved source IDs: " + repr(unresolved))
    if len(records) != 1000 or {record["word_id"] for record in records} != packaged_ids:
        raise ValueError("Output must preserve all and only the 1000 packaged candidate IDs")
    audit["validation"] = {"unique_word_ids": len({record["word_id"] for record in records}), "all_packaged_ids_covered": True, "recursive_source_ids_resolved": True, "unresolved_source_ids": unresolved, "records_payload_sha256": digest(json.dumps(records, ensure_ascii=False, sort_keys=True).encode()), "English_definitions_from_JMdict": 0}
    audit["limitations"][3] = "Official rendering was checked for entries without explicit phonetics; no English IPA was recovered for the remaining 27 expressions. Pronunciations in other language sections were deliberately excluded. Translation subpages are not automatically followed."
    write(OUT / "english_enrichment.json", {"schema_version": 1, "generated_at_utc": generated, "records": records})
    write(OUT / "sources_english_enrichment.json", {"schema_version": 1, "sources": [dict(source, id=source["source_id"]) for source in sources.values()], "bulk_manifest": bulk_manifest})
    write(ROOT / "research/english_enrichment_audit.json", audit)
    print(json.dumps({"records": len(records), "coverage": audit["coverage"], "missing": audit["missing_by_field"]}), flush=True)


if __name__ == "__main__":
    main()
