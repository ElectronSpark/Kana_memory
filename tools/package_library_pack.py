"""Package runtime resources as a kanamemory-library-pack v1 ZIP (design 08 §4).

The pack file is written as ``.kmlib`` (design 08 section 16.8; the format is the same ZIP): given a
directory (default: the current one) the tool names it KanaMemory-<app version>.<edition>-<short id>.kmlib,
for example KanaMemory-0.36.7.1-core.kmlib: <app version> is this tree's app version (build_info.VERSION),
<edition> is --edition N (a positive integer), else one more than the highest edition of the files
KanaMemory-<app version>.*-<short id>.kmlib already in that directory (1 when there is none), and <short id>
is --short-id, else the part of the library id after its last dot (kanamemory.core -> core). An explicit
output file name is kept as given, an older ``.zip`` name too. The name changes nothing inside the pack
(its minimum app version and library_version stay what they are).

The default library is the core library ``kanamemory.core``: the teaching
database (v1 or v2) is carried once, as teaching/teaching_content.sqlite3, and
its namespaces are listed in provides.namespaces; the app reads an installed
core's teaching content from that database (content_pack.pack_path), so the
namespaces are not exported again as teaching/<name>.json (the rules accept a
core pack without them; core packs built before still carry them and still
import). The lexicon, annotation databases, the review overlay, media and
NOTICE.txt follow the layer layout. ``--extension`` packs only the selected
namespaces as an extension library, one teaching/<name>.json document each (no
lexicon, no teaching database). Before the archive is written the file table is
checked against the app's size limits (content_library_rules MAX_PACK_BYTES,
MAX_FILE_BYTES, MAX_TEACHING_BYTES); a pack over one stops with a breakdown of
the bytes by kind. Nothing executable or learner-related enters the archive;
tools/package_content_bundle.py is unchanged.

Phase 3/4: every manifest carries provides/references/retires/aliases/overrides
and item revisions (conflict rule c). references are derived: depends_on ids
of provided items that the pack itself does not define (required unless named
with --optional-reference). For tests and native checks, ``--rewrite-ids``
turns a copy of (demo) content into a separate extension library: every
catalogue id family:rest becomes family:<library_id>:rest throughout the
selected namespaces; ``--depend-on-source`` makes each copied item depend on
its original id (a cross-library required reference).

Phase 5: ``--sign-key <local key file>`` signs the manifest with Ed25519 through
the same implementation as tools/sign_library_pack.py
(kanamemory.content_library_signing); ``--key-id`` names the public key.

Study routes (design 10, WP-R4a): ``--route <json file>`` (repeatable,
extension only) writes route specs into the study_routes namespace
({"routes": {route_id: spec}}); a file holds one spec with its route_id or a
list of them, and route_id must start with route:<library_id>:. With
``--rewrite-ids``, ``--sample-route`` adds route:<library_id>:sample with two
explicit sections over the rewritten ids (and their source ids). Route ids
are listed in manifest provides.routes; route identities outside the pack
that no depends_on already references become optional references (Q7).
WP-R4b: a spec without library_id/version gets the library's id and version 1,
which the install precheck's full route check requires.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app" / "src"))
from kanamemory import content_library_merge as merge  # noqa: E402
from kanamemory import content_library_rules as rules  # noqa: E402
from kanamemory import content_library_signing as signing  # noqa: E402
from kanamemory.content_pack import namespace_document, read_pack_namespaces, validate_pack  # noqa: E402
from kanamemory.material_review import item_fingerprint  # noqa: E402

from kanamemory.build_info import VERSION as APP_VERSION  # noqa: E402
from kanamemory.library_file_open import EXTENSION as PACK_SUFFIX  # noqa: E402

LEARNER_TABLES = {"study_card", "review_event", "session_checkpoint", "learning_unit", "app_setting"}
ANNOTATION_FILES = ("sentence_annotations.sqlite3", "natural_translations.sqlite3", "order_translations.sqlite3",
                    "lexical_sentence_review.sqlite3")
REVIEW_FILE = "learning_material_review.json"


def short_id(library_id):
    """The library id's part after its last dot: kanamemory.core -> core, mylibrary -> mylibrary."""
    return str(library_id).rsplit(".", 1)[-1]


def pack_file_name(app_version, edition, short):
    """The default file name of a pack: KanaMemory-<app version>.<edition>-<short id>.kmlib."""
    if type(edition) is not int or edition < 1:
        raise ValueError("The edition is a positive integer")
    for part in (str(app_version), str(short)):
        if not part or any(ch in part for ch in '/\\:*?"<>|') or not part.isprintable():
            raise ValueError("Not usable in a file name: " + part[:80])
    return f"KanaMemory-{app_version}.{edition}-{short}{PACK_SUFFIX}"


def next_edition(directory, app_version, short):
    """One more than the highest edition N of KanaMemory-<app version>.<N>-<short id>.kmlib in ``directory``
    (1 when there is none)."""
    pattern = re.compile(re.escape(f"KanaMemory-{app_version}.") + r"([1-9][0-9]*)"
                         + re.escape(f"-{short}{PACK_SUFFIX}"))
    found = [int(match.group(1)) for path in Path(directory).iterdir() if (match := pattern.fullmatch(path.name))]
    return max(found, default=0) + 1


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _check_file(path, resources):
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):
        raise ValueError("Library packs cannot contain links")
    relative = path.relative_to(resources)
    if path.suffix.lower() in rules.FORBIDDEN_SUFFIXES or any(p.startswith(".") for p in relative.parts):
        raise ValueError("Executable code, temporary files or credential material is not library content: " + str(relative))
    raw = path.read_bytes()
    if raw.startswith(b"SQLite format 3\0"):
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables & LEARNER_TABLES:
            raise ValueError("Learner state must never enter a library pack: " + str(relative))
    return raw


def _table_items(payload, keys):
    """(content_id, row) pairs from the given item tables of one namespace payload."""
    if not isinstance(payload, dict):
        return []
    found = []
    for key in sorted(keys):
        table = payload.get(key)
        if isinstance(table, dict):
            found.extend((cid, item) for cid, item in table.items() if isinstance(cid, str) and isinstance(item, dict))
        elif isinstance(table, list):
            found.extend((row["id"], row) for row in table if isinstance(row, dict) and isinstance(row.get("id"), str))
    return found


def _items(payload):
    """(content_id, item) pairs of the lessons one namespace payload defines.

    Only the learning-catalogue tables count (content_library_merge.CATALOGUE_TABLE_KEYS).
    The "items" table of a per-item side table (varied_practice, context_choice,
    question_dependencies, question_readings) holds records about lessons, not lessons: its
    ids are never provided items, so a side table that names a core lesson does not claim
    (and conflict with) that lesson at install. That is all it means: the App merges a side
    table per content id and the first owner wins, the core first (content_pack._LayeredItems).
    Rows a library ships for a core lesson are dropped as a whole wherever the core has an entry
    for that lesson (reported as a merge conflict), or replace the core's whole entry when the
    learner accepted the override; a library can rely on side-table rows only for its own items.
    """
    return _table_items(payload, merge.CATALOGUE_TABLE_KEYS)


def _rewrite(value, mapping):
    """Exact-string id rewrite through a decoded payload (keys and values)."""
    if isinstance(value, str):
        return mapping.get(value, value)
    if isinstance(value, dict):
        return {mapping.get(k, k) if isinstance(k, str) else k: _rewrite(v, mapping) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return type(value)(_rewrite(v, mapping) for v in value)
    return value


def derived_id(content_id, library_id):
    family, _, rest = content_id.partition(":")
    return f"{family}:{library_id}:{rest}" if rest else f"{content_id}:{library_id}"


def _derive(values, library_id, depend_on_source):
    mapping = {}
    for value in values.values():
        for content_id, _ in _table_items(value, rules.ITEM_TABLE_KEYS):  # side-table keys are rewritten too
            mapping.setdefault(content_id, derived_id(content_id, library_id))
    result = {}
    for name, value in values.items():
        value = _rewrite(value, mapping)
        if depend_on_source and isinstance(value, dict):
            source = {new: old for old, new in mapping.items()}
            for key in rules.ITEM_TABLE_KEYS - {"items"}:
                table = value.get(key)
                rows = table.values() if isinstance(table, dict) else table if isinstance(table, list) else ()
                for row in rows:
                    if isinstance(row, dict) and row.get("id") in source:
                        row["depends_on"] = [*row.get("depends_on", []), source[row["id"]]]
        result[name] = value
    return result, mapping


def load_route_specs(path):
    """Route specs from one JSON file: a spec object or a list of spec objects."""
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    specs = value if isinstance(value, list) else [value]
    if not specs or any(not isinstance(spec, dict) or not isinstance(spec.get("route_id"), str) for spec in specs):
        raise ValueError("A route file holds a spec object (with route_id) or a list of them: " + str(path))
    return specs


def sample_route(library_id, title, mapping, provided):
    """route:<library_id>:sample: two explicit sections over rewritten ids (test libraries)."""
    rewritten = set(mapping.values())
    own = [cid for cid in provided if cid in rewritten][:6]
    if not own:
        raise ValueError("--sample-route needs rewritten items")
    source = {new: old for old, new in mapping.items()}
    sections = [{"id": "own", "title": "本库内容", "entries": own}]
    originals = [source[cid] for cid in own if cid in source]
    if originals:
        sections.append({"id": "source", "title": "原内容对照", "entries": originals})
    return {"route_id": rules.route_prefix(library_id) + "sample", "title": (title or library_id) + " 示例路线",
            "description": "打包工具生成的示例路线", "library_id": library_id, "version": 1,
            "stages": [{"id": "sample", "title": "示例",
                        "units": [{"id": "main", "title": "示例单元", "sections": sections}]}]}


def _route_table(library_id, specs):
    table = {}
    for spec in specs:
        route_id = spec.get("route_id")
        problems = rules.route_spec_problems(library_id, route_id, spec)
        if problems:
            raise ValueError(f"Invalid route {str(route_id)[:200]}: {problems[0]}")
        if route_id in table:
            raise ValueError("Duplicate route_id: " + route_id)
        # The full route check (study_routes.validate_route_spec) needs these;
        # a hand-written spec may leave them to the tool (WP-R4b).
        table[route_id] = {**spec, "library_id": spec.get("library_id", library_id), "version": spec.get("version", 1)}
    return table


SIZE_KINDS = ("teaching sqlite", "teaching json", "lexicon", "annotations", "media", "review", "other")


def size_kind(path):
    """The kind a pack member is counted under in the size breakdown."""
    if path == rules.TEACHING_PACK_FILE:
        return "teaching sqlite"
    if path.startswith(rules.LAYER_PREFIX["teaching"]):
        return "teaching json"
    for kind in ("lexicon", "annotations", "media", "review"):
        if path.startswith(rules.LAYER_PREFIX[kind]):
            return kind
    return "other"


def size_breakdown(files):
    """{kind: [member count, bytes]} for every kind in SIZE_KINDS."""
    totals = {kind: [0, 0] for kind in SIZE_KINDS}
    for path, info in files.items():
        row = totals[size_kind(path)]
        row[0] += 1
        row[1] += info["bytes"]
    return totals


def _mib(value):
    return f"{value / (1024 * 1024):.1f} MiB"


def size_problem(files):
    """A message naming every size limit of the app the file table breaks, with the breakdown by kind; or None.

    The same limits validate_manifest and validate_library_pack apply, checked before the archive is
    written so that the content side sees what grew rather than a bare refusal.
    """
    problems = []
    total = sum(info["bytes"] for info in files.values())
    if total > rules.MAX_PACK_BYTES:
        problems.append(f"the members add up to {total:,} bytes ({_mib(total)}), over MAX_PACK_BYTES "
                        f"{rules.MAX_PACK_BYTES:,} ({_mib(rules.MAX_PACK_BYTES)}) by {total - rules.MAX_PACK_BYTES:,}")
    for path, info in sorted(files.items()):
        if info["bytes"] > rules.MAX_FILE_BYTES:
            problems.append(f"{path} is {info['bytes']:,} bytes, over MAX_FILE_BYTES {rules.MAX_FILE_BYTES:,}")
        elif size_kind(path) == "teaching json" and info["bytes"] > rules.MAX_TEACHING_BYTES:
            problems.append(f"{path} is {info['bytes']:,} bytes, over MAX_TEACHING_BYTES "
                            f"{rules.MAX_TEACHING_BYTES:,} (one namespace document)")
    if not problems:
        return None
    lines = ["Library pack exceeds the app's size limits: " + "; ".join(problems) + ".", "Bytes by kind:"]
    for kind, (count, size) in size_breakdown(files).items():
        if count:
            lines.append(f"  {kind:16s} {count:5d} files {size:>14,d} ({_mib(size)})")
    largest = sorted(files.items(), key=lambda pair: -pair[1]["bytes"])[:5]
    lines.append("Largest members: " + ", ".join(f"{path} {_mib(info['bytes'])}" for path, info in largest))
    return "\n".join(lines)


def package(resources, output, *, library_id=rules.CORE_LIBRARY_ID, library_version=None, title=None,
            extension=False, namespaces=None, include_media=True, description="", rewrite_ids=False,
            depend_on_source=False, optional_references=(), retires=(), aliases=None, overrides=(),
            sign_seed=None, key_id=None, routes=(), with_sample_route=False, app_min_version=None, edition=None,
            short=None):
    resources, output = Path(resources).resolve(), Path(output).resolve()
    if app_min_version is not None and rules.app_version_key(app_min_version) is None:
        raise ValueError("app_min_version must be a dotted number such as 0.36.0")
    to_directory = output.is_dir()
    if output.is_relative_to(resources) or (output.exists() and not to_directory):
        raise ValueError("Choose a new output file outside the runtime resources")
    if not rules.LIBRARY_ID.fullmatch(library_id):
        raise ValueError("Invalid library_id")
    if extension == (library_id == rules.CORE_LIBRARY_ID):
        raise ValueError("The core library id is reserved for core packs and required for them")
    teaching_db = resources / "teaching_content.sqlite3"
    metadata = validate_pack(teaching_db)
    selected = set(namespaces or ())
    entries, files, layers = {}, {}, {layer: [] for layer in rules.LAYERS}
    provides_items, fingerprints, provided_namespaces = [], {}, []

    def add(name, raw, layer):
        if name in entries:
            raise ValueError("Duplicate pack entry: " + name)
        entries[name] = raw
        files[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        layers[layer].append(name)

    if (rewrite_ids or depend_on_source) and not extension:
        raise ValueError("Only an extension library can be derived with rewritten ids")
    if (routes or with_sample_route) and not extension:
        raise ValueError("Only an extension library carries study routes")
    if with_sample_route and not rewrite_ids:
        raise ValueError("--sample-route needs --rewrite-ids")
    values = {name: value for name, value in read_pack_namespaces(teaching_db) if not selected or name in selected}
    mapping = {}
    if rewrite_ids:
        values, mapping = _derive(values, library_id, depend_on_source)
    if (routes or with_sample_route) and rules.ROUTE_NAMESPACE in values:
        raise ValueError("The selected namespaces already contain study_routes")
    revisions, dependencies = {}, set()
    for name, value in values.items():
        provided_namespaces.append(name)
        if extension:  # a core pack serves its namespaces from the teaching database it carries
            add("teaching/" + name + ".json", _encoded(namespace_document(name, value)), "teaching")
        for content_id, item in _items(value):
            if content_id not in fingerprints:
                provides_items.append(content_id)
                fingerprints[content_id] = item_fingerprint(item)
                if item.get("revision") is not None:
                    revisions[content_id] = str(item["revision"])[:200]
            dependencies.update(d for d in item.get("depends_on", ()) if isinstance(d, str))
    if selected - set(provided_namespaces):
        raise ValueError("Unknown namespaces: " + ", ".join(sorted(selected - set(provided_namespaces))))
    route_specs = [*routes, *([sample_route(library_id, title, mapping, provides_items)] if with_sample_route else [])]
    route_table = _route_table(library_id, route_specs)
    route_references = []
    if route_table:
        provided_namespaces.append(rules.ROUTE_NAMESPACE)
        add("teaching/" + rules.ROUTE_NAMESPACE + ".json",
            _encoded(namespace_document(rules.ROUTE_NAMESPACE, {rules.ROUTE_TABLE_KEY: route_table})), "teaching")
        for spec in route_table.values():
            route_references.extend(cid for cid in rules.route_entry_ids(spec)
                                    if cid not in fingerprints and not cid.startswith(rules.KANA_IDENTITY_PREFIX)
                                    and cid not in dependencies)
        route_references = sorted(set(route_references))
    if not extension:
        add(rules.TEACHING_PACK_FILE, _check_file(teaching_db, resources), "teaching")
        add(rules.LEXICON_FILE, _check_file(resources / "content.sqlite3", resources), "lexicon")
        for name in ANNOTATION_FILES:
            if (resources / name).is_file():
                add("annotations/" + name, _check_file(resources / name, resources), "annotations")
        if (resources / REVIEW_FILE).is_file():
            add("review/" + REVIEW_FILE, _check_file(resources / REVIEW_FILE, resources), "review")
        if include_media and (resources / "media").is_dir():
            for path in sorted(p for p in (resources / "media").rglob("*") if p.is_file()):
                add(path.relative_to(resources).as_posix(), _check_file(path, resources), "media")
    add(rules.NOTICE_FILE, _check_file(resources / rules.NOTICE_FILE, resources), "notice")
    problem = size_problem(files)
    if problem:
        raise ValueError(problem)
    with closing(sqlite3.connect(resources.joinpath("content.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
        lexicon = dict(db.execute("SELECT key,value FROM metadata"))
    version = library_version or json.loads(lexicon.get("content_version", '"' + metadata["content_sha256"][:12] + '"'))
    version = str(version)
    if not rules.VERSION_TEXT.fullmatch(version):
        version = metadata["content_sha256"][:12]
    if to_directory:
        label = short or short_id(library_id)
        output = output / pack_file_name(APP_VERSION, next_edition(output, APP_VERSION, label)
                                         if edition is None else edition, label)
        if output.exists():
            raise ValueError("Choose a new output file: " + output.name)
    manifest = {
        "format": rules.PACK_FORMAT, "version": rules.PACK_VERSION, "library_id": library_id,
        "library_version": version, "version_scheme": "date" if version[:8].isdigit() else "semver",
        "title": title or ("KanaMemory 核心内容库" if not extension else library_id),
        "description": description, "language_pair": "ja-zh",
        "profile": "extension" if extension else "core", "teaching_profile": metadata["profile"],
        "requires": {"teaching_format": 1, "namespace_version": rules.NAMESPACE_VERSION,
                     **({} if app_min_version is None else {"app_min_version": app_min_version.strip()})},
        "layers": layers, "files": files, "content_sha256": rules.content_digest(files),
        "provides": {"items": provides_items, "namespaces": sorted(provided_namespaces),
                     **({"routes": list(route_table)} if route_table else {})},
        "fingerprints": fingerprints, "revisions": revisions,
        "references": [{"id": cid, "kind": "item",
                        "requirement": "optional" if cid in set(optional_references) else "required"}
                       for cid in sorted(dependencies - set(fingerprints))]
                      + [{"id": cid, "kind": "item", "requirement": "optional"} for cid in route_references],
        "depends_libraries": ([{"library_id": rules.CORE_LIBRARY_ID, "version_range": ""}]
                              if extension and dependencies - set(fingerprints) else []),
        "overrides": [{"id": cid, "reason": "declared by the packaging tool"} for cid in overrides],
        "retires": [{"id": cid, "replaced_by": replacement, "reason": "declared by the packaging tool"}
                    for cid, replacement in retires],
        "aliases": dict(aliases or {}), "merge_policies": {},
    }
    rules.validate_manifest(manifest)
    if sign_seed is not None:
        manifest = signing.sign_manifest(manifest, sign_seed, public_key_id=key_id)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(zipfile.ZipInfo(rules.MANIFEST_FILE, date_time=(1980, 1, 1, 0, 0, 0)),
                         _encoded(manifest), compress_type=zipfile.ZIP_DEFLATED)
        for name in sorted(entries):
            archive.writestr(zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)), entries[name],
                             compress_type=zipfile.ZIP_DEFLATED)
    report = rules.validate_library_pack(output)
    return {"path": str(output), "library_id": library_id, "library_version": version,
            "content_sha256": manifest["content_sha256"], "files": len(files), "items": len(provides_items),
            "namespaces": len(provided_namespaces), "bytes": report.total_bytes,
            "references": len(manifest["references"]), "provides": list(provides_items),
            "signature_status": report.signature.status, "public_key_id": report.signature.public_key_id,
            "routes": list(route_table)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resources", type=Path, help="Runtime resources directory (content.sqlite3, teaching_content.sqlite3, NOTICE.txt)")
    parser.add_argument("output", type=Path, nargs="?", default=Path("."),
                        help="Directory for KanaMemory-<app version>.<edition>-<short id>.kmlib (default: the "
                        "current directory), or a new file name kept as given (an older .zip name too); outside "
                        "the resources directory")
    parser.add_argument("--edition", type=int, help="Edition in the default file name, a positive integer "
                        "(default: one more than the highest edition of this app version and short id there)")
    parser.add_argument("--short-id", help="Short id in the default file name (default: the library id after its "
                        "last dot)")
    parser.add_argument("--library-id", default=rules.CORE_LIBRARY_ID)
    parser.add_argument("--library-version")
    parser.add_argument("--title")
    parser.add_argument("--description", default="")
    parser.add_argument("--extension", action="store_true", help="Package an extension library (selected namespaces only)")
    parser.add_argument("--namespace", action="append", default=[], help="Namespace to include (repeatable)")
    parser.add_argument("--no-media", action="store_true")
    parser.add_argument("--rewrite-ids", action="store_true",
                        help="Extension only: copy content under new ids family:<library_id>:rest (test libraries)")
    parser.add_argument("--depend-on-source", action="store_true",
                        help="With --rewrite-ids: each copied item depends on its original id (required reference)")
    parser.add_argument("--optional-reference", action="append", default=[], help="Reference id marked optional")
    parser.add_argument("--retire", action="append", default=[], help="ID or ID=REPLACEMENT this library retires")
    parser.add_argument("--alias", action="append", default=[], help="OLD=NEW identity alias")
    parser.add_argument("--override", action="append", default=[], help="ID this library intentionally overrides")
    parser.add_argument("--sign-key", type=Path, help="Local Ed25519 key file (64 hex digits) to sign the manifest")
    parser.add_argument("--key-id", help="public_key_id for --sign-key (default ed25519:<fingerprint prefix>)")
    parser.add_argument("--route", action="append", default=[], type=Path,
                        help="Extension only: JSON file with a study route spec (or a list of specs); repeatable")
    parser.add_argument("--app-min-version", help="The oldest app version that can use this library (a dotted "
                        "number such as 0.36.0); an older app then refuses the import with a message")
    parser.add_argument("--sample-route", action="store_true",
                        help="With --rewrite-ids: add route:<library_id>:sample over the rewritten ids")
    args = parser.parse_args()
    if args.key_id and not args.sign_key:
        parser.error("--key-id needs --sign-key")
    seed = signing.parse_seed(args.sign_key.read_text(encoding="ascii")) if args.sign_key else None
    retires = [tuple(value.split("=", 1)) if "=" in value else (value, None) for value in args.retire]
    aliases = dict(value.split("=", 1) for value in args.alias)
    result = package(args.resources, args.output, library_id=args.library_id, library_version=args.library_version,
                     title=args.title, extension=args.extension, namespaces=args.namespace or None,
                     include_media=not args.no_media, description=args.description, rewrite_ids=args.rewrite_ids,
                     depend_on_source=args.depend_on_source, optional_references=args.optional_reference,
                     retires=retires, aliases=aliases, overrides=args.override, sign_seed=seed, key_id=args.key_id,
                     routes=[spec for path in args.route for spec in load_route_specs(path)],
                     with_sample_route=args.sample_route, app_min_version=args.app_min_version,
                     edition=args.edition, short=args.short_id)
    result.pop("provides")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
