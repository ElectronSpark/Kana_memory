"""A curriculum overlay over existing memory identities, never a new scheduler.

Only explicit additions write the small enrollment setting. Reading the
path derives progress from real learner records, including older app versions.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
import json
import secrets
from types import MappingProxyType

from .domain import WORD_SKILLS, HANDWRITING_SKILLS, LISTENING_SKILLS, _utc, question_identities
from .learning_content import learning_item_views
from .kana_question_policy import direction_allowed, question_allowed
from .study_routes import CORE_LIBRARY_ID, CORE_ROUTE_ID, CORE_ROUTE_TITLE, ROUTES_SETTING

PATH_SETTING = "study_path_v1"
_UNREAD = object()


def _core_key(kana_rows, items, saved):
    """Digest of what build_study_path reads from its arguments; None when they are not plain data.

    Every field of every lesson except its question templates (which the
    builder never reads), the personal lessons outside the catalogue and the
    kana rows: another catalogue, or the same ids with other content, is
    another key.
    """
    import hashlib

    def small(item):
        return {name: value for name, value in item.items() if name != "question_templates"}
    known = {item["id"] for item in items}
    try:
        encoded = json.dumps([[small(item) for item in items],
                              [small(item) for item in saved if item["id"] not in known],
                              [[row.get(name) for name in ("id", "hiragana", "katakana", "category", "tier")]
                               for row in kana_rows]],
                             ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(encoded.encode("utf-8", "surrogatepass")).digest()


@dataclass(frozen=True)
class KanaQueuePlan:
    """Read-only result of one kana queue assembly (candidates, templates, snapshot).

    Built by StudyPathService.plan_kana_queue, inline or on a worker connection
    (plan_kana_queue_in_worker). It grants no write: only ensure_kana_queue
    commits it, on the UI connection, after `current` proves the inputs and
    revision are unchanged; append_prepared_session then re-validates the queue
    token, reservations and due directions inside its own write transaction.
    """
    requested_kinds: tuple
    audio_available: bool
    handwriting_available: bool
    lazy_questions: bool
    now: datetime
    session_id: str | None
    prepared: object | None
    count: int
    # Set only on worker results: the UI connection's read_revision when the
    # worker was launched, and the queue token the worker read.
    revision: tuple | None = None
    append_token: str | None = None

    def current(self, store, requested_kinds, audio_available, handwriting_available, lazy_questions) -> bool:
        return (self.revision is not None and not store.in_transaction
                and self.requested_kinds == tuple(requested_kinds)
                and self.audio_available == bool(audio_available)
                and self.handwriting_available == bool(handwriting_available)
                and self.lazy_questions == bool(lazy_questions)
                and store.read_revision() == self.revision)


def _kana_queue_kinds(kinds, audio_available, handwriting_available):
    from .domain import SKILLS
    if isinstance(kinds, (str, bytes)) or any(k not in SKILLS for k in kinds):
        raise ValueError("未知假名题型。")
    requested = list(kinds)
    usable = [k for k in dict.fromkeys(kinds) if k not in WORD_SKILLS
              and (audio_available or k not in LISTENING_SKILLS)
              and (handwriting_available or k not in HANDWRITING_SKILLS)]
    return requested, usable


def usable_queue_kinds(kinds, audio_available, handwriting_available):
    """The kana kinds the main-lane queue builds from ``kinds`` on this device (no word skills; listening and
    handwriting only when available), in order; extra practice reads its pool with them."""
    return tuple(_kana_queue_kinds(kinds, audio_available, handwriting_available)[1])


def card_questions(repo, store, content_id, kind, script, *, seed, now):
    """The question about one kana scheduling state (content, kind, script), as the main-lane queue builds each:
    the single-skill builder restricted to that content, at most one question, extended tiers included.

    Shared by plan_kana_queue and the early batch of extra practice (kana_early_batch); each caller adds
    its own metadata. Empty when the builder's own eligibility leaves the content out.
    """
    from .domain import build_questions, _BatchQuestionStore
    return build_questions(repo, _BatchQuestionStore(store, [content_id]), kind=kind, script=script, limit=1,
                           seed=seed, now=now, include_extended=True)


def plan_kana_queue_in_worker(content, progress, *, catalogue, kinds, audio_available,
                              handwriting_available, lazy_questions, revision):
    """practice_worker.run_in_background job: assemble on the worker's own connections.

    ``catalogue`` is StudyPathService.kana_catalogue() (detached copies made on
    the UI thread); ``revision`` is the UI connection's read_revision at launch.
    Only reads: the worker never takes the progress write lock.
    """
    from .material_qualification import bind_resources
    bind_resources(progress, repo=content)
    view = StudyPathService.worker_view(content, progress, catalogue)
    plan = view.plan_kana_queue(kinds, audio_available=audio_available,
                                handwriting_available=handwriting_available, lazy_questions=lazy_questions)
    token = None
    if plan.session_id and plan.prepared is not None:
        old_lane = progress.practice_lane
        progress.practice_lane = "main"
        try:
            token = progress.session_append_token(plan.session_id)
        finally:
            progress.practice_lane = old_lane
    return replace(plan, revision=tuple(revision), append_token=token)


class _StudyPathRead:
    """One synchronous UI action. Never retained by a page or used to enroll.

    No transaction/cache is created. The next action gets current settings,
    content and queue state again; writes independently revalidate their inputs.
    """

    def __init__(self, service: StudyPathService, now: datetime):
        self.service, self.now = service, now
        self._checkpoint, self._states, self._course_rows = _UNREAD, None, None
        self._context, self._route_states = None, {}
        self.active = True

    @property
    def pending(self) -> dict | None:
        self.check_active()
        if self._checkpoint is _UNREAD:
            self._checkpoint = self.service.store.load_session_progress(lane="main", lazy_questions=True)
        return self._checkpoint

    @property
    def course_rows(self) -> list:
        self.check_active()
        if self._course_rows is None:
            self._course_rows = self.service.store.queries.course_progress_rows()
        return self._course_rows

    @property
    def context(self) -> dict:
        """Item-level inputs of every entry state, read once for all routes (design 10 section 4.1)."""
        self.check_active()
        if self._context is None:
            self._context = self.service._state_context(pending=self.pending, learning_rows=self.course_rows)
        return self._context

    @property
    def states(self) -> Mapping[str, str]:
        self.check_active()
        if self._states is None:
            self._states = MappingProxyType(self.service._entry_states(context=self.context))
        return self._states

    def route_states(self, route_id) -> Mapping[str, str]:
        """States of one route's entries; the core route is ``states``."""
        self.check_active()
        rid = self.service._route_key(route_id)
        if rid == CORE_ROUTE_ID:
            return self.states
        if rid not in self._route_states:
            self._route_states[rid] = MappingProxyType(self.service._route_states(
                rid, context=self.context, core_states=self.states))
        return self._route_states[rid]

    def check_active(self) -> None:
        if not self.active:
            raise RuntimeError("Study path read operation has ended")

    def overview(self, route_id=None):
        self.check_active()
        if route_id is None:
            return self.service._overview(self.states)
        return self.service._overview(self.route_states(route_id), route_id=route_id)

    def preview_entries(self, entry_ids, route_id=None):
        self.check_active()
        if route_id is None:
            return self.service._entry_plan(entry_ids, states=self.states)[0]
        rid = self.service._route_key(route_id)
        states = self.states if rid == CORE_ROUTE_ID else {**self.states, **self.route_states(rid)}
        return self.service._entry_plan(entry_ids, states=states, route_id=route_id)[0]

    def plan_next(self, count=1, route_id=None):
        self.check_active()
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        return self.service._plan_next(count, self.overview(route_id))


def validate_path_state(value):
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("学习路线记录版本无效。")
    for key in ("added_sections", "reserved_entries", "reference_read"):
        items = value.get(key)
        if (not isinstance(items, list) or len(items) > 20000
                or any(not isinstance(x, str) or not x or len(x) > 1024 for x in items)
                or len(set(items)) != len(items)):
            raise ValueError("学习路线记录无效：" + key)
    return deepcopy(value)


class _RouteView:
    """One route's tree and indexes (the core route uses the service's own)."""
    __slots__ = ("route_id", "tree", "sections", "by_id", "entries", "entry_sections")

    def __init__(self, route_id, tree, sections, by_id, entries, entry_sections):
        self.route_id, self.tree, self.sections, self.by_id = route_id, tree, sections, by_id
        self.entries, self.entry_sections = entries, entry_sections


class StudyPathService:
    """Study path reads and enrollment over the parallel routes of one catalogue.

    Every public method takes an optional route_id; None means the core route
    (route:kanamemory.core:main) with the unchanged pre-route results. Entry
    states are item-level and shared by all routes (design 10 section 4).
    The kana queue/advancement candidates are the union of enabled routes.
    library_routes (tests/tools): ((route_id, library_id, spec), ...) used
    instead of the effective catalogue's study_routes().
    """

    def __init__(self, repo, store, learning_store, catalogue=None, *, library_routes=None, previous=None,
                 route_mix=None):
        """``previous``: the service this one replaces in the same app (study_path_service).

        The app rebuilds its service whenever a lesson is enrolled, and each
        rebuild built the core route tree again from the whole catalogue
        although the tree only changes with the catalogue or with a personal
        lesson outside it. With the same inputs (core_key) the previous tree
        is taken over; it is read-only here as it always was (overview copies
        before it adds states). The route rules themselves are fixed for a
        process, so only a service of the same app may be passed.

        ``route_mix`` (design 11 section 16, M10; route_mix.service.MainRouteMix):
        derivation_path is the tree built as above (M8(a) cuts included); path
        is the mixed tree of a stored plan for exactly that tree, else the
        derivation tree itself. Without a route_mix (tests, tools) path is the
        derivation tree, as before. The hand-over takes the previous derivation
        tree, never a mixed one, and applies the current plan to it.
        """
        from .study_path_catalog import build_study_path, flatten_sections
        self.repo, self.store, self.learning = repo, store, learning_store
        # Views: the service lives as long as the app and reads lessons; enrolment copies the ones it adds.
        items = learning_item_views() if catalogue is None else list(catalogue)
        saved = learning_store.list_items()
        self.items = {item["id"]: item for item in items}
        self.items.update({item["id"]: item for item in saved})
        # 08 section 8.2: content of a disabled/missing library is not in the
        # route; its progress and the saved path strings stay as they are.
        view = learning_store.availability() if callable(getattr(learning_store, "availability", None)) else None
        self.unavailable_ids = view.unavailable_ids if view is not None else frozenset()
        if view is not None and view.registry is not None:
            items = [item for item in items
                     if item["id"] in view.states or view.available(item["id"], {"dictionary_only": item.get("dictionary_only") is True})]
            items = [item for item in items if item["id"] not in self.unavailable_ids]
        saved = [item for item in saved if item["id"] not in self.unavailable_ids]
        kana_rows = repo.kana_list()
        # Inputs of the other routes (design 10 section 6), built on first use.
        self._route_inputs = (kana_rows, items, saved)
        self._core_key_value = _UNREAD
        # Design 11 section 16 M8: the provider of each lesson, from the effective catalogue (a core id stays
        # core even when an accepted override gives it to a library).
        if view is not None and view.registry is not None:
            from .route_mix.sources import route_library_of
            self.library_of = route_library_of(view.catalogue)
        else:
            self.library_of = None
        earlier = getattr(previous, "core_key", None)
        key = self.core_key() if callable(earlier) else None
        handed = getattr(previous, "derivation_path", None)
        self.derivation_path = (handed if handed is not None and key is not None and key == earlier()
                                and (getattr(previous, "library_of", None) is None) == (self.library_of is None)
                                else build_study_path(kana_rows, items, extra_items=saved, library_of=self.library_of))
        self.route_mix = route_mix
        mixed = route_mix.mixed(self.derivation_path, self.library_of) if route_mix is not None else None
        self.path = self.derivation_path if mixed is None else mixed
        self._route_catalogue = (getattr(view.catalogue, "effective", None)
                                 if view is not None and view.registry is not None else None)
        # Review B-05: library routes follow the live registry, like dispatch.
        self._route_registry = view.registry if view is not None else None
        self._library_routes = None if library_routes is None else tuple(library_routes)
        self._route_set, self._route_enabled = None, {}
        self.sections = flatten_sections(self.path)
        self.by_id = {section["id"]: section for section in self.sections}
        self.entries = {entry["id"]: entry for section in self.sections for entry in section["entries"]}
        self.entry_sections = {entry["id"]: section["id"] for section in self.sections for entry in section["entries"]}
        self.learning_entries = {entry["content_id"]: entry["id"] for entry in self.entries.values()
                                 if entry["kind"] == "learning"}

    def kana_catalogue(self):
        """Detached copies of the kana entries a worker plan reads (cached per service).

        The union of enabled routes (design 10 section 8.2); the core route
        alone gives the pre-route value.
        """
        cached = getattr(self, "_kana_catalogue_copy", None)
        if cached is None:
            extra = self._kana_entries(order="entries")[len(self.entries):]
            cached = (tuple(deepcopy(entry) for entry in self._kana_entries(order="sections")
                            if entry["kind"] == "kana"),
                      tuple((key, deepcopy(entry)) for key, entry in
                            [*self.entries.items(), *((entry["id"], entry) for entry in extra)]
                            if entry["kind"] == "kana"))
            self._kana_catalogue_copy = cached
        return cached

    @classmethod
    def worker_view(cls, repo, store, catalogue):
        """A queue-planning view over a worker's own repo/store; no course store."""
        view = cls.__new__(cls)
        view.repo, view.store, view.learning = repo, store, None
        view._route_inputs = None  # the catalogue below is already the route union
        section_entries, entries = catalogue
        view.sections = [{"entries": list(section_entries)}]
        view.entries = dict(entries)
        return view

    def kana_queue_plan_options(self, kinds, *, audio_available=False, handwriting_available=False,
                                lazy_questions=False):
        """Options for plan_kana_queue_in_worker, or None if no kana direction can be planned."""
        requested, usable = _kana_queue_kinds(kinds, audio_available, handwriting_available)
        if not usable:
            return None
        return {"catalogue": self.kana_catalogue(), "kinds": tuple(requested),
                "audio_available": bool(audio_available), "handwriting_available": bool(handwriting_available),
                "lazy_questions": bool(lazy_questions)}

    def core_key(self):
        """What this service's core route tree was built from (_core_key), computed on first use."""
        if self._core_key_value is _UNREAD:
            self._core_key_value = _core_key(*self._route_inputs)
        return self._core_key_value

    def route_mix_job(self):
        """Design 11 section 16 (M10): a worker-thread job computing the mix plan of this derivation
        tree (route_mix.service.MainRouteMix.job), or None without a route_mix or when the plan applied
        is the current one. A plan computed for other kind switches is applied but not current (section
        16.10): the job is still made, so the warm-up replaces it."""
        mix = getattr(self, "route_mix", None)
        if mix is None or (self.path is not self.derivation_path and mix.current_for(self.derivation_path)):
            return None
        return mix.job(self.derivation_path, self._route_inputs[1], self.library_of)

    # ------------------------------------------------------------ routes (design 10, WP-R2)

    def _library_route_rows(self):
        """(rows, cache token): library routes whose library the live registry still serves.

        The effective catalogue is a process constant; a library disabled,
        damaged or removed since the start drops out of the RouteSet at once
        (status library_unavailable), not only after a restart (review B-05).
        """
        if self._library_routes is not None:
            rows, token = self._library_routes, None
        else:
            effective = self._route_catalogue
            if effective is None or not callable(getattr(effective, "study_routes", None)):
                return (), None
            rows, token = tuple(effective.study_routes()), getattr(effective, "token", None)
        registry = getattr(self, "_route_registry", None)
        if registry is None:
            return rows, token
        from .content_availability import library_usable
        return tuple(row for row in rows if library_usable(registry, row[1])), token

    def _refused_route_ids(self):
        """route_ids the merge's prefix rule refused (status refused, not library_unavailable)."""
        effective = getattr(self, "_route_catalogue", None)
        return frozenset(str(row[2]) for row in getattr(effective, "refused_routes", ()) or ())

    def _routes_state(self):
        from .study_routes import routes_state
        return routes_state(self.store.get_setting(ROUTES_SETTING, None))

    def route_set(self):
        """This service's RouteSet; library and personal routes are built once.

        A route that fails validation is skipped, kept in RouteSet.diagnostics
        and recorded as a content_library/read diagnostic event.
        """
        if self._route_set is None:
            from .study_routes import build_route_set
            from . import diagnostic_events
            state = self._routes_state()
            local = [(rid, row["spec"]) for rid, row in sorted(state["routes"].items())
                     if row["source"] == "local" and "spec" in row]
            rows, token = self._library_route_rows()
            kana_rows, items, saved = self._route_inputs
            route_set = build_route_set(self.path, library_rows=rows, local_specs=local, catalogue=items,
                                        kana_rows=kana_rows, extra_items=saved, cache_token=token)
            for error in route_set.errors:
                diagnostic_events.record_error("content_library", "read", error)
            self._route_enabled = {rid: row["enabled"] for rid, row in state["routes"].items()}
            self._route_set = route_set
        return self._route_set

    def _route_key(self, route_id):
        if route_id is None or route_id == CORE_ROUTE_ID:
            return CORE_ROUTE_ID
        if not isinstance(route_id, str) or route_id not in self.route_set():
            raise ValueError("学习路线不存在或所属库未启用。")
        return route_id

    def _route_view(self, rid):
        if rid == CORE_ROUTE_ID:
            return _RouteView(rid, self.path, self.sections, self.by_id, self.entries, self.entry_sections)
        route_set = self.route_set()
        return _RouteView(rid, route_set.route(rid), route_set.sections(rid), route_set.sections_by_id(rid),
                          route_set.entries(rid), route_set.entry_sections(rid))

    def _enabled_other_routes(self):
        if getattr(self, "_route_inputs", None) is None:
            return ()
        route_set = self.route_set()
        return tuple(rid for rid in route_set.route_ids()
                     if rid != CORE_ROUTE_ID and self._route_enabled.get(rid, True))

    def _kana_entries(self, route_id=None, *, order):
        """Entries a kana queue/advancement pass enumerates.

        None: the core route's entries (``order`` "sections" or "entries", as
        before) followed by the other enabled routes' kana entries not already
        present (union by identity, design 10 section 8.2). A route_id limits
        the pass to that route.
        """
        rid = None if route_id is None else self._route_key(route_id)
        if rid is not None and rid != CORE_ROUTE_ID:
            view = self._route_view(rid)
            return ([e for s in view.sections for e in s["entries"]] if order == "sections"
                    else list(view.entries.values()))
        base = ([e for s in self.sections for e in s["entries"]] if order == "sections"
                else list(self.entries.values()))
        others = self._enabled_other_routes() if rid is None else ()
        if others:
            seen = {entry["id"] for entry in base}
            for other in others:
                view = self._route_view(other)
                for entry in ([e for s in view.sections for e in s["entries"]] if order == "sections"
                              else view.entries.values()):
                    if entry["kind"] == "kana" and entry["id"] not in seen:
                        seen.add(entry["id"])
                        base.append(entry)
        return base

    def entry_owners(self, identity):
        """Route IDs (core first, then by route_id) whose tree contains identity."""
        owners = [CORE_ROUTE_ID] if identity in self.entries else []
        route_set = self.route_set()
        owners += [rid for rid in route_set.route_ids()
                   if rid != CORE_ROUTE_ID and identity in route_set.entries(rid)]
        return owners

    def _closure_by_route(self, rid, identities):
        """Design 10 D4: split an enrollment closure by the routes holding each item.

        An identity outside this route counts once under every other route
        that has it; one held by no route counts as no_route.
        """
        this_route = self._route_view(rid).entries
        mine, others, none = [], {}, []
        for identity in identities:
            if identity in this_route:
                mine.append(identity)
                continue
            owners = [owner for owner in self.entry_owners(identity) if owner != rid]
            for owner in owners:
                others.setdefault(owner, []).append(identity)
            if not owners:
                none.append(identity)
        counts = {"this_route": len(mine), "other_routes": {owner: len(ids) for owner, ids in others.items()},
                  "no_route": len(none)}
        return counts, {"this_route": mine, "other_routes": others, "no_route": none}

    def _route_source(self, rid):
        route_set = self.route_set()
        return route_set.source(rid), route_set.route(rid)["library_id"]

    def active_route_id(self):
        """The current route; a stored route that is gone or disabled reads as the core route (no write)."""
        state = self._routes_state()
        active = state["active_route"]
        if active != CORE_ROUTE_ID and (active not in self.route_set()
                                        or not state["routes"].get(active, {}).get("enabled", True)):
            return CORE_ROUTE_ID
        return active

    def set_active_route(self, route_id):
        """Make route_id current (study_routes_v1.active_route only); True if written."""
        from .study_routes import with_active_route
        rid = self._route_key(route_id)
        state = self._routes_state()
        if rid != CORE_ROUTE_ID and not state["routes"].get(rid, {}).get("enabled", True):
            raise ValueError("学习路线已停用。")
        if state["active_route"] == rid:
            return False
        source, library_id = self._route_source(rid) if rid != CORE_ROUTE_ID else ("core", CORE_LIBRARY_ID)
        self.store.set_setting(ROUTES_SETTING, with_active_route(
            state, rid, source=source, library_id=library_id, now_utc=_utc(None).isoformat()))
        return True

    def set_route_enabled(self, route_id, enabled):
        """Enable/disable a non-core route (12 Q2: the core route cannot be disabled); True if written."""
        from .study_routes import route_row, validate_routes_state
        if type(enabled) is not bool:
            raise ValueError("启用状态无效。")
        if route_id == CORE_ROUTE_ID:
            raise ValueError("核心主线不能停用。")
        state = self._routes_state()
        if route_id in state["routes"]:
            if state["routes"][route_id]["enabled"] == enabled:
                return False
            row = deepcopy(state["routes"][route_id])
        else:
            rid = self._route_key(route_id)
            source, library_id = self._route_source(rid)
            row = route_row(state, rid, source=source, library_id=library_id, now_utc=_utc(None).isoformat())
        row["enabled"] = enabled
        state["routes"][route_id] = row
        self.store.set_setting(ROUTES_SETTING, validate_routes_state(state))
        self._route_enabled[route_id] = enabled
        self._kana_catalogue_copy = None
        return True

    def route_state(self, route_id=None):
        """Detached route-level state: added_sections, enabled, source, library_id.

        The core route's added_sections are study_path_v1's; another route
        without a stored row reads as an empty, enabled, unsaved row. A stored
        row whose library is unavailable is still returned (stored=True).
        """
        if route_id is None or route_id == CORE_ROUTE_ID:
            return {"route_id": CORE_ROUTE_ID, "added_sections": list(self._state()["added_sections"]),
                    "enabled": True, "source": "core", "library_id": CORE_LIBRARY_ID, "stored": True}
        state = self._routes_state()
        if route_id in state["routes"]:
            return {"route_id": route_id, **deepcopy(state["routes"][route_id]), "stored": True}
        rid = self._route_key(route_id)
        source, library_id = self._route_source(rid)
        return {"route_id": rid, "added_sections": [], "enabled": True, "source": source,
                "library_id": library_id, "stored": False}

    # ------------------------------------------------------------ personal routes (design 10, WP-R5)

    def _routes_changed(self):
        """A personal spec was written: the RouteSet and kana union rebuild on next use."""
        self._route_set, self._route_enabled = None, {}
        self._kana_catalogue_copy = None

    def _personal_row(self, state, route_id):
        from .study_routes import is_local_route
        try:
            local = isinstance(route_id, str) and is_local_route(route_id)
        except ValueError:
            local = False
        row = state["routes"].get(route_id) if local else None
        if row is None or row["source"] != "local" or "spec" not in row:
            raise ValueError("个人路线不存在。")
        return row

    def create_personal_route(self, title, target_identities, *, now=None):
        """Save the chosen items as a personal route (design 10 section 6.1); return its route_id.

        The spec (targets plus their hard-prerequisite closure as a separate
        "前置" stage, 12 Q3) is built once before saving, so a stored personal
        route is never skipped. Only study_routes_v1 is written (one
        StudyStore.set_setting); no item is enrolled and no progress changes.
        """
        from .study_routes import LOCAL_LIBRARY_ID, build_route, personal_route_spec, with_personal_route
        moment = _utc(now)
        kana_rows, items, saved = self._route_inputs
        spec = personal_route_spec(title, target_identities, catalogue=items, kana_rows=kana_rows,
                                   extra_items=saved, now=moment)
        build_route(spec, catalogue=items, kana_rows=kana_rows, extra_items=saved, library_id=LOCAL_LIBRARY_ID)
        state = with_personal_route(self._routes_state(), spec, now_utc=moment.isoformat())
        self.store.set_setting(ROUTES_SETTING, state)
        self._routes_changed()
        return spec["route_id"]

    def delete_personal_route(self, route_id):
        """Remove a personal route's spec and route state only; True when written.

        Learning progress, reserved_entries/reference_read (study_path_v1) and
        every progress table stay unchanged; the core route becomes current
        when the deleted route was current.
        """
        from .study_routes import without_route
        state = self._routes_state()
        self._personal_row(state, route_id)
        self.store.set_setting(ROUTES_SETTING, without_route(state, route_id))
        self._routes_changed()
        return True

    def rename_personal_route(self, route_id, title):
        """Give a personal route a new title; True when written."""
        from .study_routes import renamed_route_spec, with_route_spec
        state = self._routes_state()
        row = self._personal_row(state, route_id)
        spec = renamed_route_spec(row["spec"], title)
        if spec == row["spec"]:
            return False
        self.store.set_setting(ROUTES_SETTING, with_route_spec(state, route_id, spec))
        self._routes_changed()
        return True

    @staticmethod
    def _route_summary(overview):
        keys = ("required_total_sections", "required_added_sections", "required_partial_sections",
                "required_completed_sections", "total_sections", "added_sections", "partial_sections",
                "completed_sections", "in_progress_sections")
        return {key: overview[key] for key in keys}

    def routes_overview(self):
        """Every route for the route list page (design 10 sections 8.3, 9, 10.2).

        {"active_route_id", "stored_active_route_id", "routes": [row...],
         "diagnostics": [{code, route_id, library_id, source, error_type, reason}]}
        A row: route_id, title, library_id, source (core/library/local),
        available, status (available / library_unavailable / invalid / refused:
        the merge refused its route_id prefix),
        enabled, active, progress (overview counters or None), next_section
        ({id, title, new_count} or None), in_progress_sections,
        recorded_added_sections. Stored rows whose route is not in the set
        (library disabled or not installed, or a broken personal spec) are
        listed read-only after the available routes.
        """
        route_set = self.route_set()
        state = self._routes_state()
        active = self.active_route_id()
        rows = []
        with self.read_operation() as read:
            for rid in route_set.route_ids():
                overview = read.overview(None if rid == CORE_ROUTE_ID else rid)
                following = overview["next_section"]
                if rid == CORE_ROUTE_ID:
                    title, library_id, source = CORE_ROUTE_TITLE, CORE_LIBRARY_ID, "core"
                    recorded = len(self._state()["added_sections"])
                else:
                    tree = route_set.route(rid)
                    title, library_id, source = tree["title"], tree["library_id"], route_set.source(rid)
                    recorded = len(state["routes"].get(rid, {}).get("added_sections", ()))
                rows.append({"route_id": rid, "title": title, "library_id": library_id, "source": source,
                             "available": True, "status": "available",
                             "enabled": rid == CORE_ROUTE_ID or state["routes"].get(rid, {}).get("enabled", True),
                             "active": rid == active, "progress": self._route_summary(overview),
                             "next_section": None if following is None else {
                                 "id": following["id"], "title": following["title"],
                                 "new_count": following["new_count"]},
                             "in_progress_sections": overview["in_progress_sections"],
                             "recorded_added_sections": recorded})
        skipped = {row["route_id"] for row in route_set.diagnostics}
        refused = self._refused_route_ids()
        for rid, row in sorted(state["routes"].items()):
            if rid in route_set:
                continue
            title = row.get("spec", {}).get("title") if row["source"] == "local" else None
            rows.append({"route_id": rid, "title": title if isinstance(title, str) and title.strip()
                         else rid.split(":", 2)[2], "library_id": row["library_id"],
                         "source": row["source"], "available": False,
                         "status": ("invalid" if rid in skipped else "refused" if rid in refused
                                    else "library_unavailable"),
                         "enabled": row["enabled"], "active": False, "progress": None, "next_section": None,
                         "in_progress_sections": 0, "recorded_added_sections": len(row["added_sections"])})
        return {"active_route_id": active, "stored_active_route_id": state["active_route"], "routes": rows,
                "diagnostics": [dict(row) for row in route_set.diagnostics]}

    def _state(self):
        return validate_path_state(self.store.get_setting(PATH_SETTING, {
            "version": 1, "added_sections": [], "reserved_entries": [], "reference_read": []}))

    @contextmanager
    def read_operation(self, *, now: datetime | None = None) -> Iterator[_StudyPathRead]:
        """Share reads only inside one synchronous action, with no await/write."""
        read = _StudyPathRead(self, _utc(now))
        try:
            yield read
        finally:
            read.active = False
            read._checkpoint = read._states = read._course_rows = read._context = None
            read._route_states = {}

    def _state_context(self, *, pending=_UNREAD, learning_rows=None):
        """Item-level inputs of entry states; independent of any route (design 10 section 4.1)."""
        state = self._state()
        reserved, read = set(state["reserved_entries"]), set(state["reference_read"])
        if learning_rows is None:
            learning_rows = self.store.queries.course_progress_rows()
        from .read_only_lessons import retired_ability
        stages = {}
        for row in learning_rows:
            scope = row.get('instruction_scope') if isinstance(row, dict) else None
            if scope is not None and retired_ability(row['content_id'], scope):
                continue   # J6 R4: the scope of an ability the lesson no longer lists does not hold the lesson
            stages.setdefault(row['content_id'], []).append(row['stage'])
        learning = {cid: 'done' if all(s == 'done' for s in values)
                    else 'intro' if all(s == 'intro' for s in values) else 'guide'
                    for cid, values in stages.items()}
        # J6 R1/R4: a lesson that is only read is complete once read, else new content of its section.
        read_only = getattr(self.store.queries, 'read_only_lessons', None)
        # learning_rows hold every enrolled lesson (course_progress_rows): with none there is nothing to read.
        lesson_ids = {row['content_id'] for row in learning_rows}
        for cid, value in (read_only(lesson_ids=lesson_ids) if callable(read_only) else {}).items():
            learning[cid] = 'done' if value['read'] else 'intro'
        needs_teaching = {row["content_id"] for row in learning_rows
                          if json.loads(row["checkpoint_json"]).get("initial_familiarity", {}).get(
                              "diagnostic_state") == "needs_teaching"}
        reviewed = {(row["content_id"], row["script"]) for row in self.store.queries.reviewed_kana_rows()
            if row["skill"] not in WORD_SKILLS}
        if pending is _UNREAD:
            pending = self.store.load_session_progress(lane="main", lazy_questions=True)
        # Progress needs only identity headers, including completed history.
        # The public header projection avoids constructing/decorating old Questions.
        queued = {(cid, script) for cid, kind, script in
                  (self.store.session_question_headers(pending["session_id"]) if pending else ())
                  if kind not in WORD_SKILLS}
        introductions = self.store.get_setting("kana_introductions_v1", {}).get("introduced", {})
        return {"reserved": reserved, "read": read, "learning": learning, "needs_teaching": needs_teaching,
                "reviewed": reviewed, "queued": queued, "introductions": introductions}

    @staticmethod
    def _entry_status(context, entry):
        """entry_state(item progress, item intent, entry): the same in every route."""
        eid, cid = entry["id"], entry["content_id"]
        status = "added" if eid in context["reserved"] else "new"
        if entry["kind"] == "reference":
            if eid in context["read"] or (entry.get("practice_retired")
                                          and (cid, entry["script"]) in context["reviewed"]):
                status = "completed"
        elif entry["kind"] == "learning":
            stage = context["learning"].get(cid)
            if stage == "done":
                status = "completed"
            elif stage and (stage != "intro" or cid in context["needs_teaching"]):
                status = "started"
            elif stage:
                status = "added"
        else:
            key = cid, entry["script"]
            if key in context["reviewed"]:
                status = "completed"
            elif cid + "|" + entry["script"] in context["introductions"]:
                status = "started"
            elif key in context["queued"]:
                status = "added"
        return status

    def _entry_states(self, *, pending=_UNREAD, learning_rows=None, context=None):
        """Core route entry states (identity -> new/added/started/completed)."""
        if context is None:
            context = self._state_context(pending=pending, learning_rows=learning_rows)
        result = {}
        for section in self.sections:
            for entry in section["entries"]:
                result[entry["id"]] = self._entry_status(context, entry)
        return result

    def _route_states(self, rid, *, context=None, core_states=None):
        """One route's entry states, projected from the same item-level context.

        An identity the core route holds with the same kind reuses its core
        state; only a differing kind (e.g. a route's reference view of a kana
        identity) is evaluated again, with the same context.
        """
        if context is None:
            context = self._state_context()
        if core_states is None:
            core_states = self._entry_states(context=context)
        if rid == CORE_ROUTE_ID:
            return core_states
        result = {}
        for section in self._route_view(rid).sections:
            for entry in section["entries"]:
                core = self.entries.get(entry["id"])
                if core is not None and core["kind"] == entry["kind"] and entry["id"] in core_states:
                    result[entry["id"]] = core_states[entry["id"]]
                else:
                    result[entry["id"]] = self._entry_status(context, entry)
        return result

    def _states_for(self, rid):
        """Core states plus (for another route) its own entry states, one context."""
        context = self._state_context()
        core_states = self._entry_states(context=context)
        if rid == CORE_ROUTE_ID:
            return core_states
        return {**core_states, **self._route_states(rid, context=context, core_states=core_states)}

    @staticmethod
    def _progress(states):
        states = list(states)
        return {"total": len(states), "added": sum(s != "new" for s in states),
                "started": sum(s in {"started", "completed"} for s in states),
                "completed": sum(s == "completed" for s in states)}

    @staticmethod
    def _summary_state(progress):
        if progress["total"] and progress["completed"] == progress["total"]:
            return "completed"
        if progress["started"]:
            return "started"
        if progress["added"] == progress["total"]:
            return "added"
        return "partial" if progress["added"] else "new"

    @staticmethod
    def _section_progress(sections):
        """Parent progress counts whole sections, retaining a partial subtotal."""
        return {"total": len(sections),
                "added": sum(bool(s["progress"]["total"]) and s["new_count"] == 0 for s in sections),
                "partial": sum(0 < s["progress"]["added"] < s["progress"]["total"] for s in sections),
                "started": sum(s["progress"]["started"] > 0 for s in sections),
                "completed": sum(s["state"] == "completed" for s in sections)}

    def overview(self, route_id=None):
        if route_id is None:
            return self._overview(self._entry_states())
        rid = self._route_key(route_id)
        return self._overview(self._states_for(rid), route_id=rid)

    def _overview(self, states, route_id=None):
        rid = self._route_key(route_id)
        result = deepcopy(self.path if rid == CORE_ROUTE_ID else self._route_view(rid).tree)
        sections = []
        for stage in result["stages"]:
            stage_sections = []
            for unit in stage["units"]:
                for section in unit["sections"]:
                    for entry in section["entries"]:
                        entry["state"] = states[entry["id"]]
                    section["progress"] = self._progress(states[e["id"]] for e in section["entries"])
                    section["state"] = self._summary_state(section["progress"])
                    section["new_count"] = section["progress"]["total"] - section["progress"]["added"]
                unit["progress"] = self._section_progress(unit["sections"])
                stage_sections.extend(unit["sections"])
            stage["progress"] = self._section_progress(stage_sections)
            sections.extend(stage_sections)
        required = [s for s in sections if not s.get("optional")]
        def fully_added(section):
            return bool(section["progress"]["total"]) and section["new_count"] == 0
        def partially_added(section):
            return 0 < section["progress"]["added"] < section["progress"]["total"]
        result.update(required_total_sections=len(required),
            required_added_sections=sum(fully_added(s) for s in required),
            required_partial_sections=sum(partially_added(s) for s in required),
            required_completed_sections=sum(s["state"] == "completed" for s in required),
            total_sections=len(sections),
            added_sections=sum(fully_added(s) for s in sections),
            partial_sections=sum(partially_added(s) for s in sections),
            completed_sections=sum(s["state"] == "completed" for s in sections),
            in_progress_sections=sum(s["progress"]["added"] > 0 and s["state"] != "completed" for s in sections),
            # Added (learned) content whose library is currently unavailable.
            unavailable_added=len(getattr(self, "unavailable_ids", ())),
            queued_section_ids=[s["id"] for s in sections if s["progress"]["added"] > 0 and s["state"] != "completed"],
            next_section=next((s for s in sections if s["new_count"] and not s.get("optional")), None))
        return result

    def section(self, section_id, route_id=None):
        for stage in self.overview(route_id)["stages"]:
            for unit in stage["units"]:
                for section in unit["sections"]:
                    if section["id"] == section_id:
                        return section
        raise KeyError(section_id)

    def plan_next(self, count=1, route_id=None):
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        return self._plan_next(count, self.overview(route_id))

    @staticmethod
    def _plan_next(count, overview):
        if type(count) is not int or count < 1:
            raise ValueError("请输入大于零的小节数量。")
        sections = [s for stage in overview["stages"] for u in stage["units"] for s in u["sections"]]
        return [s for s in sections if s["new_count"] and not s.get("optional")][:count]

    def add_sections(self, section_ids, *, start_learning=False, action_id=None, route_id=None):
        """Enroll whole sections of one route (default: the core route).

        The hard-prerequisite closure is the items' own (build_dependency_plan,
        including items outside the route). The route's added_sections go to
        study_path_v1 (core) or study_routes_v1 (other routes) in the
        enrollment transaction; reserved_entries stay item-level. With an
        explicit route_id the result also has closure_by_route (design 10 D4).
        """
        if type(start_learning) is not bool:
            raise ValueError('Invalid explicit start choice')
        rid = self._route_key(route_id)
        view = self._route_view(rid)
        lookup = self.entries if rid == CORE_ROUTE_ID else {**self.entries, **view.entries}
        initial_states = (self._states_for(rid) if rid != CORE_ROUTE_ID else self._entry_states()) if start_learning else {}
        started_at = _utc(None)
        if isinstance(section_ids, str):
            section_ids = [section_ids]
        chosen, visiting = set(), set()
        def visit(sid):
            if sid in chosen:
                return
            if sid not in view.by_id or sid in visiting:
                raise ValueError("学习小节或前置顺序无效。")
            visiting.add(sid)
            for dep in view.by_id[sid].get("depends_on", []):
                visit(dep)
            visiting.remove(sid)
            chosen.add(sid)
        for sid in section_ids:
            visit(sid)
        sections = [s for s in view.sections if s["id"] in chosen]
        targets = [self.items[e["content_id"]] for s in sections for e in s["entries"] if e["kind"] == "learning"]
        from .learning_dependencies import build_dependency_plan
        planned_items = build_dependency_plan([item["id"] for item in targets], self.items, self.learning)["items"] if targets else []
        state = self._state()
        routes_encoded = None
        if rid == CORE_ROUTE_ID:
            added = [s["id"] for s in sections if s["id"] not in state["added_sections"]]
            state["added_sections"] = list(dict.fromkeys([*state["added_sections"], *(s["id"] for s in sections)]))
        else:
            from .study_routes import with_route_sections
            routes = self._routes_state()
            before = routes["routes"].get(rid, {}).get("added_sections", [])
            added = [s["id"] for s in sections if s["id"] not in before]
            source, library_id = self._route_source(rid)
            routes = with_route_sections(routes, rid, [s["id"] for s in sections], source=source,
                                         library_id=library_id, now_utc=started_at.isoformat())
            routes_encoded = json.dumps(routes, ensure_ascii=False, allow_nan=False)
        state["reserved_entries"] = list(dict.fromkeys([*state["reserved_entries"], *(e["id"] for s in sections for e in s["entries"])]))
        validate_path_state(state)
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        roots = list(dict.fromkeys([*(e['id'] for s in sections for e in s['entries']),
            *(self.learning_entries[item['id']] for item in planned_items if item['id'] in self.learning_entries)]))
        def reserve(db):
            # Keep whole-section enrollment and reservations in one transaction,
            # including kana-only sections and retries after a failed save.
            db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (PATH_SETTING, encoded))
            if routes_encoded is not None:
                db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (ROUTES_SETTING, routes_encoded))
            if start_learning and any(initial_states.get(eid) == 'new'
                    and lookup[eid]['kind'] in {'learning', 'kana'} for eid in roots):
                from .explicit_learning_start import arm, candidates
                arm(db, candidates(self, roots), action_id=action_id, now=started_at, service=self)
        added_learning = self.learning.enroll(planned_items, _after_enroll=reserve)
        result = {"added_sections": added, "added_learning": added_learning,
                  "reserved_entries": state["reserved_entries"], "sections": sections}
        if route_id is not None:
            result["closure_by_route"], result["closure_entry_ids_by_route"] = self._closure_by_route(rid, roots)
        return result

    def _entry_plan(self, entry_ids, *, states=None, route_id=None):
        """Resolve exact new entries and their own prerequisites, without writes.

        Section dependencies aggregate every member's prerequisites. They are
        intentionally not used here: selecting one member must not enroll its
        siblings or their unrelated prerequisites. An enrolled but unstarted
        prerequisite still needs its own foundations; actual prior study and
        preserved answer drafts keep their existing route.
        """
        from .learning_dependencies import dependencies_of, effective_dependencies, prerequisite_status
        if isinstance(entry_ids, str):
            entry_ids = [entry_ids]
        try:
            requested = list(entry_ids)
        except TypeError as exc:
            raise ValueError("学习项选择无效。") from exc
        rid = self._route_key(route_id)
        view = self._route_view(rid)
        # Prerequisites are items of the whole catalogue (design 10 D4): an
        # entry of this route keeps its own kind, any other is the core entry.
        lookup = self.entries if rid == CORE_ROUTE_ID else {**self.entries, **view.entries}
        order = (self.entries if rid == CORE_ROUTE_ID
                 else [*view.entries, *(eid for eid in self.entries if eid not in view.entries)])
        if any(not isinstance(eid, str) or eid not in view.entries for eid in requested):
            raise ValueError("学习项选择含无效标识。")
        # Old bookmarks for standalone signs now open the one shared rule.
        # Keep historical entries/records intact; only a new selection maps.
        requested = [self.learning_entries.get(view.entries[eid].get("reading_rule_id"), eid)
                     for eid in requested]
        if states is None:
            states = self._entry_states() if rid == CORE_ROUTE_ID else self._states_for(rid)
        requested = set(requested)
        selected = [eid for eid in order if eid in requested and states[eid] == "new"]
        chosen, visited, visiting, dependency_order = set(), set(), set(), []
        kana_state = None

        def visit(eid):
            nonlocal kana_state
            if eid in visited:
                return
            if eid in visiting:
                raise ValueError("学习项前置关系成环。")
            visiting.add(eid)
            entry = lookup[eid]
            source = self.items.get(entry["content_id"]) if entry["kind"] == "learning" else entry
            if source is None:
                raise ValueError("学习项缺少教学材料。")
            dependencies = dependencies_of(source)
            if entry["kind"] == "learning":
                dependencies = effective_dependencies(self.learning, source)
                if states[eid] != "new":
                    status = prerequisite_status(self.learning, source)
                    if status["grandfathered"] or status["waived"]:
                        dependencies = []
            elif entry["kind"] == "kana" and states[eid] != "new":
                from .kana_dependency_runtime import kana_dependency_state
                if kana_state is None:
                    kana_state = kana_dependency_state(self.store)
                if eid in kana_state.reviewed:
                    dependencies = []
            elif states[eid] != "new":
                dependencies = []
            for dependency in dependencies:
                target = self.learning_entries.get(dependency, dependency)
                if target not in lookup:
                    raise ValueError("学习路线缺少前置内容：" + dependency)
                visit(target)
            visiting.remove(eid)
            visited.add(eid)
            if states[eid] == "new":
                chosen.add(eid)
                dependency_order.append(eid)

        for eid in selected:
            visit(eid)
        combined = [eid for eid in order if eid in chosen]
        prerequisites = [eid for eid in combined if eid not in requested]
        section_set = {view.entry_sections[eid] for eid in combined if eid in view.entry_sections}
        sections = [s["id"] for s in view.sections if s["id"] in section_set]
        summary = "尚未选择新内容"
        if selected:
            parts = [f"已选 {len(selected)} 项"]
            if prerequisites:
                parts.append(f"自动加入前置 {len(prerequisites)} 项")
            parts.append(f"涉及 {len(sections)} 小节")
            summary = " · ".join(parts)
        preview = {"selected_entry_ids": selected, "prerequisite_entry_ids": prerequisites,
                   "entry_ids": combined, "selected_count": len(selected),
                   "prerequisite_count": len(prerequisites), "total_count": len(combined),
                   "section_ids": sections, "section_count": len(sections),
                   "selected_section_count": len({view.entry_sections[eid] for eid in selected
                                                  if eid in view.entry_sections}),
                   "summary": summary}
        if route_id is not None:
            preview["closure_by_route"], preview["closure_entry_ids_by_route"] = self._closure_by_route(rid, combined)
        return preview, dependency_order, states

    def preview_entries(self, entry_ids, route_id=None):
        """Return a stable, read-only preview of explicit and prerequisite entries."""
        return self._entry_plan(entry_ids, route_id=route_id)[0]

    def add_entries(self, entry_ids, *, start_learning=False, action_id=None, route_id=None):
        """Append exact selected identities; never replace a queue or schedule."""
        if type(start_learning) is not bool:
            raise ValueError('Invalid explicit start choice')
        started_at = _utc(None)
        rid = self._route_key(route_id)
        view = self._route_view(rid)
        lookup = self.entries if rid == CORE_ROUTE_ID else {**self.entries, **view.entries}
        preview, dependency_order, states = self._entry_plan(entry_ids, route_id=route_id)
        state = self._state()
        if not preview["entry_ids"]:
            return {**preview, "added_sections": [], "added_learning": [],
                    "reserved_entries": state["reserved_entries"], "sections": []}
        added_ids = set(preview["entry_ids"])
        sections = [view.by_id[sid] for sid in preview["section_ids"]]
        full_sections = [s["id"] for s in sections if s["entries"] and all(
            states[e["id"]] != "new" or e["id"] in added_ids for e in s["entries"])]
        routes_encoded = None
        if rid == CORE_ROUTE_ID:
            added_sections = [sid for sid in full_sections if sid not in state["added_sections"]]
            state["added_sections"] = list(dict.fromkeys([*state["added_sections"], *full_sections]))
        else:
            from .study_routes import with_route_sections
            routes = self._routes_state()
            before = routes["routes"].get(rid, {}).get("added_sections", [])
            added_sections = [sid for sid in full_sections if sid not in before]
            if full_sections:
                source, library_id = self._route_source(rid)
                routes = with_route_sections(routes, rid, full_sections, source=source,
                                             library_id=library_id, now_utc=started_at.isoformat())
                routes_encoded = json.dumps(routes, ensure_ascii=False, allow_nan=False)
        state["reserved_entries"] = list(dict.fromkeys([*state["reserved_entries"], *preview["entry_ids"]]))
        validate_path_state(state)
        encoded = json.dumps(state, ensure_ascii=False, allow_nan=False)
        targets = [self.items[lookup[eid]["content_id"]] for eid in dependency_order
                   if lookup[eid]["kind"] == "learning"]
        def reserve(db):
            # Use the enrollment transaction directly. Calling set_setting
            # here would open a nested context and could commit too early.
            db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                       (PATH_SETTING, encoded))
            if routes_encoded is not None:
                db.execute("INSERT INTO app_setting VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                           (ROUTES_SETTING, routes_encoded))
            if start_learning and any(lookup[eid]['kind'] in {'learning', 'kana'}
                                      for eid in preview['entry_ids']):
                from .explicit_learning_start import arm, candidates
                arm(db, candidates(self, preview['entry_ids']), action_id=action_id, now=started_at, service=self)
        added_learning = self.learning.enroll(targets, _after_enroll=reserve)
        return {**preview, "added_sections": added_sections, "added_learning": added_learning,
                "reserved_entries": state["reserved_entries"], "sections": deepcopy(sections)}

    def reference_entry(self, entry_id, route_id=None):
        """Return one reference's saved state without enrolling its siblings."""
        rid = self._route_key(route_id)
        view = self._route_view(rid)
        entry = view.entries.get(entry_id)
        if not entry or entry["kind"] != "reference":
            raise ValueError("参考项无效。")
        section = view.by_id[view.entry_sections[entry_id]]
        states = self._entry_states() if rid == CORE_ROUTE_ID else self._states_for(rid)
        return {**deepcopy(entry), "state": states[entry_id],
                "section_id": section["id"], "section_title": section["title"]}

    def next_reference_entry(self, route_id=None):
        """Resume the first unread enrolled reference in catalogue order."""
        rid = self._route_key(route_id)
        view = self._route_view(rid)
        states = self._entry_states() if rid == CORE_ROUTE_ID else self._states_for(rid)
        for entry in view.entries.values():
            if entry["kind"] == "reference" and states[entry["id"]] == "added":
                section = view.by_id[view.entry_sections[entry["id"]]]
                return {**deepcopy(entry), "state": "added", "section_id": section["id"],
                        "section_title": section["title"]}
        return None

    def mark_reference_entry_read(self, entry_id, route_id=None):
        """Acknowledge only the displayed, already enrolled reference.

        This is deliberately stricter than the legacy section-level browsing
        action. Opening or completing one item must never add or read siblings.
        reference_read is item-level: every route sees the reference as read.
        """
        entry = self._route_view(self._route_key(route_id)).entries.get(entry_id)
        state = self._state()
        if (not entry or entry["kind"] != "reference"
                or entry_id not in state["reserved_entries"]):
            raise ValueError("只能标记已加入的参考项。")
        if entry_id in state["reference_read"]:
            return False
        state["reference_read"].append(entry_id)
        self.store.set_setting(PATH_SETTING, state)
        return True

    def mark_reference_read(self, section_id, route_id=None):
        state = self._state()
        section = self._route_view(self._route_key(route_id)).by_id[section_id]
        refs = [e["id"] for e in section["entries"] if e["kind"] == "reference"]
        if not refs:
            return
        reserved = set(state["reserved_entries"])
        selected_refs = [eid for eid in refs if eid in reserved]
        if selected_refs:
            # An entry-level selection must remain exact when its reference
            # page is later marked read. Do not silently enroll its siblings.
            refs = selected_refs
        else:
            # Directly browsing a wholly unselected section and explicitly
            # marking that section read retains the existing whole-section UI.
            if route_id is None:
                self.add_sections([section_id])
            else:
                self.add_sections([section_id], route_id=route_id)
            state = self._state()
        state["reference_read"] = list(dict.fromkeys([*state["reference_read"], *refs]))
        self.store.set_setting(PATH_SETTING, state)

    def kana_advancement_targets(self, kinds, *, now=None, pending=_UNREAD, stages=None, lazy_questions=False,
                                 cards=None, unavailable=None, route_id=None):
        """Read-only unlocked, ungraded directions shared by home and resume.

        cards (scheduling_cards(aliases=True)) and unavailable (item controls)
        may come from the caller's same synchronous read; None reads live.
        Candidates are the kana entries of every enabled route (union by
        identity, design 10 section 8.2), or of route_id only when given.
        """
        from .domain import _adaptive_stages, _ADAPTIVE_LEVELS
        kinds = [kind for kind in kinds if kind not in WORD_SKILLS and _ADAPTIVE_LEVELS[kind] > 0]
        if not kinds:
            return []
        now = _utc(now)
        pending = self.store.load_session_progress(lane="main", lazy_questions=lazy_questions) if pending is _UNREAD else pending
        active = {(q.content_id, q.kind, q.script) for q in
                  (pending["questions"][pending["question_index"]:] if pending else [])
                  if question_allowed(q)}
        cards = {(r["content_id"], r["skill"], r["script"]): r for r in
                 (self.store.scheduling_cards(aliases=True) if cards is None else cards)}
        unavailable = self.store.unavailable_content_ids(now) if unavailable is None else unavailable
        from .kana_routing import identity
        occupied = {identity(self.store, cid, kind, script) for cid, kind, script in active}
        reviewed = {(cid, script) for (cid, _, script), card in cards.items() if card["review_count"] > 0}
        blocked = active | cards.keys()
        stages = stages if stages is not None else {
            script: _adaptive_stages(self.store, script, now) for script in ("hiragana", "katakana")}
        targets = []
        for entry in self._kana_entries(route_id, order="entries"):
            cid, script = entry["content_id"], entry.get("script")
            if (entry["kind"] != "kana" or (cid, script) not in reviewed
                    or cid in unavailable):
                continue
            stage = stages[script].get(cid, 0)
            for kind in kinds:
                if (_ADAPTIVE_LEVELS[kind] <= stage and (cid, kind, script) not in blocked
                        and identity(self.store, cid, kind, script) not in occupied
                        and direction_allowed(cid, kind, script)):
                    targets.append((entry, kind, stage))
        return targets

    def ensure_kana_queue(self, kinds, *, audio_available=False, handwriting_available=False, now=None,
                          lazy_questions=False, plan=None, route_id=None):
        """Append exact reserved script identities, retaining old snapshots/drafts.

        Current settings are authoritative. An unavailable stimulus leaves the
        reservation visible for later, without inventing a question or success.
        plan: a worker's KanaQueuePlan for these same inputs. It is committed
        only while KanaQueuePlan.current holds; otherwise the queue is assembled
        here again (identical to passing no plan). A worker plan always covers
        the route union, so a route_id (one route only) assembles here.
        """
        from .kana_dependency_repair import ensure_kana_prerequisites
        requested_kinds, kinds = _kana_queue_kinds(kinds, audio_available, handwriting_available)
        def with_prerequisites(result):
            repaired = ensure_kana_prerequisites(self.repo, self.store, kinds, now=now,
                audio_available=audio_available, handwriting_available=handwriting_available,
                lazy_questions=lazy_questions)
            from .kana_context_words import ensure_kana_context_word, ensure_kana_rule_probe
            probe = ensure_kana_rule_probe(self.repo, self.store, kinds, now=now)
            context = ensure_kana_context_word(self.repo, self.store, requested_kinds,
                audio_available=audio_available, now=now)
            return {"added": result["added"] + repaired["added"] + context["added"],
                    "session_id": result["session_id"] or repaired["session_id"] or context["session_id"] or probe["session_id"]}
        if not kinds:
            return with_prerequisites({"added": 0, "session_id": None})
        if plan is not None and (route_id is not None or not plan.current(
                self.store, requested_kinds, audio_available, handwriting_available, lazy_questions)):
            plan = None
        if plan is None:
            if route_id is None:
                plan = self.plan_kana_queue(requested_kinds, audio_available=audio_available,
                    handwriting_available=handwriting_available, now=now, lazy_questions=lazy_questions)
            else:
                plan = self.plan_kana_queue(requested_kinds, audio_available=audio_available,
                    handwriting_available=handwriting_available, now=now, lazy_questions=lazy_questions,
                    route_id=route_id)
        now = plan.now
        if plan.prepared is None:
            return with_prerequisites({"added": 0, "session_id": None})
        old_lane = self.store.practice_lane
        self.store.practice_lane = "main"
        try:
            if plan.session_id:
                sid = plan.session_id
                token = plan.append_token if plan.append_token is not None else self.store.session_append_token(sid)
                self.store.append_prepared_session(sid, plan.prepared, expected_token=token,
                    distinct_scripts=True, curriculum_directions=True, now=now)
            else:
                sid = self.store.start_prepared_session(plan.prepared, now=now)
                self.store.save_session_progress(sid, 0, 0, state={})
        finally:
            self.store.practice_lane = old_lane
        return with_prerequisites({"added": plan.count, "session_id": sid})

    def plan_kana_queue(self, kinds, *, audio_available=False, handwriting_available=False, now=None,
                        lazy_questions=False, route_id=None) -> KanaQueuePlan:
        """Read-only half of ensure_kana_queue: candidates, templates and snapshot.

        Nothing here writes; it runs on the UI connection or a worker's own one.
        Candidates: reserved kana of every enabled route (union by identity,
        design 10 section 8.2), or of route_id only when given.
        """
        from .domain import (_adaptive_stages, _adaptive_new_kinds,
                             _eligible_kana, _check_build_cancelled, prepare_session_snapshot)
        requested_kinds, kinds = _kana_queue_kinds(kinds, audio_available, handwriting_available)
        now = _utc(now)
        # One read of item controls, card projection and adaptive stages for
        # this queue pass; nothing below writes before the final append.
        unavailable = self.store.unavailable_content_ids(now)
        reserved = set(self._state()["reserved_entries"])
        pending = self.store.load_session_progress(lane="main", lazy_questions=lazy_questions)
        active = (set(question_identities(pending["questions"], pending["question_index"],
                                         excluded_kinds=WORD_SKILLS)) if pending else set())
        active_scripts = {(cid, script) for cid, _, script in active}
        reviewed = {(r["content_id"], r["script"]) for r in self.store.queries.reviewed_kana_rows()}
        targets = [e for e in self._kana_entries(route_id, order="sections") if e["kind"] == "kana"
                   and e["id"] in reserved and (e["content_id"], e["script"]) not in reviewed | active_scripts
                   and e["content_id"] not in unavailable]
        seed, questions = secrets.randbits(32), []
        audio_candidates = None
        from .kana_routing import identity, candidate_cards, deduplicate_questions, project_cards
        projected = project_cards(self.store, aliases=True)
        stages = {script: _adaptive_stages(self.store, script, now) for script in ("hiragana", "katakana")}
        occupied = {identity(self.store, cid, kind, script) for cid, kind, script in active}

        def can_build(content_id, kind, script):
            nonlocal audio_candidates
            if identity(self.store, content_id, kind, script) in occupied:
                return False
            if not direction_allowed(content_id, kind, script):
                _check_build_cancelled(self.store)
                return False
            if kind != "kana_handwriting":
                return True
            if audio_candidates is None:
                # Advancement can repeatedly offer an intrinsically ambiguous
                # isolated sound (e.g. お/を). The single-skill builder already
                # excludes it, but would reread the entire catalogue each time.
                # Use that same predicate once per visit; retain no stale cache
                # across repository or settings changes. Other builders keep
                # their original eligibility and choice generation unchanged.
                rows = self.repo.kana_list()
                audio_candidates = {
                    alphabet: {row.get("id", row.get("entry_id")) for row in rows
                               if _eligible_kana(row, alphabet, True, audio_only=True)}
                    for alphabet in ("hiragana", "katakana")}
            eligible = content_id in audio_candidates[script]
            if not eligible:
                # The skipped builder would still honor a worker cancellation.
                _check_build_cancelled(self.store)
            return eligible

        # Reuse existing due cards without bringing future reviews forward.
        for card in candidate_cards(self.store, kinds, now, cards=projected, stages=stages):
            if datetime.fromisoformat(card["due_at_utc"]) > now:
                continue
            cid, script, kind = card["content_id"], card["script"], card["skill"]
            if (kind not in kinds or (cid, kind, script) in active
                    or cid in unavailable):
                continue
            if not can_build(cid, kind, script):
                continue
            made = card_questions(self.repo, self.store, cid, kind, script, seed=seed, now=now)
            for q in made:
                q.metadata.update(study_path_due_v1=True, not_before_utc=card["due_at_utc"])
                q.metadata["practice_config"].update(schedule_mode="spaced", kinds=kinds, rounds=1)
            questions.extend(made)
        for entry in targets:
            cid, script = entry["content_id"], entry["script"]
            selected = _adaptive_new_kinds(kinds, stages[script].get(cid, 0))
            for kind in selected:
                card = self.store.get_scheduling_card(cid, kind, script)
                if card and card.get("suspended"):
                    continue
                if not can_build(cid, kind, script):
                    continue
                made = card_questions(self.repo, self.store, cid, kind, script, seed=seed, now=now)
                for q in made:
                    q.metadata.update(kana_intro_v1=True, kana_familiarity_v1=True, study_path_entry=entry["id"],
                                      schedule_mode="spaced", adaptive_policy="scaffold-v1",
                                      adaptive_stage=stages[script].get(cid, 0))
                    q.metadata["practice_config"].update(schedule_mode="spaced", adaptive=True,
                        kinds=kinds, requested_script=script, rounds=1)
                questions.extend(made)
        # Initial enrollment is not the only opportunity to unlock a direction.
        # Later independent recalls can support typing/grid/writing while the
        # original cards retain their own due dates. Include legacy learned
        # symbols too: the path already treats them as enrolled/completed.
        for entry, kind, stage in self.kana_advancement_targets(kinds, now=now, pending=pending, stages=stages,
                                                                cards=projected, unavailable=unavailable,
                                                                route_id=route_id):
            cid, script = entry["content_id"], entry["script"]
            if not can_build(cid, kind, script):
                continue
            made = card_questions(self.repo, self.store, cid, kind, script, seed=seed, now=now)
            for q in made:
                q.metadata.update(study_path_entry=entry["id"], schedule_mode="spaced",
                                  adaptive_policy="scaffold-v1", adaptive_stage=stage)
                q.metadata["practice_config"].update(schedule_mode="spaced", adaptive=True,
                    kinds=kinds, requested_script=script, rounds=1)
            questions.extend(made)
        questions = deduplicate_questions(self.store, questions, now, new_abilities=True)
        prepared = prepare_session_snapshot(questions, seed) if questions else None
        return KanaQueuePlan(tuple(requested_kinds), bool(audio_available), bool(handwriting_available),
                             bool(lazy_questions), now, pending["session_id"] if pending and questions else None,
                             prepared, len(questions))
