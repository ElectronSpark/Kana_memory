"""StoreAdmission: the add-content use case put together for one learner store (design 13 S2).

The composition root of the package: the two caches (AdmissionCosts), the
workload context admission_workload reads, and the service. Built lazily by
its user (kanamemory.admission_runtime for the app) and replaced together
with the store object, like arrangement.assembly.StoreArrangement: the caches
belong to one connection.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..admission_workload import WorkloadContext
from .costs import AdmissionCosts
from .model import AddInputs
from .service import AddContentService


@dataclass(frozen=True)
class StoreAdmission:
    store: Any
    learning_store: Any
    costs: AdmissionCosts          # the question-plan preview (shared with the home counts) and the existing work
    workload: WorkloadContext      # what admission_workload reads
    service: AddContentService     # the operations of the add page

    @classmethod
    def for_store(cls, store, learning_store, *, paths: Callable[[], Any] | None = None,
                  repository: Callable[[], Any] | None = None,
                  inputs: Callable[[Any], AddInputs] | None = None) -> StoreAdmission:
        """``paths``: the app's current StudyPathService; ``repository``: its content repository;
        ``inputs(pending)``: the device abilities and settings, read at every workload count.

        Without them only what needs none of them works (the caches, the
        course and kana dues); asking a missing one raises LookupError.
        """
        costs = AdmissionCosts(store)
        workload = WorkloadContext(store, learning_store, costs, paths or _missing("paths"),
                                   repository or _missing("repository"), inputs or _missing("inputs"))
        service = AddContentService(paths=workload.paths, workload=workload,
                                    restart=lambda: _restart_main_round(store))
        return cls(store, learning_store, costs, workload, service)

    def serves(self, store, learning_store) -> bool:
        return self.store is store and self.learning_store is learning_store


def _missing(name: str) -> Callable[..., Any]:
    def ask(*args):
        raise LookupError(f"StoreAdmission was built without {name}")
    return ask


def _restart_main_round(store) -> None:
    """One new scheduling pass of the main lane after an enrollment (owner: practice_round)."""
    from ..practice_round import restart
    restart(store, lane="main")
