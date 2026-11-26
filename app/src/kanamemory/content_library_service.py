"""Content library registry, install, enable/disable and remove (design 08 §5).

The registry ``content_libraries_v1`` is one app_setting JSON written through
StudyStore.set_setting; a mirror file in the library root lets content_pack
read the same registry while modules import. A chosen file is received into
a staging folder in blocks (open_staging; copied and hashed in one pass) and
stage_file unpacks each member once while validating it (design 08 section
16.10); installing moves the unpacked members into
``<root>/<library_id>/<version>.<sha 8>/`` and records state
pending_restart: content takes effect after the App restarts. Removing or
replacing a library only changes the registry (pending_delete); its folder is
deleted by the next startup_sync, never while this process may read it. Progress tables
are never touched; availability is a read-time projection.

Wiring (design 08 section 16.5): packs are validated with the registry's
trusted keys (stage_bytes takes a registry read made on the store thread; the
precheck re-decides trust against the latest registry), an invalid signature
or a rule-c package error is refused whatever the learner chose
(accept_missing_references only skips missing-reference blocks), the registry
source records the signature status and key fingerprint, trust_signing_key
writes a confirmed key and install(accept_overrides=...) the accepted
overrides of the installed library.

Study routes (design 10, WP-R4b): the install precheck fully validates each
route of the pack (study_routes.validate_route_spec over the installed
catalogue plus the pack's provides; kana identities from the injected
kana_identities callable, else the routes' own kana entries are taken as
known); a failing route is a warning and is ignored after install.
libraries() rows carry routes_ignored and routes_ignored_reasons for routes
of the running effective catalogue that the merge refused (prefix) or that
fail the same full check, i.e. the routes a RouteSet will skip.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile

from . import content_library_rules as rules
from . import content_pack
from .content_caches import register as register_content_cache
from .app_clock import utc_now
from .build_info import VERSION as APP_VERSION

LEARNER_TABLES = frozenset({"study_card", "review_event", "session_checkpoint", "learning_unit", "app_setting",
                            "learning_content", "learning_question", "learning_event"})


UNPACKED_FOLDER = "pack"  # in a staging folder: the members of the staged pack, unpacked once


class PackStaging:
    """A pack file being received (ContentLibraryService.open_staging), design 08 section 16.10.

    The platform writes the file's bytes here block by block as they arrive; the same pass counts them
    against MAX_PACK_BYTES and hashes them, so the file is never held in memory or read again to hash it.
    File system only, so any one thread may write it. stage_file() then validates and unpacks it; discard()
    removes everything.
    """

    def __init__(self, root, source_name):
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix=".incoming-", dir=root))
        self.archive = self.folder / "pack.zip"
        self.source_name = str(source_name)[:200]
        self.size = 0
        self._digest = hashlib.sha256()
        self._stream = open(self.archive, "wb")

    def write(self, block):
        """One block of the file; ValueError(PACK_TOO_LARGE) once the file passes MAX_PACK_BYTES."""
        if self._stream is None:
            raise ValueError("The pack file is already complete")
        size = len(block)
        if self.size + size > rules.MAX_PACK_BYTES:
            raise ValueError(rules.PACK_TOO_LARGE)
        self._digest.update(block)
        self._stream.write(block)
        self.size += size
        return size

    def write_all(self, data, block=1024 * 1024):
        """Bytes already in memory (an older caller), written block by block."""
        view = memoryview(data)
        for start in range(0, len(view), block):
            self.write(view[start:start + block])

    def named(self, name):
        """The file's display name once the platform knows it (the picker reports it after the choice)."""
        if name:
            self.source_name = str(name)[:200]

    def finish(self):
        """Close the file; its sha256."""
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        return self._digest.hexdigest()

    def discard(self):
        if self._stream is not None:
            self._stream.close()
            self._stream = None
        shutil.rmtree(self.folder, ignore_errors=True)


@dataclass(frozen=True)
class StagedPack:
    """A staged, file-level validated pack, its members unpacked; no learner database was touched.

    stage_file may run on a worker thread; precheck_staged then reads the
    registry and progress on the thread that owns the store connection.
    """
    staging: Path
    unpacked: Path
    report: rules.PackReport
    source_name: str
    source_sha256: str
    members: tuple = ()  # (path, size, modification time ns) of each unpacked member when it was checked

    @property
    def manifest(self):
        return self.report.manifest


@dataclass(frozen=True)
class PackInspection:
    """A staged, validated pack with its offline precheck (section 5.2 step 2/3)."""
    staging: Path
    unpacked: Path
    report: rules.PackReport
    precheck: rules.PackPrecheck
    source_name: str
    source_sha256: str
    members: tuple = ()  # as StagedPack.members

    @property
    def manifest(self):
        return self.report.manifest


@dataclass(frozen=True)
class PreparedInstall:
    """An install up to its registry write (prepare_install): the folder is written, the registry not yet."""
    inspection: PackInspection
    base: dict          # the registry the install was prepared against
    registry: dict      # the registry to write
    entry: dict
    target: Path


class _RegistryReader:
    """The store of a snapshot(): one registry read, no write."""

    def __init__(self, registry, data_dir):
        self._registry = json.loads(json.dumps(registry))
        self.data_dir = data_dir

    def get_setting(self, key, default=None):
        return json.loads(json.dumps(self._registry)) if key == rules.REGISTRY_SETTING else default

    def set_setting(self, key, value):
        raise RuntimeError("A content library snapshot never writes the store")


def _no_store_read():
    raise RuntimeError("A content library snapshot reads no availability")


def _no_links(path, root):
    """``path`` (inside ``root``) with no symbolic link at or below the library's own folders.

    The folders from the root upwards are the system's, not a library's: on Android the app's
    files directory is reached through the link /data/user/0, and checking it made every
    installed manifest unreadable there (native8: an update compared against an empty library).
    """
    path, root = Path(path), Path(root)
    inside = len(path.relative_to(root).parts)  # ValueError: not inside the root
    if path.is_symlink() or any(parent.is_symlink() for parent in tuple(path.parents)[:inside - 1]):
        raise ValueError("Library directories cannot contain links")
    return path


def _remove_tree(root, target):
    """Delete only a directory inside the library root; follow no links."""
    root, target = Path(root).resolve(), Path(target)
    if target.is_symlink() or not target.is_dir():
        if target.exists() or target.is_symlink():
            raise ValueError("Refusing to delete a non-directory library entry")
        return
    if not target.resolve().is_relative_to(root) or target.resolve() == root:
        raise ValueError("Refusing to delete outside the library root")
    for current, directories, files in os.walk(target):
        for name in directories + files:
            if (Path(current) / name).is_symlink():
                raise ValueError("Library directories cannot contain links")
    shutil.rmtree(target)


def _learner_tables(path):
    with open(path, "rb") as stream:
        header = stream.read(16)
    if not header.startswith(b"SQLite format 3\0"):
        raise ValueError("Library database is not an SQLite file")
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables & LEARNER_TABLES:
        raise ValueError("Learner state must never enter a content library")


_DEPENDENCIES = []  # [(catalogue ids, {content id: depends_on})], one per process catalogue
register_content_cache(lambda: _DEPENDENCIES.clear(), "content_library_service._DEPENDENCIES")


def _catalogue_dependencies(ids):
    if _DEPENDENCIES and _DEPENDENCIES[0][0] == ids:
        return _DEPENDENCIES[0][1]
    from .learning_content import learning_item_views
    dependencies = {item["id"]: tuple(item.get("depends_on") or ()) for item in learning_item_views()
                    if item.get("depends_on")}
    _DEPENDENCIES[:] = [(ids, dependencies)]
    return dependencies


def availability_catalogue(*, word_exists=None):
    """The effective catalogue as a content_availability.Catalogue (design 08 section 8.1).

    ids are the merged catalogue this process serves and ``effective`` the
    content_pack.effective_catalogue() of the same startup merge (owning
    library, retirements, missing required references; process constants).
    The Store adds the current registry and progress rows per read action.
    """
    from .content_availability import Catalogue
    from .learning_content import catalogue_ids
    ids = catalogue_ids()
    return Catalogue(ids=ids, effective=content_pack.effective_catalogue(), word_exists=word_exists,
                     dependencies=_catalogue_dependencies(ids))


def registry_for_availability(store):
    """The saved registry, or None before the first start wrote one; never writes."""
    raw = store.get_setting(rules.REGISTRY_SETTING, None)
    return None if raw is None else rules.validate_registry(raw)


def library_titles(store):
    """library_id -> display title for the learning library's 所属库 tags; never writes."""
    registry = registry_for_availability(store) or rules.default_registry()
    return {entry["library_id"]: ("核心库" if entry["library_id"] == rules.CORE_LIBRARY_ID
                                  else entry.get("summary", {}).get("title") or entry["library_id"])
            for entry in registry["libraries"]}


class ContentLibraryService:
    def __init__(self, store, *, root=None, catalogue_ids=None, catalogue_fingerprints=None, learned_ids=None,
                 word_exists=None, catalogue_revisions=None, kana_identities=None, availability=None):
        self.store = store
        self._availability = availability
        self.root = Path(root) if root is not None else (content_pack.library_root()
                                                          or Path(store.data_dir) / rules.LIBRARIES_DIRECTORY)
        self._catalogue_ids = catalogue_ids
        self._catalogue_fingerprints = catalogue_fingerprints
        self._catalogue_revisions = catalogue_revisions
        self._learned_ids = learned_ids
        self._word_exists = word_exists
        self._kana_identities = kana_identities
        self._manifests = {}

    # ------------------------------------------------------------ registry

    def registry(self):
        """The validated registry; the first start writes the core-only default."""
        raw = self.store.get_setting(rules.REGISTRY_SETTING, None)
        if raw is None:
            registry = rules.default_registry(content_pack.bundled_stamp())
            self._write(registry)
            return registry
        return rules.validate_registry(raw)

    def _write(self, registry):
        registry = rules.validate_registry(registry)
        self.store.set_setting(rules.REGISTRY_SETTING, registry)
        self.write_mirror(registry)
        return registry

    def write_mirror(self, registry=None):
        registry = rules.validate_registry(registry if registry is not None else self.registry())
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / rules.MIRROR_FILE
        handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", prefix=".mirror-", suffix=".json",
                                             dir=self.root, delete=False)
        try:
            with handle:
                json.dump(registry, handle, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
            Path(handle.name).replace(target)
        except BaseException:
            Path(handle.name).unlink(missing_ok=True)
            raise
        return target

    # ------------------------------------------------------------ bundled content replaced a core pack (5.7)

    def _bundled_notice_path(self):
        return self.root / rules.BUNDLED_NOTICE_FILE

    def _write_bundled_notice(self, notice):
        """Kept beside the mirror (device state, never in a backup) until the next library change."""
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            self._bundled_notice_path().write_text(json.dumps(notice, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # the page then shows no notice; the registry change stands

    def _clear_bundled_notice(self):
        try:
            self._bundled_notice_path().unlink(missing_ok=True)
        except OSError:
            pass

    def bundled_notice(self):
        """{"replaced_version", "bundled_version"} after a start replaced an imported core pack by newer bundled
        content, until the next library change (install, enable/disable, remove); else None."""
        try:
            value = json.loads(self._bundled_notice_path().read_text("utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return None
        if (not isinstance(value, dict) or set(value) != {"replaced_version", "bundled_version"}
                or not all(isinstance(text, str) and rules.VERSION_TEXT.fullmatch(text) for text in value.values())):
            return None
        return value

    def root_mismatch(self):
        """content_pack resolved another root; its libraries would never load."""
        loaded = content_pack.library_root()
        return loaded is not None and Path(loaded).resolve() != self.root.resolve()

    def startup_sync(self):
        """Reconcile the registry with what content_pack loaded in this process.

        Damaged libraries are marked (progress stays); an enabled library
        marked damaged earlier (not loaded by this process) is checked again
        and becomes ready when its files pass (review B-04: damaged is not a
        final state); pending_restart entries that this process loaded become
        ready; superseded version folders of ready libraries and the folders
        listed in pending_delete (removed or replaced libraries, review B-03)
        are deleted; the mirror is rewritten when it differs.

        Every saved change this process started with is settled here: a file entry pending_restart whose version
        and switch this process loaded becomes ready whether or not it was loaded, and the bundled core entry a
        removal left pending_restart becomes ready once this process serves the bundled content. Section 5.7:
        an imported core pack not newer than the stamped bundled content is replaced by it first
        (bundled_core_replacement; the notice is kept for the libraries page).
        """
        registry = self.registry()
        loaded = content_pack.active_registry()
        status = content_pack.library_status()
        changed = False
        # The bundled core entry stores the stamp's version (section 5.7; "bundled" without a valid stamp). A
        # registry written before, or by an app with other bundled content, is brought up here; content_pack
        # did the same to the registry it loaded, so this is no change that waits for a restart.
        current = rules.with_bundled_version(registry, content_pack.bundled_stamp())
        if current is not None:
            registry, changed = current, True
        serves_bundled = loaded is None or (rules.registry_entry(loaded, rules.CORE_LIBRARY_ID) or {}).get(
            "source", {}).get("kind") != "file"
        # Section 5.7: newer bundled content replaces an older imported core pack. content_pack made the same
        # change to the registry it loaded, so nothing waits for a restart (unless this process serves the pack).
        replaced = content_pack.bundled_core_replacement(registry, self.root)
        if replaced is not None:
            registry, notice = replaced
            if not serves_bundled:
                rules.registry_entry(registry, rules.CORE_LIBRARY_ID)["state"] = "pending_restart"
            self._write_bundled_notice(notice)
            changed = True
        for entry in registry["libraries"]:
            if entry["source"]["kind"] != "file":
                # The bundled core after a removal of an imported one: settled once this process serves it.
                if entry["state"] == "pending_restart" and serves_bundled:
                    entry["state"], changed = "ready", True
                continue
            seen = status.get(entry["library_id"])
            if seen is not None and seen["state"] == "damaged":
                if entry["state"] != "damaged":
                    entry["state"], changed = "damaged", True
                continue
            if entry["state"] == "damaged" and entry["enabled"] and seen is None:
                if self._passes_recheck(entry):
                    entry["state"], changed = "ready", True  # loaded from the next start on
                continue
            if entry["state"] == "pending_restart" and loaded is not None and entry["enabled"]:
                # This process started with this version switched on: the change is applied, loaded or not
                # (a library left out of this start is "ready" and its row says why it was not loaded).
                mirrored = rules.registry_entry(loaded, entry["library_id"])
                if (mirrored is not None and mirrored["content_sha256"] == entry["content_sha256"]
                        and mirrored["enabled"]):
                    entry["state"], changed = "ready", True
            if entry["state"] == "pending_restart" and not entry["enabled"] and loaded is not None:
                mirrored = rules.registry_entry(loaded, entry["library_id"])
                if mirrored is not None and not mirrored["enabled"]:
                    entry["state"], changed = "ready", True
            if entry["state"] == "ready":
                self._remove_superseded(entry)
        finished = self._delete_pending(registry)
        if finished:
            registry, changed = rules.with_pending_delete(registry, drop=finished), True
        if changed:
            registry = self._write(registry)
        elif loaded != registry:
            self.write_mirror(registry)
        return {"root_mismatch": self.root_mismatch(), "pending_restart": rules.pending_restart(registry)}

    def _passes_recheck(self, entry):
        """A damaged library's files pass the startup checks again (manifest, sizes, teaching checksums).

        A merge failure is only seen by the next start's merge, which marks
        the library damaged again if it still fails.
        """
        layer = content_pack.LibraryLayer(entry, self.root / entry["path"])
        if layer.damaged:
            return False
        try:
            for name in sorted(layer.teaching):
                layer.read_namespace(name)
        except (OSError, ValueError, RecursionError):
            return False
        return True

    def _delete_pending(self, registry):
        """Delete pending_delete folders nothing uses any more; return the rows that are done.

        A folder the registry still points into (a reinstalled library) is
        left to _remove_superseded; one a library layer of this process reads
        stays listed for a later start; so does one whose deletion failed
        (a locked file).
        """
        done = []
        if not registry.get("pending_delete"):
            return done
        used = [entry["path"] for entry in registry["libraries"] if entry["source"]["kind"] == "file"]
        reading = [layer.directory.resolve() for layer in content_pack.library_layers()]
        for path in registry.get("pending_delete", ()):
            if any(row == path or row.startswith(path + "/") for row in used):
                done.append(path)
                continue
            target = self.root / path
            try:
                resolved = target.resolve()
                if any(folder == resolved or folder.is_relative_to(resolved) for folder in reading):
                    continue
                _remove_tree(self.root, target)
            except (OSError, ValueError):
                continue
            done.append(path)
        return done

    def _remove_superseded(self, entry):
        parent = self.root / entry["library_id"]
        if not parent.is_dir() or parent.is_symlink():
            return
        keep = (self.root / entry["path"]).resolve()
        # A folder this process still reads (parts of its documents are read on demand) waits for a later start.
        reading = {layer.directory.resolve() for layer in content_pack.library_layers()}
        for child in parent.iterdir():
            if (child.is_dir() and not child.is_symlink() and child.resolve() != keep
                    and child.resolve() not in reading and not child.name.startswith(".")):
                try:
                    _remove_tree(self.root, child)
                except (OSError, ValueError):
                    continue

    # ------------------------------------------------------------ views

    def catalogue_ids(self):
        if self._catalogue_ids is not None:
            return frozenset(self._catalogue_ids())
        from .learning_content import catalogue_ids
        return catalogue_ids()

    def catalogue_fingerprints(self):
        if self._catalogue_fingerprints is not None:
            return dict(self._catalogue_fingerprints())
        from .learning_content import catalogue_fingerprints
        return catalogue_fingerprints()

    def catalogue_revisions(self, content_ids):
        """content id -> revision text of the core catalogue items among content_ids (rule c)."""
        if self._catalogue_revisions is not None:
            revisions = dict(self._catalogue_revisions())
            return {cid: str(revisions[cid]) for cid in content_ids if revisions.get(cid) is not None}
        from .learning_content import get_learning_item
        result = {}
        for content_id in content_ids:
            item = get_learning_item(content_id)
            if item is not None and item.get("revision") is not None:
                result[content_id] = str(item["revision"])
        return result

    def kana_identities(self):
        """kana:<id>:<script> identities for the route check, or None when no kana table was given."""
        if self._kana_identities is None:
            return None
        value = self._kana_identities()
        return None if value is None else frozenset(value)

    def learned_ids(self):
        if self._learned_ids is not None:
            return frozenset(self._learned_ids())
        return frozenset(self.store.queries.course_ids())

    def availability(self):
        """The AvailabilityView the home page counts from (LearningStore.availability(), review B-11)."""
        if self._availability is not None:
            return self._availability()
        from .learning_store import availability_view
        return availability_view(self.store)

    def installed_manifest(self, entry):
        if entry["source"]["kind"] != "file":
            return None
        key = (entry["library_id"], entry["content_sha256"])
        if key not in self._manifests:
            try:
                path = _no_links(self.root / entry["path"] / rules.MANIFEST_FILE, self.root)
                self._manifests[key] = rules.validate_manifest(json.loads(path.read_text("utf-8")))
            except (OSError, ValueError, UnicodeDecodeError):
                self._manifests[key] = None
        return self._manifests[key]

    def _provided_ids(self, entry):
        if entry["source"]["kind"] == "bundled":
            return self.catalogue_ids()
        manifest = self.installed_manifest(entry)
        return frozenset(manifest["provides"]["items"]) if manifest else frozenset()

    def _route_count(self, entry):
        """Study routes a file library provides (design 10 section 9); 0 for the bundled core."""
        manifest = self.installed_manifest(entry)
        if not manifest:
            return 0
        return sum(1 for route_id in manifest["provides"].get("routes", ())
                   if rules.route_id_problem(entry["library_id"], route_id) is None)

    def _ignored_routes(self, registry, catalogue):
        """library_id -> ((route_id, reason), ...): routes of this process's catalogue a RouteSet skips."""
        effective = content_pack.effective_catalogue()
        result = {}
        for library_id, _, route_id, reason in getattr(effective, "refused_routes", ()):
            result.setdefault(library_id, []).append((str(route_id)[:200], str(reason)[:200]))
        rows = tuple(effective.study_routes()) if callable(getattr(effective, "study_routes", None)) else ()
        if rows:
            known = set(catalogue)
            for entry in rules.effective_entries(registry):
                if entry["source"]["kind"] == "file":
                    known.update(self._provided_ids(entry))
            problems = rules.route_problems_in_catalogue(rows, catalogue_ids=known,
                                                         kana_identities=self.kana_identities())
            for library_id, found in problems.items():
                result.setdefault(library_id, []).extend(found)
        return {library_id: tuple(found) for library_id, found in result.items()}

    def libraries(self):
        """List rows for the settings page; no SQL, one registry read."""
        from .content_availability import ORPHANED, library_of_content, library_state_text
        registry = self.registry()
        status = content_pack.library_status()
        notes = content_pack.library_load_notes()
        catalogue, learned = self.catalogue_ids(), self.learned_ids()
        view = self.availability()
        orphans = {cid: library_of_content(view.catalogue, cid)
                   for cid, state in view.states.items() if state == ORPHANED}
        ignored = self._ignored_routes(registry, catalogue)
        stamp = content_pack.bundled_stamp()  # section 5.7: the bundled content's version, when stamped
        rows = []
        for entry in rules.effective_entries(registry) + [e for e in registry["libraries"]
                                                          if e not in rules.effective_entries(registry)]:
            summary = entry.get("summary", {})
            bundled = entry["source"]["kind"] == "bundled"
            provided = self._provided_ids(entry)
            if bundled:
                # The process catalogue also holds the items of the loaded extension libraries;
                # the core's own are those no library provides (native8: 语法 425 with a library on, 324 off).
                provided = frozenset(cid for cid in provided if view.catalogue.provider(cid) is None)
            # The home page's orphans (review B-11), by owning library; one no
            # loaded library names counts for the installed library providing it.
            orphaned = 0 if bundled else sum(1 for cid, owner in orphans.items()
                                             if owner == entry["library_id"] or (owner is None and cid in provided))
            seen = status.get(entry["library_id"])
            if entry["state"] == "damaged":
                check = "校验失败，已视为停用" + (f"：{seen['reason']}" if seen and seen.get("reason") else "")
            elif entry["state"] == "pending_restart":
                check = "重启后生效"
            elif bundled:
                check = "内置，随应用校验"
            elif seen is None:
                note = notes.get(entry["library_id"])
                check = "本次启动未载入" + (f"：{note}" if note else "")
            else:
                check = "校验通过"
            families = summary.get("families") if summary.get("families") else rules.count_families(provided)
            source = entry["source"]
            bundled_version = stamp["library_version"] if bundled and stamp else None
            rows.append({"library_id": entry["library_id"], "title": summary.get("title") or entry["library_id"],
                         "version": bundled_version or entry["library_version"], "bundled_version": bundled_version,
                         "profile": entry["profile"], "enabled": entry["enabled"],
                         "state": entry["state"], "source_kind": source["kind"],
                         "source_name": source["name"], "signature_status": source["signature_status"],
                         "installed_at_utc": entry["installed_at_utc"], "source_sha256": source["sha256"],
                         "key_fingerprint": source.get("key_fingerprint"), "public_key_id": source.get("public_key_id"),
                         "check": check, "families": families, "item_count": summary.get("item_count", len(provided)),
                         "learned": len(provided & learned), "orphaned": orphaned,
                         "status_text": None if bundled else library_state_text(registry, entry["library_id"]),
                         "is_core": entry["library_id"] == rules.CORE_LIBRARY_ID, "removable": not bundled,
                         "priority": registry["priority"].index(entry["library_id"]),
                         "route_count": self._route_count(entry),
                         "routes_ignored": len(ignored.get(entry["library_id"], ())),
                         "routes_ignored_reasons": ignored.get(entry["library_id"], ())})
        return rows

    def pending_restart(self):
        """A saved change this process does not serve yet (it keeps the content it started with).

        A library installed, enabled or disabled is marked pending_restart; a library removed, or
        overrides accepted or withdrawn, leave no such mark, so the registry this process loaded
        (content_pack.active_registry) is compared with the saved one for those.
        """
        registry = self.registry()
        if rules.pending_restart(registry):
            return True
        loaded = content_pack.active_registry()
        if loaded is None:
            return False
        kept = {entry["library_id"] for entry in registry["libraries"]}
        return (any(entry["library_id"] not in kept for entry in rules.effective_entries(loaded))
                or rules.accepted_overrides(loaded) != rules.accepted_overrides(registry))

    # ------------------------------------------------------------ install

    def open_staging(self, source_name="library.zip"):
        """A PackStaging in the library root that the platform writes the chosen file into (any thread)."""
        return PackStaging(self.root, source_name)

    def stage_file(self, staging, *, registry=None):
        """Validate a received pack file (a PackStaging) and unpack its members; safe on a worker thread.

        Only the file system and the archive are read here, never the store:
        pass ``registry`` (a registry() read made on the store thread) to judge
        the signature against its trusted keys; precheck_staged re-decides the
        trust against the latest registry either way. The staging folder lives
        in the library root; discard() removes it, and so does any failure here.

        Streaming (design 08 section 16.10): each member is read from the archive once, block by block, and
        written to its place in the staging folder while it is hashed; the databases are checked there; the
        archive itself is deleted once its members are out. Nothing holds the pack or a database in memory.
        """
        try:
            sha256 = staging.finish()
            if not staging.size:
                raise ValueError("请选择一个内容库文件。")
            unpacked = staging.folder / UNPACKED_FOLDER
            report = self._validate_archive(staging.archive, unpacked, registry=registry)
            staging.archive.unlink()
            return StagedPack(staging=staging.folder, unpacked=unpacked, report=report,
                              source_name=staging.source_name, source_sha256=sha256,
                              members=self._member_stamps(unpacked, report.manifest))
        except BaseException:
            staging.discard()
            raise

    def stage_bytes(self, data, source_name="library.zip", *, registry=None):
        """stage_file for pack bytes already in memory (an older caller, tests): written block by block."""
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("请选择一个内容库文件。")
        if len(data) > rules.MAX_PACK_BYTES:
            raise ValueError(rules.PACK_TOO_LARGE)
        staging = self.open_staging(source_name)
        try:
            staging.write_all(data)
        except BaseException:
            staging.discard()
            raise
        return self.stage_file(staging, registry=registry)

    def precheck_staged(self, staged):
        """Offline merge precheck against the registry and progress (store thread).

        The signature's trust is re-decided against the current registry, so a
        key confirmed meanwhile (or a pack staged without a registry) is judged
        by the trusted keys of this moment.
        """
        try:
            registry = self.registry()
            report = replace(staged.report, signature=rules.pack_signature(staged.report, registry))
            precheck = self._precheck(report, registry, staged.unpacked)
        except BaseException:
            self.discard(staged)
            raise
        return PackInspection(staging=staged.staging, unpacked=staged.unpacked, report=report, precheck=precheck,
                              source_name=staged.source_name, source_sha256=staged.source_sha256,
                              members=staged.members)

    def inspect_bytes(self, data, source_name="library.zip"):
        """stage_bytes followed by precheck_staged on the calling thread."""
        return self.precheck_staged(self.stage_bytes(data, source_name, registry=self.registry()))

    @staticmethod
    def _refuse_early(manifest):
        """The refusals a manifest alone decides, before any member is unpacked."""
        if manifest["profile"] == "core" and manifest["library_id"] != rules.CORE_LIBRARY_ID:
            raise rules.ContentLibraryError("核心库的 library_id 必须是 " + rules.CORE_LIBRARY_ID)
        if manifest["profile"] != "core" and manifest["library_id"] == rules.CORE_LIBRARY_ID:
            raise rules.ContentLibraryError("扩展库不能使用核心库的 library_id")
        # A pack made for a newer app may hold content this app refuses lesson by lesson, or cannot read at
        # all: say so before anything is installed (design 08 section 4.2).
        needed = rules.app_too_old(manifest, APP_VERSION)
        if needed:
            raise rules.AppTooOldError(rules.APP_TOO_OLD.format(needed=needed, current=APP_VERSION))

    def _validate_archive(self, archive, unpacked, *, registry=None):
        """validate_library_pack, every member unpacked under ``unpacked`` in the same pass (typed values of the
        teaching documents decoded while each is in hand), then the database checks on the unpacked files."""
        unpacked.mkdir()

        def unpack(name):
            target = unpacked / name
            target.parent.mkdir(parents=True, exist_ok=True)
            return open(target, "xb")
        report = rules.validate_library_pack(archive, registry=registry, unpack=unpack,
                                             check_manifest=self._refuse_early, decode=content_pack.decode_value)
        for path in report.manifest["files"]:
            if path.endswith(".sqlite3"):
                _learner_tables(unpacked / path)
                if path == rules.TEACHING_PACK_FILE:
                    content_pack.validate_pack(unpacked / path)
        return report

    def _member_stamps(self, unpacked, manifest):
        """(path, size, modification time ns) of every unpacked member, manifest.json included, right after
        the check: what install compares (a stat each, no file opened again)."""
        rows = []
        for name in (rules.MANIFEST_FILE, *sorted(manifest["files"])):
            stat = _no_links(Path(unpacked) / name, self.root).stat()
            rows.append((name, stat.st_size, stat.st_mtime_ns))
        return tuple(rows)

    def _recheck_unpacked(self, inspection, registry):
        """The staged members are still what was checked (install): each member's size and modification time
        as recorded right after the check (sha256 and sizes were verified then, while unpacking), and the
        manifest. Returns the report with the signature's trust re-decided against ``registry``."""
        unpacked = Path(inspection.unpacked)
        if not inspection.members:
            raise rules.ContentLibraryError("Library file checksum mismatch: " + rules.MANIFEST_FILE)
        for name, size, modified in inspection.members:
            try:
                stat = _no_links(unpacked / name, self.root).stat()
            except (OSError, ValueError) as error:
                raise rules.ContentLibraryError("Library file checksum mismatch: " + name[:200]) from error
            if (stat.st_size, stat.st_mtime_ns) != (size, modified):
                raise rules.ContentLibraryError("Library file checksum mismatch: " + name[:200])
        try:
            manifest = rules.validate_manifest(json.loads((unpacked / rules.MANIFEST_FILE).read_text("utf-8")))
        except (OSError, ValueError, UnicodeDecodeError) as error:
            raise rules.ContentLibraryError("Library file checksum mismatch: " + rules.MANIFEST_FILE) from error
        if manifest != inspection.manifest or {row[0] for row in inspection.members} != {
                rules.MANIFEST_FILE, *manifest["files"]}:
            raise ValueError("内容库文件在检查后发生了变化，请重新选择。")
        return replace(inspection.report, signature=rules.pack_signature(inspection.report, registry))

    def _precheck(self, report, registry=None, unpacked=None):
        """precheck_pack with the pack's signature, conflict rule c (installed revisions)
        and, given the staged pack's unpacked members, a trial merge with the installed libraries (review B-01)."""
        manifest = report.manifest
        registry = self.registry() if registry is None else registry
        existing = rules.registry_entry(registry, manifest["library_id"])
        installed, revisions = {}, {}
        core_fingerprints = {} if manifest["profile"] == "core" else self.catalogue_fingerprints()
        # The version being replaced does not stay installed. While it is loaded the process catalogue holds
        # its items (the pack then "conflicted" with its own old version); while it is switched off it holds
        # none (every item then counted as new). An id the catalogue serves from another owner still counts.
        previous, own = self._previous_and_own(manifest, registry)
        core_fingerprints = {cid: value for cid, value in core_fingerprints.items() if cid not in own}
        installed.update(core_fingerprints)
        for entry in rules.effective_entries(registry):
            if entry["library_id"] == manifest["library_id"] or entry["source"]["kind"] != "file":
                continue
            other = self.installed_manifest(entry)
            if other:
                installed.update(other["fingerprints"])
                for content_id in other["provides"]["items"]:
                    installed.setdefault(content_id, "")
                for content_id, revision in other.get("revisions", {}).items():
                    revisions.setdefault(content_id, revision)
        declared = manifest.get("revisions", {})
        core_candidates = sorted(cid for cid in declared if cid in core_fingerprints and cid not in revisions)
        if core_candidates:
            for content_id, revision in self.catalogue_revisions(core_candidates).items():
                revisions.setdefault(content_id, revision)
        catalogue = set(installed) | (set() if manifest["profile"] == "core" else set(self.catalogue_ids()) - own)
        merge_errors = (self._trial_merge(report, registry, unpacked, catalogue)
                        if unpacked is not None and manifest["profile"] != "core" else ())
        check = rules.precheck_pack(manifest, installed_fingerprints=installed, learned_ids=self.learned_ids(),
                                    installed_library_ids=[e["library_id"] for e in registry["libraries"]],
                                    catalogue_ids=catalogue,
                                    replaces_version=existing["library_version"] if existing else None,
                                    replaces_core=manifest["profile"] == "core",
                                    installed_revisions=revisions, signature=report.signature,
                                    routes=report.study_routes, kana_identities=self.kana_identities(),
                                    merge_errors=merge_errors, previous_ids=previous)
        bundled_error = rules.bundled_precheck_error(manifest, content_pack.bundled_stamp())
        if bundled_error:
            check = replace(check, bundled_error=bundled_error)
        same_version = rules.same_version_error(manifest, existing)
        if same_version:
            check = replace(check, same_version_error=same_version)
        if previous:
            # Rule c against the version being replaced, loaded or not: an item whose content changed keeps its
            # revision only by a package error (saved lessons and their questions are matched by revision).
            old = self.installed_manifest(existing) or {}
            changed = {cid for cid, value in old.get("fingerprints", {}).items()
                       if value and manifest["fingerprints"].get(cid) not in (None, value)}
            stale = rules.revision_errors_of(manifest, old.get("revisions", {}), changed)
            if stale:
                check = replace(check, revision_errors=tuple(sorted({*check.revision_errors, *stale})))
        return check

    def _previous_and_own(self, manifest, registry):
        """The ids of the version being replaced, and those the running catalogue serves from it."""
        fixed = getattr(self, "_fixed_own", None)
        if fixed is not None and fixed[0] == manifest["library_id"]:
            return fixed[1], fixed[2]
        existing = rules.registry_entry(registry, manifest["library_id"])
        if manifest["profile"] == "core" and existing is not None and existing["source"]["kind"] == "bundled":
            # A core pack over the built-in core is an update of it (design 08 section 5.5): the built-in
            # content is the previous version (the catalogue's ids), so 新增 is only what it lacks. The
            # precheck of a core pack counts no catalogue item as installed, so none is "its own" here.
            return self._provided_ids(existing), frozenset()
        previous = (self._provided_ids(existing) if existing is not None and existing["source"]["kind"] == "file"
                    and manifest["profile"] != "core" else frozenset())
        provider = self.availability().catalogue.provider if previous else None
        return previous, frozenset(cid for cid in previous if provider(cid) == manifest["library_id"])

    def snapshot(self, manifest):
        """A read-only copy of this service for a worker thread, for one pack (``manifest``).

        Everything this pack's precheck and install read from the store (registry, learned ids, the
        availability of the version being replaced, kana identities) and from the in-memory catalogue
        is read HERE, on the store's thread; the copy then reads only files. Its registry() is this
        registry and it never writes: commit_install, on the store's thread, writes the registry.
        """
        registry = self.registry()
        learned = self.learned_ids()
        kana = self.kana_identities()
        ids = self.catalogue_ids()
        fingerprints = self.catalogue_fingerprints()
        previous, own = self._previous_and_own(manifest, registry)
        declared = sorted(cid for cid in manifest.get("revisions", {}) if cid in fingerprints)
        revisions = self.catalogue_revisions(declared) if declared else {}
        view = ContentLibraryService(_RegistryReader(registry, getattr(self.store, "data_dir", None)), root=self.root,
                                     catalogue_ids=lambda: ids, catalogue_fingerprints=lambda: fingerprints,
                                     catalogue_revisions=lambda: revisions, learned_ids=lambda: learned,
                                     word_exists=self._word_exists, kana_identities=lambda: kana,
                                     availability=_no_store_read)
        view._manifests = self._manifests
        view._fixed_own = (manifest["library_id"], previous, own)
        return view

    def _trial_merge(self, report, registry, unpacked, catalogue):
        """content_library_merge.offline_merge_problems: the pack over the enabled installed libraries.

        Installed extension layers are read as content_pack reads them at
        startup (an unreadable one is left out: it would not load either);
        core items stand in by id (their prerequisites never point into an
        extension). The pack takes its own library_id's place in priority.
        The pack's documents are read from its unpacked members (nothing is decompressed again).
        """
        from . import content_library_merge as merge
        manifest = report.manifest
        payloads = {}
        for name, path in report.teaching_documents:
            raw = _no_links(Path(unpacked) / path, self.root).read_text("utf-8")
            payloads[name] = content_pack.decode_value(json.loads(raw)["payload"])
            del raw
        candidate = merge.ExtensionLibrary(manifest["library_id"], manifest, payloads)
        libraries, placed = [], False
        for entry in rules.effective_entries(registry):
            if entry["library_id"] == manifest["library_id"]:
                libraries.append(candidate)
                placed = True
                continue
            if entry["source"]["kind"] != "file" or entry["profile"] == "core":
                continue
            layer = content_pack.LibraryLayer(entry, self.root / entry["path"])
            if layer.damaged:
                continue
            try:
                libraries.append(merge.ExtensionLibrary(layer.library_id, layer.manifest, {
                    name: layer.read_namespace(name) for name in sorted(layer.teaching)}))
            except (OSError, ValueError, RecursionError):
                continue
        if not placed:
            libraries.append(candidate)
        return merge.offline_merge_problems(core_items={cid: {} for cid in catalogue}, extensions=libraries,
                                            overrides_accepted=rules.accepted_overrides(registry))

    def discard(self, inspection):
        shutil.rmtree(inspection.staging, ignore_errors=True)

    def install(self, inspection, *, accept_missing_references=False, accept_overrides=False, now=None):
        """Copy the staged pack into the library root and register it pending restart.

        The staged bytes and the precheck are re-read against the latest
        registry. An invalid signature (install_refusal) or a rule-c package
        error is always refused; accept_missing_references only skips the
        missing-required-reference block. accept_overrides (True: every
        override the manifest declares, or an iterable of declared ids)
        records them in overrides_accepted; without it the rows already
        accepted for this library are kept for the ids it still declares.
        A new version of an installed library keeps that library's switch:
        a library the learner turned off stays off after the update.

        prepare_install (files; on a worker with snapshot()) and commit_install
        (the registry write, on the store's thread) are its two halves.
        """
        return self.commit_install(self.prepare_install(
            inspection, accept_missing_references=accept_missing_references, accept_overrides=accept_overrides,
            now=now))

    def commit_install(self, prepared):
        """Write the registry prepare_install made (store thread). Refused, and the new folder removed, when the
        registry changed since it was prepared. The staging folder goes either way."""
        try:
            if self.registry() != prepared.base:
                _remove_tree(self.root, prepared.target)
                raise ValueError("内容库设置在安装期间有了变化，请重新添加这个内容库文件。")
            entry = prepared.entry
            self._manifests.pop((entry["library_id"], entry["content_sha256"]), None)
            self._write(prepared.registry)
            self._clear_bundled_notice()
            return entry
        finally:
            self.discard(prepared.inspection)

    def abandon_install(self, prepared):
        """A prepared install that is not committed: its new folder and the staging go."""
        _remove_tree(self.root, prepared.target)
        self.discard(prepared.inspection)

    def prepare_install(self, inspection, *, accept_missing_references=False, accept_overrides=False, now=None):
        """install() without its registry write: the re-check and the move of the unpacked members into a fresh
        folder; reads only files on a snapshot(). Returns a PreparedInstall for commit_install; the staging goes
        on failure, and the installed libraries are untouched until commit_install writes the registry."""
        try:
            registry = self.registry()
            base = json.loads(json.dumps(registry))
            report = self._recheck_unpacked(inspection, registry)  # the staged members, re-checked
            refusal = rules.install_refusal(report)
            if refusal:
                raise ValueError(refusal)
            precheck = self._precheck(report, registry, inspection.unpacked)
            blocking = precheck.blocking_reasons()
            if blocking and (precheck.refusals() or not accept_missing_references):
                raise ValueError("；".join(blocking))
            manifest = report.manifest
            accepted = self._accepted_override_ids(manifest, registry, accept_overrides)
            existing = rules.registry_entry(registry, manifest["library_id"])
            if (existing is not None and existing["source"]["kind"] == "file" and existing["state"] != "damaged"
                    and existing["content_sha256"] == manifest["content_sha256"]):
                raise ValueError("这个版本的内容库已经安装。")
            # Review B-03: never delete or overwrite a folder this process may
            # still read; a fresh <version>.<sha 8> folder is written and the
            # one it supersedes is deleted by a later startup_sync.
            relative = self._fresh_path(manifest)
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            fresh = Path(tempfile.mkdtemp(prefix=".new-", dir=target.parent))
            try:
                # The staging folder is in the same library root: its unpacked members move, nothing is copied.
                fresh.rmdir()
                Path(inspection.unpacked).replace(fresh)
                self._write_indexes(fresh, manifest)
                fresh.replace(target)
            except BaseException:
                shutil.rmtree(fresh, ignore_errors=True)
                raise
            # An update keeps the learner's switch: a library that was turned off stays off (native8).
            enabled = existing is None or bool(existing["enabled"])
            entry = {"library_id": manifest["library_id"], "library_version": manifest["library_version"],
                     "profile": manifest["profile"], "content_sha256": manifest["content_sha256"], "enabled": enabled,
                     "installed_at_utc": rules.now_utc_text(now or utc_now()),
                     "source": {"kind": "file", "name": inspection.source_name, "sha256": inspection.source_sha256,
                                **rules.source_signature_fields(report, registry)},
                     "path": relative, "state": "pending_restart", "summary": rules.manifest_summary(manifest)}
            if existing is None:
                registry["libraries"].append(entry)
                registry["priority"].append(entry["library_id"])
            else:
                registry["libraries"][registry["libraries"].index(existing)] = entry
            if accepted is not None:
                registry = rules.with_accepted_overrides(registry, entry["library_id"], accepted)
            if entry["library_id"] in registry.get("pending_delete", ()):
                # Reinstalled after a removal: superseded folders go with _remove_superseded.
                registry = rules.with_pending_delete(registry, drop=(entry["library_id"],))
            return PreparedInstall(inspection=inspection, base=base, registry=rules.validate_registry(registry),
                                   entry=entry, target=target)
        except BaseException:
            self.discard(inspection)
            raise

    @staticmethod
    def _write_indexes(directory, manifest):
        """The indexes that let the next start read this library on demand (section 6.5); best effort.

        A document that cannot be indexed or a folder that cannot be written
        never fails the install: that start reads the document whole, as
        before, and tries again.
        """
        try:
            layer = content_pack.LibraryLayer(
                {"library_id": manifest["library_id"], "content_sha256": manifest["content_sha256"]}, directory)
            for name in sorted(layer.teaching):
                layer.build_index(name, validate=False)  # the precheck of this install decoded every document
        except (OSError, ValueError, RecursionError, MemoryError):
            pass

    def _fresh_path(self, manifest):
        """<library_id>/<version>.<content sha 8>, suffixed when that folder exists."""
        base = f"{manifest['library_id']}/{manifest['library_version']}.{manifest['content_sha256'][:8]}"
        relative, number = base, 1
        while (self.root / relative).exists() or (self.root / relative).is_symlink():
            number += 1
            relative = f"{base}-{number}"
        return rules.relative_path(relative)

    @staticmethod
    def _accepted_override_ids(manifest, registry, accept_overrides):
        """Override ids to record for this library, or None to leave overrides_accepted untouched."""
        declared = [row["id"] for row in manifest["overrides"]]
        library_id = manifest["library_id"]
        if accept_overrides is True:
            chosen = declared
        elif accept_overrides is False or accept_overrides is None:
            previous = [row["id"] for row in registry.get("overrides_accepted", ()) if row["library_id"] == library_id]
            kept = [cid for cid in previous if cid in set(declared)]
            return kept if kept != previous else None
        else:
            if isinstance(accept_overrides, (str, bytes)):
                raise ValueError("请选择要接受的替代项。")
            chosen = list(dict.fromkeys(accept_overrides))
            unknown = [cid for cid in chosen if cid not in set(declared)]
            if unknown:
                raise ValueError("只能接受本库在 overrides 中声明的替代项：" + unknown[0])
        if chosen and manifest["profile"] == "core":
            raise ValueError("核心库不需要接受替代。")
        return chosen

    def trust_signing_key(self, public_key_id, public_key, *, now=None):
        """TOFU (section 4.5): record one learner-confirmed signing key in the registry.

        The same key again is a no-op; a known key id with another public key
        is refused (ValueError). Written through the registry's set_setting.
        """
        return self._write(rules.with_trusted_key(self.registry(), public_key_id, public_key, now=now or utc_now()))

    # ------------------------------------------------------------ enable / remove

    def set_enabled(self, library_id, enabled):
        registry = self.registry()
        entry = rules.registry_entry(registry, library_id)
        if entry is None:
            raise ValueError("没有这个内容库。")
        if library_id == rules.CORE_LIBRARY_ID:
            raise ValueError("核心库始终启用。")
        if entry["enabled"] == bool(enabled) and not (enabled and entry["state"] == "damaged"):
            return entry
        entry["enabled"] = bool(enabled)
        if entry["state"] != "damaged" or entry["enabled"]:
            # Enabling a damaged library retries it at the next start (review B-04).
            entry["state"] = "pending_restart"
        self._write(registry)
        self._clear_bundled_notice()
        return entry

    def removal_notice(self, library_id):
        """Text for the confirmation page: learned items that become orphans."""
        registry = self.registry()
        entry = rules.registry_entry(registry, library_id)
        if entry is None:
            raise ValueError("没有这个内容库。")
        learned = len(self._provided_ids(entry) & self.learned_ids())
        if entry["source"]["kind"] == "bundled":
            raise ValueError("内置核心库不能移除。")
        if library_id == rules.CORE_LIBRARY_ID:
            return (f"将移除导入的核心库，恢复为内置核心库；已学项 {learned} 个的内容以内置库为准，进度保留。"
                    "重启后生效。")
        return (f"已学项 {learned} 个将变为孤儿，进度保留；再次导入相同 library_id 即恢复。"
                "重启后生效。")

    def remove(self, library_id):
        registry = self.registry()
        entry = rules.registry_entry(registry, library_id)
        if entry is None:
            raise ValueError("没有这个内容库。")
        if entry["source"]["kind"] == "bundled":
            raise ValueError("内置核心库不能移除。")
        if library_id == rules.CORE_LIBRARY_ID:
            replacement = rules.core_entry(content_pack.bundled_stamp())
            replacement["state"] = "pending_restart"
            registry["libraries"][registry["libraries"].index(entry)] = replacement
        else:
            if any(row["library_id"] == library_id for row in registry.get("overrides_accepted", ())):
                registry = rules.with_accepted_overrides(registry, library_id, ())  # rows must name a registered library
                entry = rules.registry_entry(registry, library_id)
            registry["libraries"].remove(entry)
            registry["priority"].remove(library_id)
        # Review B-03: this process may still read the folder (lazy v2 shards,
        # the lexicon); the next startup_sync deletes it once nothing uses it.
        registry = rules.with_pending_delete(registry, add=(entry["library_id"],))
        self._write(registry)
        self._clear_bundled_notice()
        self._manifests.pop((entry["library_id"], entry["content_sha256"]), None)
        return registry
