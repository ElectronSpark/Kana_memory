"""Synthetic content-library fixtures: tiny core resources and library packs.

Everything here is original test data; no private course content is read.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import zipfile

from kanamemory import content_library_rules as rules
from kanamemory.content_pack import namespace_document

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("build_teaching_content", _ROOT / "tools/build_teaching_content.py")
TEACHING_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(TEACHING_BUILDER)

CORE_ITEMS = {
    "grammar:demo:desu": {"id": "grammar:demo:desu", "revision": 1, "family": "grammar", "domain": "grammar",
                          "title": "です", "reading": "", "meaning": "是", "instruction": "名词句",
                          "question_templates": [], "source_urls": []},
    "word:demo:mizu": {"id": "word:demo:mizu", "revision": 1, "family": "word", "domain": "vocabulary",
                       "title": "水", "reading": "みず", "meaning": "水", "instruction": "",
                       "question_templates": [], "source_urls": []},
}
CORE_NAMESPACES = {
    "lesson": {"CONTENT_REVISION": 1, "_ITEMS": CORE_ITEMS, "SOURCES": [{"id": "core"}]},
    "route": {"STAGES": ["intro"], "WORD_TOPICS": {"basic": ["word:demo:mizu"]}},
}


def write_namespace_sources(folder, namespaces):
    folder.mkdir(parents=True, exist_ok=True)
    for name, value in namespaces.items():
        (folder / (name + ".json")).write_text(json.dumps(namespace_document(name, value), ensure_ascii=False),
                                               encoding="utf-8")


def build_core_resources(root, namespaces=None, *, profile="demo", content_version="20260101"):
    """A runtime resources folder with a real teaching pack and a tiny lexicon."""
    root = Path(root)
    source = root / "namespace-source"
    write_namespace_sources(source, namespaces or CORE_NAMESPACES)
    root.mkdir(parents=True, exist_ok=True)
    TEACHING_BUILDER.build(source, root / "teaching_content.sqlite3", profile=profile)
    with sqlite3.connect(root / "content.sqlite3") as db:
        db.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT)")
        db.execute("INSERT INTO metadata VALUES ('content_version',?)", (json.dumps(content_version),))
        db.execute("CREATE TABLE kana(id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO kana VALUES ('kana-3042')")
    (root / "NOTICE.txt").write_text("Synthetic notice\n", encoding="utf-8")
    return root


def pack_bytes(library_id, namespaces, *, version="20260102", profile="extension", title="测试扩展库",
               items=None, fingerprints=None, extra_files=None, manifest_overrides=None, drop_manifest_keys=(),
               corrupt=None):
    """Bytes of a library pack ZIP built from plain namespace payloads."""
    from kanamemory.material_review import item_fingerprint
    entries = {}
    for name, value in namespaces.items():
        entries["teaching/" + name + ".json"] = json.dumps(namespace_document(name, value), ensure_ascii=False).encode("utf-8")
    entries[rules.NOTICE_FILE] = b"Synthetic extension notice\n"
    for name, raw in (extra_files or {}).items():
        entries[name] = raw
    files = {name: {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in entries.items()}
    provided = items
    if provided is None:
        provided, fingerprints = [], {}
        for value in namespaces.values():
            for key in rules.ITEM_TABLE_KEYS:
                table = value.get(key) if isinstance(value, dict) else None
                if isinstance(table, dict):
                    for cid, item in table.items():
                        provided.append(cid)
                        fingerprints[cid] = item_fingerprint(item)
    manifest = {"format": rules.PACK_FORMAT, "version": rules.PACK_VERSION, "library_id": library_id,
                "library_version": version, "version_scheme": "date", "title": title, "profile": profile,
                "requires": {"teaching_format": 1, "namespace_version": 1},
                "layers": {"teaching": sorted(n for n in entries if n.startswith("teaching/")), "notice": [rules.NOTICE_FILE],
                           "lexicon": [], "annotations": [], "media": [], "review": []},
                "files": files, "content_sha256": rules.content_digest(files),
                "provides": {"items": list(provided), "namespaces": sorted(namespaces)},
                "fingerprints": fingerprints or {}, "references": [], "depends_libraries": [], "overrides": [],
                "retires": [], "aliases": {}, "merge_policies": {}}
    manifest.update(manifest_overrides or {})
    for key in drop_manifest_keys:
        manifest.pop(key, None)
    if corrupt:
        entries[corrupt] = entries[corrupt] + b"x"
    import io
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(rules.MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False))
        for name, raw in entries.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


def extension_namespaces(conflict=False):
    """An extension adding one grammar item to the lesson namespace."""
    item = {"id": "grammar:ext:masu", "revision": 1, "family": "grammar", "domain": "grammar", "title": "ます",
            "reading": "", "meaning": "敬体", "instruction": "动词敬体", "question_templates": [], "source_urls": []}
    items = {"grammar:ext:masu": item}
    if conflict:
        changed = dict(CORE_ITEMS["grammar:demo:desu"], meaning="改写")
        items["grammar:demo:desu"] = changed
    return {"lesson": {"CONTENT_REVISION": 99, "_ITEMS": items, "SOURCES": [{"id": "ext"}]},
            "route": {"STAGES": ["ext-only"], "WORD_TOPICS": {"ext": ["grammar:ext:masu"]}}}
