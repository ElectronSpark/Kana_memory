"""Split a large JSON document into one small file per item and join it back byte for byte.

Large content sources cannot be versioned well as single files: every batch rewrites one huge
blob. This tool stores such a document as a directory: one piece file per member of each large
table, consecutive chunks of each long array, and a manifest that keeps every byte between the
pieces verbatim. Nothing is re-serialised, so `join` reproduces the source exactly, whatever its
formatting, key order, escapes or line endings.

Layout of a document directory:
  manifest.json                     ordered part list: inline text and piece files with sha256
  <table>/<2 hex>/<14 hex>.json     the value bytes of one member of an object table
  <table>/<00000>.chunk             the bytes of consecutive array elements (with their commas)
  text-000.txt                      a long stretch of literal text between pieces

Tables are the members of the top-level object; a member named "payload" that is an object is
replaced by its own members. Members of `payload` keep their name as directory, other top-level
tables get an "@" prefix. Only standard library code; no git interaction beyond writing
`.gitattributes` with `* -text` at the root of a batch tree.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
import hashlib
import json
from json.decoder import scanstring
import os
from pathlib import Path
import re
import sys
import time

MANIFEST_NAME = "manifest.json"
INDEX_NAME = "index.json"
GITATTRIBUTES_NAME = ".gitattributes"
GITATTRIBUTES_TEXT = b"* -text\n"
MANIFEST_FORMAT = "kanamemory-content-source-split"
INDEX_FORMAT = "kanamemory-content-source-split-index"
FORMAT_VERSION = 1
TEMP_SUFFIX = ".tmp-split"
PAYLOAD = "payload"

_WHITESPACE = re.compile(r"[ \t\n\r]*")
_SAFE_TABLE = re.compile(r"[A-Za-z0-9_-]{1,24}\Z")
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)),
                     *(f"LPT{n}" for n in range(1, 10))}
_SLUG = r"@?[A-Za-z0-9_-]{1,24}"
# Every file name this tool can create inside a document directory (manifest excluded).
_PIECE_PATH = re.compile(rf"(?:text-[0-9]{{3,}}\.txt|{_SLUG}/[0-9a-f]{{2}}/[0-9a-f]{{1,62}}\.json"
                         rf"|{_SLUG}/[0-9]{{5,}}\.chunk)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class SplitError(Exception):
    """A document, tree or option that the tool refuses; the message says why."""


@dataclass(frozen=True)
class Settings:
    """Thresholds that decide what becomes a piece; recorded in each manifest.

    min_document_bytes: smaller documents stay whole (one literal), so small namespaces are not
        scattered into many tiny files.
    min_table_bytes: smaller tables stay literal text.
    min_members: an object table needs at least this many members to be split per member.
    chunk_elements: an array table is cut into chunks of this many elements; it is split only
        when it holds more than one chunk.
    max_inline_bytes: literal text up to this size stays inside the manifest, longer stretches
        get their own text file so their diff stays readable.
    name_digits: hex digits of the sha256 of a member key used as its file name (2 of them form
        the fan-out directory).
    """

    min_document_bytes: int = 1 << 20
    min_table_bytes: int = 64 << 10
    min_members: int = 16
    chunk_elements: int = 64
    max_inline_bytes: int = 512
    name_digits: int = 16

    def __post_init__(self):
        limits = {"min_document_bytes": 0, "min_table_bytes": 0, "min_members": 1,
                  "chunk_elements": 1, "max_inline_bytes": 0, "name_digits": 3}
        for name, low in limits.items():
            value = getattr(self, name)
            if type(value) is not int or value < low:
                raise SplitError(f"setting {name} must be an integer >= {low}, got {value!r}")
        if self.name_digits > 64:
            raise SplitError("setting name_digits must be at most 64")


SETTING_NAMES = tuple(field.name for field in fields(Settings))


def resolve_settings(overrides=None, previous=None):
    """Explicit options win, then the settings of the existing manifest, then the defaults.

    Reusing the previous settings keeps a re-split from churning every piece when the built-in
    defaults change later.
    """
    values = {}
    if isinstance(previous, dict):
        values.update({name: previous[name] for name in SETTING_NAMES if name in previous})
    values.update({name: value for name, value in (overrides or {}).items() if value is not None})
    unknown = set(values) - set(SETTING_NAMES)
    if unknown:
        raise SplitError("unknown settings: " + ", ".join(sorted(unknown)))
    return Settings(**values)


# ---------------------------------------------------------------- scanning (text spans only)

@dataclass
class Node:
    """The span [start, end) of one JSON value in the document text.

    `members` lists (key, Node) for a scanned object, `elements` the Nodes of a scanned array;
    both stay None for a value that was only measured.
    """

    start: int
    end: int
    members: list | None = None
    elements: list | None = None


def _reject_constant(name):
    raise ValueError(f"{name} is not valid JSON")


_DECODER = json.JSONDecoder(parse_constant=_reject_constant)


def _error(message, text, pos):
    line = text.count("\n", 0, pos) + 1
    return SplitError(f"not valid JSON: {message} at line {line} (character {pos})")


def _skip(text, pos):
    return _WHITESPACE.match(text, pos).end()


def _leaf(text, pos):
    """Measure one complete value with the standard decoder (validates it on the way)."""
    try:
        _, end = _DECODER.raw_decode(text, pos)
    except json.JSONDecodeError as exc:
        raise _error(exc.msg, text, exc.pos) from None
    except (ValueError, RecursionError) as exc:
        raise _error(str(exc) or type(exc).__name__, text, pos) from None
    return Node(pos, end)


def _table(text, pos):
    """A table value: objects and arrays are scanned one level deep, other values measured."""
    char = text[pos:pos + 1]
    if char == "{":
        return _object(text, pos, lambda key: _leaf)
    if char == "[":
        return _array(text, pos, _leaf)
    return _leaf(text, pos)


def _top_member(key):
    return _payload if key == PAYLOAD else _table


def _payload(text, pos):
    if text[pos:pos + 1] == "{":
        return _object(text, pos, lambda key: _table)
    return _table(text, pos)


def _object(text, pos, scanner_for):
    """Scan an object starting at text[pos] == '{'; scanner_for(key) scans each member value."""
    start, members = pos, []
    pos = _skip(text, pos + 1)
    if text[pos:pos + 1] == "}":
        return Node(start, pos + 1, members=members)
    while True:
        if text[pos:pos + 1] != '"':
            raise _error("expected a member name", text, pos)
        try:
            key, pos = scanstring(text, pos + 1, True)
        except json.JSONDecodeError as exc:
            raise _error(exc.msg, text, exc.pos) from None
        pos = _skip(text, pos)
        if text[pos:pos + 1] != ":":
            raise _error("expected ':'", text, pos)
        pos = _skip(text, pos + 1)
        node = scanner_for(key)(text, pos)
        members.append((key, node))
        pos = _skip(text, node.end)
        char = text[pos:pos + 1]
        if char == "}":
            return Node(start, pos + 1, members=members)
        if char != ",":
            raise _error("expected ',' or '}'", text, pos)
        pos = _skip(text, pos + 1)


def _array(text, pos, scan_element):
    start, elements = pos, []
    pos = _skip(text, pos + 1)
    if text[pos:pos + 1] == "]":
        return Node(start, pos + 1, elements=elements)
    while True:
        node = scan_element(text, pos)
        elements.append(node)
        pos = _skip(text, node.end)
        char = text[pos:pos + 1]
        if char == "]":
            return Node(start, pos + 1, elements=elements)
        if char != ",":
            raise _error("expected ',' or ']'", text, pos)
        pos = _skip(text, pos + 1)


def decode_text(data):
    """Strict UTF-8 text of a document; a byte order mark or invalid UTF-8 is refused."""
    if not isinstance(data, bytes):
        raise TypeError("documents are handled as bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SplitError(f"not UTF-8: {exc.reason} at byte {exc.start}") from None


def scan_document(text):
    """Validate the whole text as JSON and return the span tree of its root value.

    Every value is either scanned here or measured by the standard decoder, so the whole text is
    checked without ever building the complete document in memory.
    """
    pos = _skip(text, 0)
    if text[pos:pos + 1] == "{":
        root = _object(text, pos, _top_member)
    else:
        root = _leaf(text, pos)
    if _skip(text, root.end) != len(text):
        raise _error("extra data after the document", text, _skip(text, root.end))
    return root


# ---------------------------------------------------------------- planning the pieces

@dataclass(frozen=True)
class Part:
    """One part of the document in order: inline text (path None) or a file holding `data`."""

    data: bytes
    path: str | None = None
    key: str | None = None
    first: int | None = None
    count: int | None = None


def _digest(text):
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()


def table_slug(name, top_level):
    """Short, stable directory name of a table; unsafe or reserved names become a digest."""
    if _SAFE_TABLE.match(name) and name.upper() not in _WINDOWS_RESERVED:
        slug = name
    else:
        slug = "h-" + _digest(("@" if top_level else "") + name)[:10]
    return ("@" if top_level else "") + slug


def _tables(root):
    """(name, node, top_level) of every table, in document order.

    A `payload` object was scanned one level deeper, so its members are the tables.
    """
    for key, node in root.members:
        if key == PAYLOAD and node.members is not None:
            for name, table in node.members:
                yield name, table, False
        else:
            yield key, node, True


def _refuse_duplicates(keys, where):
    seen = set()
    for key in keys:
        if key in seen:
            raise SplitError(f"duplicate key {key!r} in {where}")
        seen.add(key)


def _byte_length(text, node):
    return len(text[node.start:node.end].encode("utf-8"))


def _split_ranges(text, name, node, top_level, settings):
    """(start, end, path, key, first, count) of the pieces of one table, or [] to keep it."""
    if _byte_length(text, node) < settings.min_table_bytes:
        return []
    slug = table_slug(name, top_level)
    where = f"table {name!r}"
    if node.members is not None and len(node.members) >= settings.min_members:
        _refuse_duplicates((key for key, _ in node.members), where)
        names = {}
        ranges = []
        for key, member in node.members:
            digest = _digest(key)[:settings.name_digits]
            path = f"{slug}/{digest[:2]}/{digest[2:]}.json"
            if path in names:
                raise SplitError(f"file name collision in {where}: keys {names[path]!r} and {key!r} both "
                                 f"map to {path}; split with a larger --name-digits")
            names[path] = key
            ranges.append((member.start, member.end, path, key, None, None))
        return ranges
    if node.elements is not None and len(node.elements) > settings.chunk_elements:
        size = settings.chunk_elements
        ranges = []
        for index, first in enumerate(range(0, len(node.elements), size)):
            chunk = node.elements[first:first + size]
            ranges.append((chunk[0].start, chunk[-1].end, f"{slug}/{index:05d}.chunk", None, first, len(chunk)))
        return ranges
    return []


def plan_parts(data, settings=None):
    """Cut a document into ordered parts whose concatenation is exactly `data`."""
    settings = settings or Settings()
    text = decode_text(data)
    root = scan_document(text)
    ranges = []
    if len(data) >= settings.min_document_bytes and root.members is not None:
        _refuse_duplicates((key for key, _ in root.members), "the top-level object")
        for key, node in root.members:
            if key == PAYLOAD and node.members is not None:
                _refuse_duplicates((name for name, _ in node.members), "the payload object")
        slugs = {}
        for name, node, top_level in _tables(root):
            table_ranges = _split_ranges(text, name, node, top_level, settings)
            if not table_ranges:
                continue
            slug = table_slug(name, top_level).casefold()
            if slug in slugs:
                raise SplitError(f"tables {slugs[slug]!r} and {name!r} map to the same directory")
            slugs[slug] = name
            ranges.extend(table_ranges)
    parts, cursor = [], 0
    for start, end, path, key, first, count in ranges:
        if start > cursor:
            parts.append(Part(text[cursor:start].encode("utf-8")))
        parts.append(Part(text[start:end].encode("utf-8"), path, key, first, count))
        cursor = end
    if cursor < len(text):
        parts.append(Part(text[cursor:].encode("utf-8")))
    parts = _literal_files(parts, settings)
    if b"".join(part.data for part in parts) != data:
        raise SplitError("internal error: the parts do not reproduce the document")
    return parts


def _literal_files(parts, settings):
    """Move literal text longer than max_inline_bytes into numbered text files."""
    result, number = [], 0
    for part in parts:
        if part.path is None and len(part.data) > settings.max_inline_bytes:
            part = Part(part.data, f"text-{number:03d}.txt")
            number += 1
        result.append(part)
    return result


# ---------------------------------------------------------------- manifest

def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def _part_record(part):
    if part.path is None:
        return {"text": part.data.decode("utf-8")}
    record = {"file": part.path, "bytes": len(part.data), "sha256": sha256_hex(part.data)}
    if part.key is not None:
        record["key"] = part.key
    if part.first is not None:
        record["first"], record["count"] = part.first, part.count
    return record


def _json_line(value):
    """One JSON line; keys holding lone surrogates (from \\u escapes) fall back to ASCII escapes."""
    line = json.dumps(value, ensure_ascii=False)
    try:
        line.encode("utf-8")
    except UnicodeEncodeError:
        line = json.dumps(value, ensure_ascii=True)
    return line


def manifest_bytes(data, parts, settings):
    """The manifest text: a header and one line per part, so diffs show one line per change."""
    head = {"format": MANIFEST_FORMAT, "version": FORMAT_VERSION,
            "document": {"bytes": len(data), "sha256": sha256_hex(data)},
            "settings": asdict(settings),
            "pieces": sum(1 for part in parts if part.path is not None)}
    lines = ["{"]
    lines.extend(f" {json.dumps(key)}: {_json_line(value)}," for key, value in head.items())
    lines.append(' "parts": [')
    records = [_json_line(_part_record(part)) for part in parts]
    lines.extend(f"  {record}{',' if index < len(records) - 1 else ''}" for index, record in enumerate(records))
    lines.extend([" ]", "}", ""])
    return "\n".join(lines).encode("utf-8")


def _int_field(record, name, where):
    value = record.get(name)
    if type(value) is not int or value < 0:
        raise SplitError(f"{where}: field {name!r} must be a non-negative integer")
    return value


def parse_manifest(raw, where="manifest"):
    """Validate a manifest; every file path must be one this tool could have written."""
    try:
        manifest = json.loads(decode_text(raw))
    except (SplitError, ValueError) as exc:
        raise SplitError(f"{where} is not readable JSON: {exc}") from None
    if not isinstance(manifest, dict) or manifest.get("format") != MANIFEST_FORMAT:
        raise SplitError(f"{where} is not a {MANIFEST_FORMAT} manifest")
    if manifest.get("version") != FORMAT_VERSION:
        raise SplitError(f"{where} has unsupported version {manifest.get('version')!r}")
    document = manifest.get("document")
    if not isinstance(document, dict) or not _SHA256.match(str(document.get("sha256"))):
        raise SplitError(f"{where}: document record is missing or invalid")
    _int_field(document, "bytes", where)
    parts = manifest.get("parts")
    if not isinstance(parts, list):
        raise SplitError(f"{where}: parts must be a list")
    seen = set()
    for number, part in enumerate(parts):
        label = f"{where} part {number}"
        if not isinstance(part, dict):
            raise SplitError(f"{label} is not an object")
        if "text" in part:
            if set(part) != {"text"} or not isinstance(part["text"], str):
                raise SplitError(f"{label}: an inline part holds only a 'text' string")
            continue
        path = part.get("file")
        if not isinstance(path, str) or not _PIECE_PATH.match(path):
            raise SplitError(f"{label}: invalid file path {path!r}")
        if path.casefold() in seen:
            raise SplitError(f"{label}: file {path} is listed twice")
        seen.add(path.casefold())
        _int_field(part, "bytes", label)
        if not _SHA256.match(str(part.get("sha256"))):
            raise SplitError(f"{label}: invalid sha256")
    return manifest


def _read_manifest(directory):
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        raise SplitError(f"no {MANIFEST_NAME} in {directory}")
    return parse_manifest(path.read_bytes(), str(path))


# ---------------------------------------------------------------- file writing

def _write_atomic(path, data):
    """Write through a temporary file and an atomic replace, flushed to disk first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + TEMP_SUFFIX)
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _write_if_changed(path, data):
    """True when the file was (re)written; an identical file keeps its bytes and mtime."""
    if path.is_file() and path.stat().st_size == len(data) and path.read_bytes() == data:
        return False
    _write_atomic(path, data)
    return True


def _relative_files(directory):
    directory = Path(directory)
    return sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*") if path.is_file())


def _is_own_file(relative):
    """Files this tool may create (and therefore may remove) inside a document directory."""
    name = relative[:-len(TEMP_SUFFIX)] if relative.endswith(TEMP_SUFFIX) else relative
    return name == MANIFEST_NAME or bool(_PIECE_PATH.match(name))


def _remove_empty_dirs(directory):
    directory = Path(directory)
    for path in sorted((p for p in directory.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()


# ---------------------------------------------------------------- split / join / verify

@dataclass
class SplitResult:
    files: int
    written: int
    kept: int
    removed: int
    manifest_written: bool
    foreign: list
    sha256: str
    bytes: int


def split_document(data, directory, overrides=None):
    """Write or update the directory of one document; returns counts of what changed.

    Order: changed pieces first, the manifest last (atomic replace), then stale pieces are
    removed. Only files with this tool's own names are ever removed; other files are reported.
    """
    directory = Path(directory)
    previous = None
    manifest_path = directory / MANIFEST_NAME
    if manifest_path.is_file():
        try:
            previous = parse_manifest(manifest_path.read_bytes(), str(manifest_path)).get("settings")
        except SplitError as exc:
            print(f"warning: ignoring the old manifest: {exc}", file=sys.stderr)
    elif directory.is_dir():
        foreign = [rel for rel in _relative_files(directory) if not _is_own_file(rel)]
        if foreign:
            raise SplitError(f"{directory} holds files that are not a split tree (e.g. {foreign[0]})")
    settings = resolve_settings(overrides, previous)
    parts = plan_parts(data, settings)
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {part.path for part in parts if part.path is not None}
    written = kept = 0
    for part in parts:
        if part.path is None:
            continue
        if _write_if_changed(directory / part.path, part.data):
            written += 1
        else:
            kept += 1
    manifest_written = _write_if_changed(manifest_path, manifest_bytes(data, parts, settings))
    removed, foreign = 0, []
    for relative in _relative_files(directory):
        if relative == MANIFEST_NAME or relative in wanted:
            continue
        if _is_own_file(relative):
            (directory / relative).unlink()
            removed += 1
        else:
            foreign.append(relative)
    _remove_empty_dirs(directory)
    return SplitResult(len(wanted), written, kept, removed, manifest_written, foreign,
                       sha256_hex(data), len(data))


def assemble(directory):
    """The document bytes of a split directory; refuses any piece whose sha256 differs."""
    directory = Path(directory)
    manifest = _read_manifest(directory)
    chunks = []
    for part in manifest["parts"]:
        if "text" in part:
            try:
                chunks.append(part["text"].encode("utf-8"))
            except UnicodeEncodeError:
                raise SplitError("manifest text holds an invalid character") from None
            continue
        path = directory / part["file"]
        if not path.is_file():
            raise SplitError(f"missing piece {part['file']}")
        data = path.read_bytes()
        if len(data) != part["bytes"] or sha256_hex(data) != part["sha256"]:
            raise SplitError(f"piece {part['file']} does not match its sha256 in the manifest")
        chunks.append(data)
    data = b"".join(chunks)
    document = manifest["document"]
    if len(data) != document["bytes"] or sha256_hex(data) != document["sha256"]:
        raise SplitError("the joined document does not match the sha256 in the manifest")
    return data


def _differs(destination, data):
    """An existing destination file whose bytes are not `data` (joining would replace other content)."""
    return destination.is_file() and (destination.stat().st_size != len(data) or destination.read_bytes() != data)


def join_document(directory, destination, overwrite=False):
    """Write the joined document atomically; nothing is written when any check fails.

    A destination that already holds different bytes is refused unless `overwrite` is set: the
    tree may be older than the file (a batch that was not split yet), and the file may be the
    only copy of that work. An identical destination is left untouched.
    """
    data = assemble(directory)
    destination = Path(destination)
    if destination.is_dir():
        raise SplitError(f"{destination} is a directory")
    if _differs(destination, data):
        if not overwrite:
            raise SplitError(f"{destination} exists with different content; split it first if it is newer, "
                             "or pass --overwrite to replace it")
        _write_atomic(destination, data)
    elif not destination.is_file():
        _write_atomic(destination, data)
    return data


def verify_document(directory, source=None):
    """Problems found in a split directory (empty list when everything holds).

    Checks every piece against the manifest, that no unlisted file sits in the directory, that
    the joined bytes match the recorded document, and optionally that they equal `source`.
    """
    directory = Path(directory)
    try:
        manifest = _read_manifest(directory)
    except SplitError as exc:
        return [str(exc)]
    problems = []
    listed = {part["file"] for part in manifest["parts"] if "file" in part}
    for part in manifest["parts"]:
        if "file" not in part:
            continue
        path = directory / part["file"]
        if not path.is_file():
            problems.append(f"missing piece {part['file']}")
            continue
        data = path.read_bytes()
        if len(data) != part["bytes"] or sha256_hex(data) != part["sha256"]:
            problems.append(f"piece {part['file']} differs from its sha256 in the manifest")
    for relative in _relative_files(directory):
        if relative != MANIFEST_NAME and relative not in listed:
            problems.append(f"unlisted file {relative}")
    if problems:
        return problems
    try:
        data = assemble(directory)
    except SplitError as exc:
        return [str(exc)]
    if source is not None:
        source = Path(source)
        if not source.is_file():
            problems.append(f"source file {source} does not exist")
        elif source.read_bytes() != data:
            problems.append(f"joining would not reproduce {source.name}")
    return problems


# ---------------------------------------------------------------- batch mode

def _source_files(source_dir):
    """The *.json files of a directory; each is stored in the tree under its name minus '.json'."""
    if not Path(source_dir).is_dir():
        raise SplitError(f"source directory {source_dir} does not exist")
    files = sorted(path for path in Path(source_dir).glob("*.json") if path.is_file())
    seen = {}
    for path in files:
        folded = _dir_name(path.name).casefold()
        if folded in seen:
            raise SplitError(f"{seen[folded]} and {path.name} would share one directory")
        if folded in {"", ".", "..", INDEX_NAME, GITATTRIBUTES_NAME}:
            raise SplitError(f"{path.name} cannot be stored in a tree")
        seen[folded] = path.name
    return files


def _dir_name(file_name):
    return file_name[:-len(".json")]


def index_bytes(entries):
    lines = ["{", f' "format": {json.dumps(INDEX_FORMAT)},', f' "version": {FORMAT_VERSION},', ' "documents": [']
    records = [_json_line(entry) for entry in entries]
    lines.extend(f"  {record}{',' if index < len(records) - 1 else ''}" for index, record in enumerate(records))
    lines.extend([" ]", "}", ""])
    return "\n".join(lines).encode("utf-8")


def read_index(tree):
    path = Path(tree) / INDEX_NAME
    if not path.is_file():
        raise SplitError(f"no {INDEX_NAME} in {tree}")
    try:
        index = json.loads(decode_text(path.read_bytes()))
    except (SplitError, ValueError) as exc:
        raise SplitError(f"{path} is not readable JSON: {exc}") from None
    if not isinstance(index, dict) or index.get("format") != INDEX_FORMAT or index.get("version") != FORMAT_VERSION:
        raise SplitError(f"{path} is not a {INDEX_FORMAT} index of version {FORMAT_VERSION}")
    entries = index.get("documents")
    if not isinstance(entries, list):
        raise SplitError(f"{path}: documents must be a list")
    for entry in entries:
        name = entry.get("file") if isinstance(entry, dict) else None
        if not isinstance(name, str) or not name.endswith(".json") or "/" in name or "\\" in name \
                or entry.get("dir") != _dir_name(name) or entry["dir"] in {"", ".", ".."} \
                or not isinstance(entry.get("sha256"), str) or type(entry.get("bytes")) is not int:
            raise SplitError(f"{path}: invalid document entry {entry!r}")
    return entries


def _remove_document_dir(directory):
    """Remove only this tool's files of a document directory; returns the foreign files left."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    foreign = []
    for relative in _relative_files(directory):
        if _is_own_file(relative):
            (directory / relative).unlink()
        else:
            foreign.append(relative)
    _remove_empty_dirs(directory)
    if not foreign:
        directory.rmdir()
    return foreign


def split_all(source_dir, tree, overrides=None, prune=False, report=print):
    """Split every *.json of source_dir into tree/<name>/ and write the index last."""
    tree = Path(tree)
    sources = _source_files(source_dir)
    old_entries = read_index(tree) if (tree / INDEX_NAME).is_file() else []
    if not (tree / INDEX_NAME).is_file() and tree.is_dir() and any(tree.iterdir()):
        raise SplitError(f"{tree} is not empty and has no {INDEX_NAME}")
    tree.mkdir(parents=True, exist_ok=True)
    _write_if_changed(tree / GITATTRIBUTES_NAME, GITATTRIBUTES_TEXT)
    entries, totals = [], {"written": 0, "kept": 0, "removed": 0}
    for source in sources:
        started = time.perf_counter()
        result = split_document(source.read_bytes(), tree / _dir_name(source.name), overrides)
        seconds = time.perf_counter() - started
        for name in totals:
            totals[name] += getattr(result, name)
        entries.append({"file": source.name, "dir": _dir_name(source.name), "sha256": result.sha256,
                        "bytes": result.bytes, "pieces": result.files})
        report(f"{source.name}: {result.files} files, written {result.written}, kept {result.kept}, "
               f"removed {result.removed}{', manifest updated' if result.manifest_written else ''} "
               f"({seconds:.1f} s)")
        for relative in result.foreign:
            report(f"  warning: unlisted file left in place: {_dir_name(source.name)}/{relative}")
    names = {source.name for source in sources}
    for entry in old_entries:
        if entry["file"] in names:
            continue
        if prune:
            foreign = _remove_document_dir(tree / entry["dir"])
            report(f"{entry['file']}: source removed, pruned from the tree"
                   + (f" (foreign files left: {', '.join(foreign)})" if foreign else ""))
        else:
            entries.append(entry)
            report(f"{entry['file']}: source removed; kept in the tree (pass --prune to delete it)")
    entries.sort(key=lambda entry: entry["file"])
    index_written = _write_if_changed(tree / INDEX_NAME, index_bytes(entries))
    report(f"total: {len(entries)} documents, written {totals['written']}, kept {totals['kept']}, "
           f"removed {totals['removed']}{', index updated' if index_written else ''}")
    return totals, index_written


def join_all(tree, destination, overwrite=False, report=print):
    """Join every document of a tree into destination; all or nothing.

    Every document is assembled and checked first. Existing destination files with different
    content are refused together (see join_document) unless `overwrite` is set, before anything
    is written; identical files are left untouched.
    """
    tree, destination = Path(tree), Path(destination)
    entries = read_index(tree)
    if destination.is_file():
        raise SplitError(f"{destination} is a file")
    joined = []
    for entry in entries:
        started = time.perf_counter()
        data = assemble(tree / entry["dir"])
        if sha256_hex(data) != entry["sha256"] or len(data) != entry["bytes"]:
            raise SplitError(f"{entry['file']}: the joined document does not match the index")
        target = destination / entry["file"]
        if target.is_dir():
            raise SplitError(f"{target} is a directory")
        joined.append((entry, data, target, _differs(target, data), time.perf_counter() - started))
    differing = [entry["file"] for entry, _, _, differs, _ in joined if differs]
    if differing and not overwrite:
        raise SplitError(f"{len(differing)} file(s) in {destination} exist with different content "
                         f"({', '.join(differing[:8])}{', …' if len(differing) > 8 else ''}); split them first "
                         "if they are newer, or pass --overwrite to replace them")
    destination.mkdir(parents=True, exist_ok=True)
    for entry, data, target, differs, seconds in joined:
        if differs or not target.is_file():
            _write_atomic(target, data)
            state = "replaced" if differs else "written"
        else:
            state = "unchanged"
        report(f"{entry['file']}: {len(data)} bytes, {state} ({seconds:.1f} s)")
    return entries


def verify_all(tree, source_dir=None):
    """Problems of a whole tree; with source_dir the tree must reproduce exactly those files."""
    tree = Path(tree)
    try:
        entries = read_index(tree)
    except SplitError as exc:
        return [str(exc)]
    problems = []
    attributes = tree / GITATTRIBUTES_NAME
    if not attributes.is_file() or attributes.read_bytes() != GITATTRIBUTES_TEXT:
        problems.append(f"{GITATTRIBUTES_NAME} is missing or not '* -text'")
    sources = {}
    if source_dir is not None:
        try:
            sources = {path.name: path for path in _source_files(source_dir)}
        except SplitError as exc:
            problems.append(str(exc))
    dirs = {entry["dir"] for entry in entries}
    for path in sorted(tree.iterdir()):
        if path.name not in {INDEX_NAME, GITATTRIBUTES_NAME} and not (path.is_dir() and path.name in dirs):
            problems.append(f"unlisted entry {path.name} in the tree")
    for entry in entries:
        directory = tree / entry["dir"]
        source = sources.get(entry["file"])
        if source_dir is not None and source is None:
            problems.append(f"{entry['file']}: no longer in the source directory")
        found = verify_document(directory, source)
        problems.extend(f"{entry['file']}: {problem}" for problem in found)
        if not found:
            document = _read_manifest(directory)["document"]
            if document["sha256"] != entry["sha256"] or document["bytes"] != entry["bytes"]:
                problems.append(f"{entry['file']}: the index does not match its manifest")
    for name in sorted(set(sources) - {entry["file"] for entry in entries}):
        problems.append(f"{name}: in the source directory but not in the tree")
    return problems


# ---------------------------------------------------------------- command line

def _add_settings(parser):
    group = parser.add_argument_group("thresholds (default: the existing manifest, else built in)")
    defaults = Settings()
    for name in SETTING_NAMES:
        group.add_argument("--" + name.replace("_", "-"), dest=name, type=int, default=None,
                           help=f"built-in default {getattr(defaults, name)}")


def _overrides(args):
    return {name: getattr(args, name) for name in SETTING_NAMES}


def _print_problems(problems):
    for problem in problems:
        print("FAIL " + problem)
    print("verify: " + ("ok" if not problems else f"{len(problems)} problem(s)"))
    return 0 if not problems else 1


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    split = commands.add_parser("split", help="split one document into a directory")
    split.add_argument("document", type=Path)
    split.add_argument("directory", type=Path)
    _add_settings(split)
    overwrite_help = "replace destination files that exist with different content (default: refuse)"
    join = commands.add_parser("join", help="join a directory back into the document")
    join.add_argument("directory", type=Path)
    join.add_argument("document", type=Path)
    join.add_argument("--overwrite", action="store_true", help=overwrite_help)
    verify = commands.add_parser("verify", help="check a directory (and optionally a document)")
    verify.add_argument("directory", type=Path)
    verify.add_argument("document", type=Path, nargs="?")
    split_many = commands.add_parser("split-all", help="split every *.json of a directory into a tree")
    split_many.add_argument("source_dir", type=Path)
    split_many.add_argument("tree", type=Path)
    split_many.add_argument("--prune", action="store_true", help="delete documents whose source was removed")
    _add_settings(split_many)
    join_many = commands.add_parser("join-all", help="join every document of a tree into a directory")
    join_many.add_argument("tree", type=Path)
    join_many.add_argument("destination", type=Path)
    join_many.add_argument("--overwrite", action="store_true", help=overwrite_help)
    verify_many = commands.add_parser("verify-all", help="check a tree (and optionally its source directory)")
    verify_many.add_argument("tree", type=Path)
    verify_many.add_argument("source_dir", type=Path, nargs="?")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if args.command == "split":
            result = split_document(args.document.read_bytes(), args.directory, _overrides(args))
            print(f"{args.document.name}: {result.files} files, written {result.written}, kept {result.kept}, "
                  f"removed {result.removed}{', manifest updated' if result.manifest_written else ''}")
            for relative in result.foreign:
                print(f"warning: unlisted file left in place: {relative}")
            return 0
        if args.command == "join":
            data = join_document(args.directory, args.document, args.overwrite)
            print(f"{args.document.name}: {len(data)} bytes, sha256 {sha256_hex(data)}")
            return 0
        if args.command == "verify":
            return _print_problems(verify_document(args.directory, args.document))
        if args.command == "split-all":
            split_all(args.source_dir, args.tree, _overrides(args), args.prune)
            return 0
        if args.command == "join-all":
            join_all(args.tree, args.destination, args.overwrite)
            return 0
        return _print_problems(verify_all(args.tree, args.source_dir))
    except (SplitError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
