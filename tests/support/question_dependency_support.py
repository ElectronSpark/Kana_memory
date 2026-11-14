"""Shared synthetic question_dependencies fixtures (design 11 section 5); no collected scenarios."""
from kanamemory import learning_questions
from kanamemory.learning_questions import template_fingerprint
from kanamemory.study_queries import StudyQueries

from support.learning_practice_policy_support import lesson

WORD_ID = "word:synthetic:dep"
GRAMMAR_ID = "grammar:synthetic:plan"


def requirement(words=(), grammar=(), kana=(), oov=()):
    """One template's dependency record; empty fields are omitted."""
    return {name: list(values) for name, values in
            (("words", words), ("grammar", grammar), ("kana", kana), ("oov", oov)) if values}


def namespace(*declarations):
    """Build a v1 namespace from (content_id, kind, raw_template, requirement) rows."""
    items = {}
    for content_id, kind, raw, value in declarations:
        items.setdefault(content_id, {}).setdefault(kind, {})[template_fingerprint(raw)] = value
    return {"schema_version": 1, "items": items}


def install(monkeypatch, value):
    """Replace the loaded namespace for one test; the installed pack is untouched."""
    monkeypatch.setattr(learning_questions, "_DEPENDENCIES", value)


def bare_lesson():
    """The policy test lesson with every reviewed practice block removed (undeclared templates)."""
    item = lesson()
    for values in item["question_templates"].values():
        for raw in values:
            raw.pop("practice", None)
    return item


def choice(prompt, answer, other, **extra):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "说明",
            "input_mode": "choice", "safe": True, **extra}


def word(cid=WORD_ID, abilities=("understanding", "reading", "use")):
    """A synthetic word lesson whose templates carry no practice metadata."""
    return {"id": cid, "content_id": cid, "family": "word", "title": "語一", "reading": "ごいち",
            "meaning": "词一", "revision": "1", "abilities": list(abilities), "source_ids": ["test"],
            "instruction": {"summary": "词一", "rule": "名词。", "steps": [], "example": "語一です。",
                            "translation": "是词一。", "prerequisite": "", "contrast": ""},
            "question_templates": {
                "vocab_meaning": [choice("語一", "词一", "别的词"),
                                  choice("語一は本です。", "词一", "别的词")],
                "vocab_reading_choice": [choice("語一", "ごいち", "ごに", task="読み方")],
                "vocab_reading": [{"prompt": "語一", "answer": "ごいち", "input_mode": "text",
                                   "feedback": "说明", "safe": True}],
                "vocab_usage": [choice("＿＿です。", "語一", "語二"),
                                choice("＿＿を読みます。", "語一", "語二")]}}


def grammar(cid=GRAMMAR_ID):
    """A synthetic grammar lesson with three understanding templates and one application template."""
    return {"id": cid, "family": "grammar", "revision": "v1", "abilities": ["understanding", "application"],
            "enrolled_abilities": ["understanding", "application"],
            "instruction": {"title": "〜ている", "summary": "正在做", "rule": "て形＋います",
                            "example": "本を読んでいます。", "translation": "正在读书"},
            "question_templates": {
                "grammar_meaning": [choice("本を読んでいます。", "正在读书", "读过书"),
                                    choice("水を飲んでいます。", "正在喝水", "喝过水"),
                                    choice("見ています。", "正在看", "看过")],
                "grammar_cloze": [choice("本を読んで（　）。", "います", "でした")]}}


def introduce_background(monkeypatch, *items, variants=None, answered=False):
    """Treat the lessons' declared question dependencies as already introduced.

    Design 11 section 5 (D3/D4): guides and batch quizzes never show unmet
    material, and the route introduces a lesson's background words, grammar
    and kana before it. Controller tests that enroll one lesson into an empty
    store use this instead: the ids join StudyQueries.instruction_ready_ids, which
    the gate's known set (course_planning.known_ids) and the new-content checks
    read; no progress row is written. Out-of-vocabulary surfaces stay unknown.
    With variants ({kind: indexes}) only those templates' requirements are added.
    With answered=True they also count as independently answered
    (learning_dependencies.independent_ready_ids, read late by diagnostics):
    for scenarios whose learner is mid-course; never for an item under test's
    own prerequisites.
    Returns the added ids.
    """
    from kanamemory.learning_questions import OOV_TOKEN_PREFIX, practice_templates, template_requires
    extra = set()
    for item in items:
        for kind, templates in (practice_templates(item) or {}).items():
            for index, raw in enumerate(templates):
                if variants is not None and index not in variants.get(kind, ()):
                    continue
                extra.update(cid for cid in template_requires(item, kind, raw) or ()
                             if not cid.startswith(OOV_TOKEN_PREFIX))
    _add_ready(monkeypatch, extra)
    if answered:
        from kanamemory import learning_dependencies
        original = learning_dependencies.independent_ready_ids
        monkeypatch.setattr(learning_dependencies, "independent_ready_ids",
                            lambda *args, **kwargs: set(original(*args, **kwargs)) | extra)
    return frozenset(extra)


def introduce_example_background(monkeypatch, app, *items):
    """Treat every item referenced by the lessons' shared examples as introduced.

    Design 11 section 5 (D3): intro and detail pages show only examples whose
    other references are known. Like introduce_background, only the read of
    instruction-ready ids grows. Returns the added ids.
    """
    service = app.example_store()
    extra = {target for item in items for example in service.for_item(item["id"])
             for target in service.reference_targets(example["id"])}
    _add_ready(monkeypatch, extra)
    return frozenset(extra)


def _add_ready(monkeypatch, extra):
    original = StudyQueries.instruction_ready_ids
    monkeypatch.setattr(StudyQueries, "instruction_ready_ids", lambda self: original(self) | set(extra))
