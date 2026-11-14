"""content_library_index: a library document read on demand equals the document decoded whole (design 08 section 6.5)."""
from copy import deepcopy
import hashlib
import json

import pytest

from kanamemory import content_library_index as index_module
from kanamemory.content_library_index import (LazyMapping, LazyTable, LibraryIndexError, SharedMapping, build_index,
                                              decode_index, encode_index, isolated_copy, resolve)
from kanamemory.content_pack import decode_value, encode_value

NAME = "vocabulary_catalog"


def _templates(seed):
    return {"vocab_meaning": [{"prompt": "猫" * (seed + 1), "task": "意思是？", "answer": "猫", "choices": ["猫", "狗", "鸟"],
                               "readings": {"猫": "ねこ"}, "weight": 1.5, "flags": [True, False, None]}],
            "vocab_reading": [{"prompt": "ね\"こ\\", "answer": "neko", "n": seed}]}


def _payload():
    return {
        "WORD_LESSONS": [
            {"id": "word:test:neko", "family": "word", "title": "猫", "reading": "ねこ", "depends_on": [],
             "question_templates": _templates(0), "instruction": {"summary": "猫。"}},
            {"id": "word:test:inu", "family": "word", "title": "犬", "question_templates": _templates(1),
             "depends_on": ["word:test:neko"]},
            {"id": "word:test:none", "family": "word", "title": "無", "question_templates": None},
            {"id": "word:test:bare", "family": "word", "title": "裸"},
            "not a row",
        ],
        "_GRAMMAR": {"grammar:test:wa": {"id": "grammar:test:wa", "family": "grammar", "title": "は",
                                         "question_templates": {"grammar_cloze": [{"prompt": "これ（　）本です。"}]}}},
        "items": {"word:test:neko": {"vocab_meaning": {"a" * 64: {"words": ["word:test:inu"], "oov": []}}},
                  "word:test:inu": {"vocab_meaning": {}}, "scalar": 7, "listed": [1, 2, 3]},
        "REVISION": "r1", "SOURCES": ("a", "b"), "TOPICS": {"动物": {"猫", "犬"}}, "empty": {}, "numbers": [0, -1, 2.5],
    }


def _document(payload=None, **dump):
    document = {"format": "kanamemory-teaching-namespace", "version": 1, "name": NAME,
                "payload": encode_value(_payload() if payload is None else payload)}
    dump.setdefault("ensure_ascii", False)
    return json.dumps(document, **dump).encode("utf-8")


def _reader(raw, calls=None):
    def read(offset, length, sha):
        if calls is not None:
            calls.append((offset, length))
        return raw[offset:offset + length]
    return read


def _eager(raw):
    return decode_value(json.loads(raw.decode("utf-8"))["payload"])


@pytest.mark.parametrize("dump", [{}, {"indent": 1}, {"ensure_ascii": True}, {"separators": (",", ":")},
                                  {"indent": "\t", "ensure_ascii": True}])
def test_a_resolved_payload_equals_the_decoded_document(dump):
    raw = _document(**dump)
    index = build_index(raw, NAME)
    envelope, payload = resolve(index, _reader(raw), decode_value)
    assert envelope == {"format": "kanamemory-teaching-namespace", "version": 1, "name": NAME}
    assert isinstance(payload["WORD_LESSONS"][0]["question_templates"], LazyMapping)
    assert isinstance(payload["_GRAMMAR"]["grammar:test:wa"]["question_templates"], LazyMapping)
    assert isinstance(payload["items"], LazyTable)
    assert payload["WORD_LESSONS"][2]["question_templates"] is None and "question_templates" not in payload["WORD_LESSONS"][3]
    assert payload["WORD_LESSONS"][4] == "not a row"
    eager = _eager(raw)
    assert payload == eager and eager == payload            # read through the mappings
    assert deepcopy(payload) == eager                         # and as plain independent data
    plain = deepcopy(payload)
    assert type(plain["WORD_LESSONS"][0]["question_templates"]) is dict and type(plain["items"]) is dict
    assert list(payload["items"]) == list(eager["items"]) and len(payload["items"]) == 4
    assert json.dumps(plain, ensure_ascii=False, sort_keys=True, default=sorted) == json.dumps(
        eager, ensure_ascii=False, sort_keys=True, default=sorted)


def test_an_index_survives_storage_and_is_bound_to_its_document():
    raw = _document(indent=1)
    index = build_index(raw, NAME)
    sha, size = hashlib.sha256(raw).hexdigest(), len(raw)
    stored = encode_index(index)
    assert decode_index(stored, name=NAME, sha256=sha, size=size) == index
    assert len(stored) < len(raw)  # the templates and the item records are not in it
    for wrong in ({"name": "grammar_curriculum"}, {"sha256": "0" * 64}, {"size": size + 1}):
        with pytest.raises(LibraryIndexError):
            decode_index(stored, **{"name": NAME, "sha256": sha, "size": size, **wrong})
    tampered = json.loads(stored)
    tampered["skeleton"]["payload"]["REVISION"] = "r2"
    for data in (json.dumps(tampered).encode(), stored[:-9], b"\xff\xfe", b"[]"):
        with pytest.raises(LibraryIndexError):
            decode_index(data, name=NAME, sha256=sha, size=size)
    # A reference that points outside the document is refused before anything is read.
    broken = json.loads(stored)
    broken["skeleton"]["payload"]["items"]["scalar"] = {index_module.REFERENCE: [size - 1, 5, "0" * 64]}
    broken["sha256"] = index_module._self_digest(broken)
    with pytest.raises(LibraryIndexError):
        resolve(decode_index(json.dumps(broken).encode(), name=NAME, sha256=sha, size=size), _reader(raw), decode_value)


def test_nothing_is_read_before_it_is_used_and_copies_are_independent():
    raw, calls = _document(), []
    index_module.clear_cache()
    _, payload = resolve(build_index(raw, NAME), _reader(raw, calls), decode_value)
    rows = payload["WORD_LESSONS"]
    assert [row["id"] for row in rows[:4]] and rows[1]["depends_on"] == ["word:test:neko"] and calls == []
    assert "word:test:neko" in payload["items"] and len(payload["items"]) == 4 and calls == []
    assert rows[0]["question_templates"]["vocab_meaning"][0]["answer"] == "猫" and len(calls) == 1
    assert list(rows[0]["question_templates"]) == ["vocab_meaning", "vocab_reading"] and len(calls) == 1  # shared read
    assert payload["items"]["scalar"] == 7 and payload["items"]["listed"] == [1, 2, 3] and len(calls) == 3
    assert payload["items"]["scalar"] == 7 and len(calls) == 3
    copy = deepcopy(rows[0])
    assert type(copy["question_templates"]) is dict and len(calls) == 4       # decoded again: nothing is shared
    copy["question_templates"]["vocab_meaning"][0]["answer"] = "changed"
    assert rows[0]["question_templates"]["vocab_meaning"][0]["answer"] == "猫"
    assert rows[0]["question_templates"].plain() == _templates(0)
    kept = isolated_copy(rows)
    assert kept == rows and kept is not rows and kept[0] is not rows[0]
    assert kept[0]["question_templates"] is rows[0]["question_templates"]      # still deferred
    with pytest.raises(TypeError):
        json.dumps(rows[1])  # a deferred value is never serialized by accident; copy the item first
    with pytest.raises(TypeError):
        rows[1]["question_templates"]["vocab_meaning"] = []
    assert "missing" not in payload["items"] and payload["items"].get("missing") is None and [] not in payload["items"]
    with pytest.raises(KeyError):
        payload["items"]["missing"]


def test_a_document_changed_after_indexing_is_noticed_when_the_part_is_read():
    raw = _document()
    index = build_index(raw, NAME)
    changed = raw.replace("ねこ".encode("utf-8"), "ねご".encode("utf-8"))
    assert len(changed) == len(raw)
    index_module.clear_cache()
    _, payload = resolve(index, _reader(changed), decode_value)
    assert payload["WORD_LESSONS"][3]["title"] == "裸"  # the small fields come from the index
    with pytest.raises(LibraryIndexError):
        payload["WORD_LESSONS"][0]["question_templates"]["vocab_meaning"]
    with pytest.raises(LibraryIndexError):
        deepcopy(payload["WORD_LESSONS"][0])
    _, short = resolve(index, lambda offset, length, sha: raw[offset:offset + length - 1], decode_value)
    with pytest.raises(LibraryIndexError):
        short["items"]["scalar"]


def test_a_failing_part_is_reported_once_and_always_raises_the_index_error():
    raw = _document()
    index = build_index(raw, NAME)
    reports = []
    index_module.clear_cache()
    _, payload = resolve(index, _reader(raw.replace("ねこ".encode("utf-8"), "ねご".encode("utf-8"))), decode_value,
                         reports.append)
    for _ in range(2):
        with pytest.raises(LibraryIndexError):
            payload["WORD_LESSONS"][0]["question_templates"]["vocab_meaning"]
    assert len(reports) == 1 and isinstance(reports[0], LibraryIndexError)

    def unreadable(offset, length, sha):
        raise PermissionError("gone")
    seen = []
    _, payload = resolve(index, unreadable, decode_value, seen.append)
    with pytest.raises(LibraryIndexError):  # a file error is not left for callers that expect a ValueError
        payload["items"]["scalar"]
    with pytest.raises(LibraryIndexError):
        deepcopy(payload["items"])
    assert len(seen) == 1 and isinstance(seen[0], PermissionError)

    def refusing(error):
        raise RuntimeError("the reporter itself failed")
    _, payload = resolve(index, unreadable, decode_value, refusing)
    with pytest.raises(LibraryIndexError):
        payload["items"]["scalar"]


def test_unpaired_surrogate_escapes_are_stored_and_read_back():
    raw = (b'{"format": "kanamemory-teaching-namespace", "version": 1, "name": "vocabulary_catalog", "payload": '
           b'{"WORD_LESSONS": [{"id": "w", "title": "\\ud800", "question_templates": {"k": ["\\udfff", "\\ud83d\\ude00"]}}],'
           b' "items": {"w": {"note": "\\ud800x"}}, "NOTE": "\\udc00"}}')
    eager = _eager(raw)
    assert eager["WORD_LESSONS"][0]["title"] == "\ud800"
    index = build_index(raw, NAME, modified_ns=5)
    stored = encode_index(index)
    again = decode_index(stored, name=NAME, sha256=hashlib.sha256(raw).hexdigest(), size=len(raw), modified_ns=5)
    assert again == index
    _, payload = resolve(again, _reader(raw), decode_value)
    assert deepcopy(payload) == eager and payload["NOTE"] == "\udc00"


def test_the_index_names_the_documents_modification_time():
    raw = _document()
    sha, size = hashlib.sha256(raw).hexdigest(), len(raw)
    stored = encode_index(build_index(raw, NAME, modified_ns=1234567890123456789))
    assert decode_index(stored, name=NAME, sha256=sha, size=size, modified_ns=1234567890123456789)
    for other in (None, 1234567890123456790):
        with pytest.raises(LibraryIndexError):  # the file was written again: its index is not used
            decode_index(stored, name=NAME, sha256=sha, size=size, modified_ns=other)


def test_validate_sees_every_deferred_part_and_may_refuse_the_document():
    raw, seen = _document(), []
    build_index(raw, NAME, validate=seen.append)
    eager_json = json.loads(raw.decode("utf-8"))["payload"]
    expected = [row["question_templates"] for row in eager_json["WORD_LESSONS"][:2]]
    expected.append(eager_json["_GRAMMAR"]["grammar:test:wa"]["question_templates"])
    expected.extend(eager_json["items"].values())
    assert sorted(map(repr, seen)) == sorted(map(repr, expected))
    nan = _document().replace(b"1.5", b"NaN")
    assert nan != _document()
    build_index(nan, NAME)  # JSON accepts it; only the content decoder refuses it ...
    with pytest.raises(LibraryIndexError):
        build_index(nan, NAME, validate=decode_value)  # ... which the validated build finds at once


def test_the_decoded_cache_is_bounded(monkeypatch):
    raw, calls = _document(), []
    monkeypatch.setattr(index_module, "_DECODED", index_module._Decoded(1))  # room for one value
    _, payload = resolve(build_index(raw, NAME), _reader(raw, calls), decode_value)
    first, second = (row["question_templates"] for row in payload["WORD_LESSONS"][:2])
    assert first["vocab_reading"] and first["vocab_reading"] and len(calls) == 1
    assert second["vocab_reading"] and len(calls) == 2
    assert first["vocab_reading"] and len(calls) == 3  # evicted, read again: the same value
    assert first == _templates(0) and second == _templates(1)


@pytest.mark.parametrize("payload", [
    {"WORD_LESSONS": ({"id": "word:test:a", "question_templates": {"k": []}},)},      # a typed (tuple) table
    {"items": frozenset({"a"})},
])
def test_typed_tables_are_left_to_the_whole_document_decoder(payload):
    raw = _document(payload)
    try:
        index = build_index(raw, NAME)
    except LibraryIndexError:
        return  # the caller decodes the document whole
    _, resolved = resolve(index, _reader(raw), decode_value)
    assert deepcopy(resolved) == _eager(raw)


@pytest.mark.parametrize("raw", [
    b"", b"[]", b"{", b'{"payload": 1}', b'{"payload": {"items": {"a": 1}}} x', b'{"payload": {"items": {"a": }}}',
    b'{"payload": {"WORD_LESSONS": [{"id": "a", "question_templates": {"k": [}]}}', b"\xff\xfe{}",
    b'{"payload": {"__km_content_type__": "tuple", "items": []}}', b'{"payload": {"WORD_LESSONS": [1, 2', b'{"a" 1}',
])
def test_documents_that_cannot_be_indexed_are_refused(raw):
    with pytest.raises(LibraryIndexError):
        build_index(raw, NAME)
    with pytest.raises(LibraryIndexError):
        build_index("text", NAME)


def test_a_repeated_key_keeps_its_last_value_like_the_whole_decoder():
    raw = (b'{"format": "kanamemory-teaching-namespace", "version": 1, "name": "vocabulary_catalog", "payload": '
           b'{"items": {"a": {"n": 1}, "a": {"n": 2}}, "items": {"b": {"n": 3}, "b": {"n": 4}},'
           b' "WORD_LESSONS": [{"id": "w", "question_templates": {"k": [1]}, "question_templates": {"k": [2]}}]}}')
    _, payload = resolve(build_index(raw, NAME), _reader(raw), decode_value)
    assert deepcopy(payload) == _eager(raw) == {"items": {"b": {"n": 4}},
                                                "WORD_LESSONS": [{"id": "w", "question_templates": {"k": [2]}}]}


def test_a_hash_of_views_equals_the_hash_of_plain_data_and_reads_no_deferred_value():
    plain = {"id": "w", "question_templates": {"kind": [{"prompt": "これ", "n": 1}]}, "title": "語"}
    view = {**plain, "question_templates": SharedMapping(plain["question_templates"])}
    dump = lambda value: json.dumps(value, ensure_ascii=False, sort_keys=True, default=index_module.digest_default)  # noqa: E731
    assert dump(view) == dump(plain) == json.dumps(plain, ensure_ascii=False, sort_keys=True)
    raw, calls = _document(), []
    _, payload = resolve(build_index(raw, NAME), _reader(raw, calls), decode_value)
    rows = payload["WORD_LESSONS"]
    first, second = dump(rows[0]), dump(rows[1])
    assert calls == [] and first != second                      # hashed by the checksum of their bytes
    assert rows[0]["question_templates"].content_sha256() in first and len(rows[0]["question_templates"].content_sha256()) == 64
    assert dump(rows[0]) == first
    with pytest.raises(TypeError):
        dump({"other": object()})


def test_a_shared_mapping_is_read_only_and_copies_to_plain_data():
    data = {"kind": [{"prompt": "これ"}]}
    shared = SharedMapping(data)
    assert shared == data and data == shared and dict(shared) == data and len(shared) == 1 and "kind" in shared
    assert shared["kind"] is data["kind"]  # reads share the catalogue's value
    copy = deepcopy({"question_templates": shared})["question_templates"]
    assert type(copy) is dict and copy == data and copy["kind"] is not data["kind"]
    assert isolated_copy([shared])[0] is shared
    with pytest.raises(TypeError):
        shared["other"] = 1
    with pytest.raises(TypeError):
        json.dumps(shared)
    assert isolated_copy({"a": [1, (2, {"b": {3}})]}) == {"a": [1, (2, {"b": {3}})]}
