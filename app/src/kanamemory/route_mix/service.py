"""MainRouteMix: one app's main-route mix (design 11 section 16, M10).

mixed(tree, library_of) is the UI-thread read: the mixed tree when the stored
plan was computed for exactly this derivation tree and content and applies to
it, else None (the derivation order, as before); a plan computed for other
kind switches of the learner is used until the worker has saved the plan of
the current ones. prepare(...) is the
worker-thread computation: it reads the readiness of the route's items,
computes a plan, checks that the plan applies to its own tree, saves it and
then raises ``generation``; the app keys its StudyPathService by it. A worker
gets detached inputs (plan.snapshot of the tree, the catalogue items, the
library of each entry) and never touches a learner database. cancel() (app
exit) stops a computation at its next check (between readiness chunks and
between stages); a cancelled computation saves nothing.
"""
from __future__ import annotations

import asyncio
from functools import partial
from pathlib import Path
import threading
import time

from .params import DEFAULT_PARAMS, MixParams, switched_kinds
from .placement import MixCancelled
from .plan import MixPlan, apply_plan, plan_key, plan_mix, same_apart_from_switches, snapshot, tree_digest
from .store import PlanFile

_UNREAD = object()
REFUSED = "PlanRefused"  # last_error: a computed plan does not apply to its own tree; nothing was saved


class MainRouteMix:
    def __init__(self, data_dir, *, params: MixParams = DEFAULT_PARAMS, identity=None, readiness=None,
                 plan_file=None):
        """identity() -> content identity or None; readiness(item ids, items) -> Readiness (tests, tools)."""
        self.data_dir = Path(data_dir)
        self.params = params
        self._identity_source, self._readiness_source = identity, readiness
        self._file = PlanFile(self.data_dir) if plan_file is None else plan_file
        self._lock = threading.RLock()
        self._cancel = threading.Event()
        self._plan = _UNREAD
        self._identity = _UNREAD
        self._digest = None      # (tree, digest) of the last tree asked about
        self._applied = None     # (tree, generation, without library_of, mixed tree)
        self.generation = 0
        self.last_error = None   # why the last computation or save failed (an error type name, or REFUSED)
        self.last_timings = None

    def cancel(self) -> None:
        """App exit: a running computation stops at its next check and saves nothing; none starts."""
        self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ------------------------------------------------------------------ reads (UI thread)

    def identity(self):
        """The content identity, read once (None: no plan file is read or written)."""
        with self._lock:
            if self._identity is _UNREAD:
                if self._identity_source is None:
                    from .sources import content_identity
                    self._identity = content_identity()
                else:
                    self._identity = self._identity_source()
            return self._identity

    def stored(self) -> MixPlan | None:
        """The current plan: the one saved by prepare, else the plan file's, read once and only
        when there is a file and a content identity (no file: not even the identity is read)."""
        with self._lock:
            if self._plan is _UNREAD:
                value = self._file.load() if self._file.present() and self.identity() is not None else None
                try:
                    self._plan = None if value is None else MixPlan.from_json(value)
                except ValueError:
                    self._plan = None
            return self._plan

    def key_for(self, tree):
        """The plan key this derivation tree (or its snapshot) needs; None without a content identity."""
        identity = self.identity()
        if identity is None:
            return None
        memo = self._digest
        if memo is not None and memo[0] is tree:
            digest = memo[1]
        else:
            digest = tree_digest(tree)
            self._digest = (tree, digest)
        return plan_key(self.params, digest, identity)

    def mixed(self, tree, library_of=None):
        """The mixed tree of a stored plan for this very tree, else None. ``library_of``: the content
        id -> extension library the tree was built with (M8(b) moves are checked against it). The
        same tree object (as a replaced service hands it over) gives the same mixed tree object
        until a new plan is saved."""
        with self._lock:  # the plan and its generation together: a worker may save a new plan meanwhile
            generation, plan, applied = self.generation, self.stored(), self._applied
        bare = library_of is None
        if applied is not None and applied[0] is tree and applied[1] == generation and applied[2] == bare:
            return applied[3]
        result = None
        if plan is not None:
            key = self.key_for(tree)
            # A plan of this very tree, content and algorithm that was computed for other switches stays in
            # use until prepare (which compares the whole key) has saved the plan of the current ones: after
            # a switch change the route does not fall back to the derivation order in between.
            if key is not None and same_apart_from_switches(plan.key, key):
                from .sources import library_entries, mixable_stage_ids
                result = apply_plan(tree, plan, mixable=mixable_stage_ids(tree),
                                    providers=library_entries(tree, library_of))
        self._applied = (tree, generation, bare, result)  # a later generation reads again
        return result

    def current_for(self, tree) -> bool:
        """Whether the stored plan was computed for exactly this derivation tree, content, algorithm and
        switches (the whole key). mixed() also applies a plan of other switches: that plan is in use but
        not current, and the warm-up still has to compute this learner's (StudyPathService.route_mix_job)."""
        plan = self.stored()
        key = self.key_for(tree) if plan is not None else None
        return key is not None and dict(plan.key) == key

    # ------------------------------------------------------------------ computation (worker thread)

    def job(self, tree, items, library_of):
        """A callable for a worker thread that computes and saves the plan of ``tree``, made from
        detached copies on the calling (UI) thread; None when no plan can be kept (no identity,
        cancelled)."""
        if self._cancel.is_set() or self.identity() is None:
            return None
        from .sources import library_entries, overridden_entries
        detached = snapshot(tree)
        providers = library_entries(detached, library_of)
        overridden = overridden_entries(detached, library_of)
        catalogue = {item["id"]: item for item in items}
        return partial(self.prepare, detached, catalogue, providers, overridden)

    def compute(self, tree, items, providers, overridden=frozenset()) -> MixPlan | None:
        """The plan of a derivation tree (or snapshot) without saving it; None without identity.
        ``overridden``: sources.overridden_entries. MixCancelled when cancel() was called meanwhile."""
        from . import sources
        identity = self.identity()
        if identity is None:
            return None
        key = plan_key(self.params, tree_digest(tree), identity)
        started = time.perf_counter()
        mixable = sources.mixable_stage_ids(tree)
        needed = sources.needed_items(tree, mixable, providers)
        if self._readiness_source is None:
            readiness = sources.read_readiness(needed, items, cancelled=self._cancel.is_set, kinds=self.params.kinds)
        else:
            readiness = self._readiness_source(needed, items)
        read = time.perf_counter()
        plan = plan_mix(tree, readiness, mixable=mixable, library_of=providers, key=key, params=self.params,
                        cancelled=self._cancel.is_set, overridden=overridden)
        self.last_timings = {"sources": read - started, "placement": time.perf_counter() - read,
                             "items": len(needed)}
        return plan

    def prepare(self, tree, items, providers, overridden=frozenset()) -> bool:
        """Compute and save the plan of this tree unless the stored one is for it and applies to it;
        True when a new plan is in place (``generation`` raised). Thread-safe; reads no learner data.

        A new plan is saved only when it applies to its own tree (else last_error is REFUSED and
        nothing changes); a cancelled computation saves nothing.
        """
        if self._cancel.is_set():
            return False
        identity = self.identity()
        if identity is None:
            return False
        from .sources import mixable_stage_ids
        mixable = mixable_stage_ids(tree)
        current = self.stored()
        if (current is not None and dict(current.key) == plan_key(self.params, tree_digest(tree), identity)
                and apply_plan(tree, current, mixable=mixable, providers=providers) is not None):
            return False
        try:
            plan = self.compute(tree, items, providers, overridden)
        except MixCancelled:
            return False
        if plan is None or self._cancel.is_set():
            return False
        if apply_plan(tree, plan, mixable=mixable, providers=providers) is None:
            self.last_error = REFUSED
            return False
        try:
            self._file.save(plan.to_json())
        except (OSError, ValueError) as error:
            self.last_error = type(error).__name__  # still used in this process
        with self._lock:
            self._plan = plan
            self.generation += 1
        return True


def main_route_mix(previous, data_dir, kinds=()):
    """The MainRouteMix of this data directory: the replaced service's, else a new one for these
    switches; None without a data directory.

    ``kinds``: the learner's switched-on kinds, or a zero-argument callable returning them, called only
    when a new mix is made (part of its plan key). The replaced service's mix is kept whatever the
    switches are now: a switch changed while the app runs keeps the plan in use, and the next start-up
    warm-up computes the plan of the new switches in the background; until it is saved, mixed() goes on
    with the stored plan of the old switches (the stored file holds one plan)."""
    if data_dir is None:
        return None
    mix = getattr(previous, "route_mix", None)
    if isinstance(mix, MainRouteMix) and mix.data_dir == Path(data_dir):
        return mix
    return MainRouteMix(data_dir, params=MixParams(kinds=switched_kinds(kinds() if callable(kinds) else kinds)))


async def prepare_in_background(source) -> bool:
    """Startup warm-up step: the plan of a StudyPathService's derivation tree on a worker thread when it
    has none (``source``: the service, or a zero-argument callable returning it, called here). True
    when a new plan was saved (the next StudyPathService uses it). Any failure, getting the service and
    making the job included (content identity, snapshot), leaves the derivation order."""
    service = None
    try:
        service = source() if callable(source) else source
        make = getattr(service, "route_mix_job", None)
        job = make() if callable(make) else None
        if job is None:
            return False
        return bool(await asyncio.to_thread(job))
    except Exception as error:  # noqa: BLE001 - optional warm-up: the derivation order stays
        mix = getattr(service, "route_mix", None)
        if mix is not None:
            mix.last_error = type(error).__name__
        return False
