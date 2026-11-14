"""Pure rules for importable content libraries (design 08, phases 0-1 and 3-4).

Registry shape, library-pack manifest shape, file-level pack validation, the
namespace merge policy table of section 6.2 and the offline install precheck
live here (read-time availability is content_availability's; review B-11
removed the phase-0 orphan projection). Nothing here touches Toga, the
platform, the network or the learner database; the only file access is
reading a candidate pack ZIP. A library pack never contains executable code
and never gains permission to change scheduling, settings or enrolment.
Phase 3/4: the merge policy table and conflict rules live in
content_library_merge (re-exported here); this module adds the registry's
overrides_accepted, the manifest's optional revisions, conflict rule c in
the precheck and the same-library upgrade precheck (sections 4.2, 5.5, 6.3).
Phase 5: the manifest's optional signature block, the pack's signature status
(content_library_signing: Ed25519 + TOFU against the registry's trusted_keys),
signature-aware source fields and the invalid-signature install refusal.
Parallel study routes (design 10 sections 6.2/10.4, WP-R4a): manifest
provides may list ``routes``; PackReport carries the pack's raw study_routes
payload; route_spec_problems is the shape pre-check (route_id prefix, stages
or build, entry shapes, the 20000-entry limit) and precheck_pack counts the
pack's routes, their entries and identities outside the pack (classified by
references required/optional; undeclared ones are an author-error warning).
WP-R4b: after the shape pre-check every route also gets the full
study_routes.validate_route_spec check against the installed effective
catalogue plus the pack's provides (kana identities from the kana table when
given); a route failing it is listed in route_errors as a warning and is
ignored after install, never blocking (design 10 section 6.1 note).
route_problems_in_catalogue applies the same check to the routes of the
running effective catalogue (library rows' routes_ignored).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
import zipfile

from . import content_library_signing as signing
from . import study_routes
from .content_library_merge import (  # noqa: F401  (re-exported policy table)
    CONTRIBUTION_KEYS, ITEM_TABLE_KEYS, MergeReport, ROUTE_NAMESPACE, ROUTE_TABLE_KEY, SINGLETON_KEYS,
    merge_namespace, route_id_problem, route_prefix,
)

REGISTRY_SETTING = "content_libraries_v1"
# A restored backup's registry is kept here read-only (device configuration
# stays local, design 08 Q7 / review B-02); nothing installs from it.
RESTORED_HINT_SETTING = "content_libraries_restored_hint_v1"
MAX_PENDING_DELETE = 256
REGISTRY_VERSION = 1
MIRROR_FILE = "content_libraries.json"
LIBRARIES_DIRECTORY = "libraries"
CORE_LIBRARY_ID = "kanamemory.core"
PACK_FORMAT = "kanamemory-library-pack"
PACK_VERSION = 1
NAMESPACE_FORMAT = "kanamemory-teaching-namespace"
NAMESPACE_VERSION = 1
MANIFEST_FILE = "manifest.json"
NOTICE_FILE = "NOTICE.txt"
SIGNATURE_FILE = "signature.json"
TEACHING_DIRECTORY = "teaching"
TEACHING_PACK_FILE = "teaching/teaching_content.sqlite3"
LEXICON_FILE = "lexicon/content.sqlite3"
LAYERS = ("teaching", "lexicon", "annotations", "media", "review", "notice")
LAYER_PREFIX = {"teaching": "teaching/", "lexicon": "lexicon/", "annotations": "annotations/",
                "media": "media/", "review": "review/", "notice": ""}
PROFILES = frozenset({"core", "extension"})
TEACHING_PROFILES = frozenset({"personal", "demo"})
STATES = frozenset({"ready", "pending_restart", "damaged"})
SOURCE_KINDS = frozenset({"bundled", "file"})
# Registry source statuses: "unverified"/"verified" are phase 0-2 records kept
# readable; phase 5 writes unsigned/valid/untrusted_key ("invalid" never installs).
SIGNATURE_STATUSES = frozenset({"unsigned", "unverified", "verified", "valid", "untrusted_key"})
PACK_SIGNATURE_STATUSES = frozenset(signing.PACK_SIGNATURE_STATUSES)
SOURCE_KEYS = frozenset({"kind", "name", "sha256", "signature_status"})
SOURCE_SIGNATURE_KEYS = frozenset({"public_key_id", "key_fingerprint"})
VERSION_SCHEMES = frozenset({"date", "semver"})
REQUIREMENTS = frozenset({"required", "optional"})
MERGE_POLICIES = frozenset({"items_by_id", "map_by_key", "contribution", "singleton"})
# Same rejection rule as tools/package_content_bundle.py, plus native code.
FORBIDDEN_SUFFIXES = frozenset({".py", ".pyc", ".pyd", ".dll", ".exe", ".ps1", ".js", ".jks", ".keystore",
                                ".so", ".sh", ".bat", ".cmd", ".jar", ".dex", ".apk"})
# The full core pack (lexicon 99 MiB, annotations, teaching, media) is about
# 306 MiB uncompressed, hence 512 MiB for a pack and 256 MiB for one file,
# above the 256 MiB that 08 §5.2 first suggested (08 section 16). The
# import channel of both platforms and the service refuse at this one limit.
MAX_PACK_BYTES = 512 * 1024 * 1024
PACK_TOO_LARGE = f"内容库文件超过 {MAX_PACK_BYTES // (1024 * 1024)} MiB 限制。"
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TEACHING_BYTES = 64 * 1024 * 1024  # one namespace document; same bound as content_pack._MAX_PAYLOAD_BYTES
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_LIBRARIES = 64
CONTENT_ID_LIMIT = 10000
LIBRARY_ID = re.compile(r"[a-z][a-z0-9_.-]{2,63}\Z")
NAMESPACE_NAME = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
VERSION_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")
APP_VERSION = re.compile(r"\d+(?:\.\d+){0,3}\Z")  # requires.app_min_version that can be compared: 0.36 or 0.36.0
APP_TOO_OLD = "这个内容库需要 {needed} 或更新版本的应用（当前 {current}）。请先更新应用，再导入。"
FAMILIES = ("word", "grammar", "pronunciation", "kana")
ROUTE_ENTRY_LIMIT = 20000  # same limit as study_path.validate_path_state
KANA_IDENTITY_PREFIX = "kana:"

class ContentLibraryError(ValueError):
    """The registry, manifest or pack does not have a supported shape."""


def _text(value, name, *, allow_empty=False, limit=4096):
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > limit or "\x00" in value:
        raise ContentLibraryError("Invalid " + name)
    return value


def _utc_text(value, name):
    try:
        parsed = datetime.fromisoformat(_text(value, name, limit=64))
    except ValueError as error:
        raise ContentLibraryError("Invalid " + name) from error
    if parsed.tzinfo is None:
        raise ContentLibraryError("Invalid " + name)
    return value


def now_utc_text(now):
    """ISO text of an aware instant; callers pass the App clock's time."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ContentLibraryError("Need an aware datetime")
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def relative_path(value, name="path", *, allow_empty=False):
    """A POSIX relative path inside a library directory or pack; no escapes."""
    text = _text(value, name, allow_empty=allow_empty, limit=512)
    if text == "":
        return text
    parts = text.split("/")
    if (text.startswith("/") or "\\" in text or ":" in text or any(not part or part in {".", ".."} for part in parts)
            or any(part.startswith(".") for part in parts)):
        raise ContentLibraryError("Invalid " + name)
    return text


def content_digest(files):
    """sha256 over the sorted (path, sha256) list; the manifest's content_sha256."""
    rows = [[path, files[path]["sha256"]] for path in sorted(files)]
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def family_of(content_id):
    head = content_id.split(":", 1)[0] if isinstance(content_id, str) else ""
    if head in FAMILIES:
        return head
    if isinstance(content_id, str) and content_id.startswith("kana-"):
        return "kana"
    return "other"


def count_families(content_ids):
    counts = {}
    for content_id in content_ids:
        family = family_of(content_id)
        counts[family] = counts.get(family, 0) + 1
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------- registry

def core_entry():
    return {"library_id": CORE_LIBRARY_ID, "library_version": "bundled", "profile": "core",
            "content_sha256": None, "enabled": True, "installed_at_utc": None,
            "source": {"kind": "bundled", "name": "resources", "sha256": None, "signature_status": "unsigned"},
            "path": "", "state": "ready", "summary": {"title": "内置核心库"}}


def default_registry():
    """First start: only the bundled core library, enabled and not removable."""
    return {"version": REGISTRY_VERSION, "libraries": [core_entry()], "priority": [CORE_LIBRARY_ID],
            "trusted_keys": []}


def _summary(value):
    if not isinstance(value, dict) or set(value) - {"title", "families", "namespaces", "item_count",
                                                     "teaching_profile", "description", "language_pair"}:
        raise ContentLibraryError("Invalid library summary")
    result = {}
    for key in ("title", "description", "language_pair", "teaching_profile"):
        if key in value:
            result[key] = _text(value[key], "library " + key, allow_empty=True, limit=2000)
    if "families" in value:
        families = value["families"]
        if (not isinstance(families, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0
                                                  for k, v in families.items())):
            raise ContentLibraryError("Invalid library family counts")
        result["families"] = dict(families)
    if "namespaces" in value:
        names = value["namespaces"]
        if not isinstance(names, list) or any(not isinstance(n, str) or not NAMESPACE_NAME.fullmatch(n) for n in names):
            raise ContentLibraryError("Invalid library namespace list")
        result["namespaces"] = list(names)
    if "item_count" in value:
        if type(value["item_count"]) is not int or value["item_count"] < 0:
            raise ContentLibraryError("Invalid library item count")
        result["item_count"] = value["item_count"]
    return result


def validate_entry(entry):
    required = {"library_id", "library_version", "profile", "content_sha256", "enabled", "installed_at_utc",
                "source", "path", "state"}
    if not isinstance(entry, dict) or not required <= set(entry) or set(entry) - required - {"summary"}:
        raise ContentLibraryError("Invalid library registry entry")
    library_id = _text(entry["library_id"], "library_id", limit=64)
    if not LIBRARY_ID.fullmatch(library_id):
        raise ContentLibraryError("Invalid library_id")
    if not VERSION_TEXT.fullmatch(_text(entry["library_version"], "library_version", limit=64)):
        raise ContentLibraryError("Invalid library_version")
    if entry["profile"] not in PROFILES or type(entry["enabled"]) is not bool or entry["state"] not in STATES:
        raise ContentLibraryError("Invalid library registry entry")
    source = entry["source"]
    if (not isinstance(source, dict) or not SOURCE_KEYS <= set(source) or set(source) - SOURCE_KEYS - SOURCE_SIGNATURE_KEYS
            or source["kind"] not in SOURCE_KINDS or source["signature_status"] not in SIGNATURE_STATUSES):
        raise ContentLibraryError("Invalid library source")
    if "public_key_id" in source and (not isinstance(source["public_key_id"], str)
                                      or not signing.PUBLIC_KEY_ID.fullmatch(source["public_key_id"])):
        raise ContentLibraryError("Invalid library source key id")
    if "key_fingerprint" in source and (not isinstance(source["key_fingerprint"], str)
                                        or not SHA256.fullmatch(source["key_fingerprint"])):
        raise ContentLibraryError("Invalid library source key fingerprint")
    if (set(source) & SOURCE_SIGNATURE_KEYS) and source["signature_status"] not in {"valid", "untrusted_key"}:
        raise ContentLibraryError("Only a signed source records its key")
    _text(source["name"], "source name", allow_empty=True, limit=512)
    bundled = source["kind"] == "bundled"
    for key, value in (("content_sha256", entry["content_sha256"]), ("source sha256", source["sha256"])):
        if value is None:
            if not bundled:
                raise ContentLibraryError("Installed library needs a " + key)
        elif not isinstance(value, str) or not SHA256.fullmatch(value):
            raise ContentLibraryError("Invalid " + key)
    if entry["installed_at_utc"] is not None:
        _utc_text(entry["installed_at_utc"], "installed_at_utc")
    elif not bundled:
        raise ContentLibraryError("Installed library needs installed_at_utc")
    path = relative_path(entry["path"], allow_empty=True)
    if bundled != (path == ""):
        raise ContentLibraryError("Library path must match its source")
    if bundled and library_id != CORE_LIBRARY_ID:
        raise ContentLibraryError("Only the core library is bundled")
    if library_id == CORE_LIBRARY_ID and (not entry["enabled"] or entry["profile"] != "core"):
        raise ContentLibraryError("The core library stays enabled")
    result = {key: entry[key] for key in required}
    result["source"] = dict(source)
    if "summary" in entry:
        result["summary"] = _summary(entry["summary"])
    return result


def validate_registry(value):
    """Return a normalized copy of content_libraries_v1 or raise."""
    base = {"version", "libraries", "priority", "trusted_keys"}
    if (not isinstance(value, dict) or not base <= set(value) or set(value) - base - {"overrides_accepted", "pending_delete"}
            or value["version"] != REGISTRY_VERSION or not isinstance(value["libraries"], list)
            or not isinstance(value["priority"], list) or not isinstance(value["trusted_keys"], list)
            or not value["libraries"] or len(value["libraries"]) > MAX_LIBRARIES):
        raise ContentLibraryError("Invalid content library registry")
    libraries = [validate_entry(entry) for entry in value["libraries"]]
    ids = [entry["library_id"] for entry in libraries]
    if len(set(ids)) != len(ids) or CORE_LIBRARY_ID not in ids:
        raise ContentLibraryError("Registry needs unique libraries including the core library")
    if sum(1 for entry in libraries if entry["profile"] == "core") != 1:
        raise ContentLibraryError("Exactly one core library is registered")
    priority = value["priority"]
    if sorted(priority) != sorted(ids) or priority[0] != CORE_LIBRARY_ID:
        raise ContentLibraryError("Priority must list every library once with the core library first")
    try:
        keys = signing.validate_trusted_keys(value["trusted_keys"])
    except signing.SigningError as error:
        raise ContentLibraryError(str(error)) from error
    result = {"version": REGISTRY_VERSION, "libraries": libraries, "priority": list(priority), "trusted_keys": keys}
    if "overrides_accepted" in value:
        result["overrides_accepted"] = _overrides_accepted(value["overrides_accepted"], ids)
    if "pending_delete" in value:
        result["pending_delete"] = _pending_delete(value["pending_delete"])
    return result


def _pending_delete(rows):
    """Library-root relative folders a later startup deletes (review B-03)."""
    if not isinstance(rows, list) or len(rows) > MAX_PENDING_DELETE:
        raise ContentLibraryError("Invalid pending deletions")
    paths = [relative_path(row, "pending deletion") for row in rows]
    if len(set(paths)) != len(paths):
        raise ContentLibraryError("Duplicate pending deletion")
    return paths


def with_pending_delete(registry, add=(), drop=()):
    """A new registry whose pending_delete lists ``add`` and no longer lists ``drop``.

    Removing or replacing a library never deletes a folder the running
    process may still read (v2 shards are read lazily); the service's next
    startup_sync deletes these folders instead. The oldest rows are dropped
    when the list would exceed MAX_PENDING_DELETE (a leftover folder is inert).
    """
    registry = validate_registry(registry)
    gone = set(drop)
    rows = [path for path in registry.get("pending_delete", []) if path not in gone]
    rows += [relative_path(path, "pending deletion") for path in dict.fromkeys(add)
             if path not in rows and path not in gone]
    rows = rows[-MAX_PENDING_DELETE:]
    if rows:
        registry["pending_delete"] = rows
    else:
        registry.pop("pending_delete", None)
    return validate_registry(registry)


def _overrides_accepted(rows, library_ids):
    """Section 6.3 b: (library_id, id) pairs the user accepted; never for the core library."""
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ContentLibraryError("Invalid accepted overrides")
    result, seen = [], set()
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"library_id", "id"} or row["library_id"] not in library_ids
                or row["library_id"] == CORE_LIBRARY_ID):
            raise ContentLibraryError("Invalid accepted override")
        pair = (row["library_id"], _text(row["id"], "override id", limit=CONTENT_ID_LIMIT))
        if pair in seen:
            raise ContentLibraryError("Duplicate accepted override")
        seen.add(pair)
        result.append({"library_id": pair[0], "id": pair[1]})
    return result


def accepted_overrides(registry):
    """(library_id, id) pairs of the registry's overrides_accepted (empty when absent)."""
    return frozenset((row["library_id"], row["id"]) for row in (registry or {}).get("overrides_accepted", ()))


def with_accepted_overrides(registry, library_id, content_ids):
    """A new registry accepting library_id's declared overrides for content_ids.

    Rows of the same library are replaced; the caller (the service) persists it.
    """
    registry = validate_registry(registry)
    rows = [row for row in registry.get("overrides_accepted", []) if row["library_id"] != library_id]
    rows += [{"library_id": library_id, "id": cid} for cid in dict.fromkeys(content_ids)]
    if rows:
        registry["overrides_accepted"] = rows
    else:
        registry.pop("overrides_accepted", None)
    return validate_registry(registry)


def with_trusted_key(registry, public_key_id, public_key, *, now):
    """A new registry with one learner-confirmed signing key (TOFU, section 4.5).

    now is the App clock's aware datetime. The same key again is a no-op; a
    known public_key_id with a different public key is refused (never
    replaced automatically). The service persists the result through its
    registry write (StudyStore.set_setting).
    """
    registry = validate_registry(registry)
    try:
        registry["trusted_keys"] = signing.with_trusted_key(registry["trusted_keys"], public_key_id, public_key,
                                                            now_utc_text(now))
    except signing.SigningError as error:
        raise ContentLibraryError(str(error)) from error
    return validate_registry(registry)


def pack_signature(report, registry=None):
    """The pack's SignatureCheck, trust re-decided against registry when given."""
    check = report.signature
    return check.with_trust(registry) if registry is not None else check


def install_refusal(report):
    """Reason an install must be refused whatever the learner chose, or None."""
    return report.signature.refusal_reason()


def source_signature_fields(report, registry=None):
    """signature_status (+ public_key_id, key_fingerprint) for the registry source."""
    try:
        return signing.source_signature_fields(pack_signature(report, registry))
    except signing.SigningError as error:
        raise ContentLibraryError(str(error)) from error


def registry_entry(registry, library_id):
    return next((entry for entry in registry["libraries"] if entry["library_id"] == library_id), None)


def effective_entries(registry):
    """Enabled, undamaged libraries in priority order; the core library first."""
    order = {library_id: index for index, library_id in enumerate(registry["priority"])}
    entries = [entry for entry in registry["libraries"] if entry["enabled"] and entry["state"] != "damaged"]
    return sorted(entries, key=lambda entry: order[entry["library_id"]])


def pending_restart(registry):
    return any(entry["state"] == "pending_restart" for entry in registry["libraries"])


def change_blocked_reason(*, backup_busy=False, round_running=False, focused=False):
    """Section 5.3: no exclusive stage and no practice round in progress."""
    if backup_busy:
        return "备份或恢复正在进行，请等它结束后再更改内容库。"
    if round_running or focused:
        return "当前练习轮次还没有结束，请先完成或退出练习，再更改内容库。"
    return None


# ---------------------------------------------------------------- manifest

def _string_list(value, name, *, limit=100000, item_limit=CONTENT_ID_LIMIT):
    if not isinstance(value, list) or len(value) > limit:
        raise ContentLibraryError("Invalid " + name)
    for item in value:
        _text(item, name + " entry", limit=item_limit)
    if len(set(value)) != len(value):
        raise ContentLibraryError("Duplicate " + name + " entry")
    return list(value)


def _file_table(files):
    if not isinstance(files, dict) or not files or len(files) > 20000:
        raise ContentLibraryError("Invalid file table")
    total, result = 0, {}
    for path, info in files.items():
        name = relative_path(path, "file path")
        if (name == MANIFEST_FILE or not isinstance(info, dict) or set(info) != {"bytes", "sha256"}
                or type(info["bytes"]) is not int or not 0 <= info["bytes"] <= MAX_FILE_BYTES
                or not isinstance(info["sha256"], str) or not SHA256.fullmatch(info["sha256"])):
            raise ContentLibraryError("Invalid file entry: " + str(path)[:200])
        total += info["bytes"]
        result[name] = {"bytes": info["bytes"], "sha256": info["sha256"]}
    if total > MAX_PACK_BYTES:
        raise ContentLibraryError("Library pack exceeds its size limit")
    return result


def app_version_key(text):
    """The numbers of a dotted app version (0.36.0 -> (0, 36, 0, 0)), or None when the text is not one."""
    text = text.strip() if isinstance(text, str) else ""
    if not APP_VERSION.fullmatch(text):
        return None
    numbers = tuple(int(part) for part in text.split("."))
    return numbers + (0,) * (4 - len(numbers))


def app_too_old(manifest, app_version):
    """requires.app_min_version of a manifest when the running app is older than it, else None.

    Compared number by number (0.36 equals 0.36.0). A requirement or an app version that is not a dotted
    number cannot be compared and refuses nothing: packs written before the comparison existed may hold any
    text in that field. The pack tool writes only comparable values.
    """
    needed = manifest.get("requires", {}).get("app_min_version")
    wanted, have = app_version_key(needed), app_version_key(app_version)
    return needed.strip() if wanted is not None and have is not None and have < wanted else None


def validate_manifest(value):
    """Return a normalized manifest (section 4.2) or raise ContentLibraryError."""
    required = {"format", "version", "library_id", "library_version", "version_scheme", "title", "profile",
                "requires", "layers", "files", "content_sha256", "provides"}
    optional = {"description", "language_pair", "authors", "license", "license_notice", "teaching_profile",
                "references", "depends_libraries", "overrides", "retires", "aliases", "merge_policies",
                "fingerprints", "revisions", "signature"}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ContentLibraryError("Invalid library manifest")
    if value["format"] != PACK_FORMAT or value["version"] != PACK_VERSION:
        raise ContentLibraryError("Unsupported library pack format")
    result = {"format": PACK_FORMAT, "version": PACK_VERSION}
    result["library_id"] = _text(value["library_id"], "library_id", limit=64)
    if not LIBRARY_ID.fullmatch(result["library_id"]):
        raise ContentLibraryError("Invalid library_id")
    result["library_version"] = _text(value["library_version"], "library_version", limit=64)
    if not VERSION_TEXT.fullmatch(result["library_version"]) or value["version_scheme"] not in VERSION_SCHEMES:
        raise ContentLibraryError("Invalid library_version")
    result["version_scheme"] = value["version_scheme"]
    result["title"] = _text(value["title"], "title", limit=200)
    if value["profile"] not in PROFILES:
        raise ContentLibraryError("Invalid library profile")
    result["profile"] = value["profile"]
    for key in ("description", "language_pair", "license", "license_notice"):
        if key in value:
            result[key] = _text(value[key], key, allow_empty=True, limit=20000)
    if "authors" in value:
        result["authors"] = _string_list(value["authors"], "authors", limit=100, item_limit=200)
    if "teaching_profile" in value:
        if value["teaching_profile"] not in TEACHING_PROFILES:
            raise ContentLibraryError("Invalid teaching profile")
        result["teaching_profile"] = value["teaching_profile"]
    requires = value["requires"]
    if (not isinstance(requires, dict) or set(requires) - {"app_min_version", "teaching_format", "namespace_version"}
            or requires.get("teaching_format") != 1 or requires.get("namespace_version") != NAMESPACE_VERSION):
        raise ContentLibraryError("Unsupported library requirements")
    result["requires"] = {"teaching_format": 1, "namespace_version": NAMESPACE_VERSION}
    if "app_min_version" in requires:
        result["requires"]["app_min_version"] = _text(requires["app_min_version"], "app_min_version", limit=32)
    result["files"] = _file_table(value["files"])
    if value["content_sha256"] != content_digest(result["files"]):
        raise ContentLibraryError("Library content checksum does not match its file table")
    result["content_sha256"] = value["content_sha256"]
    layers = value["layers"]
    if not isinstance(layers, dict) or set(layers) - set(LAYERS):
        raise ContentLibraryError("Invalid layer table")
    result["layers"] = {}
    for layer in LAYERS:
        paths = _string_list(layers.get(layer, []), layer + " layer", limit=20000, item_limit=512)
        for path in paths:
            if relative_path(path, layer + " layer path") not in result["files"] or not path.startswith(LAYER_PREFIX[layer]):
                raise ContentLibraryError("Layer lists a file outside its folder: " + path[:200])
            if layer == "notice" and path != NOTICE_FILE:
                raise ContentLibraryError("The notice layer holds only NOTICE.txt")
        result["layers"][layer] = paths
    if NOTICE_FILE not in result["files"]:
        raise ContentLibraryError("Library pack needs NOTICE.txt")
    provides = value["provides"]
    if not isinstance(provides, dict) or not {"items", "namespaces"} <= set(provides) \
            or set(provides) - {"items", "namespaces", "routes"}:
        raise ContentLibraryError("Invalid provides table")
    names = _string_list(provides["namespaces"], "namespaces", limit=1000, item_limit=80)
    if any(not NAMESPACE_NAME.fullmatch(name) for name in names):
        raise ContentLibraryError("Invalid namespace name")
    result["provides"] = {"items": _string_list(provides["items"], "items"), "namespaces": names}
    if "routes" in provides:  # route ids of the study_routes namespace (design 10, shape only)
        result["provides"]["routes"] = _string_list(provides["routes"], "routes", limit=1000, item_limit=512)
    declared = {path[len(LAYER_PREFIX["teaching"]):-5] for path in result["layers"]["teaching"] if path.endswith(".json")}
    if declared != set(names) and TEACHING_PACK_FILE not in result["files"]:
        raise ContentLibraryError("Teaching files and provided namespaces disagree")
    if result["profile"] != "core" and (LEXICON_FILE in result["files"] or TEACHING_PACK_FILE in result["files"]):
        raise ContentLibraryError("Only the core library may carry a lexicon or a teaching database")
    fingerprints = value.get("fingerprints", {})
    if (not isinstance(fingerprints, dict) or set(fingerprints) - set(result["provides"]["items"])
            or any(not isinstance(v, str) or not SHA256.fullmatch(v) for v in fingerprints.values())):
        raise ContentLibraryError("Invalid item fingerprints")
    result["fingerprints"] = dict(fingerprints)
    revisions = value.get("revisions", {})
    if (not isinstance(revisions, dict) or set(revisions) - set(result["provides"]["items"])
            or any(not isinstance(v, str) or len(v) > 200 for v in revisions.values())):
        raise ContentLibraryError("Invalid item revisions")
    if "revisions" in value:
        result["revisions"] = dict(revisions)
    references = value.get("references", [])
    if not isinstance(references, list) or len(references) > 100000:
        raise ContentLibraryError("Invalid references")
    result["references"] = []
    for reference in references:
        if (not isinstance(reference, dict) or not {"id", "kind", "requirement"} <= set(reference)
                or set(reference) - {"id", "kind", "requirement", "note"} or reference["kind"] != "item"
                or reference["requirement"] not in REQUIREMENTS):
            raise ContentLibraryError("Invalid reference")
        result["references"].append({"id": _text(reference["id"], "reference id"), "kind": "item",
                                     "requirement": reference["requirement"],
                                     "note": _text(reference.get("note", ""), "reference note", allow_empty=True, limit=2000)})
    depends = value.get("depends_libraries", [])
    if not isinstance(depends, list) or len(depends) > MAX_LIBRARIES:
        raise ContentLibraryError("Invalid library dependencies")
    result["depends_libraries"] = []
    for item in depends:
        if (not isinstance(item, dict) or set(item) != {"library_id", "version_range"}
                or not isinstance(item["library_id"], str) or not LIBRARY_ID.fullmatch(item["library_id"])):
            raise ContentLibraryError("Invalid library dependency")
        result["depends_libraries"].append({"library_id": item["library_id"],
                                            "version_range": _text(item["version_range"], "version_range", allow_empty=True, limit=200)})
    for key, fields in (("overrides", {"id", "reason"}), ("retires", {"id", "replaced_by", "reason"})):
        rows = value.get(key, [])
        if not isinstance(rows, list) or len(rows) > 100000:
            raise ContentLibraryError("Invalid " + key)
        result[key] = []
        for row in rows:
            if not isinstance(row, dict) or "id" not in row or set(row) - fields:
                raise ContentLibraryError("Invalid " + key + " entry")
            entry = {"id": _text(row["id"], key + " id"), "reason": _text(row.get("reason", ""), key + " reason", allow_empty=True, limit=2000)}
            if key == "retires":
                entry["replaced_by"] = _text(row["replaced_by"], "replaced_by") if row.get("replaced_by") is not None else None
            result[key].append(entry)
    aliases = value.get("aliases", {})
    if not isinstance(aliases, dict) or len(aliases) > 100000:
        raise ContentLibraryError("Invalid aliases")
    result["aliases"] = {_text(old, "alias"): _text(new, "alias target") for old, new in aliases.items()}
    policies = value.get("merge_policies", {})
    if not isinstance(policies, dict) or any(not isinstance(k, str) or not NAMESPACE_NAME.fullmatch(k)
                                             or v not in MERGE_POLICIES for k, v in policies.items()):
        raise ContentLibraryError("Invalid merge policies")
    if result["profile"] != "core" and "singleton" in policies.values():
        raise ContentLibraryError("Only the core library may declare singleton namespaces")
    result["merge_policies"] = dict(policies)
    if "signature" in value:
        block = value["signature"]
        # Shape only; content_library_signing decides valid/invalid/untrusted_key.
        if (not isinstance(block, dict) or len(block) > 8
                or any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 64 or len(v) > 512
                       for k, v in block.items())):
            raise ContentLibraryError("Invalid signature block")
        result["signature"] = dict(block)
    return result


def validate_namespace_document(value, name):
    if (not isinstance(value, dict) or set(value) != {"format", "version", "name", "payload"}
            or value["format"] != NAMESPACE_FORMAT or value["version"] != NAMESPACE_VERSION or value["name"] != name):
        raise ContentLibraryError("Invalid teaching namespace document: " + name)
    return value


def manifest_summary(manifest):
    """The small registry copy of a manifest shown in the library list."""
    summary = {"title": manifest["title"], "families": count_families(manifest["provides"]["items"]),
               "namespaces": list(manifest["provides"]["namespaces"]), "item_count": len(manifest["provides"]["items"])}
    for key in ("description", "language_pair", "teaching_profile"):
        if key in manifest:
            summary[key] = manifest[key]
    return summary


# ---------------------------------------------------------------- pack file

@dataclass(frozen=True)
class PackReport:
    manifest: dict
    files: dict
    teaching_documents: tuple  # (namespace, path) pairs for JSON teaching files
    has_teaching_pack: bool
    has_lexicon: bool
    total_bytes: int
    signed: bool
    signature: signing.SignatureCheck = field(default=signing.SignatureCheck("unsigned"))
    study_routes: object = None  # raw JSON payload of teaching/study_routes.json (design 10), or None


def _check_entry(info):
    if info.is_dir():
        return None
    if (info.external_attr >> 16) & 0o170000 == 0o120000:
        raise ContentLibraryError("Library packs cannot contain links")
    name = relative_path(info.filename, "pack entry")
    suffix = name.rsplit(".", 1)[-1].lower() if "." in name.rsplit("/", 1)[-1] else ""
    if suffix and "." + suffix in FORBIDDEN_SUFFIXES:
        raise ContentLibraryError("Executable code or credential material is not library content: " + name[:200])
    if info.file_size > MAX_FILE_BYTES:
        raise ContentLibraryError("Library file exceeds its size limit: " + name[:200])
    return name


def _hash_member(archive, info, expected_size):
    digest, total = hashlib.sha256(), 0
    with archive.open(info) as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > expected_size:
                raise ContentLibraryError("Library file is larger than its manifest says: " + info.filename[:200])
            digest.update(block)
    if total != expected_size:
        raise ContentLibraryError("Library file size does not match its manifest: " + info.filename[:200])
    return digest.hexdigest()


def validate_library_pack(zip_path, *, registry=None):
    """File-level validation of a library pack: shape, suffixes, sizes, sha256.

    Returns a PackReport whose signature (a SignatureCheck) has status
    unsigned, valid, invalid or untrusted_key; trust is decided against the
    registry's trusted_keys (none given: every valid key is untrusted_key).
    An invalid signature is reported, not raised: install_refusal(report)
    and PackPrecheck.blocking_reasons refuse it. Teaching JSON documents are parsed for shape only;
    typed-value decoding and the learner-table check on bundled databases are
    the service's job after extraction.
    """
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as error:
        raise ContentLibraryError("Not a library pack ZIP file") from error
    with archive:
        members = {}
        for info in archive.infolist():
            name = _check_entry(info)
            if name is None:
                continue
            if name in members:
                raise ContentLibraryError("Duplicate pack entry: " + name[:200])
            members[name] = info
        if MANIFEST_FILE not in members:
            raise ContentLibraryError("Library pack has no manifest.json")
        if members[MANIFEST_FILE].file_size > MAX_MANIFEST_BYTES:
            raise ContentLibraryError("Library manifest is too large")
        try:
            raw_manifest = json.loads(archive.read(members[MANIFEST_FILE]).decode("utf-8"))
            manifest = validate_manifest(raw_manifest)
        except (UnicodeDecodeError, ValueError) as error:
            if isinstance(error, ContentLibraryError):
                raise
            raise ContentLibraryError("Library manifest is not valid JSON") from error
        listed = set(members) - {MANIFEST_FILE}
        if listed != set(manifest["files"]):
            raise ContentLibraryError("Pack files and manifest file table disagree")
        allowed_roots = {prefix.rstrip("/") for prefix in LAYER_PREFIX.values() if prefix}
        total = 0
        for name, info in members.items():
            if name == MANIFEST_FILE:
                continue
            root = name.split("/", 1)[0] if "/" in name else None
            if root is not None and root not in allowed_roots or root is None and name not in {NOTICE_FILE, SIGNATURE_FILE}:
                raise ContentLibraryError("Unexpected file in library pack: " + name[:200])
            expected = manifest["files"][name]
            if _hash_member(archive, info, expected["bytes"]) != expected["sha256"]:
                raise ContentLibraryError("Library file checksum mismatch: " + name[:200])
            total += expected["bytes"]
        documents, route_payload = [], None
        for path in manifest["layers"]["teaching"]:
            if path == TEACHING_PACK_FILE:
                continue
            if not path.endswith(".json"):
                raise ContentLibraryError("Teaching layer holds only namespace JSON documents")
            name = path[len(LAYER_PREFIX["teaching"]):-5]
            if not NAMESPACE_NAME.fullmatch(name) or name not in manifest["provides"]["namespaces"]:
                raise ContentLibraryError("Teaching document is not a declared namespace: " + path[:200])
            if manifest["files"][path]["bytes"] > MAX_TEACHING_BYTES:
                raise ContentLibraryError("Teaching namespace exceeds its size limit: " + name)
            try:
                document = validate_namespace_document(json.loads(archive.read(members[path]).decode("utf-8")), name)
                if name == ROUTE_NAMESPACE:
                    route_payload = document["payload"]
            except (UnicodeDecodeError, ValueError, RecursionError) as error:
                if isinstance(error, ContentLibraryError):
                    raise
                raise ContentLibraryError("Teaching document is not valid JSON: " + name) from error
            documents.append((name, path))
        for layer in ("lexicon", "annotations", "review"):
            for path in manifest["layers"][layer]:
                if not path.endswith((".sqlite3", ".json", ".txt")):
                    raise ContentLibraryError("Unexpected file type in " + layer + " layer: " + path[:200])
        if TEACHING_PACK_FILE in manifest["files"] and TEACHING_PACK_FILE not in manifest["layers"]["teaching"]:
            raise ContentLibraryError("Teaching database must be listed in the teaching layer")
        if LEXICON_FILE in manifest["files"] and LEXICON_FILE not in manifest["layers"]["lexicon"]:
            raise ContentLibraryError("Lexicon must be listed in the lexicon layer")
        return PackReport(manifest=manifest, files=dict(manifest["files"]), teaching_documents=tuple(documents),
                          has_teaching_pack=TEACHING_PACK_FILE in manifest["files"],
                          has_lexicon=LEXICON_FILE in manifest["files"], total_bytes=total,
                          signed=SIGNATURE_FILE in manifest["files"] or "signature" in manifest,
                          signature=signing.check_manifest_signature(raw_manifest, registry),
                          study_routes=route_payload)


# ---------------------------------------------------------------- precheck

@dataclass(frozen=True)
class PackPrecheck:
    """Offline merge result shown before installing (section 5.2 step 2)."""
    library_id: str
    library_version: str
    replaces_version: str | None
    replaces_core: bool
    new_item_count: int
    same_fingerprint_ids: tuple
    conflicting_ids: tuple
    missing_required_references: tuple
    missing_optional_references: tuple
    retired_learned_ids: tuple
    missing_libraries: tuple
    undeclared_overrides: tuple
    unknown_fingerprint_count: int
    warnings: tuple = field(default=())
    revision_errors: tuple = field(default=())  # rule c: same id and revision, different fingerprint
    signature_error: str | None = None  # phase 5: an invalid signature is never installable
    # Design 10 (WP-R4a): the pack's study routes. Shape errors and undeclared
    # outside identities are warnings: merge ignores a refused route.
    route_count: int = 0                     # routes passing the shape and full checks (WP-R4b)
    route_entry_counts: tuple = ()           # (route_id, entry count) of those routes
    route_external_required: tuple = ()      # identities outside the pack declared required
    route_external_optional: tuple = ()      # identities outside the pack declared optional
    route_external_undeclared: tuple = ()    # outside identities missing from references (author error)
    route_external_missing: tuple = ()       # outside identities absent from the installed catalogue
    route_errors: tuple = ()                 # (route_id, reason) refused by the shape or full check
    # Review B-01: a trial merge with the installed libraries (cycle, chain too
    # deep, any merge failure). Never installable, like a signature error.
    merge_errors: tuple = ()

    def blocking_reasons(self):
        reasons = []
        if self.signature_error:
            reasons.append(self.signature_error)
        reasons.extend(self.merge_errors)
        if self.revision_errors:
            reasons.append(f"有 {len(self.revision_errors)} 项与已安装内容同 ID、同修订号但内容不同（包错误）")
        if self.missing_required_references:
            reasons.append(f"有 {len(self.missing_required_references)} 个必需引用的内容不在已安装的库中")
        return tuple(reasons)


def _is_sequence(value):
    return isinstance(value, (list, tuple))


def _entry_identity(entry):
    """The identity of one route entry: a text id or {id, kind}; None if malformed."""
    if isinstance(entry, str):
        return entry if entry.strip() and len(entry) <= CONTENT_ID_LIMIT and "\x00" not in entry else None
    if (isinstance(entry, dict) and set(entry) == {"id", "kind"} and isinstance(entry["id"], str)
            and entry["id"].strip() and len(entry["id"]) <= CONTENT_ID_LIMIT
            and isinstance(entry["kind"], str) and entry["kind"].strip() and len(entry["kind"]) <= 64):
        return entry["id"]
    return None


def route_entry_ids(spec):
    """Entry identities of an explicit-stages spec in order (malformed entries skipped)."""
    found = []
    stages = spec.get("stages") if isinstance(spec, dict) else None
    for stage in stages if _is_sequence(stages) else ():
        units = stage.get("units") if isinstance(stage, dict) else None
        for unit in units if _is_sequence(units) else ():
            sections = unit.get("sections") if isinstance(unit, dict) else None
            for section in sections if _is_sequence(sections) else ():
                entries = section.get("entries") if isinstance(section, dict) else None
                for entry in entries if _is_sequence(entries) else ():
                    identity = _entry_identity(entry)
                    if identity is not None:
                        found.append(identity)
    return found


def route_spec_problems(library_id, route_id, spec):
    """Shape pre-check of one study route spec (design 10 section 6.2); [] when it passes.

    Checks the route_id prefix, exactly one of stages/build, the
    stage/unit/section/entries nesting, entry shapes (text id or {id, kind})
    and the total entry limit. Whether identities exist, duplicates, optional
    flags and after are left to study_routes.validate_route_spec (WP-R1).
    """
    problem = route_id_problem(library_id, route_id, spec)
    if problem:
        return [problem]
    if not isinstance(spec, dict):
        return ["route spec is not a mapping"]
    problems = []
    if ("stages" in spec) == ("build" in spec):
        problems.append("route spec needs exactly one of stages or build")
    if "build" in spec and (not isinstance(spec["build"], dict) or not isinstance(spec["build"].get("kind"), str)):
        problems.append("build must be a mapping with a kind")
    total = 0
    if "stages" in spec:
        stages = spec["stages"]
        if not _is_sequence(stages) or not stages:
            problems.append("stages must be a non-empty list")
            stages = ()
        for stage in stages:
            units = stage.get("units") if isinstance(stage, dict) else None
            if not _is_sequence(units):
                problems.append("every stage needs a units list")
                continue
            for unit in units:
                sections = unit.get("sections") if isinstance(unit, dict) else None
                if not _is_sequence(sections):
                    problems.append("every unit needs a sections list")
                    continue
                for section in sections:
                    entries = section.get("entries") if isinstance(section, dict) else None
                    if not _is_sequence(entries):
                        problems.append("every section needs an entries list")
                        continue
                    total += len(entries)
                    if any(_entry_identity(entry) is None for entry in entries):
                        problems.append("entries are text ids or {id, kind} objects")
    if total > ROUTE_ENTRY_LIMIT:
        problems.append(f"route has more than {ROUTE_ENTRY_LIMIT} entries")
    return list(dict.fromkeys(problems))


def kana_identities_of(kana_rows):
    """kana:<id>:<script> identities of kana table rows (the study path's kana entries).

    kana_rows is the repository's kana_list() list; anything else means no
    kana table is at hand and gives None (the route check then takes the
    routes' own kana entries as known).
    """
    if not isinstance(kana_rows, (list, tuple)):
        return None
    return frozenset(f"{KANA_IDENTITY_PREFIX}{row['id']}:{script}" for row in kana_rows
                     for script in ("hiragana", "katakana") if row.get(script))


def route_full_problem(library_id, route_id, spec, *, catalogue_ids, kana_identities=None):
    """The full study_routes.validate_route_spec check of one shape-valid route; None when it passes.

    kana_identities None (no kana table at hand) takes every kana:<id>:<script>
    entry of the spec as known; the route build re-checks them.
    """
    if kana_identities is None:
        kana_identities = {identity for identity in route_entry_ids(spec)
                           if identity.startswith(KANA_IDENTITY_PREFIX)}
    try:
        study_routes.validate_route_spec(spec, catalogue_ids=catalogue_ids, kana_identities=kana_identities,
                                         library_id=library_id)
    except ValueError as error:
        return (str(error).splitlines()[0] if str(error) else type(error).__name__)[:200]
    if spec.get("route_id") != route_id:
        return "route spec route_id differs from its key"
    return None


def route_problems_in_catalogue(rows, *, catalogue_ids, kana_identities=None):
    """{library_id: ((route_id, reason), ...)} for routes of a running effective catalogue.

    rows are EffectiveCatalogue.study_routes() triples (route_id, library_id,
    spec); a route listed here is skipped when the RouteSet is built.
    """
    catalogue_ids = set(catalogue_ids)
    result = {}
    for route_id, library_id, spec in rows:
        problems = route_spec_problems(library_id, route_id, spec)
        reason = problems[0] if problems else route_full_problem(
            library_id, route_id, spec, catalogue_ids=catalogue_ids, kana_identities=kana_identities)
        if reason is not None:
            result.setdefault(library_id, []).append((str(route_id)[:200], reason))
    return {library_id: tuple(found) for library_id, found in result.items()}


def route_precheck(manifest, payload, *, catalogue_ids, kana_identities=None):
    """Route fields of PackPrecheck plus warning lines, from the raw study_routes payload.

    A route passing the shape pre-check is then fully validated against
    catalogue_ids | provides (and kana_identities); a failure joins
    route_errors (warning only, the route is ignored after install). Outside
    identities are classified for every shape-valid route.
    """
    library_id, provided = manifest["library_id"], set(manifest["provides"]["items"])
    references = {row["id"]: row["requirement"] for row in manifest["references"]}
    result = {"route_count": 0, "route_entry_counts": (), "route_external_required": (),
              "route_external_optional": (), "route_external_undeclared": (), "route_external_missing": (),
              "route_errors": ()}
    listed = manifest["provides"].get("routes")
    if payload is None:
        warnings = ["manifest 列出了路线，但包内没有 study_routes 文档"] if listed else []
        return result, warnings
    table = payload.get(ROUTE_TABLE_KEY) if isinstance(payload, dict) else None
    if not isinstance(table, dict):
        result["route_errors"] = (("*", "study_routes 文档应为 {routes: {route_id: 规格}}"),)
        return result, ["study_routes 文档形状不对，本库路线将被忽略"]
    errors, counts, outside, shaped = [], [], [], []
    catalogue = set(catalogue_ids) | provided
    for route_id, spec in table.items():
        problems = route_spec_problems(library_id, route_id, spec)
        if problems:
            errors.append((str(route_id)[:200], problems[0]))
            continue
        shaped.append(route_id)
        identities = route_entry_ids(spec)
        outside.extend(identity for identity in identities
                       if identity not in provided and not identity.startswith(KANA_IDENTITY_PREFIX))
        problem = route_full_problem(library_id, route_id, spec, catalogue_ids=catalogue,
                                     kana_identities=kana_identities)
        if problem is not None:
            errors.append((str(route_id)[:200], problem))
            continue
        counts.append((route_id, len(identities)))
    outside = list(dict.fromkeys(outside))
    result.update(route_count=len(counts), route_entry_counts=tuple(counts), route_errors=tuple(errors),
                  route_external_required=tuple(cid for cid in outside if references.get(cid) == "required"),
                  route_external_optional=tuple(cid for cid in outside if references.get(cid) == "optional"),
                  route_external_undeclared=tuple(cid for cid in outside if cid not in references),
                  route_external_missing=tuple(cid for cid in outside if cid not in catalogue))
    warnings = []
    if errors:
        warnings.append(f"{len(errors)} 条路线规格有误，安装后将被忽略：" + "、".join(rid for rid, _ in errors[:3]))
    if result["route_external_undeclared"]:
        warnings.append(f"包作者错误：路线引用了 {len(result['route_external_undeclared'])} 个本库以外、"
                        "未在 references 中声明的身份")
    if result["route_external_missing"]:
        warnings.append(f"路线引用的 {len(result['route_external_missing'])} 个身份不在已安装的库中，相关条目将显示为依赖缺失")
    if listed is not None and set(listed) != set(shaped):
        warnings.append("manifest 列出的路线与 study_routes 文档不一致")
    return result, warnings


def precheck_pack(manifest, *, installed_fingerprints, learned_ids, installed_library_ids,
                  catalogue_ids=None, replaces_version=None, replaces_core=False, installed_revisions=None,
                  signature=None, routes=None, kana_identities=None, merge_errors=(), previous_ids=()):
    """Compare a validated manifest with what the device already has.

    installed_fingerprints: content id -> item_fingerprint for the libraries
    that stay installed (the replaced version of the same library excluded).
    learned_ids: content ids with learner progress. catalogue_ids defaults to
    the installed fingerprint keys and is what references are resolved against.
    previous_ids: what the replaced version of this library provides; such an
    item is neither new nor a duplicate of installed content (upgrade_precheck
    reports what changed in it), so new_item_count is what the update adds.
    installed_revisions (content id -> revision text) enables conflict rule c.
    signature (the pack's SignatureCheck) adds the invalid-signature refusal.
    routes (PackReport.study_routes, the raw study_routes payload) adds the
    route summary of design 10 section 10.4; its warnings follow the others.
    kana_identities (kana:<id>:<script>, see kana_identities_of) is used by the
    full route check; None takes the routes' kana entries as known.
    merge_errors (content_library_merge.offline_merge_problems) are blocking.
    """
    provided = manifest["provides"]["items"]
    fingerprints = manifest["fingerprints"]
    catalogue = set(installed_fingerprints) if catalogue_ids is None else set(catalogue_ids)
    overrides = {row["id"] for row in manifest["overrides"]}
    previous = set(previous_ids)
    same, conflicts, undeclared, unknown = [], [], [], 0
    new_items = 0
    for content_id in provided:
        if content_id in installed_fingerprints:
            theirs, ours = installed_fingerprints[content_id], fingerprints.get(content_id)
            if ours is None:
                unknown += 1
                conflicts.append(content_id)
            elif ours == theirs:
                same.append(content_id)
            else:
                conflicts.append(content_id)
                if content_id not in overrides and manifest["profile"] != "core":
                    undeclared.append(content_id)
        elif content_id in catalogue:
            same.append(content_id)
        elif content_id not in previous:
            new_items += 1
    available = catalogue | set(provided)
    required = tuple(r["id"] for r in manifest["references"] if r["requirement"] == "required" and r["id"] not in available)
    optional = tuple(r["id"] for r in manifest["references"] if r["requirement"] == "optional" and r["id"] not in available)
    learned = set(learned_ids)
    retired = tuple(sorted(row["id"] for row in manifest["retires"] if row["id"] in learned))
    missing_libraries = tuple(d["library_id"] for d in manifest["depends_libraries"]
                              if d["library_id"] not in set(installed_library_ids))
    warnings = []
    if undeclared:
        warnings.append(f"{len(undeclared)} 项与已安装内容同 ID 但内容不同，且未在 overrides 中声明；按优先级以已安装库为准")
    if missing_libraries:
        warnings.append("依赖的库尚未安装：" + "、".join(missing_libraries))
    if optional:
        warnings.append(f"{len(optional)} 个可选引用的内容不存在，相关前置或背景将被省略")
    if unknown:
        warnings.append(f"{unknown} 项没有指纹，按冲突处理")
    revision_errors = revision_errors_of(manifest, installed_revisions or {}, set(conflicts))
    route_fields, route_warnings = route_precheck(manifest, routes, catalogue_ids=catalogue,
                                                  kana_identities=kana_identities)
    warnings.extend(route_warnings)
    return PackPrecheck(library_id=manifest["library_id"], library_version=manifest["library_version"],
                        replaces_version=replaces_version, replaces_core=replaces_core,
                        new_item_count=new_items, same_fingerprint_ids=tuple(same), conflicting_ids=tuple(conflicts),
                        missing_required_references=required, missing_optional_references=optional,
                        retired_learned_ids=retired, missing_libraries=missing_libraries,
                        undeclared_overrides=tuple(undeclared), unknown_fingerprint_count=unknown,
                        warnings=tuple(warnings), revision_errors=revision_errors,
                        signature_error=signature.refusal_reason() if signature is not None else None,
                        merge_errors=tuple(merge_errors), **route_fields)


def revision_errors_of(manifest, installed_revisions, conflicting_ids):
    """Rule c of 08 §6.3: a changed fingerprint needs a changed revision."""
    revisions = manifest.get("revisions", {})
    return tuple(sorted(cid for cid in conflicting_ids
                        if cid in revisions and cid in installed_revisions
                        and str(installed_revisions[cid]) == revisions[cid]))


@dataclass(frozen=True)
class UpgradePrecheck:
    """Extra differences when a new version of an installed library arrives (section 5.5)."""
    library_id: str
    previous_version: str | None
    library_version: str
    fingerprint_changed_ids: tuple      # same id, different fingerprint
    fingerprint_changed_learned: tuple  # of those, learned: saved snapshot now differs from the catalogue
    added_ids: tuple
    removed_ids: tuple                  # no longer provided, without alias or retirement
    removed_learned: tuple              # of those, learned: they become orphans
    retired_ids: tuple                  # (id, replaced_by|None)
    retired_learned: tuple              # (id, replaced_by|None) with progress
    aliases: tuple                      # (old, new)
    aliased_learned: tuple              # (old, new) whose old id has progress
    overrides: tuple                    # ids declared as intentional overrides of other libraries
    unknown_fingerprint_count: int = 0

    def lines(self):
        """One display line per difference category."""
        return (f"指纹变化 {len(self.fingerprint_changed_ids)} 项（其中已学 {len(self.fingerprint_changed_learned)} 项，"
                "已学项沿用保存的教案快照与原路线）",
                f"新版本新增 {len(self.added_ids)} 项",
                f"新版本不再提供 {len(self.removed_ids)} 项（其中已学 {len(self.removed_learned)} 项将变为孤儿，进度保留）",
                f"被 retires 的项 {len(self.retired_ids)} 项（其中已学 {len(self.retired_learned)} 项）",
                f"别名 {len(self.aliases)} 条（旧 ID 已学 {len(self.aliased_learned)} 项；别名只影响新入学与显示，不合并进度）",
                f"声明替代其他库同 ID 内容 {len(self.overrides)} 项（核心库优先；须确认后才以本库为准）")


def upgrade_precheck(manifest, *, previous_fingerprints, learned_ids, previous_version=None):
    """Compare a new version with the installed one of the same library_id.

    previous_fingerprints: content id -> item_fingerprint of the installed
    version (its manifest fingerprints, or the App catalogue for the bundled
    core library). learned_ids: ids with progress. Pure; nothing is written.
    """
    previous, learned = dict(previous_fingerprints), set(learned_ids)
    provided, fingerprints = manifest["provides"]["items"], manifest["fingerprints"]
    changed, unknown = [], 0
    for cid in provided:
        if cid in previous:
            ours = fingerprints.get(cid)
            if ours is None:
                unknown += 1
            elif previous[cid] and ours != previous[cid]:
                changed.append(cid)
    retired = tuple((row["id"], row.get("replaced_by")) for row in manifest["retires"])
    retired_ids = {cid for cid, _ in retired}
    aliases = tuple(sorted(manifest["aliases"].items()))
    aliased = {old for old, _ in aliases}
    current = set(provided)
    removed = tuple(sorted(cid for cid in previous
                           if cid not in current and cid not in retired_ids and cid not in aliased))
    return UpgradePrecheck(
        library_id=manifest["library_id"], previous_version=previous_version,
        library_version=manifest["library_version"], fingerprint_changed_ids=tuple(changed),
        fingerprint_changed_learned=tuple(cid for cid in changed if cid in learned),
        added_ids=tuple(cid for cid in provided if cid not in previous), removed_ids=removed,
        removed_learned=tuple(cid for cid in removed if cid in learned), retired_ids=retired,
        retired_learned=tuple(row for row in retired if row[0] in learned), aliases=aliases,
        aliased_learned=tuple(row for row in aliases if row[0] in learned),
        overrides=tuple(row["id"] for row in manifest["overrides"]), unknown_fingerprint_count=unknown)


# ---------------------------------------------------------------- merge

def item_ids_of(payload):
    """Content ids a namespace payload defines in its item tables."""
    ids = []
    if not isinstance(payload, dict):
        return ids
    for key in ITEM_TABLE_KEYS:
        table = payload.get(key)
        if isinstance(table, dict):
            ids.extend(k for k in table if isinstance(k, str))
        elif isinstance(table, list):
            ids.extend(row["id"] for row in table if isinstance(row, dict) and isinstance(row.get("id"), str))
    return ids
