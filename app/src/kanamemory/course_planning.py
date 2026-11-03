"""Read-only course planning; snapshot writes remain owned by LearningStore."""
from copy import deepcopy
from collections.abc import Iterable, Iterator
from typing import NamedTuple, Protocol
from .learning_questions import KIND_ABILITIES, question_kinds, build_learning_question

class CourseQuestionPlan(NamedTuple):
    unit: dict | None
    question: dict


class CourseQueries(Protocol):
    def pending_abilities(self, content_id: str) -> set[str]: ...
    def instruction_ready_ids(self) -> Iterable[str]: ...


class CourseProgressReader(Protocol):
    def get_unit(self, content_id: str, ability: str) -> dict | None: ...
    def practice_history(self, content_id: str, ability: str) -> list[dict]: ...


def iter_question_plans(item: dict, *, enabled_kinds: Iterable[str], queries: CourseQueries,
                        learning_store: CourseProgressReader, system_japanese_input: bool) -> Iterator[CourseQuestionPlan]:
    """Read missing ability candidates; existing snapshots remain owners.

    Explicit templates carry the response mode and reviewed scaffold needed
    to preview a due time. Build their full question only on continuation.
    The few legacy template-free lessons use the original builder directly.
    """
    enabled = set(enabled_kinds)
    pending = queries.pending_abilities(item["id"])
    admitted = item.get("enrolled_abilities", item.get("abilities", []))
    if set(admitted) <= pending:
        return
    from .learning_practice_policy import select_practice_plan
    from .learning_questions import practice_templates
    available = question_kinds(item)
    templates = practice_templates(item)
    for ability in admitted:
        if ability in pending:
            continue
        kinds = [k for k in available if KIND_ABILITIES[k] == ability and k in enabled]
        if not kinds:
            continue
        state = learning_store.get_unit(item["id"], ability)
        count = int((state or {}).get("attempt_count", 0))
        policy_state = dict(state or {})
        policy_state["system_japanese_input"] = system_japanese_input
        # Only courses which actually name background learning items need
        # this small admission query. It does not manufacture mastery or
        # alter those items' review dates.
        if any(raw["practice"].get("background_ids")
               for kind in kinds for raw in (templates or {}).get(kind, [])
               if isinstance(raw, dict) and isinstance(raw.get("practice"), dict)):
            policy_state["known_background_ids"] = {item["id"], *queries.instruction_ready_ids()}
        history = learning_store.practice_history(item["id"], ability)
        selected = select_practice_plan(item, ability, kinds, policy_state, history)
        if selected is None:
            continue
        kind, variant = selected["kind"], selected["variant"]
        if templates is not None:
            q = {"target_id": item["id"], "ability": ability, "kind": kind,
                 "variant": variant,
                 "input_mode": selected["input_mode"]}
            raw = templates[kind][variant]
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
