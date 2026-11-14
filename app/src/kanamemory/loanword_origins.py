"""Source language and source word of katakana loanword lessons (design 14 section 7.2, design 11 section 19).

Pure rules: no Toga, learner database, clock, platform or app import. The optional teaching namespace
``loanword_origins`` (version 1) is {"schema_version": 1, "items": {word lesson content id: entry}}:
    {"title": ..., "reading": ...,          binding: equal to the lesson's title and reading, else stale
     "kind": direct | multiple | clipping | wasei | hybrid | exempt,
     "sources": [{"language": code, "word": source spelling, "part": katakana part (hybrid only)}],
     "full_form": unclipped kana form (clipping: required; wasei: only for a clipped coinage),
     "reason": mimetic | sino_japanese | native (exempt only),
     "note_zh": optional Chinese note, at most 120 characters,
     "evidence": {"source_ids": [1..8 ids]},
     "review": {"status": candidate | reviewed, "by": optional}}

Rules
1. A word lesson NEEDS an entry when at least half of its title's characters are katakana (ー counts;
   spaces and 〜~・（）() are not counted): needs_origin. A source entry or an exempt one satisfies it.
2. validate_entry / validate_namespace check every field rule above and raise LoanwordOriginError naming
   the lesson and the rule. The pack build runs validate_namespace (content_pack); the App never does:
   at run time a malformed, stale or missing entry is simply not shown (origin_for returns None).
3. origin_line gives the one line of the learner pages; an unreviewed entry ends with （待核实）, an
   exempt one shows nothing. note_zh is returned separately (origin_note) so a page can place it.
4. Display only after teaching or after an answer, never before or while a question is answered:
   a recall question asks for the katakana word, and its source would give the answer away.
5. coverage lists the lessons that need an entry and lack a valid bound one, and entries without a
   lesson, for the content test, the pack check and the library tools.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import re
from types import MappingProxyType

NAMESPACE = "loanword_origins"
SCHEMA_VERSION = 1

# ISO 639-1 codes the content may use (and ain, which has no two-letter code) -> Chinese name.
LANGUAGES = MappingProxyType({
    "en": "英语", "pt": "葡萄牙语", "de": "德语", "nl": "荷兰语", "fr": "法语", "it": "意大利语",
    "es": "西班牙语", "ru": "俄语", "la": "拉丁语", "el": "希腊语", "ko": "朝鲜语", "zh": "汉语",
    "sa": "梵语", "ar": "阿拉伯语", "ain": "阿伊努语",
})
# JMdict's ISO 639-2 (bibliographic) codes and Wiktionary's three-letter codes -> LANGUAGES codes.
THREE_LETTER_CODES = MappingProxyType({
    "eng": "en", "por": "pt", "ger": "de", "deu": "de", "dut": "nl", "nld": "nl", "fre": "fr", "fra": "fr",
    "ita": "it", "spa": "es", "rus": "ru", "lat": "la", "gre": "el", "ell": "el", "kor": "ko", "chi": "zh",
    "zho": "zh", "san": "sa", "ara": "ar", "ain": "ain",
})
KINDS = ("direct", "multiple", "clipping", "wasei", "hybrid", "exempt")
EXEMPT_REASONS = ("mimetic", "sino_japanese", "native")
REVIEW_STATUSES = ("candidate", "reviewed")
ENTRY_FIELDS = frozenset({"title", "reading", "kind", "sources", "full_form", "reason", "note_zh", "evidence",
                          "review"})
SOURCE_FIELDS = frozenset({"language", "word", "part"})
CANDIDATE_SUFFIX = "（待核实）"
MAX_NOTE_CHARS = 120
MAX_WORD_CHARS = 80
MAX_SOURCES = 8
MAX_EVIDENCE_IDS = 8
_MAX_ID_CHARS = 1024
_MAX_TEXT_CHARS = 200
_KANA = re.compile(r"[぀-ヿㇰ-ㇿｦ-ﾟ]")
_FORBIDDEN_WORD = re.compile(r"[\[\]#|]")
_FULL_FORM = re.compile(r"[ぁ-ゖァ-ヺー・]{1,60}\Z")
_NOT_COUNTED = frozenset("〜~・（）()")


class LoanwordOriginError(ValueError):
    """A loanword_origins entry or namespace breaks the format."""


def needs_origin(title):
    """True when at least half of the title's counted characters are katakana (ー counts)."""
    counted = [c for c in str(title or "") if not c.isspace() and c not in _NOT_COUNTED]
    katakana = sum(1 for c in counted if "ァ" <= c <= "ヺ" or "ー" <= c <= "ヿ")
    return bool(counted) and 2 * katakana >= len(counted)


def language_code(raw):
    """The LANGUAGES code of a stored language tag (en, en-US, eng, por, ...), else None."""
    if not isinstance(raw, str):
        return None
    base = raw.strip().lower().replace("_", "-").split("-")[0]
    code = THREE_LETTER_CODES.get(base, base)
    return code if code in LANGUAGES else None


def language_name(raw):
    """Chinese name of a stored language tag, else None (unknown or not listed)."""
    code = language_code(raw)
    return LANGUAGES[code] if code else None


def _text(value, limit=_MAX_TEXT_CHARS):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip() and len(value) <= limit


def _source_problem(source, kind, title):
    if not isinstance(source, Mapping) or set(source) - SOURCE_FIELDS:
        return "a source must be {language, word[, part]}"
    if source.get("language") not in LANGUAGES:
        return "unknown source language " + repr(source.get("language"))[:40]
    word = source.get("word")
    if (not _text(word, MAX_WORD_CHARS) or _KANA.search(word) or _FORBIDDEN_WORD.search(word)):
        return "source word must be 1-80 characters, without kana and without [ ] # |"
    if kind == "hybrid":
        part = source.get("part")
        if not _text(part) or part not in title:
            return "a hybrid source needs part, a substring of the title"
    elif "part" in source:
        return "part is only for hybrid entries"
    return None


def _kind_problem(entry, kind, sources):
    if kind == "exempt":
        if sources:
            return "an exempt entry has no sources"
        if entry.get("reason") not in EXEMPT_REASONS:
            return "an exempt entry needs reason: " + " / ".join(EXEMPT_REASONS)
    elif "reason" in entry:
        return "reason is only for exempt entries"
    elif not 1 <= len(sources) <= MAX_SOURCES:
        return f"needs 1-{MAX_SOURCES} sources"
    if kind == "direct" and len(sources) != 1:
        return "a direct entry has exactly one source"
    if kind == "multiple" and (len(sources) < 2 or len({s["language"] for s in sources}) < 2):
        return "a multiple entry needs two or more sources in at least two languages"
    if kind == "clipping" and "full_form" not in entry:
        return "a clipping needs full_form"
    if kind not in ("clipping", "wasei") and "full_form" in entry:
        return "full_form is only for clipping and wasei entries"
    if "full_form" in entry and not (isinstance(entry["full_form"], str) and _FULL_FORM.fullmatch(entry["full_form"])):
        return "full_form must be the unclipped form in kana"
    return None


def _entry_problem(entry):
    if not isinstance(entry, Mapping):
        return "entry is not a mapping"
    unknown = set(entry) - ENTRY_FIELDS
    if unknown:
        return "unknown fields " + ", ".join(sorted(map(str, unknown)))[:80]
    if not _text(entry.get("title")) or not _text(entry.get("reading")):
        return "title and reading (the binding) are required"
    kind = entry.get("kind")
    if kind not in KINDS:
        return "kind must be one of " + " / ".join(KINDS)
    sources = entry.get("sources", [])
    if not isinstance(sources, list):
        return "sources must be a list"
    for source in sources:
        problem = _source_problem(source, kind, entry["title"])
        if problem:
            return problem
    problem = _kind_problem(entry, kind, sources)
    if problem:
        return problem
    if "note_zh" in entry and not _text(entry["note_zh"], MAX_NOTE_CHARS):
        return f"note_zh must be 1-{MAX_NOTE_CHARS} characters"
    evidence = entry.get("evidence")
    ids = evidence.get("source_ids") if isinstance(evidence, Mapping) and set(evidence) == {"source_ids"} else None
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_EVIDENCE_IDS
            or any(not _text(value) for value in ids) or len(set(ids)) != len(ids)):
        return f"evidence must be {{source_ids: 1-{MAX_EVIDENCE_IDS} distinct ids}}"
    review = entry.get("review")
    if (not isinstance(review, Mapping) or set(review) - {"status", "by"}
            or review.get("status") not in REVIEW_STATUSES or ("by" in review and not _text(review["by"]))):
        return "review must be {status: candidate | reviewed[, by]}"
    return None


def entry_problem(content_id, entry):
    """Why one entry breaks the format ("<content id>: <rule>"), or None; never raises."""
    if not _text(content_id, _MAX_ID_CHARS):
        return "an entry key must be a lesson content id"
    problem = _entry_problem(entry)
    return f"{content_id}: {problem}" if problem else None


def validate_entry(content_id, entry):
    """Raise LoanwordOriginError when one entry breaks the format; return the entry."""
    problem = entry_problem(content_id, entry)
    if problem:
        raise LoanwordOriginError(problem)
    return entry


def validate_namespace(value):
    """The whole namespace (v1): {"schema_version": 1, "items": {content id: entry}}; return it."""
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "items"}
            or type(value["schema_version"]) is not int or value["schema_version"] != SCHEMA_VERSION
            or not isinstance(value["items"], Mapping)):
        raise LoanwordOriginError("Unsupported loanword origins namespace")
    for content_id, entry in value["items"].items():
        validate_entry(content_id, entry)
    return value


def namespace_items(value):
    """The items mapping of a namespace value; {} for a missing or malformed namespace."""
    if (isinstance(value, Mapping) and value.get("schema_version") == SCHEMA_VERSION
            and isinstance(value.get("items"), Mapping)):
        return value["items"]
    return {}


@lru_cache(maxsize=1)
def _installed_items():
    from .content_pack import ContentPackError, load_namespace
    try:
        value = load_namespace(NAMESPACE, {})
    except ContentPackError:
        return MappingProxyType({})  # an unreadable namespace shows no origin; the pack check reports it
    return MappingProxyType(dict(namespace_items(value)))


def installed_items():
    """Entries of the installed packs and enabled libraries (read once per process, read-only)."""
    return _installed_items()


def clear_cache():
    """Forget the installed entries (tests and a pack change in the same process)."""
    _installed_items.cache_clear()


def origin_for(item, items=None):
    """The entry bound to a word lesson (same id, title and reading), else None; never raises."""
    if not isinstance(item, Mapping) or item.get("family") != "word":
        return None
    content_id = item.get("id")
    table = installed_items() if items is None else items
    entry = table.get(content_id) if isinstance(content_id, str) and isinstance(table, Mapping) else None
    if entry is None or entry_problem(content_id, entry):
        return None
    if entry["title"] != item.get("title") or entry["reading"] != item.get("reading"):
        return None  # stale: the lesson changed after the entry was written
    return entry


def _source_text(source):
    return f"{LANGUAGES[source['language']]} {source['word']}"


def _line(entry):
    kind, sources = entry["kind"], entry.get("sources", [])
    if kind in ("direct", "multiple"):
        return "来源：" + "／".join(map(_source_text, sources))
    if kind == "clipping":
        return "来源：" + "／".join(map(_source_text, sources)) + f" 的缩略（{entry['full_form']}）"
    if kind == "hybrid":
        return "混种词：" + "；".join(f"{source['part']}←{_source_text(source)}" for source in sources)
    languages = list(dict.fromkeys(source["language"] for source in sources))  # wasei
    if len(languages) == 1:
        name = LANGUAGES[languages[0]]
        words = "＋".join(source["word"] for source in sources)
        head, tail = f"和制{name}：{words}", f"（{name}里不这样说）"
    else:
        head = "和制外来语：" + "＋".join(map(_source_text, sources))
        tail = "（原语言里不这样说）"
    clipped = f"，{entry['full_form']} 的缩略" if "full_form" in entry else ""
    return head + clipped + tail


def origin_line(entry):
    """The one line the learner sees; （待核实） ends a candidate; None for exempt or malformed."""
    if _entry_problem(entry) or entry["kind"] == "exempt":
        return None
    line = _line(entry)
    return line + CANDIDATE_SUFFIX if entry["review"]["status"] == "candidate" else line


def origin_note(entry):
    """note_zh of a shown entry, else None (exempt and malformed entries show nothing)."""
    return entry.get("note_zh") if origin_line(entry) is not None else None


def origin_texts(item, items=None):
    """(line, note or None) for a word lesson's page, or () when nothing is shown."""
    entry = origin_for(item, items)
    line = origin_line(entry) if entry is not None else None
    return ((line, origin_note(entry)),) if line else ()


def reviewed_dictionary_texts(form, reading, lesson_view, items=None):
    """(line, note) of every reviewed source entry bound to a course lesson written form / read reading.

    For the dictionary detail page, which knows a word but no lesson: the entries' own binding finds
    the lessons, lesson_view(content id) returns the catalogue lesson (or None) without a query, and
    an entry counts only while origin_for still binds it. Candidates and exempt entries are left out.
    """
    table = installed_items() if items is None else items
    found = []
    for content_id, entry in (table.items() if isinstance(table, Mapping) else ()):
        if not isinstance(entry, Mapping) or entry.get("title") != form or entry.get("reading") != reading:
            continue
        bound = origin_for(lesson_view(content_id), {content_id: entry})
        line = origin_line(bound) if bound is not None and bound["review"]["status"] == "reviewed" else None
        if line and (line, bound.get("note_zh")) not in found:
            found.append((line, bound.get("note_zh")))
    return tuple(found)


def coverage(items, namespace):
    """(content id, problem) of lessons needing an entry without a valid bound one, and of stray entries.

    items: the word lessons to check (other families are ignored); namespace: the namespace value.
    Problems: "missing", "stale" (title or reading changed), the format rule broken, "no such lesson".
    """
    table = namespace_items(namespace)
    lessons = {item["id"]: item for item in items
               if isinstance(item, Mapping) and item.get("family") == "word" and isinstance(item.get("id"), str)}
    problems = []
    for content_id, item in lessons.items():
        entry = table.get(content_id)
        if entry is None:
            if needs_origin(item.get("title")):
                problems.append((content_id, "missing"))
            continue
        problem = entry_problem(content_id, entry)
        if problem:
            problems.append((content_id, problem.removeprefix(f"{content_id}: ")))
        elif origin_for(item, {content_id: entry}) is None:
            problems.append((content_id, "stale"))
    problems.extend((str(content_id), "no such lesson") for content_id in table if content_id not in lessons)
    return tuple(sorted(problems))


def candidate_count(namespace):
    """How many entries are unreviewed candidates (a released pack should hold none)."""
    return sum(1 for entry in namespace_items(namespace).values()
               if isinstance(entry, Mapping) and isinstance(entry.get("review"), Mapping)
               and entry["review"].get("status") == "candidate")
