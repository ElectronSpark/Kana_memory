"""Question-kind specifications and their derived lookup tables.

Pure rules: no Toga, database, platform, network or other kanamemory imports.
A registry only holds specifications and optional callable hooks; it never
holds content, reads settings or grants permission to issue a question. The
derived views reproduce the kind tables that learning_questions exposes, in
registration order, so existing consumers keep their exact values.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

FAMILIES = ("word", "grammar", "pronunciation")
KNOWN_ABILITIES = ("understanding", "listening", "reading", "use", "application")
# validate_templates reasons with this prefix mean "skip this payload", not
# "reject the whole library": an older App may meet a newer library's kinds.
IGNORED = "ignored:"
# Template sources whose variants a provider derives from the lesson's own
# fields: available without a reviewed pack, never supplied by a library.
GENERATED_SOURCES = frozenset({"sound"})
# Normal forms a KindGrader may name; learning_questions owns their functions.
GRADER_NORMALIZERS = ("text", "reading")
# Where an answer-only order translation finds its complete local sentence:
# the public prompt, the prompt with its single gap filled by the answer, or
# the canonical answer (the stimulus may be wrong or only shuffled pieces).
ORDER_TRANSLATION_SOURCES = ("prompt", "gap", "answer")


class TemplateEnv(Protocol):
    """What the question builder lends a template provider at call time.

    Providers hold no content: the builder owns content namespaces and their
    loading, so lookups always see its current values.
    """

    def word_pool(self) -> tuple:
        """(id, title, normal reading, attested readings, cohort) for bundled words
        (word_sound_questions.word_pool; bank variants filter it by bank_pool)."""

    def practice_binding(self, item: Mapping, namespace: str) -> Mapping | None:
        """The item_fingerprint-bound entry for item in a named template
        namespace (current or exact history), or None when unbound/unknown."""

    def source_templates(self, item: Mapping, kind: str) -> list:
        """Fresh copies of the lesson's own reviewed templates of kind, in
        variant order ([] when it has none); derived kinds read their source."""

    def spoken_prompt(self, item: Mapping, kind: str, raw: Mapping) -> str | None:
        """What the app would speak for raw's prompt as a question of kind
        (question_support.prompt_audio_text), or None when nothing."""


class BuildEnv(TemplateEnv, Protocol):
    """What the question builder lends a legacy ``builder`` at call time."""

    def legacy_word_base(self, item: Mapping) -> dict:
        """A fresh shared legacy word question (meaning choice over the
        original lessons); a builder updates it for its own kind."""

    def legacy_word_lessons(self, item: Mapping) -> list:
        """The original authored lessons ([] for a dictionary-only lesson)."""

    def content(self, name: str, default: Any) -> Any:
        """An isolated copy of a learning_questions content table."""

    def reading_normal(self, text: Any) -> str:
        """The kana-folded normal form used for readings."""

    def legacy_grammar_variants(self, item: Mapping, kind: str) -> list:
        """A fresh copy of a grammar lesson's original authored variants of
        kind ([] when it has none)."""


@dataclass(frozen=True)
class KindGrader:
    """Kind-specific steps of the generic local grader, in pipeline order.

    normalizer names a normal form owned by the grader ("text" or "reading").
    visible_answer(question, normal) -> result | None runs before the accepted
    set: the stimulus may already show the answer. match(question, value,
    normal, accepted) -> (result | None, accepted) runs after the personal
    rubric and may refine the accepted set of the generic exact match.
    incorrect_fields extend a strict or known-wrong incorrect result.
    """

    normalizer: str = "text"
    visible_answer: Callable[..., Any] | None = None
    match: Callable[..., Any] | None = None
    incorrect_fields: tuple[tuple[str, Any], ...] = ()


@dataclass(frozen=True)
class KindPresentation:
    """Instruction cues of one kind, read by question_presentation in order.

    quoted_tasks: full-match patterns with a ``word`` group that locate one
    target in the prompt (cue quoted_cue); focused_tasks apply only with
    explicit target spans, plain_tasks otherwise; task_cue(task) -> cue | None
    rewrites an unlisted task; folds_translation marks kinds whose known
    translation cues move into the folded hint. prompt_is_instruction marks
    kinds whose prompt is an instruction (pieces to order, a requirement),
    so reviewed target spans never turn it into study text.
    form_cue(question, instruction) -> cue | None rewrites the final cue
    with a form category of the canonical answer (never answer text). Cues are
    Japanese study text unless listed in ui_cues; study_tasks are tasks shown
    verbatim that are study text too (Registry.study_cues collects both).
    """

    quoted_tasks: tuple[str, ...] = ()
    quoted_cue: str = ""
    focused_tasks: tuple[tuple[str, str], ...] = ()
    plain_tasks: tuple[tuple[str, str], ...] = ()
    task_cue: Callable[[str], str | None] | None = None
    folds_translation: bool = False
    prompt_is_instruction: bool = False
    form_cue: Callable[[Mapping[str, Any], str], str | None] | None = None
    ui_cues: tuple[str, ...] = ()
    study_tasks: tuple[str, ...] = ()


class TemplateProvider(Protocol):
    """Hook ``template_provider(item, spec, env) -> list[dict]``.

    Returns this kind's raw template variants for the item, in their stable
    order; [] when the item cannot have this kind. Must not mutate item and
    must return fresh objects (callers may change them). Variants are indexed
    by question variant, so their order is part of the snapshot contract.
    """

    def __call__(self, item: Mapping, spec: QuestionKindSpec, env: TemplateEnv) -> list[dict]: ...


@dataclass(frozen=True)
class QuestionKindSpec:
    """Everything known about one kind string; kind strings never change."""

    kind: str
    label: str
    family: str
    ability: str | None
    default_enabled: bool = True
    opt_in_listening: bool = False
    needs_audio: bool = False
    text_input: bool = False
    practice_only: bool = False
    added_at_version: int | None = None
    template_source: str = "authored"
    experimental: bool = False
    # Cross-family snapshot rules, read by learning_questions as derived sets.
    # safe_support: a non-guided choice snapshot may enable reviewed reading,
    # accent and optional audio support (the answer is not in its prompt).
    safe_support: bool = False
    # background_support: a non-guided snapshot may gloss learned background
    # words of its sentence (question_support.background_support).
    background_support: bool = False
    # order_translation_source: one of ORDER_TRANSLATION_SOURCES, or None
    # when this kind never binds an answer-only order translation.
    order_translation_source: str | None = None
    # dictionary_lesson: offered for a dictionary-only lesson without
    # reviewed templates (no guessed distractors).
    dictionary_lesson: bool = False
    # reads_written_form: asks for the reading of a written form, so it is not
    # offered when the form already is its own kana reading.
    reads_written_form: bool = False
    # Hooks stay None until a kind family is migrated (strangler steps); a
    # None hook means the generic path in learning_questions handles it.
    # template_provider: see TemplateProvider. A generated source needs one.
    template_provider: TemplateProvider | None = None
    # builder(item, spec, env: BuildEnv, variant) -> raw template for a lesson
    # without reviewed templates (the legacy word path).
    builder: Callable[..., Any] | None = None
    grader: KindGrader | None = None
    presenter: KindPresentation | None = None
    # input_mode_policy(question) -> romaji composition style of a free-text
    # answer ("reading": the whole draft; "runs": Latin runs only), or None.
    input_mode_policy: Callable[..., Any] | None = None
    # snapshot_finisher(result, raw, target) adds this kind's own snapshot
    # fields after the generic fields, in place (may raise ValueError).
    snapshot_finisher: Callable[..., Any] | None = None
    feedback_renderer_id: str | None = None
    ai_capability: str | None = None
    # derived_from: the kind whose live templates of the same lesson this
    # kind's provider turns into variants with the same numbers (a heard twin
    # of each). A variant's dependency facts, release and input availability
    # are its source's; a library never supplies such templates.
    derived_from: str | None = None


class Registry:
    """Ordered, duplicate-free kind specifications; sealed after setup."""

    def __init__(self) -> None:
        self._specs: dict[str, QuestionKindSpec] = {}
        self._sealed = False

    def register(self, spec: QuestionKindSpec) -> QuestionKindSpec:
        if self._sealed:
            raise RuntimeError("Question kind registry is sealed")
        if not isinstance(spec, QuestionKindSpec):
            raise TypeError("Expected a QuestionKindSpec")
        if not isinstance(spec.kind, str) or not spec.kind:
            raise ValueError("A question kind needs a non-empty name")
        if spec.kind in self._specs:
            raise ValueError(f"Duplicate question kind {spec.kind!r}")
        if spec.family not in FAMILIES:
            raise ValueError(f"Unknown family {spec.family!r} for {spec.kind!r}")
        # Experimental kinds are not issued, so they need no ability or label.
        if not (spec.experimental and spec.ability is None) and spec.ability not in KNOWN_ABILITIES:
            raise ValueError(f"Unknown ability {spec.ability!r} for {spec.kind!r}")
        if not spec.experimental and (not isinstance(spec.label, str) or not spec.label.strip()):
            raise ValueError(f"Question kind {spec.kind!r} needs a label")
        if spec.added_at_version is not None and (type(spec.added_at_version) is not int
                                                  or spec.added_at_version < 1):
            raise ValueError(f"Invalid added_at_version for {spec.kind!r}")
        if spec.opt_in_listening and (spec.default_enabled or not spec.needs_audio):
            raise ValueError(f"Opt-in listening kind {spec.kind!r} must need audio and start off")
        if spec.template_source in GENERATED_SOURCES and spec.template_provider is None:
            raise ValueError(f"Generated kind {spec.kind!r} needs a template provider")
        if spec.grader is not None and (not isinstance(spec.grader, KindGrader)
                                        or spec.grader.normalizer not in GRADER_NORMALIZERS):
            raise ValueError(f"Invalid grader for {spec.kind!r}")
        if spec.presenter is not None and not isinstance(spec.presenter, KindPresentation):
            raise ValueError(f"Invalid presenter for {spec.kind!r}")
        if (spec.order_translation_source is not None
                and spec.order_translation_source not in ORDER_TRANSLATION_SOURCES):
            raise ValueError(f"Invalid order translation source for {spec.kind!r}")
        if spec.experimental and spec.default_enabled:
            raise ValueError(f"Experimental kind {spec.kind!r} cannot be enabled by default")
        if spec.derived_from is not None and (spec.template_provider is None
                                              or spec.derived_from not in self._specs
                                              or self._specs[spec.derived_from].derived_from is not None):
            raise ValueError(f"Derived kind {spec.kind!r} needs a provider and a registered source kind")
        self._specs[spec.kind] = spec
        return spec

    def seal(self) -> None:
        self._sealed = True

    def get(self, kind: str) -> QuestionKindSpec | None:
        return self._specs.get(kind)

    def _live(self) -> list[QuestionKindSpec]:
        return [spec for spec in self._specs.values() if not spec.experimental]

    def kinds(self, *, include_experimental: bool = False) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._specs.values()
                     if include_experimental or not spec.experimental)

    def experimental(self) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._specs.values() if spec.experimental)

    def by_family(self, family: str, *, template_source: str | None = None) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._live() if spec.family == family
                     and (template_source is None or spec.template_source == template_source))

    def by_template_source(self, source: str) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._live() if spec.template_source == source)

    def generated(self) -> tuple[str, ...]:
        """Kinds whose templates come from lesson fields (GENERATED_SOURCES)."""
        return tuple(spec.kind for spec in self._live() if spec.template_source in GENERATED_SOURCES)

    def with_template_provider(self) -> tuple[QuestionKindSpec, ...]:
        """Live specs with a template_provider, in registration order."""
        return tuple(spec for spec in self._live() if spec.template_provider is not None)

    def derived(self) -> dict[str, str]:
        """{derived kind: its source kind} (QuestionKindSpec.derived_from)."""
        return {spec.kind: spec.derived_from for spec in self._live() if spec.derived_from is not None}

    def abilities(self) -> dict[str, str]:
        return {spec.kind: spec.ability for spec in self._live()}

    def labels(self) -> dict[str, str]:
        return {spec.kind: spec.label for spec in self._live()}

    def default_kinds(self) -> tuple[str, ...]:
        """Family order (word, grammar, pronunciation), then registration order."""
        return tuple(kind for family in FAMILIES for kind in self.by_family(family)
                     if self._specs[kind].default_enabled)

    def opt_in_listening(self) -> set[str]:
        return {spec.kind for spec in self._live() if spec.opt_in_listening}

    def listening(self) -> set[str]:
        return {spec.kind for spec in self._live() if spec.needs_audio}

    def text(self) -> set[str]:
        return {spec.kind for spec in self._live() if spec.text_input}

    def practice_only(self) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._live() if spec.practice_only)

    def added_at(self) -> dict[int, tuple[str, ...]]:
        versions = sorted({spec.added_at_version for spec in self._live()
                           if spec.added_at_version is not None})
        return {version: tuple(spec.kind for spec in self._live()
                               if spec.added_at_version == version)
                for version in versions}

    def safe_support(self) -> frozenset[str]:
        return frozenset(spec.kind for spec in self._live() if spec.safe_support)

    def background_support(self) -> frozenset[str]:
        return frozenset(spec.kind for spec in self._live() if spec.background_support)

    def order_translation_sources(self) -> dict[str, str]:
        return {spec.kind: spec.order_translation_source for spec in self._live()
                if spec.order_translation_source is not None}

    def dictionary_lesson(self) -> tuple[str, ...]:
        return tuple(spec.kind for spec in self._live() if spec.dictionary_lesson)

    def reads_written_form(self) -> frozenset[str]:
        return frozenset(spec.kind for spec in self._live() if spec.reads_written_form)

    def study_cues(self) -> set[str]:
        """Every presenter cue and verbatim task that is Japanese study text."""
        cues = set()
        for spec in self._live():
            presenter = spec.presenter
            if presenter is None:
                continue
            found = {cue for _, cue in (*presenter.focused_tasks, *presenter.plain_tasks)}
            if presenter.quoted_tasks:
                found.add(presenter.quoted_cue)
            cues |= (found - set(presenter.ui_cues)) | set(presenter.study_tasks)
        return cues

    def validate_templates(self, kind: str, payload: Any) -> tuple[bool, str]:
        """Structural check for library-supplied templates of one kind.

        Unknown, experimental, generated and derived kinds are ignorable: the reason
        starts with IGNORED and the caller skips that payload. Semantic review
        and exact item binding stay with the existing template loaders.
        """
        spec = self._specs.get(kind)
        if spec is None:
            return False, IGNORED + "unknown_kind"
        if spec.experimental:
            return False, IGNORED + "experimental_kind"
        if spec.template_source in GENERATED_SOURCES:
            return False, IGNORED + "generated_kind"
        if spec.derived_from is not None:
            return False, IGNORED + "derived_kind"
        if not isinstance(payload, list) or not payload:
            return False, "templates_not_a_list"
        for raw in payload:
            if not isinstance(raw, Mapping):
                return False, "template_not_an_object"
            # Mirrors build_learning_question's minimal local-question rule.
            if not raw.get("prompt") or not raw.get("answer") or not raw.get("feedback"):
                return False, "template_missing_prompt_answer_or_feedback"
        return True, "ok"


def is_ignorable(reason: str) -> bool:
    return isinstance(reason, str) and reason.startswith(IGNORED)
