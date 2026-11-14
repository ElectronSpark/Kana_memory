"""The split/join tool must reproduce any JSON document byte for byte and update a tree incrementally.

All documents here are synthetic; they only exercise formatting, structure and file handling.
"""
import hashlib
import json
import os

import pytest

import content_source_split as css

# Thresholds that split even tiny synthetic documents.
SMALL = {"min_document_bytes": 0, "min_table_bytes": 0, "min_members": 2, "chunk_elements": 2,
         "max_inline_bytes": 64}


def namespace(items, rows=None, extra=None, indent=None, separators=None):
    payload = {"schema_version": 1, "items": items}
    if rows is not None:
        payload["rows"] = rows
    payload.update(extra or {})
    document = {"format": "synthetic-namespace", "version": 1, "name": "demo", "payload": payload}
    return json.dumps(document, ensure_ascii=False, indent=indent, separators=separators).encode("utf-8")


def items(count, prefix="item"):
    return {f"{prefix}:{n}": {"text": f"value {n}", "list": [n, n + 1]} for n in range(count)}


def round_trip(tmp_path, data, overrides=SMALL, name="doc"):
    directory = tmp_path / name
    result = css.split_document(data, directory, overrides)
    joined = css.join_document(directory, tmp_path / f"{name}.joined.json")
    assert joined == data
    assert (tmp_path / f"{name}.joined.json").read_bytes() == data
    assert css.verify_document(directory, tmp_path / f"{name}.joined.json") == []
    return directory, result


def mtimes(directory):
    return {path: path.stat().st_mtime_ns for path in directory.rglob("*") if path.is_file()}


def manifest(directory):
    return json.loads((directory / css.MANIFEST_NAME).read_text("utf-8"))


DOCUMENTS = {
    "compact": namespace(items(5), separators=(",", ":")),
    "indented": namespace(items(5), rows=list(range(7)), indent=2),
    "crlf": namespace(items(5), rows=list(range(7)), indent=4).replace(b"\n", b"\r\n") + b"\r\n",
    "odd spacing": b' \n{ "payload" :\t{ "a" : { "x" : 1 , "y":[ 1 ,2 ] } , "b" : [ 1 , 2 , 3 ] } }\n\n',
    "non-ascii": namespace({"語:一": {"読み": "いち"}, "語:二": {"読み": "に"}, "語:三": "さん"}, indent=1),
    "escaped keys": (b'{"payload":{"t":{"q\\"uote":1,"back\\\\slash":2,"\\u8a9e":3,"\\ud83d\\ude00":4,'
                     b'"\\/":5,"lone\\ud800":6}}}'),
    "escaped values": b'{"payload":{"t":{"a":"\\u00e9\\n\\t\\"","b":"\\ud83d\\ude00","c":"caf\xc3\xa9"}}}',
    "nested": namespace({"a": {"b": {"c": [{"d": [1, {"e": None}]}]}}, "f": [[[]]], "g": True}, indent=3),
    "empty tables": b'{"payload":{"a":{},"b":[],"c":{ },"d":[\n]}}',
    "not an object": b'[{"a":1},{"b":2},3]\n',
    "scalar": b'  "just a string"  ',
    "no payload": b'{"items":{"a":1,"b":2,"c":3},"rows":[1,2,3,4,5],"name":"x"}',
    "payload not an object": b'{"payload":[1,2,3,4,5]}',
    "numbers": b'{"payload":{"t":{"a":-0.0,"b":1e400,"c":12345678901234567890,"d":1E-5}}}',
    "no trailing newline": namespace(items(3), indent=2).rstrip(b"\n"),
}


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_round_trip_is_byte_identical_for_any_formatting(tmp_path, name):
    round_trip(tmp_path, DOCUMENTS[name])


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_round_trip_with_default_thresholds_keeps_small_documents_whole(tmp_path, name):
    directory, result = round_trip(tmp_path, DOCUMENTS[name], overrides=None)
    pieces = [part for part in manifest(directory)["parts"] if "file" in part]
    assert all(part["file"].startswith("text-") for part in pieces)
    assert len(manifest(directory)["parts"]) == 1


def test_member_piece_holds_exactly_the_value_bytes_and_the_key_stays_in_the_manifest(tmp_path):
    data = DOCUMENTS["escaped keys"]
    directory, _ = round_trip(tmp_path, data)
    parts = manifest(directory)["parts"]
    by_key = {part["key"]: part for part in parts if "key" in part}
    assert set(by_key) == {'q"uote', "back\\slash", "語", "😀", "/", "lone\ud800"}
    assert (directory / by_key['q"uote']["file"]).read_bytes() == b"1"
    expected = hashlib.sha256("語".encode()).hexdigest()[:16]
    assert by_key["語"]["file"] == f"t/{expected[:2]}/{expected[2:]}.json"
    literal = "".join(part["text"] for part in parts if "text" in part)
    assert '"q\\"uote":' in literal and '"\\u8a9e":' in literal


def test_chunks_cover_consecutive_elements(tmp_path):
    data = DOCUMENTS["no payload"]
    directory, _ = round_trip(tmp_path, data)
    chunks = [part for part in manifest(directory)["parts"] if part.get("file", "").endswith(".chunk")]
    assert [(part["file"], part["first"], part["count"]) for part in chunks] == [
        ("@rows/00000.chunk", 0, 2), ("@rows/00001.chunk", 2, 2), ("@rows/00002.chunk", 4, 1)]
    assert (directory / "@rows/00000.chunk").read_bytes() == b"1,2"


def test_unsafe_and_reserved_table_names_get_digest_directories(tmp_path):
    data = b'{"payload":{"con":{"a":1,"b":2},"\xe8\xa1\xa8":{"a":1,"b":2},"ok_name":{"a":1,"b":2}}}'
    directory, _ = round_trip(tmp_path, data)
    tops = {part["file"].split("/")[0] for part in manifest(directory)["parts"] if "key" in part}
    assert "ok_name" in tops and "con" not in tops
    assert sum(top.startswith("h-") for top in tops) == 2


def test_duplicate_keys_in_a_split_table_are_refused_but_kept_verbatim_elsewhere(tmp_path):
    duplicate = b'{"payload":{"t":{"a":1,"b":2,"a":3}}}'
    with pytest.raises(css.SplitError, match="duplicate key 'a'"):
        css.split_document(duplicate, tmp_path / "dup", SMALL)
    assert not (tmp_path / "dup").exists()
    # Below the thresholds the table is literal text, so the duplicate is stored as written.
    round_trip(tmp_path, duplicate, overrides={**SMALL, "min_members": 10}, name="kept")
    with pytest.raises(css.SplitError, match="duplicate key 'payload'"):
        css.split_document(b'{"payload":{},"payload":{}}', tmp_path / "dup2", SMALL)


@pytest.mark.parametrize("data", [b"", b"   ", b'{"a":1,}', b'{"a" 1}', b'{"a":1} x', b'[1,2',
                                  b'{"a":NaN}', b'{"payload":{"t":[1,Infinity]}}', b'\xef\xbb\xbf{}',
                                  b'{"a":"\xff"}', b'{"a\x01":1}', b'{"a":tru}', b"{'a':1}"])
def test_invalid_documents_are_refused_and_nothing_is_written(tmp_path, data):
    with pytest.raises(css.SplitError):
        css.split_document(data, tmp_path / "bad", SMALL)
    assert not (tmp_path / "bad").exists()


def test_second_split_writes_nothing_and_keeps_mtimes(tmp_path):
    data = namespace(items(20), rows=list(range(9)), indent=2)
    directory, first = round_trip(tmp_path, data)
    assert first.written == first.files > 20 and first.manifest_written
    before = mtimes(directory)
    again = css.split_document(data, directory, SMALL)
    assert (again.written, again.removed, again.manifest_written) == (0, 0, False)
    assert again.kept == first.files
    assert mtimes(directory) == before


def test_changed_appended_deleted_and_reordered_members_touch_only_their_pieces(tmp_path):
    inline = {**SMALL, "max_inline_bytes": 4096}  # every literal in the manifest: only member files
    base = items(10)
    directory, _ = round_trip(tmp_path, namespace(base, indent=2), overrides=inline)
    piece_of = {part["key"]: directory / part["file"] for part in manifest(directory)["parts"] if "key" in part}
    before = mtimes(directory)

    changed = dict(base, **{"item:3": {"text": "edited"}})
    result = css.split_document(namespace(changed, indent=2), directory, inline)
    assert (result.written, result.removed) == (1, 0)
    assert piece_of["item:3"].read_bytes() == b'{\n        "text": "edited"\n      }'
    after = mtimes(directory)
    assert {path for path in before if before[path] != after[path]} == {piece_of["item:3"], directory / "manifest.json"}

    appended = dict(changed, **{"item:new": 7})
    result = css.split_document(namespace(appended, indent=2), directory, inline)
    assert (result.written, result.kept, result.removed) == (1, 10, 0)

    deleted = {key: value for key, value in appended.items() if key != "item:5"}
    result = css.split_document(namespace(deleted, indent=2), directory, inline)
    assert (result.written, result.removed) == (0, 1)
    assert not piece_of["item:5"].exists()

    reordered = dict(reversed(list(deleted.items())))
    data = namespace(reordered, indent=2)
    before = mtimes(directory)
    result = css.split_document(data, directory, inline)
    assert (result.written, result.removed, result.manifest_written) == (0, 0, True)
    assert {p: t for p, t in mtimes(directory).items() if p.name != "manifest.json"} == \
        {p: t for p, t in before.items() if p.name != "manifest.json"}
    assert css.assemble(directory) == data


def test_appending_to_a_chunked_array_rewrites_only_the_last_chunk(tmp_path):
    rows = [{"n": n} for n in range(7)]
    directory, _ = round_trip(tmp_path, namespace({}, rows=rows, indent=2))
    before = mtimes(directory)
    result = css.split_document(namespace({}, rows=rows + [{"n": 7}, {"n": 8}], indent=2), directory, SMALL)
    assert (result.written, result.removed) == (2, 0)
    changed = sorted(path.name for path in mtimes(directory) if before.get(path) != mtimes(directory)[path])
    assert changed == ["00003.chunk", "00004.chunk", "manifest.json"]


def test_verify_reports_edited_stray_missing_and_truncated(tmp_path):
    data = namespace(items(6), indent=2)
    directory, _ = round_trip(tmp_path, data)
    source = tmp_path / "source.json"
    source.write_bytes(data)
    piece = directory / next(part["file"] for part in manifest(directory)["parts"] if "key" in part)
    original = piece.read_bytes()

    piece.write_bytes(original + b" ")
    assert any("differs from its sha256" in problem for problem in css.verify_document(directory, source))
    with pytest.raises(css.SplitError, match="does not match its sha256"):
        css.join_document(directory, tmp_path / "out.json")
    assert not (tmp_path / "out.json").exists()
    piece.write_bytes(original)
    assert css.verify_document(directory, source) == []

    (directory / "stray.txt").write_bytes(b"x")
    assert css.verify_document(directory) == ["unlisted file stray.txt"]
    (directory / "stray.txt").unlink()

    piece.unlink()
    assert any(problem.startswith("missing piece") for problem in css.verify_document(directory))
    piece.write_bytes(original)

    source.write_bytes(data + b"\n")
    assert css.verify_document(directory, source) == ["joining would not reproduce source.json"]

    raw = (directory / "manifest.json").read_bytes()
    (directory / "manifest.json").write_bytes(raw[: len(raw) // 2])
    assert "not readable JSON" in css.verify_document(directory)[0]
    with pytest.raises(css.SplitError):
        css.assemble(directory)


def test_manifest_paths_outside_the_tree_are_refused(tmp_path):
    directory, _ = round_trip(tmp_path, namespace(items(3)))
    raw = json.loads((directory / "manifest.json").read_text("utf-8"))
    part = next(part for part in raw["parts"] if "file" in part)
    part["file"] = "../outside.json"
    (directory / "manifest.json").write_text(json.dumps(raw), "utf-8")
    with pytest.raises(css.SplitError, match="invalid file path"):
        css.assemble(directory)


def colliding_keys(digits):
    seen = {}
    for n in range(100000):
        key = f"k{n}"
        name = hashlib.sha256(key.encode()).hexdigest()[:digits]
        if name in seen:
            return seen[name], key
        seen[name] = key
    raise AssertionError("no collision found")


def test_file_name_collision_is_refused_and_more_digits_resolve_it(tmp_path):
    first, second = colliding_keys(3)
    data = json.dumps({"payload": {"t": {first: 1, second: 2}}}).encode()
    with pytest.raises(css.SplitError, match="file name collision"):
        css.split_document(data, tmp_path / "c", {**SMALL, "name_digits": 3})
    round_trip(tmp_path, data, overrides={**SMALL, "name_digits": 12})


def test_thresholds_decide_what_becomes_a_piece(tmp_path):
    data = namespace(items(5), rows=list(range(5)), indent=2)
    kinds = {}
    for label, overrides in {"members": {**SMALL, "min_members": 6}, "chunks": {**SMALL, "chunk_elements": 5},
                             "bytes": {**SMALL, "min_table_bytes": 10 ** 6},
                             "document": {**SMALL, "min_document_bytes": len(data) + 1}}.items():
        directory, _ = round_trip(tmp_path, data, overrides=overrides, name=label)
        kinds[label] = sorted({("chunk" if p["file"].endswith(".chunk") else "member" if "key" in p else "text")
                               for p in manifest(directory)["parts"] if "file" in p})
    assert kinds == {"members": ["chunk", "text"], "chunks": ["member", "text"], "bytes": ["text"],
                     "document": ["text"]}
    with pytest.raises(css.SplitError, match="chunk_elements"):
        css.resolve_settings({"chunk_elements": 0})


def test_resplit_reuses_the_settings_of_the_manifest(tmp_path):
    data = namespace(items(5), indent=2)
    directory, first = round_trip(tmp_path, data, overrides={**SMALL, "name_digits": 10})
    again = css.split_document(data, directory, None)
    assert (again.written, again.manifest_written) == (0, False)
    assert manifest(directory)["settings"]["name_digits"] == 10
    changed = css.split_document(data, directory, {"name_digits": 20})
    assert changed.written == 5 and changed.removed == 5


def test_inline_text_limit_moves_long_literals_into_text_files(tmp_path):
    data = namespace(items(3), extra={"notes": "n" * 300}, indent=2)
    directory, _ = round_trip(tmp_path, data, overrides={**SMALL, "max_inline_bytes": 100})
    texts = [p for p in manifest(directory)["parts"] if p.get("file", "").startswith("text-")]
    assert texts and all(p["bytes"] > 100 for p in texts)
    assert all(len(p["text"].encode()) <= 100 for p in manifest(directory)["parts"] if "text" in p)


def test_interrupted_split_is_detected_and_the_next_split_repairs_it(tmp_path, monkeypatch):
    old = namespace(items(6), indent=2)
    directory, _ = round_trip(tmp_path, old)
    new = namespace(dict(items(6), **{"item:2": "changed", "item:9": 9}), indent=2)
    real_write = css._write_atomic

    def crash_on_manifest(path, data):
        if path.name == css.MANIFEST_NAME:
            raise OSError("simulated crash")
        real_write(path, data)

    monkeypatch.setattr(css, "_write_atomic", crash_on_manifest)
    with pytest.raises(OSError):
        css.split_document(new, directory, SMALL)
    monkeypatch.setattr(css, "_write_atomic", real_write)
    problems = css.verify_document(directory)
    assert any("unlisted file" in p for p in problems) and any("differs" in p for p in problems)
    with pytest.raises(css.SplitError):
        css.assemble(directory)
    (directory / "item.json.tmp-split").write_bytes(b"leftover")  # a temp file of an interrupted write
    result = css.split_document(new, directory, SMALL)
    assert result.removed == 0 and result.foreign == ["item.json.tmp-split"]
    (directory / "item.json.tmp-split").unlink()
    own_temp = directory / "text-000.txt.tmp-split"
    own_temp.write_bytes(b"leftover")
    result = css.split_document(new, directory, SMALL)
    assert result.removed == 1 and not own_temp.exists()
    assert css.assemble(directory) == new and css.verify_document(directory) == []


def test_split_refuses_a_foreign_directory_and_leaves_foreign_files_alone(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "notes.md").write_bytes(b"mine")
    with pytest.raises(css.SplitError, match="not a split tree"):
        css.split_document(namespace(items(3)), foreign, SMALL)
    directory, _ = round_trip(tmp_path, namespace(items(3)))
    (directory / "notes.md").write_bytes(b"mine")
    result = css.split_document(namespace(items(2)), directory, SMALL)
    assert result.foreign == ["notes.md"] and (directory / "notes.md").read_bytes() == b"mine"


def write_sources(directory, documents):
    directory.mkdir(exist_ok=True)
    for name, data in documents.items():
        (directory / name).write_bytes(data)


def test_batch_mode_round_trips_reports_removed_sources_and_prunes_on_request(tmp_path):
    source, tree, joined = tmp_path / "src", tmp_path / "tree", tmp_path / "joined"
    documents = {"alpha.json": namespace(items(8), indent=2), "beta.json": DOCUMENTS["crlf"],
                 "gamma.json": DOCUMENTS["compact"]}
    write_sources(source, documents)
    (source / "readme.txt").write_bytes(b"not a document")
    lines = []
    css.split_all(source, tree, SMALL, report=lines.append)
    assert (tree / ".gitattributes").read_bytes() == b"* -text\n"
    index = json.loads((tree / "index.json").read_text("utf-8"))["documents"]
    assert [entry["file"] for entry in index] == sorted(documents)
    assert index[0]["sha256"] == hashlib.sha256(documents["alpha.json"]).hexdigest()
    assert css.verify_all(tree, source) == []
    css.join_all(tree, joined, report=lines.append)
    assert {path.name: path.read_bytes() for path in joined.iterdir()} == documents

    before = mtimes(tree)
    totals, index_written = css.split_all(source, tree, None, report=lines.append)
    assert totals["written"] == 0 and totals["removed"] == 0 and not index_written
    assert mtimes(tree) == before

    (source / "beta.json").unlink()
    lines.clear()
    css.split_all(source, tree, None, report=lines.append)
    assert any("beta.json: source removed; kept" in line for line in lines)
    assert (tree / "beta").is_dir()
    assert css.verify_all(tree) == []
    assert css.verify_all(tree, source) == ["beta.json: no longer in the source directory"]

    css.split_all(source, tree, None, prune=True, report=lines.append)
    assert not (tree / "beta").exists()
    assert css.verify_all(tree, source) == []

    (tree / "stray").mkdir()
    write_sources(source, {"delta.json": b"{}"})
    problems = css.verify_all(tree, source)
    assert "unlisted entry stray in the tree" in problems
    assert "delta.json: in the source directory but not in the tree" in problems


def test_join_never_replaces_different_content_unless_told_to(tmp_path):
    # The joined file may be the only copy of work that was not split yet: a stale tree must not overwrite it.
    old, new = namespace(items(4), indent=2), namespace(items(5), indent=2)
    directory, target = tmp_path / "doc", tmp_path / "doc.json"
    css.split_document(old, directory, SMALL)
    target.write_bytes(new)
    with pytest.raises(css.SplitError, match="different content"):
        css.join_document(directory, target)
    assert target.read_bytes() == new
    assert css.join_document(directory, target, overwrite=True) == old and target.read_bytes() == old
    stamp = target.stat().st_mtime_ns
    assert css.join_document(directory, target) == old  # identical: left untouched
    assert target.stat().st_mtime_ns == stamp

    source, tree, out = tmp_path / "src", tmp_path / "tree", tmp_path / "out"
    write_sources(source, {"a.json": old, "b.json": DOCUMENTS["compact"], "c.json": DOCUMENTS["crlf"]})
    css.split_all(source, tree, SMALL, report=lambda line: None)
    write_sources(out, {"b.json": new})                   # one newer file in the destination
    with pytest.raises(css.SplitError, match="b.json"):
        css.join_all(tree, out, report=lambda line: None)
    assert sorted(path.name for path in out.iterdir()) == ["b.json"] and (out / "b.json").read_bytes() == new
    lines = []
    css.join_all(tree, out, overwrite=True, report=lines.append)
    assert {path.name: path.read_bytes() for path in out.iterdir()} == {
        "a.json": old, "b.json": DOCUMENTS["compact"], "c.json": DOCUMENTS["crlf"]}
    assert any("b.json" in line and "replaced" in line for line in lines)
    before = mtimes(out)
    css.join_all(tree, out, report=lines.append)          # nothing differs any more: nothing is written
    assert mtimes(out) == before
    assert css.main(["join-all", str(tree), str(out)]) == 0
    (out / "a.json").write_bytes(new)
    assert css.main(["join-all", str(tree), str(out)]) == 2
    assert css.main(["join-all", str(tree), str(out), "--overwrite"]) == 0
    assert css.main(["join", str(directory), str(target), "--overwrite"]) == 0


def test_command_line_exit_codes(tmp_path, capsys):
    source = tmp_path / "doc.json"
    source.write_bytes(namespace(items(4), indent=2))
    directory = tmp_path / "pieces"
    flags = ["--min-document-bytes", "0", "--min-table-bytes", "0", "--min-members", "2"]
    assert css.main(["split", str(source), str(directory), *flags]) == 0
    assert "written" in capsys.readouterr().out
    assert css.main(["split", str(source), str(directory)]) == 0
    assert "written 0" in capsys.readouterr().out
    assert css.main(["verify", str(directory), str(source)]) == 0
    assert css.main(["join", str(directory), str(tmp_path / "back.json")]) == 0
    assert (tmp_path / "back.json").read_bytes() == source.read_bytes()
    (directory / "stray").write_bytes(b"")
    assert css.main(["verify", str(directory)]) == 1
    bad = tmp_path / "bad.json"
    bad.write_bytes(b"{")
    assert css.main(["split", str(bad), str(tmp_path / "bad")]) == 2
    os.remove(directory / "stray")
    assert css.main(["split-all", str(tmp_path / "missing"), str(tmp_path / "tree")]) == 2
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "doc.json").write_bytes(source.read_bytes())
    assert css.main(["split-all", str(tmp_path / "sources"), str(tmp_path / "tree")]) == 0
    assert css.main(["verify-all", str(tmp_path / "tree"), str(tmp_path / "sources")]) == 0
    assert css.main(["join-all", str(tmp_path / "tree"), str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "doc.json").read_bytes() == source.read_bytes()
