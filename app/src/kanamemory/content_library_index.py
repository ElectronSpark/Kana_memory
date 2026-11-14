"""Read an extension library's teaching documents on demand (design 08 section 6.5).

A library ships each teaching namespace as one JSON document. Decoding every
document at start-up cost a large library hundreds of megabytes and seconds,
although one sitting uses a few lessons. This module walks a document once
and builds an index: the same payload (the *skeleton*) with its large parts
replaced by references to byte ranges of the document:

- the question_templates object of every row of a learning-catalogue table
  (rows keep their small fields, so the catalogue still builds from them);
- every entry of an ``items`` table (records per content id).

Start-up decodes only the skeleton. A reference is read, checked against its
sha256 and decoded when the value is first used. resolve() returns a payload
equal to the eagerly decoded one: LazyMapping and LazyTable are read-only
mappings whose deepcopy is the plain decoded value, so an independent
snapshot of an item (deepcopy) is exactly the item as before.

A part that fails its check (the document changed under its index, or cannot
be read) raises LibraryIndexError where it is read; resolve's ``damaged``
callback lets the caller record it and drop the index, so that the next start
reads the whole document and refuses it as it always did.

Pure: no file access. The caller passes the document bytes to build_index and
a reader ``(offset, length, sha256) -> bytes`` to resolve.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
import re
import threading

from .content_library_merge import CATALOGUE_TABLE_KEYS

FORMAT = "kanamemory-library-index"
# Bump whenever build_index would give another skeleton or other references for the same document: a stored
# index is trusted for the small fields once its own checksum and its document's sha256/size/time match.
VERSION = 1
ITEMS_KEY = "items"                        # per content id records (templates, dependencies, readings)
LAZY_ROW_FIELDS = ("question_templates",)  # of a catalogue row; only JSON objects are deferred
REFERENCE = "__km_lazy_slice__"            # {REFERENCE: [offset, length, sha256]} in a skeleton
_TAG = "__km_content_type__"               # content_pack's typed-value tag: such a table is never deferred
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_WHITESPACE = " \t\n\r"
_DECODER = json.JSONDecoder()
_DECODED_BUDGET = 2 * 1024 * 1024          # source bytes of the decoded values kept for repeated reads


class LibraryIndexError(ValueError):
    """The document cannot be indexed, or an index does not fit its document."""


class _Text:
    """The document text with byte offsets for positions asked in increasing order."""

    def __init__(self, raw):
        self.raw = raw
        self.text = raw.decode("utf-8")
        self._ascii = len(self.text) == len(raw)
        self._chars = self._bytes = 0

    def byte_offset(self, position):
        if self._ascii:
            return position
        if position < self._chars:
            raise LibraryIndexError("Document positions must be visited in order")
        self._bytes += len(self.text[self._chars:position].encode("utf-8"))
        self._chars = position
        return self._bytes


def _skip(text, position):
    while position < len(text) and text[position] in _WHITESPACE:
        position += 1
    return position


def _value(text, position):
    try:
        return _DECODER.raw_decode(text, position)
    except (ValueError, StopIteration, RecursionError) as error:
        raise LibraryIndexError("Invalid JSON in teaching document") from error


def _object(text, position, member):
    """Decode the JSON object at ``position``; ``member(key, start) -> (value, end)`` decodes each value.

    A repeated key keeps its last value, as json.loads does.
    """
    try:
        if text[position] != "{":
            raise LibraryIndexError("Expected a JSON object")
        result, position = {}, _skip(text, position + 1)
        if text[position] == "}":
            return result, position + 1
        while True:
            key, position = _value(text, position)
            if not isinstance(key, str):
                raise LibraryIndexError("Expected a JSON object key")
            position = _skip(text, position)
            if text[position] != ":":
                raise LibraryIndexError("Expected ':' in a JSON object")
            result[key], position = member(key, _skip(text, position + 1))
            position = _skip(text, position)
            if text[position] == "}":
                return result, position + 1
            if text[position] != ",":
                raise LibraryIndexError("Expected ',' in a JSON object")
            position = _skip(text, position + 1)
    except IndexError as error:  # ran past the end of the text
        raise LibraryIndexError("Truncated teaching document") from error


def _array(text, position, element):
    try:
        if text[position] != "[":
            raise LibraryIndexError("Expected a JSON array")
        result, position = [], _skip(text, position + 1)
        if text[position] == "]":
            return result, position + 1
        while True:
            value, position = element(position)
            result.append(value)
            position = _skip(text, position)
            if text[position] == "]":
                return result, position + 1
            if text[position] != ",":
                raise LibraryIndexError("Expected ',' in a JSON array")
            position = _skip(text, position + 1)
    except IndexError as error:
        raise LibraryIndexError("Truncated teaching document") from error


def build_index(raw, name, *, validate=None, modified_ns=None):
    """The index of one teaching namespace document (``raw``: its exact bytes).

    Raises LibraryIndexError for a document this module cannot index; the caller
    then decodes it whole, as before. The envelope (format, version, name) is
    kept as it is and stays the caller's to validate.
    ``validate`` (content_pack.decode_value) is called with every deferred
    value as decoded from JSON; with it a document is indexed only if each
    part decodes, as when it is decoded whole (the skeleton is decoded by
    resolve). ``modified_ns`` is the document file's modification time: an
    index is not used for a file written again later.
    """
    try:
        return _build_index(raw, name, validate, modified_ns)
    except LibraryIndexError:
        raise
    except (ValueError, TypeError, RecursionError) as error:
        # For example a string the index cannot store, or a part validate refuses.
        raise LibraryIndexError("Teaching document cannot be indexed") from error


def _build_index(raw, name, validate, modified_ns):
    if not isinstance(raw, (bytes, bytearray)):
        raise LibraryIndexError("Document bytes expected")
    try:
        source = _Text(bytes(raw))
    except UnicodeDecodeError as error:
        raise LibraryIndexError("Teaching document is not UTF-8") from error
    text = source.text

    def reference(start, end):
        offset = source.byte_offset(start)
        length = source.byte_offset(end) - offset
        piece = source.raw[offset:offset + length]
        if piece != text[start:end].encode("utf-8"):
            raise LibraryIndexError("Document offsets do not match its bytes")
        return {REFERENCE: [offset, length, hashlib.sha256(piece).hexdigest()]}

    def row(position):
        def member(key, start):
            if key in LAZY_ROW_FIELDS and text[start] == "{":
                value, end = _value(text, start)
                if validate is not None:
                    validate(value)
                return reference(start, end), end
            return _value(text, start)
        if text[position] != "{":
            return _value(text, position)
        value, end = _object(text, position, member)
        if _TAG in value:
            raise LibraryIndexError("Typed catalogue rows are not indexed")
        return value, end

    def catalogue_table(position):
        if text[position] == "[":
            return _array(text, position, row)
        if text[position] == "{":
            value, end = _object(text, position, lambda key, start: row(start))
            if _TAG in value:
                raise LibraryIndexError("Typed catalogue tables are not indexed")
            return value, end
        return _value(text, position)

    def item_table(position):
        def member(key, start):
            value, end = _value(text, start)
            if validate is not None:
                validate(value)
            return reference(start, end), end
        value, end = _object(text, position, member)
        if _TAG in value:
            raise LibraryIndexError("Typed item tables are not indexed")
        return value, end

    rows, tables = [], []

    def payload_member(key, position):
        if key in CATALOGUE_TABLE_KEYS and text[position] in "[{":
            rows.append(key)
            return catalogue_table(position)
        if key == ITEMS_KEY and text[position] == "{":
            tables.append(key)
            return item_table(position)
        return _value(text, position)

    def document_member(key, position):
        if key == "payload" and text[position] == "{":
            return _object(text, position, payload_member)
        return _value(text, position)

    start = _skip(text, 0)
    document, end = _object(text, start, document_member)
    if _skip(text, end) != len(text):
        raise LibraryIndexError("Unexpected data after the teaching document")
    if not isinstance(document.get("payload"), dict) or _TAG in document["payload"]:
        raise LibraryIndexError("Only a plain payload object is indexed")
    index = {"format": FORMAT, "version": VERSION, "name": name,
             "document": {"sha256": hashlib.sha256(source.raw).hexdigest(), "bytes": len(source.raw),
                          "modified_ns": modified_ns},
             # A repeated key was decoded twice; the last decoding is the value kept.
             "rows": sorted(set(rows)), "tables": sorted(set(tables)), "skeleton": document}
    index["sha256"] = _self_digest(index)
    return index


def _self_digest(index):
    body = {key: value for key, value in index.items() if key != "sha256"}
    return hashlib.sha256(_encoded(body, sort_keys=True)).hexdigest()


def _encoded(value, **options):
    # surrogatepass: JSON may hold an unpaired surrogate escape; such a string is stored and read back as it is.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), **options).encode("utf-8", "surrogatepass")


def encode_index(index):
    return _encoded(index)


def _checked_reference(value, size):
    if (not isinstance(value, dict) or set(value) != {REFERENCE} or not isinstance(value[REFERENCE], list)
            or len(value[REFERENCE]) != 3):
        raise LibraryIndexError("Invalid reference in a library index")
    offset, length, sha = value[REFERENCE]
    if (type(offset) is not int or type(length) is not int or offset < 0 or length < 1 or offset + length > size
            or not isinstance(sha, str) or not _SHA256.fullmatch(sha)):
        raise LibraryIndexError("Invalid reference in a library index")
    return offset, length, sha


def decode_index(data, *, name, sha256, size, modified_ns=None):
    """Validate stored index bytes against the document they must describe."""
    try:
        index = json.loads(data.decode("utf-8", "surrogatepass"))
    except (ValueError, UnicodeDecodeError, RecursionError) as error:
        raise LibraryIndexError("Unreadable library index") from error
    if (not isinstance(index, dict)
            or set(index) != {"format", "version", "name", "document", "rows", "tables", "skeleton", "sha256"}
            or index["format"] != FORMAT or index["version"] != VERSION or index["name"] != name
            or index["document"] != {"sha256": sha256, "bytes": size, "modified_ns": modified_ns}
            or not isinstance(index["rows"], list) or not isinstance(index["tables"], list)
            or not isinstance(index["skeleton"], dict) or not isinstance(index["skeleton"].get("payload"), dict)
            or index["sha256"] != _self_digest(index)):
        raise LibraryIndexError("Library index does not match its document")
    return index


class SharedMapping(Mapping):
    """Read-only view of a mapping the catalogue owns; deepcopy is an independent plain copy."""

    __slots__ = ("_data",)

    def __init__(self, data):
        self._data = data

    def _value(self):
        return self._data

    def plain(self):
        """An independent plain copy for a caller that changes or stores it."""
        return deepcopy(self._data)

    def __getitem__(self, key):
        return self._value()[key]

    def __iter__(self):
        return iter(self._value())

    def __len__(self):
        return len(self._value())

    def __contains__(self, key):
        return key in self._value()

    def __deepcopy__(self, memo):
        return self.plain()

    def __copy__(self):
        return self.plain()

    def __reduce__(self):
        raise TypeError("A shared teaching value is not pickled; copy it with deepcopy first")

    def __repr__(self):
        return "<shared teaching value: %d keys>" % len(self)


class _Decoded:
    """Recently decoded slices for repeated reads, bounded by their source size."""

    def __init__(self, budget):
        self._budget, self._used = budget, 0
        self._rows, self._lock = OrderedDict(), threading.Lock()

    def get(self, key):
        with self._lock:
            row = self._rows.get(key)
            if row is not None:
                self._rows.move_to_end(key)
                return row[0]
        return None

    def put(self, key, value, size):
        with self._lock:
            if key in self._rows:
                return self._rows[key][0]
            self._rows[key] = (value, size)
            self._used += size
            while self._used > self._budget and len(self._rows) > 1:
                _, (_, freed) = self._rows.popitem(last=False)
                self._used -= freed
            return value

    def clear(self):
        with self._lock:
            self._rows.clear()
            self._used = 0


_DECODED = _Decoded(_DECODED_BUDGET)
_READS = [0]  # slices read from documents in this process (diagnostics and tests)


def slice_reads():
    return _READS[0]


def clear_cache():
    _DECODED.clear()


class _Source:
    """One document's reader with the decoding its payload uses."""

    __slots__ = ("reader", "decode", "token", "damaged")

    def __init__(self, reader, decode, damaged=None):
        self.reader, self.decode, self.token, self.damaged = reader, decode, object(), damaged

    def fresh(self, reference):
        offset, length, sha = reference
        try:
            raw = self.reader(offset, length, sha)
            if len(raw) != length or hashlib.sha256(raw).hexdigest() != sha:
                raise LibraryIndexError("Library teaching document changed after it was indexed")
            _READS[0] += 1
            return self.decode(json.loads(raw.decode("utf-8")))
        except (OSError, ValueError, RecursionError) as error:
            self._report(error)
            if isinstance(error, LibraryIndexError):
                raise
            raise LibraryIndexError("Library teaching document part cannot be read") from error

    def _report(self, error):
        damaged, self.damaged = self.damaged, None  # once per document
        if damaged is not None:
            try:
                damaged(error)
            except Exception:  # noqa: BLE001 - reporting never replaces the read error itself
                pass

    def shared(self, reference):
        key = (self.token, reference[0])
        value = _DECODED.get(key)
        if value is None:
            value = _DECODED.put(key, self.fresh(reference), reference[1])
        return value


class LazyMapping(SharedMapping):
    """A JSON object of a library document, decoded when first read.

    Reads share one decoded value (read-only, like every teaching table);
    deepcopy / plain() decode the bytes again, so the copy is independent.
    """

    __slots__ = ("_source", "_reference")

    def __init__(self, source, reference):
        self._source, self._reference = source, tuple(reference)

    def _value(self):
        value = self._source.shared(self._reference)
        if not isinstance(value, Mapping):
            raise LibraryIndexError("Library teaching document slice is not an object")
        return value

    def plain(self):
        value = self._source.fresh(self._reference)
        if not isinstance(value, Mapping):
            raise LibraryIndexError("Library teaching document slice is not an object")
        return value

    def content_sha256(self):
        """The sha256 of the document bytes this value is decoded from (known without reading them)."""
        return self._reference[2]

    def __repr__(self):
        return "<teaching value read on demand: %d bytes at %d>" % (self._reference[1], self._reference[0])


class LazyTable(Mapping):
    """An ``items`` table of a library document: content id -> record, decoded per id.

    Values are shared read-only objects, as for the core pack's shards;
    deepcopy returns the plain table.
    """

    __slots__ = ("_source", "_references")

    def __init__(self, source, references):
        self._source, self._references = source, references

    def __getitem__(self, key):
        try:
            reference = self._references[key]
        except (KeyError, TypeError):
            raise KeyError(key) from None
        return self._source.shared(reference)

    def __iter__(self):
        return iter(self._references)

    def __len__(self):
        return len(self._references)

    def __contains__(self, key):
        try:
            return key in self._references
        except TypeError:
            return False

    def __deepcopy__(self, memo):
        return {key: self._source.fresh(reference) for key, reference in self._references.items()}

    def __copy__(self):
        return {key: self[key] for key in self._references}

    def __repr__(self):
        return "<teaching table read on demand: %d ids>" % len(self._references)


def resolve(index, reader, decode, damaged=None):
    """The namespace payload of a validated index, large parts read through ``reader``.

    ``decode`` is content_pack.decode_value: applied to the skeleton and to
    every slice, exactly as it is applied to the whole document otherwise.
    ``damaged(error)`` is called once, before the first LibraryIndexError a
    failing part raises. Returns (envelope without payload, payload).
    """
    size = index["document"]["bytes"]
    skeleton = index["skeleton"]
    source = _Source(reader, decode, damaged)
    raw_payload = skeleton["payload"]
    references = {}
    # Take the references out first: decode() accepts only JSON content values.
    plain = dict(raw_payload)
    for key in index["tables"]:
        table = raw_payload.get(key)
        if not isinstance(table, dict):
            raise LibraryIndexError("Library index table is missing")
        references[key] = {entry: _checked_reference(value, size) for entry, value in table.items()}
        plain[key] = {}
    row_references = {}
    for key in index["rows"]:
        table = raw_payload.get(key)
        rows = table.values() if isinstance(table, dict) else table if isinstance(table, list) else None
        if rows is None:
            raise LibraryIndexError("Library index rows are missing")
        cleaned, found = [], []
        for row in rows:
            fields = {}
            if isinstance(row, dict):
                for field in LAZY_ROW_FIELDS:
                    if isinstance(row.get(field), dict):
                        fields[field] = _checked_reference(row[field], size)
                if fields:
                    row = {name: (None if name in fields else value) for name, value in row.items()}
            cleaned.append(row)
            found.append(fields)
        plain[key] = dict(zip(table, cleaned)) if isinstance(table, dict) else cleaned
        row_references[key] = found
    payload = decode(plain)
    if not isinstance(payload, dict):
        raise LibraryIndexError("Library index payload is not an object")
    for key, table in references.items():
        payload[key] = LazyTable(source, table)
    for key, found in row_references.items():
        table = payload[key]
        rows = list(table.values()) if isinstance(table, dict) else table
        for row, fields in zip(rows, found):
            for field, reference in fields.items():
                row[field] = LazyMapping(source, reference)
    envelope = {key: value for key, value in skeleton.items() if key != "payload"}
    return envelope, payload


def digest_default(value):
    """``default=`` for json.dumps when the text is only hashed (a cache key), never stored or parsed.

    A shared mapping is encoded as the mapping it stands for, so plain data
    and its catalogue view give the same text; a deferred value is encoded
    by the checksum of its bytes instead of being read.
    """
    if isinstance(value, LazyMapping):
        return {REFERENCE: value.content_sha256()}
    if isinstance(value, SharedMapping):
        return dict(value)
    raise TypeError("Object of type %s is not JSON serializable" % type(value).__name__)


_ATOMS = frozenset((str, int, float, bool, type(None)))


def isolated_copy(value):
    """An independent copy that keeps values read on demand as they are.

    deepcopy would decode every deferred value. Teaching payloads are plain
    JSON-like trees (no shared sub-objects), for which this equals deepcopy.
    """
    if type(value) in _ATOMS:
        return value  # immutable: deepcopy returns the same object, after a call per value
    if isinstance(value, dict):
        return {key: isolated_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [isolated_copy(item) for item in value]
    if isinstance(value, tuple):
        return tuple(isolated_copy(item) for item in value)
    if isinstance(value, (SharedMapping, LazyTable)):
        return value
    return deepcopy(value)
