"""Read-only teaching data. A content pack never contains executable code.

The pack is independent of learner databases: loading content neither migrates
nor writes saved cards, review history, or unfinished queues.

Pack version 2 stores every namespace as checksummed shards listed in a
manifest. Startup validates the manifest checksum and the stored shard sizes
without decoding payloads; each shard's sha256 is verified when it is first
read. Large item tables (varied_practice, context_choice) are split per
content id, and load_namespace returns a read-only mapping that decodes one
item record on first access. Version 1 packs remain readable unchanged.

Content libraries (design 08, phase 1): the service mirrors the registry
``content_libraries_v1`` to ``<library root>/content_libraries.json``. At
import this module reads that mirror, checks each enabled installed library
against its manifest and layers the libraries' teaching namespaces over the
core pack in priority order (core first, section 6.2). An installed core
library pack may replace the bundled teaching database and lexicon paths.
Changes take effect after a restart; module constants stay process constants.

Phase 3/4 (section 6.1): effective_catalogue() builds the merge once per
process with content_library_merge: ownership per content id, accepted
overrides (registry overrides_accepted), references (required/optional),
retires and aliases. Extension catalogue items get their depends_on resolved
(missing optional ids dropped; missing required ids dropped and recorded as
blocked). Any failure while reading or resolving extension libraries falls
back to the core library alone and flags the culprit as damaged. With only
the core library, load_namespace returns exactly the core value as before.

Section 6.5: an extension library's teaching documents are read on demand.
LibraryLayer.open_namespace returns the payload of content_library_index (the
question templates of catalogue rows and the entries of ``items`` tables are
decoded when first used); the index of each document is written beside the
library at install, or at the first start that finds none. Any trouble with
an index falls back to decoding the whole document, as before.

Section 16.5: a library whose items the learning catalogue rejected
(learning_content cross-namespace duplicate identities) is reported damaged
by library_status() with the reason, for the effective catalogue it was
rejected from (record_catalogue_rejection), so startup_sync and the
availability layer agree with the learning catalogue.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from contextlib import closing, contextmanager
from functools import lru_cache
import json
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading

from . import content_caches
from . import content_library_index as library_index
from . import content_library_merge as library_merge
from . import content_library_rules as library_rules
from . import loanword_origins
from .answer_policy import overlay as answer_overlay

FORMAT = "kanamemory-teaching-content"
VERSION = 1
SHARDED_VERSION = 2
NAMESPACE_FORMAT = "kanamemory-teaching-namespace"
_TAG = "__km_content_type__"
_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024  # One v1 namespace, or one v2 shard; same bound as library packs.
_MAX_SHARDED_BYTES = 256 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
WHOLE, SHARDED = "whole", "sharded"
WHOLE_KEY, TOP_PREFIX, ITEM_PREFIX = "", "top:", "item:"
PERSONAL_REQUIRED_NAMESPACES = frozenset({
    "learning_content", "learning_questions", "grammar_curriculum", "exam_grammar_curriculum",
    "usage_grammar_curriculum", "pronunciation_curriculum", "vocabulary_catalog", "vocabulary_lessons",
    "example_catalogue", "example_expansion_grammar_a", "example_expansion_grammar_a_tail",
    "example_expansion_grammar_b", "example_expansion_pronunciation", "example_expansion_words",
    "example_expansion_signatures", "structured_lessons_grammar_a", "structured_lessons_grammar_b",
    "structured_lessons_pronunciation", "structured_lessons_words", "lesson_diagrams_grammar_a",
    "lesson_diagrams_grammar_b", "lesson_diagrams_words_sounds", "study_path_catalog",
    "course_prerequisites", "question_contexts", "presentation_metadata",
})


class ContentPackError(ValueError):
    """The supplied data is not a supported teaching-content pack."""


def encode_value(value):
    """Encode inert values, preserving tuples/sets used by public legacy APIs."""
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is dict:
        if _TAG in value or any(type(key) is not str for key in value):
            raise ContentPackError("Content mappings need ordinary string keys")
        return {key: encode_value(item) for key, item in value.items()}
    if type(value) is list:
        return [encode_value(item) for item in value]
    if type(value) in (tuple, set, frozenset):
        items = [encode_value(item) for item in value]
        if type(value) in (set, frozenset):
            items.sort(key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
        return {_TAG: type(value).__name__, "items": items}
    raise ContentPackError("Unsupported content value type: " + type(value).__name__)


def decode_value(value):
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return [decode_value(item) for item in value]
    if type(value) is dict:
        if _TAG not in value:
            return {key: decode_value(item) for key, item in value.items()}
        if set(value) != {_TAG, "items"} or not isinstance(value["items"], list):
            raise ContentPackError("Invalid typed content value")
        kind = value[_TAG]
        constructor = {"tuple": tuple, "set": set, "frozenset": frozenset}.get(kind) if isinstance(kind, str) else None
        if constructor is None:
            raise ContentPackError("Unknown content value type")
        try:
            return constructor(decode_value(item) for item in value["items"])
        except (TypeError, ValueError) as error:
            raise ContentPackError("Invalid content collection") from error
    raise ContentPackError("Unsupported JSON content value")


def namespace_document(name, value):
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ContentPackError("Invalid namespace name")
    return {"format": NAMESPACE_FORMAT, "version": VERSION,
            "name": name, "payload": encode_value(value)}


# Optional teaching namespace with each raw template's dependency set (design
# 11 section 5, D1). It is looked up by content id and the raw template's
# sha256 (learning_questions.template_fingerprint), so templates and stored
# question snapshots never change. It is never required: a missing namespace
# or item leaves every template a fallback question (D3/D7).
QUESTION_DEPENDENCIES = "question_dependencies"
QUESTION_READINGS = "question_readings"  # Optional: kana for the words a question shows (11 section 7).
DEPENDENCY_ID_FIELDS = ("words", "grammar", "kana")
DEPENDENCY_FIELDS = (*DEPENDENCY_ID_FIELDS, "oov")
# Design 11 section 18: of a template's required words, those it shows only in its stem (never in an answer,
# an accepted answer or an option) and glosses there. Written by the content build; a subset of its base list.
DEPENDENCY_STEM_FIELDS = {"stem_words": "words", "stem_oov": "oov"}
_MAX_DEPENDENCY_ENTRIES = 40  # Same bound as reviewed practice background_ids.
_MAX_DEPENDENCY_ID_CHARS = 1024
_MAX_DEPENDENCY_SURFACE_CHARS = 64
_KIND_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def validate_question_dependency_requirements(value, *, target_id=None):
    """One template's needs: {"words"/"grammar"/"kana": [ids], "oov": [surfaces]}.

    Every list holds at most 40 distinct bounded strings; a template never
    requires its own target item. Optional "stem_words" / "stem_oov" name the
    members of "words" / "oov" that the template shows only in its stem and
    glosses there (DEPENDENCY_STEM_FIELDS); each must be a subset of its base
    list. They add no requirement. Returns the value unchanged.
    """
    if (not isinstance(value, Mapping)
            or set(value) - set(DEPENDENCY_FIELDS) - set(DEPENDENCY_STEM_FIELDS)):
        raise ContentPackError("Invalid question dependency requirements")
    for field, values in value.items():
        base = DEPENDENCY_STEM_FIELDS.get(field, field)
        limit = _MAX_DEPENDENCY_SURFACE_CHARS if base == "oov" else _MAX_DEPENDENCY_ID_CHARS
        if (not isinstance(values, list) or len(values) > _MAX_DEPENDENCY_ENTRIES
                or any(not isinstance(v, str) or not v.strip() or len(v) > limit for v in values)
                or len(set(values)) != len(values)
                or (base != "oov" and target_id is not None and target_id in values)):
            raise ContentPackError("Invalid question dependency list: " + field)
        if base != field:
            listed = value.get(base)
            if not isinstance(listed, list) or any(v not in listed for v in values):
                raise ContentPackError("Invalid question dependency list: " + field)
    return value


def validate_question_dependency_entry(content_id, entry):
    """One item's declared templates: {kind: {template_sha256: requirements}}."""
    if not isinstance(content_id, str) or not content_id or len(content_id) > _MAX_DEPENDENCY_ID_CHARS:
        raise ContentPackError("Invalid question dependency item id")
    if not isinstance(entry, Mapping):
        raise ContentPackError("Invalid question dependency entry")
    for kind, templates in entry.items():
        if not isinstance(kind, str) or not _KIND_NAME.fullmatch(kind) or not isinstance(templates, Mapping):
            raise ContentPackError("Invalid question dependency kind")
        for sha, requirements in templates.items():
            if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                raise ContentPackError("Invalid question dependency template fingerprint")
            validate_question_dependency_requirements(requirements, target_id=content_id)
    return entry


def validate_question_dependencies(value):
    """The whole optional namespace (v1): {"schema_version": 1, "items": {id: entry}}.

    Missing items are legal (their templates are fallback questions). The
    namespace is not in PERSONAL_REQUIRED_NAMESPACES; whether a referenced id
    exists in the catalogue is a content-build report, not a pack check.
    """
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "items"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["items"], Mapping)):
        raise ContentPackError("Unsupported question dependencies namespace")
    for content_id, entry in value["items"].items():
        validate_question_dependency_entry(content_id, entry)
    return value


# Optional teaching namespace with the kana reading of every kanji word a raw
# template shows before answering (design 11 section 7): data only, keyed like
# question_dependencies by content id, kind and raw template sha256. Which
# readings are displayed is decided at runtime from what the learner knows.
READING_ENTRY_FIELDS = ("ids", "fields", "options")
_MAX_READING_IDS = 60
_MAX_READING_SPANS = 80  # Per text (one field or one option).
_MAX_READING_FIELDS = 16
_MAX_READING_OPTIONS = 60
_MAX_READING_OPTION_CHARS = 400
_MAX_READING_OFFSET = 100000
_READING_KANA = re.compile(r"[ぁ-ゖー]{1,40}\Z")  # Hiragana and the long-vowel mark.


def validate_question_reading_spans(spans, id_count):
    """One text's spans [[start, end, reading, ref], ...]: sorted, not overlapping.

    start/end are code-point offsets into the raw string (only a reader that
    has the template can check them against the text); reading is 1-40
    hiragana; ref indexes the entry's "ids" or is -1. Returns the value.
    """
    if not isinstance(spans, list) or not 0 < len(spans) <= _MAX_READING_SPANS:
        raise ContentPackError("Invalid question reading spans")
    last = 0
    for span in spans:
        if (not isinstance(span, list) or len(span) != 4
                or type(span[0]) is not int or type(span[1]) is not int
                or not last <= span[0] < span[1] <= _MAX_READING_OFFSET
                or not isinstance(span[2], str) or not _READING_KANA.fullmatch(span[2])
                or type(span[3]) is not int or not -1 <= span[3] < id_count):
            raise ContentPackError("Invalid question reading span")
        last = span[1]
    return spans


def validate_question_reading_entry(entry):
    """One template: {"ids": [lesson ids], "fields": {name: spans}, "options": {text: spans}}.

    Empty lists and maps are omitted, so an entry holds at least one span;
    "ids" (at most 60 distinct bounded strings) is needed only for refs >= 0.
    """
    if (not isinstance(entry, Mapping) or set(entry) - set(READING_ENTRY_FIELDS)
            or not ("fields" in entry or "options" in entry)):
        raise ContentPackError("Invalid question reading entry")
    ids = entry.get("ids", [])
    if (not isinstance(ids, list) or len(ids) > _MAX_READING_IDS or ("ids" in entry and not ids)
            or any(not isinstance(v, str) or not v.strip() or len(v) > _MAX_DEPENDENCY_ID_CHARS for v in ids)
            or len(set(ids)) != len(ids)):
        raise ContentPackError("Invalid question reading ids")
    for group, limit in (("fields", _MAX_READING_FIELDS), ("options", _MAX_READING_OPTIONS)):
        if group not in entry:
            continue
        table = entry[group]
        if not isinstance(table, Mapping) or not 0 < len(table) <= limit:
            raise ContentPackError("Invalid question reading " + group)
        for key, spans in table.items():
            if (not isinstance(key, str) or not key
                    or (group == "fields" and not _KIND_NAME.fullmatch(key))
                    or (group == "options" and len(key) > _MAX_READING_OPTION_CHARS)):
                raise ContentPackError("Invalid question reading " + group)
            validate_question_reading_spans(spans, len(ids))
    return entry


def validate_question_reading_item(content_id, entry):
    """One item's templates: {kind: {template_sha256: reading entry}}."""
    if not isinstance(content_id, str) or not content_id or len(content_id) > _MAX_DEPENDENCY_ID_CHARS:
        raise ContentPackError("Invalid question reading item id")
    if not isinstance(entry, Mapping):
        raise ContentPackError("Invalid question reading item")
    for kind, templates in entry.items():
        if not isinstance(kind, str) or not _KIND_NAME.fullmatch(kind) or not isinstance(templates, Mapping):
            raise ContentPackError("Invalid question reading kind")
        for sha, reading_entry in templates.items():
            if not isinstance(sha, str) or not _SHA256.fullmatch(sha):
                raise ContentPackError("Invalid question reading template fingerprint")
            validate_question_reading_entry(reading_entry)
    return entry


def validate_question_readings(value):
    """The whole optional namespace (v1): {"schema_version": 1, "items": {id: item}}.

    Missing items and templates are legal (nothing is annotated). Never in
    PERSONAL_REQUIRED_NAMESPACES; whether a referenced id exists and whether a
    span matches its template are content-build checks, not pack checks.
    """
    if (not isinstance(value, Mapping) or set(value) != {"schema_version", "items"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or not isinstance(value["items"], Mapping)):
        raise ContentPackError("Unsupported question readings namespace")
    for content_id, entry in value["items"].items():
        validate_question_reading_item(content_id, entry)
    return value


# Optional teaching namespace with the source of every katakana loanword lesson (design 11 section 19);
# its rules live in loanword_origins. Checked by deep validation (builds) only: a v1 pack whose entries
# are malformed still opens, and the App then shows no origin.
LOANWORD_ORIGINS = loanword_origins.NAMESPACE


def _loanword_check(check):
    def checked(*args):
        try:
            return check(*args)
        except loanword_origins.LoanwordOriginError as error:
            raise ContentPackError("Invalid loanword origins: " + str(error)) from error
    return checked


# Optional teaching namespace that corrects the answer policy of installed templates (design 03, 08):
# keyed like question_readings; its rules live in answer_policy.overlay.
ANSWER_OVERLAY = answer_overlay.NAMESPACE


def _overlay_check(check):
    def checked(*args):
        try:
            return check(*args)
        except answer_overlay.OverlayError as error:
            raise ContentPackError("Invalid answer overlay: " + str(error)) from error
    return checked


# namespace -> (whole-value check, per-item check, name used in errors).
_SHARD_CHECKS = {
    QUESTION_DEPENDENCIES: (validate_question_dependencies, validate_question_dependency_entry,
                            "question dependencies"),
    QUESTION_READINGS: (validate_question_readings, validate_question_reading_item, "question readings"),
    ANSWER_OVERLAY: (_overlay_check(answer_overlay.validate_namespace), _overlay_check(answer_overlay.validate_item),
                     "answer overlay"),
    LOANWORD_ORIGINS: (_loanword_check(loanword_origins.validate_namespace),
                       _loanword_check(loanword_origins.validate_entry), "loanword origins"),
}
_DEEP_ONLY_CHECKS = frozenset({LOANWORD_ORIGINS})


def _validate_shard_value(name, key, value, *, deep=True):
    """Namespace-specific checks of one decoded shard (deep validation and builds)."""
    if name not in _SHARD_CHECKS or (name in _DEEP_ONLY_CHECKS and not deep):
        return
    whole, item, label = _SHARD_CHECKS[name]
    if key == WHOLE_KEY:
        whole(value)
    elif key == TOP_PREFIX + "schema_version":
        if type(value) is not int or value != 1:
            raise ContentPackError("Unsupported " + label + " namespace")
    elif key.startswith(ITEM_PREFIX):
        item(key[len(ITEM_PREFIX):], value)
    else:
        raise ContentPackError("Unsupported " + label + " namespace")


def _validate_shard_structure(name, layout, structure):
    if (name in _SHARD_CHECKS and layout == SHARDED
            and (set(structure["top"]) != {"schema_version", "items"} or structure["items_key"] != "items")):
        raise ContentPackError("Unsupported " + _SHARD_CHECKS[name][2] + " namespace")


def _resources():
    return Path(__file__).with_name("resources")


def default_library_root():
    """Where the service keeps installed libraries when no override is set.

    The learner data directory is only known once the Toga App exists, but
    teaching namespaces are loaded while modules import. This derives the
    same directory Toga's Paths would report (platformdirs on Windows, the
    files directory on Android); the service verifies the two agree at
    startup and reports a mismatch instead of guessing further.
    """
    try:
        if hasattr(sys, "getandroidapilevel"):
            home = os.environ.get("HOME")  # Chaquopy: Context.getFilesDir()
            return Path(home) / "data" / library_rules.LIBRARIES_DIRECTORY if home else None
        if os.name != "nt":
            return None
        from importlib.metadata import metadata
        import platformdirs
        info = metadata("kanamemory")
        name = info.get("Formal-Name") or info.get("Name") or "KanaMemory"
        author = info.get("Author") or "Unknown"
        return Path(platformdirs.user_data_path(appname=name, appauthor=author)) / "Data" / library_rules.LIBRARIES_DIRECTORY
    except Exception:
        return None


def library_root():
    """Directory holding installed libraries and the registry mirror, or None."""
    configured = os.environ.get("KANAMEMORY_LIBRARY_ROOT")
    if configured:
        return Path(configured)
    return default_library_root()


@lru_cache(maxsize=4)
def _mirror(path, modified_ns, size):
    del modified_ns, size
    try:
        return library_rules.validate_registry(json.loads(Path(path).read_text("utf-8")))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


_frozen = []  # [registry or None] once read; a process keeps one effective catalogue


def active_registry():
    """The validated registry mirror this process started with (None: core only).

    Read once per process (clear_cache forgets it): a registry written later
    takes effect after a restart, together with the module-level constants.
    """
    if _frozen:
        return _frozen[0]
    root = library_root()
    registry = None
    if root is not None:
        path = root / library_rules.MIRROR_FILE
        try:
            stat = path.stat()
            registry = _mirror(str(path), stat.st_mtime_ns, stat.st_size)
        except OSError:
            registry = None
    if registry is not None:
        # Section 5.7: newer bundled content wins over an older imported core pack before anything is loaded;
        # the service's startup_sync then saves the same change (bundled_core_replacement).
        replaced = bundled_core_replacement(registry, root)
        if replaced is not None:
            registry = replaced[0]
        # The bundled core entry stores the stamp's version (an older mirror: "bundled"); the service's
        # startup_sync saves the same, so the two agree without a restart.
        current = library_rules.with_bundled_version(registry, bundled_stamp())
        if current is not None:
            registry = current
    _frozen.append(registry)
    return registry


@lru_cache(maxsize=4)
def _teaching_sha256(path, modified_ns, size):
    del modified_ns, size
    try:
        with closing(_connect(path)) as connection:
            row = connection.execute("SELECT value FROM metadata WHERE key='content_sha256'").fetchone()
    except (OSError, sqlite3.Error):
        return None
    return row[0] if row and isinstance(row[0], str) else None


def bundled_teaching():
    """(folder, teaching database) of the bundled content whose stamp counts: the KANAMEMORY_TEACHING_PACK
    override when set (isolated builds and tests serve that database in place of the resources), else the
    runtime resources."""
    configured = os.environ.get("KANAMEMORY_TEACHING_PACK")
    if configured:
        return Path(configured).parent, Path(configured)
    return _resources(), _resources() / "teaching_content.sqlite3"


def bundled_stamp(resources=None):
    """The valid stamp of the bundled content (content_library_rules.bundled_stamp), or None.

    It counts only for the teaching database actually bundled beside it: a stale or malformed stamp, or none,
    is None, never an error.
    """
    if resources is not None:
        folder, teaching = Path(resources), Path(resources) / "teaching_content.sqlite3"
    else:
        folder, teaching = bundled_teaching()
    try:
        value = json.loads((folder / library_rules.BUNDLED_STAMP_FILE).read_text("utf-8"))
        stat = teaching.stat()
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    return library_rules.bundled_stamp(value, _teaching_sha256(str(teaching), stat.st_mtime_ns, stat.st_size))


def installed_version_scheme(entry, root):
    """version_scheme of an installed library's manifest, or None when it cannot be read."""
    try:
        manifest = json.loads((Path(root) / entry["path"] / library_rules.MANIFEST_FILE).read_text("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, TypeError):
        return None
    if not isinstance(manifest, dict) or manifest.get("library_version") != entry["library_version"]:
        return None
    return manifest.get("version_scheme")


def bundled_core_replacement(registry, root):
    """content_library_rules.core_reverted_to_bundled for this app's bundled stamp and the installed core pack."""
    entry = library_rules.registry_entry(registry, library_rules.CORE_LIBRARY_ID)
    if entry is None or entry["source"]["kind"] != "file" or root is None:
        return None
    stamp = bundled_stamp()
    if stamp is None:
        return None
    return library_rules.core_reverted_to_bundled(registry, stamp, installed_version_scheme(entry, root))


class LibraryLayer:
    """One installed library checked against its manifest at import."""

    def __init__(self, entry, directory):
        self.entry, self.directory = entry, Path(directory)
        self.library_id = entry["library_id"]
        self.manifest, self.reason = None, None
        self.teaching = {}
        self.teaching_pack = self.lexicon = None
        try:
            self._check()
        except (OSError, ValueError, UnicodeDecodeError, RecursionError) as error:
            self.manifest, self.teaching, self.teaching_pack, self.lexicon = None, {}, None, None
            self.reason = str(error)[:200] or type(error).__name__

    def _check(self):
        manifest_path = self.directory / library_rules.MANIFEST_FILE
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ValueError("Library manifest is missing")
        manifest = library_rules.validate_manifest(json.loads(manifest_path.read_text("utf-8")))
        if manifest["library_id"] != self.library_id or manifest["content_sha256"] != self.entry["content_sha256"]:
            raise ValueError("Installed library does not match its registry entry")
        for name, info in manifest["files"].items():
            path = self.directory / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size != info["bytes"]:
                raise ValueError("Library file is missing or changed: " + name)
        for path in manifest["layers"]["teaching"]:
            if path == library_rules.TEACHING_PACK_FILE:
                self.teaching_pack = self.directory / path
            else:
                name = path[len(library_rules.LAYER_PREFIX["teaching"]):-5]
                self.teaching[name] = (self.directory / path, manifest["files"][path]["sha256"], manifest["files"][path]["bytes"])
        if library_rules.LEXICON_FILE in manifest["files"]:
            self.lexicon = self.directory / library_rules.LEXICON_FILE
        self.manifest = manifest

    @property
    def damaged(self):
        return self.manifest is None

    def _verified_bytes(self, name):
        path, sha, size = self.teaching[name]
        raw = path.read_bytes()
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != sha:
            raise ContentPackError("Library teaching document checksum mismatch: " + name)
        return raw

    @staticmethod
    def _decoded(raw, name):
        try:
            document = json.loads(raw.decode("utf-8"))
            library_rules.validate_namespace_document(document, name)
            return decode_value(document["payload"])
        except (ValueError, TypeError, RecursionError, UnicodeDecodeError) as error:
            if isinstance(error, ContentPackError):
                raise
            raise ContentPackError("Invalid library teaching document: " + name) from error

    def read_namespace(self, name):
        """Decode one JSON teaching document after verifying its checksum."""
        return self._decoded(self._verified_bytes(name), name)

    def _index_path(self, name):
        return self.directory / INDEX_DIRECTORY / (name + ".json")

    def _stored_index(self, name):
        document, sha, size = self.teaching[name]
        path = self._index_path(name)
        try:
            if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
                return None
            # The index names the file's modification time: a document written again is read whole.
            return library_index.decode_index(path.read_bytes(), name=name, sha256=sha, size=size,
                                              modified_ns=document.stat().st_mtime_ns)
        except (OSError, ValueError):
            return None

    def _write_index(self, name, index):
        """Best effort: a library folder that cannot be written keeps working without a stored index."""
        path = self._index_path(name)
        temporary = path.with_name(path.name + ".new")
        try:
            path.parent.mkdir(exist_ok=True)
            if path.parent.is_symlink():
                return
            temporary.write_bytes(library_index.encode_index(index))
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink()
            except OSError:
                pass

    def build_index(self, name, *, validate=True):
        """Read and verify the whole document once and store its index.

        Returns (index, document bytes); the index is None for a document
        content_library_index does not index (it is then decoded whole).
        ``validate`` runs decode_value over every deferred part, so that a
        document is indexed only if read_namespace would accept it; the
        install skips it, having just decoded the same bytes in its precheck.
        """
        try:
            modified_ns = self.teaching[name][0].stat().st_mtime_ns  # before the read: a later write is noticed
        except OSError:
            modified_ns = None
        raw = self._verified_bytes(name)
        try:
            index = library_index.build_index(raw, name, validate=decode_value if validate else None,
                                              modified_ns=modified_ns)
        except library_index.LibraryIndexError:
            return None, raw
        if modified_ns is not None:
            self._write_index(name, index)
        return index, raw

    def open_namespace(self, name):
        """The namespace payload with its large parts read on demand (section 6.5).

        Equal to read_namespace(name). With a stored index only the index is
        read now, and each deferred part is checked against its sha256 when
        it is first read. Without one the whole document is read and verified
        as before, and its index is stored for the next start.
        """
        path = self.teaching[name][0]
        index, raw = self._stored_index(name), None
        if index is None:
            index, raw = self.build_index(name)
            if index is None:
                return self._decoded(raw, name)

        def reader(offset, length, digest, path=path):
            del digest  # checked by content_library_index against the bytes returned
            with open(path, "rb") as stream:
                stream.seek(offset)
                return stream.read(length)

        def damaged(error, index_path=self._index_path(name)):
            # A part failed its check. The read itself raises; without its index the next start reads the
            # whole document again and refuses a changed one, marking the library damaged as it always did.
            from . import diagnostic_events
            diagnostic_events.record_error("content_library", "read", error)
            try:
                index_path.unlink()
            except OSError:
                pass
        try:
            envelope, payload = library_index.resolve(index, reader, decode_value, damaged)
            library_rules.validate_namespace_document({**envelope, "payload": payload}, name)
            return payload
        except (ValueError, TypeError, RecursionError) as error:
            if isinstance(error, ContentPackError):
                raise
            # An index that does not resolve is not trusted: decode the document itself.
            return self._decoded(self._verified_bytes(name) if raw is None else raw, name)


INDEX_DIRECTORY = ".index"  # in an installed library's folder: derived data, deleted with the folder


@lru_cache(maxsize=1)
def _layers_for(root, registry_key):
    del registry_key
    registry = active_registry()
    if registry is None:
        return ()
    layers = []
    for entry in library_rules.effective_entries(registry):
        if entry["source"]["kind"] != "file":
            continue
        layers.append(LibraryLayer(entry, Path(root) / entry["path"]))
    return tuple(layers)


def library_layers():
    """Checked installed libraries in priority order (damaged ones included, flagged)."""
    registry = active_registry()
    if registry is None:
        return ()
    key = json.dumps(registry, sort_keys=True, ensure_ascii=False)
    return _layers_for(str(library_root()), key)


def library_status():
    """library_id -> {'state': 'ready'|'damaged', 'reason': str|None} for the service.

    A library that broke the effective-catalogue merge (section 6.1) is
    reported damaged; libraries left out only because of that fallback, or
    bystanders of a learning-catalogue rejection, are omitted (not loaded in
    this process; library_load_notes() says why) and never marked damaged.
    """
    layers = library_layers()
    if not layers:
        return {}
    catalogue = effective_catalogue()
    rejected = _catalogue_rejections.get(catalogue.token, {})
    bystanders = _catalogue_bystanders.get(catalogue.token, {})
    status = {}
    for layer in layers:
        if layer.damaged:
            status[layer.library_id] = {"state": "damaged", "reason": layer.reason}
        elif layer.library_id in catalogue.failed:
            status[layer.library_id] = {"state": "damaged", "reason": catalogue.failed[layer.library_id]}
        elif layer.library_id in rejected:
            status[layer.library_id] = {"state": "damaged", "reason": rejected[layer.library_id]}
        elif layer.library_id in bystanders:
            continue  # lost content to another library's rejection: library_load_notes()
        elif catalogue.fallback and layer.library_id != library_rules.CORE_LIBRARY_ID:
            continue
        else:
            status[layer.library_id] = {"state": "ready", "reason": None}
    return status


def _core_layer():
    return next((layer for layer in library_layers()
                 if layer.library_id == library_rules.CORE_LIBRARY_ID and not layer.damaged), None)


def pack_path() -> Path:
    """An explicit path supports isolated builds/tests without private content.

    Without the override, an installed core library pack that carries a
    teaching database replaces the bundled one; otherwise the resources copy.
    """
    configured = os.environ.get("KANAMEMORY_TEACHING_PACK")
    if configured:
        return Path(configured)
    core = _core_layer()
    if core is not None and core.teaching_pack is not None:
        return core.teaching_pack
    return _resources() / "teaching_content.sqlite3"


def lexicon_path() -> Path:
    """content.sqlite3 of the active core library (installed pack or bundled)."""
    core = _core_layer()
    if core is not None and core.lexicon is not None:
        return core.lexicon
    return _resources() / "content.sqlite3"


def _connect(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA temp_store=MEMORY")  # no SQLite temp directory on Android (see content.py)
    return connection


def _validate_metadata(connection):
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    if metadata.get("format") != FORMAT or metadata.get("version") not in {str(VERSION), str(SHARDED_VERSION)}:
        raise ContentPackError("Unsupported teaching content pack")
    profile = metadata.get("profile")
    if profile not in {"personal", "demo"}:
        raise ContentPackError("Unknown teaching content profile")
    names = {row[0] for row in connection.execute("SELECT name FROM namespace")}
    try:
        declared = json.loads(metadata.get("namespaces", "[]"))
        if (not isinstance(declared, list) or not declared
                or any(not isinstance(value, str) or not _NAME.fullmatch(value) for value in declared)
                or len(declared) != len(set(declared)) or set(declared) != names):
            raise ContentPackError("Teaching content namespace manifest mismatch")
    except (TypeError, ValueError) as error:
        raise ContentPackError("Invalid teaching content namespace manifest") from error
    if profile == "personal" and not PERSONAL_REQUIRED_NAMESPACES <= names:
        raise ContentPackError("Personal teaching content pack is incomplete")
    return metadata


def manifest_digest(manifest):
    """Checksum of the shard manifest; it covers every shard's sha256 and size."""
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def shard_digest(payload):
    return hashlib.sha256(payload).hexdigest()


def _structure(layout, raw):
    try:
        structure = json.loads(raw) if isinstance(raw, str) else None
    except ValueError as error:
        raise ContentPackError("Invalid teaching namespace structure") from error
    if layout == WHOLE:
        if structure != {}:
            raise ContentPackError("Invalid teaching namespace structure")
        return structure
    if (layout != SHARDED or not isinstance(structure, dict) or set(structure) != {"top", "items_key"}
            or not isinstance(structure["top"], list) or not isinstance(structure["items_key"], str)
            or any(not isinstance(key, str) for key in structure["top"])
            or len(structure["top"]) != len(set(structure["top"]))
            or structure["items_key"] not in structure["top"]):
        raise ContentPackError("Invalid teaching namespace structure")
    return structure


def _read_manifest(connection, metadata):
    """Check manifest checksum, shard presence and stored sizes; decode nothing.

    Returns name -> (layout, structure, {shard_key: (sha256, size)}, item ids).
    """
    rows = list(connection.execute("SELECT name,layout,structure_json FROM namespace ORDER BY name"))
    shards = {}
    for row in connection.execute("""SELECT namespace,shard_key,ordinal,kinds_json,item_fingerprint,size,sha256,
            length(payload),typeof(payload) FROM shard ORDER BY namespace,ordinal"""):
        shards.setdefault(row[0], []).append(row)
    if set(shards) - {row[0] for row in rows}:
        raise ContentPackError("Teaching content shard has no namespace")
    manifest, namespaces = [], {}
    for name, layout, raw in rows:
        structure = _structure(layout, raw)
        listed, table, ids, total = [], {}, [], 0
        for index, (_, key, ordinal, kinds, fingerprint, size, sha, actual, kind) in enumerate(shards.get(name, [])):
            if (not isinstance(key, str) or key in table or ordinal != index or type(size) is not int
                    or kind != "blob" or actual != size or not 0 < size <= _MAX_PAYLOAD_BYTES
                    or not isinstance(sha, str) or not _SHA256.fullmatch(sha) or not isinstance(kinds, str)
                    or fingerprint is not None and not isinstance(fingerprint, str)):
                raise ContentPackError("Teaching content shard does not match its manifest")
            total += size
            table[key] = (sha, size)
            if key.startswith(ITEM_PREFIX):
                ids.append(key[len(ITEM_PREFIX):])
            listed.append([key, ordinal, kinds, fingerprint, size, sha])
        if layout == WHOLE:
            expected = [WHOLE_KEY]
        else:
            expected = [TOP_PREFIX + key for key in structure["top"] if key != structure["items_key"]]
            expected += [ITEM_PREFIX + cid for cid in ids]
        if [entry[0] for entry in listed] != expected or total > _MAX_SHARDED_BYTES:
            raise ContentPackError("Teaching content shard is missing or unexpected")
        manifest.append([name, layout, raw, listed])
        namespaces[name] = (layout, structure, table, tuple(ids))
    digest = manifest_digest(manifest)
    if digest != metadata.get("manifest_sha256") or digest != metadata.get("content_sha256"):
        raise ContentPackError("Teaching content checksum mismatch")
    return namespaces


def _decode_payload(payload, sha, size):
    if not isinstance(payload, bytes) or len(payload) != size or shard_digest(payload) != sha:
        raise ContentPackError("Teaching content shard checksum mismatch")
    try:
        return decode_value(json.loads(payload.decode("utf-8")))
    except (ValueError, TypeError, RecursionError) as error:
        if isinstance(error, ContentPackError):
            raise
        raise ContentPackError("Invalid teaching namespace") from error


def validate_pack(path, *, deep=False):
    """Require a complete, supported, intact pack before opening learner state.

    Version 2: the manifest checksum covers each shard's sha256 and size; this
    checks it and the stored sizes, and shard checksums are verified when read.
    deep=True also verifies and decodes every shard (builds and audits).
    Version 1 packs are still decoded and hashed whole.
    """
    path = Path(path)
    if not path.is_file():
        raise ContentPackError("Teaching content pack is missing")
    try:
        with closing(_connect(path)) as connection:
            metadata = _validate_metadata(connection)
            if metadata["version"] == str(SHARDED_VERSION):
                namespaces = _read_manifest(connection, metadata)
                if deep:
                    for name, (layout, structure, table, _) in namespaces.items():
                        _validate_shard_structure(name, layout, structure)
                        for key, (sha, size) in table.items():
                            row = connection.execute("SELECT payload FROM shard WHERE namespace=? AND shard_key=?",
                                                     (name, key)).fetchone()
                            _validate_shard_value(name, key, _decode_payload(row[0] if row else None, sha, size))
                return {**metadata, "namespace_count": len(namespaces),
                        "shard_count": sum(len(entry[2]) for entry in namespaces.values())}
            documents = list(connection.execute("SELECT name,payload_json FROM namespace ORDER BY name"))
            for name, raw in documents:
                if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
                    raise ContentPackError("Teaching namespace exceeds its size limit")
                _validate_shard_value(name, WHOLE_KEY, decode_value(json.loads(raw)), deep=deep)
            digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False).encode("utf-8")).hexdigest()
            if digest != metadata.get("content_sha256"):
                raise ContentPackError("Teaching content checksum mismatch")
            return {**metadata, "namespace_count": len(documents)}
    except (sqlite3.Error, TypeError, ValueError, RecursionError) as error:
        if isinstance(error, ContentPackError):
            raise
        raise ContentPackError("Invalid teaching content pack") from error


def validate_installed_pack():
    return validate_pack(pack_path())


@lru_cache(maxsize=8)
def _pack_manifest(path, modified_ns, size):
    """Validated v2 manifest of one pack file (None for v1); payload-free."""
    del modified_ns, size  # The values invalidate the cache after replacing a pack.
    try:
        with closing(_connect(path)) as connection:
            metadata = _validate_metadata(connection)
            if metadata["version"] == str(SHARDED_VERSION):
                return _read_manifest(connection, metadata)
            return None
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error


_SHARD_READS = threading.local()  # .connections: pack path -> connection, only inside shard_reads()


@contextmanager
def shard_reads():
    """Opt-in for a thread that reads many shards (a worker): inside it this thread's shard reads
    share one read-only connection per pack file, closed when it ends. Elsewhere, and on every
    other thread, each read opens and closes its own connection, as always; checks are unchanged."""
    if getattr(_SHARD_READS, "connections", None) is not None:
        yield  # nested: the outer one owns the connections
        return
    _SHARD_READS.connections = {}
    try:
        yield
    finally:
        connections, _SHARD_READS.connections = _SHARD_READS.connections, None
        for connection in connections.values():
            connection.close()


def _read_shard(path, name, key, sha, size):
    shared = getattr(_SHARD_READS, "connections", None)
    try:
        if shared is None:
            with closing(_connect(path)) as connection:
                row = connection.execute("SELECT payload FROM shard WHERE namespace=? AND shard_key=?",
                                         (name, key)).fetchone()
        else:
            connection = shared.get(path)
            if connection is None:
                connection = shared[path] = _connect(path)
            row = connection.execute("SELECT payload FROM shard WHERE namespace=? AND shard_key=?",
                                     (name, key)).fetchone()
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error
    if row is None:
        raise ContentPackError("Teaching content shard is missing")
    return _decode_payload(row[0], sha, size)


def _read_legacy(path, name):
    try:
        with closing(_connect(path)) as connection:
            row = connection.execute("SELECT payload_json FROM namespace WHERE name=?", (name,)).fetchone()
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error
    if row is None:
        return False, None
    raw = row[0]
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ContentPackError("Teaching namespace exceeds its size limit")
    try:
        return True, decode_value(json.loads(raw))
    except (ValueError, TypeError, RecursionError) as error:
        raise ContentPackError("Invalid teaching namespace") from error


class _LazyMapping(Mapping):
    """Read-only mapping whose values load from checked shards on first access.

    Each value is decoded once per mapping and then shared by every lookup;
    callers treat it as read-only, like any module-level teaching table.
    deepcopy/copy return plain dicts.
    """

    def __init__(self, source, name, table, keys, prefix):
        self._source, self._name, self._table, self._prefix = source, name, table, prefix
        self._keys = tuple(keys)
        self._known = frozenset(self._keys)
        self._values = {}
        self._lock = threading.Lock()

    def _load(self, key):
        try:
            return self._values[key]
        except KeyError:
            pass
        with self._lock:
            if key not in self._values:
                sha, size = self._table[self._prefix + key]
                self._values[key] = _read_shard(self._source, self._name, self._prefix + key, sha, size)
            return self._values[key]

    def __getitem__(self, key):
        if not self.__contains__(key):
            raise KeyError(key)
        return self._load(key)

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __contains__(self, key):
        try:
            return key in self._known
        except TypeError:
            return False

    def loaded_keys(self):
        """Keys decoded so far (diagnostics and tests)."""
        return frozenset(self._values)

    def __deepcopy__(self, memo):
        return {key: deepcopy(self[key], memo) for key in self._keys}

    def __copy__(self):
        return {key: self[key] for key in self._keys}

    def __repr__(self):
        return "<teaching shards %s: %d keys, %d loaded>" % (self._name, len(self._keys), len(self._values))


class ShardedNamespace(_LazyMapping):
    """Read-only view of a sharded namespace, equal to the original mapping.

    Top-level values other than the item table load on first access (an unused
    provenance list is never decoded); the item table decodes per content id.
    """

    def __init__(self, source, name, structure, table, ids):
        super().__init__(source, name, table, structure["top"], TOP_PREFIX)
        self._items_key = structure["items_key"]
        self._items = _LazyMapping(source, name, table, ids, ITEM_PREFIX)

    def _load(self, key):
        return self._items if key == self._items_key else super()._load(key)

    def loaded_keys(self):
        return super().loaded_keys() | {self._items_key + "/" + key for key in self._items.loaded_keys()}


def _installed_manifest():
    path = pack_path()
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None, None
    source = str(path.resolve())
    return source, _pack_manifest(source, stat.st_mtime_ns, stat.st_size)


class LayeredNamespace(Mapping):
    """A sharded core namespace with extension library payloads layered on it.

    Top-level keys follow the section 6.2 policy table; the item table keeps
    lazy per-id decoding for the core shards (only overlapping ids decode).
    """

    def __init__(self, base, extensions, report_sink, *, namespace=None, declared=(), accepted=frozenset()):
        self._base, self._extensions = base, [(lid, p) for lid, p in extensions if isinstance(p, Mapping)]
        self._items_key = base._items_key
        self._namespace, self._declared, self._accepted = namespace, tuple(declared), frozenset(accepted)
        keys = list(base)
        for _, payload in self._extensions:
            keys.extend(k for k in payload if k not in keys
                        and library_merge.effective_policy(namespace, k, self._declared)[0] != "singleton")
        self._keys, self._known = tuple(keys), frozenset(keys)
        self._values, self._lock, self._sink = {}, threading.Lock(), report_sink

    def _compute(self, key):
        if key == self._items_key:
            layers = [(library_rules.CORE_LIBRARY_ID, self._base[key])]
            layers += [(lid, p[key]) for lid, p in self._extensions if isinstance(p.get(key), Mapping)]
            return _LayeredItems(layers, self._sink, key, self._accepted)
        layers = [(library_rules.CORE_LIBRARY_ID, {key: self._base[key]} if key in self._base else None)]
        layers += [(lid, {key: p[key]}) for lid, p in self._extensions if key in p]
        merged, report = library_merge.merge_namespace(layers, namespace=self._namespace,
                                                       declared_policies=self._declared,
                                                       fingerprint=_entry_fingerprint,
                                                       overrides_accepted=self._accepted)
        self._sink(report)
        return merged[key]

    def __getitem__(self, key):
        if key not in self._known:
            raise KeyError(key)
        with self._lock:
            if key not in self._values:
                self._values[key] = self._compute(key)
            return self._values[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def __contains__(self, key):
        try:
            return key in self._known
        except TypeError:
            return False

    def __deepcopy__(self, memo):
        return {key: deepcopy(self[key], memo) for key in self._keys}

    def __copy__(self):
        return {key: self[key] for key in self._keys}


class _LayeredItems(Mapping):
    def __init__(self, layers, sink, key, accepted=frozenset()):
        self._layers = layers
        self._order, self._owner = [], {}
        conflicts, duplicates, overridden = [], [], []
        for library_id, table in layers:
            for item_id in table:
                if item_id not in self._owner:
                    self._owner[item_id] = (library_id, table)
                    self._order.append(item_id)
                    continue
                owner, owner_table = self._owner[item_id]
                if library_id == owner:
                    continue
                if _entry_fingerprint(owner_table[item_id]) == _entry_fingerprint(table[item_id]):
                    duplicates.append((key, item_id, owner, library_id))
                elif (library_id, item_id) in accepted:
                    overridden.append((key, item_id, library_id, owner))
                    self._owner[item_id] = (library_id, table)
                else:
                    conflicts.append((key, item_id, owner, library_id))
        self._order = tuple(self._order)
        sink(library_merge.MergeReport(conflicts=tuple(conflicts), duplicates=tuple(duplicates),
                                       overridden=tuple(overridden)))

    def __getitem__(self, key):
        try:
            return self._owner[key][1][key]
        except (KeyError, TypeError):
            raise KeyError(key) from None

    def __iter__(self):
        return iter(self._order)

    def __len__(self):
        return len(self._order)

    def __contains__(self, key):
        try:
            return key in self._owner
        except TypeError:
            return False

    def __deepcopy__(self, memo):
        return {key: deepcopy(self[key], memo) for key in self._order}

    def __copy__(self):
        return {key: self[key] for key in self._order}


def _entry_fingerprint(value):
    """items_by_id equality: item_fingerprint for catalogue items, else the canonical value."""
    if isinstance(value, Mapping) and "id" in value and "family" in value:
        from .material_review import item_fingerprint
        return "item:" + item_fingerprint(value)
    return "value:" + library_merge.digest(value)


_merge_reports = {}
_merge_lock = threading.Lock()


def _record_report(name, report):
    fields = tuple(library_merge.MergeReport.__dataclass_fields__)
    if not any(getattr(report, field_name) for field_name in fields):
        return
    with _merge_lock:
        current = _merge_reports.setdefault(name, {field_name: [] for field_name in fields})
        for field_name in current:
            for row in getattr(report, field_name):
                if row not in current[field_name] and len(current[field_name]) < 10000:
                    current[field_name].append(row)


def library_merge_report():
    """namespace -> merge report rows (see content_library_merge.MergeReport) seen so far."""
    with _merge_lock:
        return deepcopy(_merge_reports)


def namespace_contributions(name):
    """(key, entry_key, library_id, added items|None) rows extension libraries contributed.

    Empty with only the core library. course_prerequisites uses this to apply
    extension rules only to consumers present in the effective catalogue.
    """
    catalogue = effective_catalogue()
    if not any(name in catalogue.payloads.get(lib, {}) for lib in catalogue.extension_ids):
        return ()
    with _merge_lock:
        known = name in _merge_reports
    if not known:
        load_namespace(name)
    with _merge_lock:
        return tuple(tuple(row) for row in _merge_reports.get(name, {}).get("contributions", ()))


def _core_namespace_names():
    source, manifest = _installed_manifest()
    if source is None:
        return ()
    if manifest is not None:
        return tuple(sorted(manifest))
    try:
        with closing(_connect(source)) as connection:
            return tuple(row[0] for row in connection.execute("SELECT name FROM namespace ORDER BY name"))
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error


def _core_catalogue_items(extra_names=()):
    """content id -> core catalogue item, from the catalogue tables of the core pack."""
    names = set(_core_namespace_names())
    wanted = [n for n in library_merge.CATALOGUE_NAMESPACES if n in names]
    wanted += sorted(n for n in set(extra_names) & names if n not in wanted)
    if not wanted:
        wanted = sorted(names)
    items = {}
    for name in wanted:
        found, value = _core_namespace(name, None)
        if found:
            for cid, item in library_merge.catalogue_items(value):
                items.setdefault(cid, item)
    return items


def _unavailable_libraries(registry, root, loaded):
    """Installed libraries outside the effective catalogue, with their provided ids."""
    rows = []
    if registry is None or root is None:
        return rows
    for entry in registry["libraries"]:
        if entry["source"]["kind"] != "file" or entry["library_id"] in loaded:
            continue
        reason = "disabled" if not entry["enabled"] else "damaged"
        try:
            manifest = library_rules.validate_manifest(json.loads(
                (Path(root) / entry["path"] / library_rules.MANIFEST_FILE).read_text("utf-8")))
            provided = tuple(manifest["provides"]["items"])
        except (OSError, ValueError, UnicodeDecodeError):
            provided = ()
        rows.append(library_merge.UnavailableLibrary(entry["library_id"], provided, reason))
    return rows


_effective = []
_effective_lock = threading.RLock()
_built_token = []  # [token] of the last effective catalogue built in this process (kept by clear_cache)
_catalogue_rejections = {}  # effective-catalogue token -> {library_id: reason} (learning catalogue build)
_catalogue_bystanders = {}  # effective-catalogue token -> {library_id: reason}: not loaded, never damaged


def record_catalogue_rejection(token, reasons, *, bystanders=()):
    """The learning catalogue rejected these libraries' items (library_id -> reason).

    Keyed by the effective catalogue's token: the learning catalogue is a
    process constant built from that merge, so a later registry (another
    token) never inherits the rejection. Read by library_status(): only the
    responsible libraries are reported damaged (startup_sync persists that).
    Libraries named in ``bystanders`` lost content only because of another
    library (review B-04); they are reported for this process alone through
    library_load_notes() ("本次未载入") and never marked damaged.
    """
    others = set(bystanders)
    rows = _catalogue_rejections.setdefault(token, {})
    notes = _catalogue_bystanders.setdefault(token, {})
    for library_id, reason in reasons.items():
        (notes if library_id in others else rows).setdefault(library_id, str(reason)[:200])


def library_load_notes():
    """library_id -> why this process left the library's content out, without marking it damaged.

    Bystanders of a learning-catalogue rejection, and every extension when the
    effective merge fell back to the core library because of another one.
    """
    if not library_layers():
        return {}
    catalogue = effective_catalogue()
    notes = dict(_catalogue_bystanders.get(catalogue.token, {}))
    if catalogue.fallback:
        reason = "其他内容库合并失败，本次只载入核心库"
        for layer in library_layers():
            if (not layer.damaged and layer.library_id != library_rules.CORE_LIBRARY_ID
                    and layer.library_id not in catalogue.failed):
                notes.setdefault(layer.library_id, reason)
    return notes


def _catalogue_token(registry):
    rows = [] if registry is None else [[e["library_id"], e["library_version"], e["content_sha256"], e["enabled"]]
                                        for e in library_rules.effective_entries(registry)]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def effective_catalogue():
    """The process-constant EffectiveCatalogue (section 6.1); built on first use.

    Read-only interface for availability consumers:
      library_of(content_id) -> str | None
      missing_required_references(content_id) -> tuple[str, ...]

    A catalogue built with another token than the one built before drops every content cache
    (content_caches.clear_all), after the lock is released: no cache outlives its catalogue.
    """
    if _effective:
        return _effective[0]
    with _effective_lock:
        if _effective:
            return _effective[0]
        catalogue = _build_effective_catalogue()
        token = getattr(catalogue, "token", None)
        changed = bool(_built_token) and _built_token[0] != token
        _built_token[:] = [token]
        _effective.append(catalogue)
    if changed:
        content_caches.clear_all()
    return catalogue


def _build_effective_catalogue():
    """The merge itself; effective_catalogue calls it holding _effective_lock."""
    registry = active_registry()
    layers = library_layers()
    token = _catalogue_token(registry)
    extensions = [layer for layer in layers
                  if not layer.damaged and layer.library_id != library_rules.CORE_LIBRARY_ID]
    loaded = {layer.library_id for layer in layers if not layer.damaged}
    core = _core_layer()
    unavailable = _unavailable_libraries(registry, library_root(), loaded)
    if not extensions:
        return library_merge.core_only(core_ids=_core_catalogue_items, unavailable=unavailable, token=token)
    return _merge_extensions(registry, extensions, core, unavailable, token)


def _merge_extensions(registry, extensions, core, unavailable, token):
    """Merge the checked extension layers over the core catalogue, or fall back to core only.

    Only a failure to read the core pack itself is raised. Any exception while
    reading an extension's documents or while building the merge (including
    RecursionError or a malformed payload) falls back to the core library and
    names the responsible libraries in ``failed`` (review B-01): the one being
    read, the ids a ContentMergeError names, else those whose merge fails
    alone (all of them when none does).
    """
    libraries, current = [], None
    try:
        for layer in extensions:
            current = layer.library_id
            libraries.append(library_merge.ExtensionLibrary(
                layer.library_id, layer.manifest, {name: layer.open_namespace(name) for name in sorted(layer.teaching)}))
    except Exception as error:  # noqa: BLE001 - an unreadable extension never stops the App starting
        return _fallback(error, (current,), extensions, unavailable, token)
    names = {name for library in libraries for name in library.payloads}
    core_items = _core_catalogue_items(names)  # the core pack itself is unreadable: never hide that
    accepted = library_rules.accepted_overrides(registry)

    def build(chosen):
        return library_merge.build_effective_catalogue(
            core_items=core_items, extensions=chosen, overrides_accepted=accepted, unavailable=unavailable,
            core_manifest=core.manifest if core is not None else None, token=token)
    try:
        return build(libraries)
    except Exception as error:  # noqa: BLE001 - see the docstring; the fallback is recorded as a diagnostic
        culprits = tuple(getattr(error, "library_ids", ()) or ())
        if not culprits:
            culprits = tuple(library.library_id for library in libraries if _fails_alone(build, library))
        return _fallback(error, culprits or tuple(library.library_id for library in libraries),
                         extensions, unavailable, token)


def _fails_alone(build, library):
    try:
        build([library])
    except Exception:  # noqa: BLE001 - only used to name the responsible library
        return True
    return False


def _fallback(error, culprits, extensions, unavailable, token):
    from . import diagnostic_events
    culprits = tuple(lid for lid in dict.fromkeys(culprits) if lid)
    diagnostic_events.record_error("content_library", "read", error)
    reason = (str(error).splitlines()[0] if str(error) else type(error).__name__)[:200]
    loaded = [row for row in unavailable]
    loaded += [library_merge.UnavailableLibrary(layer.library_id, tuple(layer.manifest["provides"]["items"]), "failed")
               for layer in extensions]
    return library_merge.core_only(core_ids=_core_catalogue_items, unavailable=loaded,
                                   failed={lid: reason for lid in culprits}, token=token, fallback=True,
                                   diagnostics=(("fallback", culprits, reason),))


def _core_namespace(name, default):
    source, manifest = _installed_manifest()
    if source is None:
        return False, deepcopy(default)
    if manifest is None:
        found, value = _read_legacy(source, name)
        return found, (value if found else deepcopy(default))
    entry = manifest.get(name)
    if entry is None:
        return False, deepcopy(default)
    layout, structure, table, ids = entry
    if layout == WHOLE:
        sha, size = table[WHOLE_KEY]
        return True, _read_shard(source, name, WHOLE_KEY, sha, size)
    return True, ShardedNamespace(source, name, structure, table, ids)


def load_namespace(name, default=None):
    """Return an isolated value; absent packs/namespaces use an explicit default.

    Missing optional content is allowed in clean/demo builds. An existing but
    invalid pack is an error, never a reason to execute or restore embedded data.
    A whole namespace is decoded afresh per call (no cached second copy); a
    sharded one is a new read-only ShardedNamespace that decodes lazily.
    Enabled extension libraries that provide the namespace are merged over
    the core value in priority order (section 6.2); the signature is unchanged.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ContentPackError("Invalid namespace name")
    found, value = _core_namespace(name, default)
    if not library_layers():
        return value
    catalogue = effective_catalogue()
    # isolated_copy: independent containers; parts read on demand stay deferred (deepcopy would decode them all).
    extensions = [(library_id, library_index.isolated_copy(catalogue.payloads[library_id][name]))
                  for library_id in catalogue.extension_ids if name in catalogue.payloads.get(library_id, {})]
    if not extensions:
        return value
    sink = lambda report: _record_report(name, report)  # noqa: E731
    declared = catalogue.declared_policies.get(name, ())
    if isinstance(value, ShardedNamespace):
        return LayeredNamespace(value, extensions, sink, namespace=name, declared=declared,
                                accepted=catalogue.accepted_overrides)
    layers = [(library_rules.CORE_LIBRARY_ID, value if found else None), *extensions]
    merged, report = library_merge.merge_namespace(layers, namespace=name, declared_policies=declared,
                                                   fingerprint=_entry_fingerprint,
                                                   overrides_accepted=catalogue.accepted_overrides)
    sink(report)
    return merged


def read_pack_namespaces(path):
    """Yield (name, fully decoded value) for every namespace of a pack file.

    Used by packaging tools; the App reads lazily through load_namespace.
    """
    path = Path(path)
    validate_pack(path)
    source = str(path.resolve())
    with closing(_connect(source)) as connection:
        metadata = _validate_metadata(connection)
        if metadata["version"] != str(SHARDED_VERSION):
            for name, raw in connection.execute("SELECT name,payload_json FROM namespace ORDER BY name"):
                yield name, decode_value(json.loads(raw))
            return
        namespaces = _read_manifest(connection, metadata)
    for name, (layout, structure, table, ids) in namespaces.items():
        if layout == WHOLE:
            sha, size = table[WHOLE_KEY]
            yield name, _read_shard(source, name, WHOLE_KEY, sha, size)
        else:
            yield name, deepcopy(ShardedNamespace(source, name, structure, table, ids))


def shard_index(name):
    """kind -> item_fingerprint -> content id, from the manifest (no decoding).

    Empty for whole, v1 or missing namespaces. The key pair is the
    item_fingerprint binding learning_questions uses and the library merge key.
    """
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ContentPackError("Invalid namespace name")
    source, manifest = _installed_manifest()
    if manifest is None or name not in manifest or manifest[name][0] != SHARDED:
        return {}
    table = manifest[name][2]
    try:
        with closing(_connect(source)) as connection:
            rows = list(connection.execute("""SELECT shard_key,kinds_json,item_fingerprint,sha256 FROM shard
                WHERE namespace=? AND shard_key GLOB 'item:*' ORDER BY ordinal""", (name,)))
    except sqlite3.Error as error:
        raise ContentPackError("Cannot read teaching content pack") from error
    result = {}
    for key, kinds, fingerprint, sha in rows:
        if table.get(key, (None,))[0] != sha:
            raise ContentPackError("Teaching content shard does not match its manifest")
        try:
            listed = json.loads(kinds)
        except ValueError as error:
            raise ContentPackError("Invalid teaching shard index") from error
        if not isinstance(fingerprint, str) or not isinstance(listed, list):
            continue
        for kind in listed:
            result.setdefault(kind, {})[fingerprint] = key[len(ITEM_PREFIX):]
    return result


def clear_cache():
    """Forget pack manifests, the registry mirror and checked library layers.

    Every cache derived from the content is dropped too (content_caches.clear_all), among them the
    built study route trees (study_routes.build_route_set, keyed by the effective catalogue token):
    a rebuilt catalogue never reuses anything of the one it replaces (design 10, WP-R4b). The token
    of the last catalogue is kept, so a catalogue rebuilt with another one drops them once more.
    """
    _pack_manifest.cache_clear()
    _mirror.cache_clear()
    _teaching_sha256.cache_clear()
    _layers_for.cache_clear()
    _frozen.clear()
    library_index.clear_cache()
    with _effective_lock:
        _effective.clear()
    with _merge_lock:
        _merge_reports.clear()
    content_caches.clear_all()
