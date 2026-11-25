"""Read-only course planning; snapshot writes remain owned by LearningStore."""
from copy import deepcopy
from collections.abc import Iterable, Iterator
from typing import NamedTuple, Protocol
from .learning_questions import KIND_ABILITIES, question_kinds, build_learning_question, variant_template
from .intro_batch import ROUTE, ROUTE_KEY

class CourseQuestionPlan(NamedTuple):
    unit: dict | None
    question: dict


class CourseQueries(Protocol):
    def pending_abilities(self, content_id: str) -> set[str]: ...
    def instruction_ready_ids(self) -> Iterable[str]: ...
    # Optional: introduced_kana_ids(), read via getattr (design 11 section 5, D5).
    # Optional: course_ids(), the enrolled content ids, read via getattr (design 11 section 18, Q6).


def known_ids(queries) -> frozenset[str]:
    """The dependency gate's known set: instruction-ready items plus introduced kana.

    Kana ids are "kana:<kana id>:<script>" (StudyQueries.introduced_kana_ids),
    the form question_dependencies "kana" lists use. A reader without that
    query contributes ready items only.
    """
    kana = getattr(queries, "introduced_kana_ids", None)
    return frozenset({*queries.instruction_ready_ids(), *(kana() if callable(kana) else ())})


def enrolled_ids_reader(queries):
    """The stem-gloss tier's reader of enrolled ids (learning_practice_policy._enrolled_reader), or None.

    queries.course_ids() is read at most once, on the first call, so one planned
    lesson costs at most one read for all its abilities, and none unless a
    template is otherwise eligible for the tier. A reader without that query
    gives None: the tier stays off (the practice deck's one-ability reader).
    """
    course_ids = getattr(queries, "course_ids", None)
    if not callable(course_ids):
        return None
    cell = []

    def read():
        if not cell:
            cell.append(tuple(course_ids()))
        return cell[0]
    return read


class CourseProgressReader(Protocol):
    def get_unit(self, content_id: str, ability: str) -> dict | None: ...
    def practice_history(self, content_id: str, ability: str) -> list[dict]: ...
    # Optional: batch_ability_gate(content_id, enabled_kinds), read via getattr.


def preview_question_plans(cache: dict | None, content_id: str, load_item, *, enabled_kinds: Iterable[str],
                           system_japanese_input: bool, unavailable_ids: Iterable[str] = (),
                           **reader) -> list[CourseQuestionPlan]:
    """iter_question_plans of one saved lesson for a read-only preview, planned once per learner state.

    Home's counts and the add page's existing-work line both plan a question
    for every lesson that lacks one, the second a moment after the first and
    from the same records. ``cache`` is a dict its owner keeps for exactly one
    unchanged learner database (None: nothing is kept); planning reads no
    clock, so the plans of that state do not age. The same plans are handed
    out again: a caller reads them and never changes them. An append plans for
    itself (iter_question_plans), at the moment it writes.
    load_item() gives the saved lesson; it is called only when the lesson has
    to be planned.
    """
    def plans():
        return list(iter_question_plans(load_item(), enabled_kinds=enabled_kinds,
                                        system_japanese_input=system_japanese_input,
                                        unavailable_ids=unavailable_ids, **reader))
    if cache is None:
        return plans()
    key = (content_id, frozenset(enabled_kinds), bool(system_japanese_input), frozenset(unavailable_ids))
    if key not in cache:
        cache[key] = plans()
    return cache[key]


def iter_question_plans(item: dict, *, enabled_kinds: Iterable[str], queries: CourseQueries,
                        learning_store: CourseProgressReader, system_japanese_input: bool,
                        unit_states: dict | None = None,
                        unavailable_ids: Iterable[str] = (),
                        avoid_templates: dict | None = None) -> Iterator[CourseQuestionPlan]:
    """Read missing ability candidates; existing snapshots remain owners.

    Explicit templates carry the response mode and reviewed scaffold needed
    to preview a due time. Build their full question only on continuation.
    The few legacy template-free lessons use the original builder directly.
    unit_states is an explicit caller-owned projection from this synchronous
    read action, never a saved preview used to authorize a later append.
    unavailable_ids (the caller's AvailabilityView.unavailable_ids, design 08
    section 8.2): content of a disabled/missing library plans nothing, before
    any template or queue read.
    """
    if item["id"] in frozenset(unavailable_ids):
        return
    enabled = set(enabled_kinds)
    pending = queries.pending_abilities(item["id"])
    admitted = item.get("enrolled_abilities", item.get("abilities", []))
    if set(admitted) <= pending:
        return
    # No enabled kind tests any of the missing abilities (a listening direction while the heard-word kind is
    # off or has no voice): nothing can be planned whatever the lesson's tasks are, so its table is not built.
    # Such a lesson stays "needing a question" and was planned in vain on every queue pass and preview.
    served = {KIND_ABILITIES[kind] for kind in enabled if kind in KIND_ABILITIES}
    if not any(ability in served for ability in admitted if ability not in pending):
        return
    from .learning_practice_policy import select_practice_plan
    from .learning_questions import practice_templates
    # One table for the whole lesson: its kinds and every ability's plan read it (they used to rebuild it,
    # and copy the lesson, once each).
    templates = practice_templates(item)
    available = question_kinds(item, templates=templates)
    # 四之二: a batch-introduced word offers its written reading only after a
    # formal reading choice. Other items keep every enabled kind.
    gate = None
    if (item.get("checkpoint") or {}).get(ROUTE_KEY) == ROUTE:
        reader = getattr(learning_store, "batch_ability_gate", None)
        gate = reader(item["id"], enabled) if callable(reader) else None
    closed_kinds = gate["closed_kinds"] if gate else frozenset()
    known = None
    enrolled = enrolled_ids_reader(queries)
    for ability in admitted:
        if ability in pending:
            continue
        kinds = [k for k in available if KIND_ABILITIES[k] == ability and k in enabled and k not in closed_kinds]
        if not kinds:
            continue
        state = (learning_store.get_unit(item["id"], ability) if unit_states is None
                 else unit_states.get(ability))
        count = int((state or {}).get("attempt_count", 0))
        policy_state = dict(state or {})
        policy_state["system_japanese_input"] = system_japanese_input
        # The dependency gate (design 11 section 5, D3) always receives the
        # known set: items whose explanation has been shown, introduced kana
        # and this item. Read once per planned item; it does not manufacture
        # mastery or alter those items' review dates.
        if known is None:
            known = known_ids(queries) | {item["id"]}
        policy_state["known_background_ids"] = known
        # Design 11 section 5, D8: only this formal course path lets a simple rule appear
        # before its lesson while the learner is at the very start (the page shows a hint).
        policy_state["early_allowance"] = True
        # Design 11 section 18: the unit's review_count (in the unit row) releases leveled templates (Q5),
        # and this path may serve a template with one or two glossed stem words the learner has not
        # enrolled (Q6; the course question page shows the template's reviewed glosses, Q7).
        if enrolled is not None:
            policy_state["gloss_allowance"] = True
            policy_state["enrolled_ids"] = enrolled
        # An open probe (design 03, probe first): an app start ended its queued question, so the unit is
        # planned again as a qualifying probe under its usual id; none now leaves the ordinary plan.
        # Asked only for an unrated unit, so the pass does not grow with a large record.
        probe_reader = getattr(learning_store, "open_probe_question", None)
        if callable(probe_reader) and not (state or {}).get("review_count"):
            probe = probe_reader(item["id"], ability, enabled_kinds=sorted(enabled - closed_kinds), known=known,
                                 system_japanese_input=system_japanese_input)
            if probe is not None:
                yield CourseQuestionPlan(state, dict(probe, id=f"{item['id']}:{ability}:review:{count}"))
                continue
        history = learning_store.practice_history(item["id"], ability)
        # avoid_templates ({ability: (kind, variant)}): only the consolidation deck passes it (design 12 N5).
        selected = select_practice_plan(item, ability, kinds, policy_state, history,
                                        templates=templates, shared=True,
                                        avoid=(avoid_templates or {}).get(ability))
        if selected is None:
            continue
        kind, variant = selected["kind"], selected["variant"]
        if templates is not None:
            q = {"target_id": item["id"], "ability": ability, "kind": kind,
                 "variant": variant,
                 "input_mode": selected["input_mode"]}
            raw = variant_template(item, kind, variant, templates)
            if "practice" in raw:
                # Scaffold validation includes public prompt spans. Keep
                # the exact metadata shape, including invalid values, so
                # this projection cannot become legacy free retrieval.
                q["prompt"] = raw.get("prompt", "")
                q["practice"] = deepcopy(raw["practice"])
        else:
            q = build_learning_question(item, kind, variant=variant)
        q["id"] = f"{item['id']}:{ability}:review:{count}"
        yield CourseQuestionPlan(state, q)
