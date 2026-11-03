"""Read-only, exact-text word readings; never a permission to reveal quiz hints.

The sidecar is built from public teaching text. No learner database, question
snapshot, answer, network service or imported ``safe`` flag is consulted here.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import closing
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3

from .pitch_accent import morae
from .content_pack import load_namespace

FORMAT = "kanamemory-sentence-annotations"
VERSION = 1
KANJI = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f々〆]")
KANJI_RUN = re.compile(KANJI.pattern + "+")
KANA = re.compile(r"[ぁ-ゖァ-ヶー]+\Z")
ANNOTATION_SOURCES = load_namespace("presentation_metadata", {}).get("annotation_sources", ())


def _database_path():
    return Path(__file__).with_name("resources") / "sentence_annotations.sqlite3"


def _plain(text, status="unavailable"):
    return {"text": text, "segments": [], "status": status, "version": VERSION,
            "unresolved": [{"start": m.start(), "end": m.end(), "surface": m.group()}
                           for m in KANJI_RUN.finditer(text)]}


def validated_accent_phrases(segment):
    """Return a sourced complete sequence, never a made-up whole-word number."""
    if not isinstance(segment, dict):
        return []
    reading = segment.get("reading")
    phrases = segment.get("accent_phrases")
    if (segment.get("accent_scope") != "phrase_sequence"
            or segment.get("accent_number") is not None
            or segment.get("accent_surface") != segment.get("surface")
            or segment.get("accent_reading") != reading
            or segment.get("accent_source_id") != "curated-accent-reference"
            or not isinstance(reading, str) or not KANA.fullmatch(reading)
            or not isinstance(phrases, list) or not 2 <= len(phrases) <= 16):
        return []
    output = []
    for phrase in phrases:
        if not isinstance(phrase, dict):
            return []
        part, number = phrase.get("reading"), phrase.get("accent_number")
        if (not isinstance(part, str) or not KANA.fullmatch(part)
                or type(number) is not int or not 0 <= number <= len(morae(part))):
            return []
        output.append({"reading": part, "accent_number": number})
    return output if "".join(p["reading"] for p in output) == reading else []


def _validated(text, record):
    """Fail closed for a stale text, broken anchor, fake number or provenance."""
    if not isinstance(record, dict) or record.get("text") != text or record.get("version") != VERSION:
        return None
    segments = record.get("segments")
    if not isinstance(segments, list) or len(segments) > 2048:
        return None
    result, offset = [], 0
    source_ids = {source["id"] for source in ANNOTATION_SOURCES}
    for item in segments:
        if not isinstance(item, dict):
            return None
        start, end, surface = item.get("start"), item.get("end"), item.get("surface")
        if (type(start) is not int or type(end) is not int or not offset <= start < end <= len(text)
                or surface != text[start:end]):
            return None
        offset = end
        reading, number = item.get("reading"), item.get("accent_number")
        if reading is not None and (not isinstance(reading, str) or not KANA.fullmatch(reading)
                or not morae(reading) or item.get("reading_source_id") not in source_ids):
            return None
        if number is not None and (reading is None or type(number) is not int
                or not 0 <= number <= len(morae(reading)) or item.get("accent_source_id") not in source_ids
                or item.get("accent_surface") != surface or item.get("accent_reading") != reading
                or item.get("accent_scope") not in {"word", "inflected_unit"}):
            return None
        if item.get("accent_phrases") and not validated_accent_phrases(item):
            return None
        if item.get("accent_scope") == "phrase_sequence" and not validated_accent_phrases(item):
            return None
        result.append(deepcopy(item))
    excluded = record.get("excluded", [])
    if not isinstance(excluded, list) or len(excluded) > 2048:
        return None
    excluded_positions = set()
    for entry in excluded:
        if not isinstance(entry, dict):
            return None
        start, end = entry.get("start"), entry.get("end")
        if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text)
                or entry.get("surface") != text[start:end]
                or entry.get("category") not in {"non_japanese", "historical"}
                or any(s["start"] < end and s["end"] > start for s in result)):
            return None
        excluded_positions.update(range(start, end))
    out = {"text": text, "segments": result, "version": VERSION,
           "status": record.get("status", "precomputed")}
    if excluded:
        out["excluded"] = deepcopy(excluded)
    covered = {i for s in result if s.get("reading") is not None
               and (s.get("accent_number") is not None or validated_accent_phrases(s))
               for i in range(s["start"], s["end"])}
    out["unresolved"] = [{"start": m.start(), "end": m.end(), "surface": m.group()}
                         for m in KANJI_RUN.finditer(text)
                         if any(i not in covered and i not in excluded_positions for i in range(m.start(), m.end()))]
    return out


@lru_cache(maxsize=512)
def _lookup(text, path, stamp):
    # Stamp keeps desktop rebuilds from reusing stale results. Android's bundled
    # sidecar is immutable; each query closes its read-only SQLite connection.
    try:
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA user_version").fetchone()[0] != VERSION:
                return _plain(text)
            identity = sha256(text.encode("utf-8")).hexdigest()
            row = db.execute("SELECT record FROM sentence WHERE fingerprint=? AND text=?", (identity, text)).fetchone()
            if row:
                record = json.loads(row[0])
                return _validated(text, record) or _plain(text)
            # For newly added text, annotate only whole multi-kanji runs having
            # one unambiguous exact dictionary reading and accent. Never take
            # a prefix of an unfamiliar compound or infer an inflected reading.
            segments = []
            for match in KANJI_RUN.finditer(text):
                if len(match.group()) < 2:
                    continue
                row = db.execute("SELECT record FROM fallback WHERE surface=?", (match.group(),)).fetchone()
                if row:
                    item = json.loads(row[0])
                    item.update(start=match.start(), end=match.end(), surface=match.group())
                    segments.append(item)
            return _validated(text, {"text": text, "version": VERSION, "segments": segments,
                                      "status": "fallback"}) or _plain(text)
    except (OSError, ValueError, TypeError, sqlite3.Error):
        return _plain(text)


def sentence_annotation_plan(text):
    if not isinstance(text, str):
        return _plain("")
    if len(text) > 10000 or not KANJI.search(text):
        return _plain(text, "plain")
    path = _database_path().resolve()
    try:
        stat = path.stat()
    except OSError:
        return _plain(text)
    return deepcopy(_lookup(text, str(path), (stat.st_mtime_ns, stat.st_size)))


def annotation_sources(text):
    plan = sentence_annotation_plan(text)
    used = {s.get(key) for s in plan["segments"] for key in ("reading_source_id", "accent_source_id")}
    sources = [deepcopy(source) for source in ANNOTATION_SOURCES if source["id"] in used]
    seen = set()
    for segment in plan["segments"]:
        additional = segment.get("source_urls", [])
        urls = [segment.get("source_url", ""), *(additional if isinstance(additional, list) else [])]
        for url in urls:
            if isinstance(url, str) and url.startswith("https://") and url not in seen:
                sources.append({"id": "curated-reading-reference", "title": segment["surface"] + "：标注依据",
                                "url": url, "note": segment.get("review_note", "")})
                seen.add(url)
    return sources


def annotation_pending(text):
    """Secondary-detail status comes from actual missing values, not guesses.

    A verified reading never implies a verified accent. Zero is a real accent,
    not a missing value. These details are never inserted into quiz prompts.
    """
    plan = sentence_annotation_plan(text)
    pending, covered = [], set()
    for segment in plan["segments"]:
        covered.update(range(segment["start"], segment["end"]))
        reading_missing = segment.get("reading") is None
        accent_missing = segment.get("accent_number") is None and not validated_accent_phrases(segment)
        if not reading_missing and not accent_missing:
            continue
        reason = segment.get("review_reason")
        if not isinstance(reason, str) or not reason.strip():
            reason = ("原句尚不足以唯一确认读法；调号也待核。" if reading_missing else
                      "尚未核实适用于当前完整词形和读法的调号。")
        pending.append({"start": segment["start"], "end": segment["end"],
                        "surface": segment["surface"], "reading_pending": reading_missing,
                        "accent_pending": accent_missing, "reason": reason})
    for span in plan.get("unresolved", []):
        if any(i not in covered for i in range(span["start"], span["end"])):
            pending.append({**span, "reading_pending": True, "accent_pending": True,
                            "reason": "当前内容尚无可核实的读法和调号。"})
    return sorted(pending, key=lambda row: (row["start"], row["end"]))
