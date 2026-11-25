"""The installed answer overlay row of a question's own template, the lesson forms of rule T8, and
the lesson readings and kana answers the near-miss rule (near_miss, rule 10) reads.

Content lookup only: no Toga, learner database, platform or clock. The namespace is read at import
like question_readings (a library change takes effect after a restart). A row is found from the
question's template, never the other way round: a question whose own template cannot be located
(AI, personal or legacy-builder questions, an id only an alias resolves, a lesson revised since)
gets no row, and a row whose sha names no installed template is never read. The located template
must be the question's own, as question_readings.question_entry decides it: a frozen reviewed task
by its template sha256, any other question by the lesson's current revision or the identical
prompt, and always the same answer. A malformed row is ignored (deep pack validation refuses it).
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache

from ..content_caches import content_cache, register as register_content_cache
from ..content_pack import load_namespace
from . import near_miss, overlay

# Absent in demo builds and in packs without rows: then every question keeps its own policy.
_OVERLAY = load_namespace(overlay.NAMESPACE, {})

# question key -> located sha256 or None; a display and its judge ask several times. Located in the
# catalogue, which the key does not name: dropped with the content caches.
_LOCATED = {}
_LOCATED_SIZE = 64
register_content_cache(lambda: _LOCATED.clear(), "answer_policy.source._LOCATED")


def _templates(content_id, kind):
    """The rows of one content id and kind ({sha: row}), or None (costs one shard lookup)."""
    namespace = _OVERLAY
    try:
        if not isinstance(namespace, Mapping) or namespace.get("schema_version") != overlay.SCHEMA_VERSION:
            return None
        entry = namespace["items"].get(content_id)
        templates = entry.get(kind) if isinstance(entry, Mapping) else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    return templates if isinstance(templates, Mapping) and templates else None


def row(content_id, kind, sha):
    """The checked row of the template ``sha`` of ``content_id`` / ``kind``, or None."""
    if not all(isinstance(value, str) for value in (content_id, kind, sha)):
        return None
    templates = _templates(content_id, kind)
    value = templates.get(sha) if templates is not None else None
    if value is None:
        return None
    try:
        return overlay.validate_row(value)
    except overlay.OverlayError:
        return None


def template_row(content_id, kind, raw):
    """The row of a raw template of ``content_id`` / ``kind`` (its sha computed only when rows exist)."""
    if not isinstance(raw, Mapping) or _templates(content_id, kind) is None:
        return None
    from ..learning_questions import template_fingerprint
    try:
        sha = template_fingerprint(dict(raw))
    except (TypeError, ValueError):
        return None
    return row(content_id, kind, sha)


def own_template_sha(question):
    """The sha256 of the question's own installed template, or None (see the module text)."""
    if not isinstance(question, Mapping) or question.get("ai_generated"):
        return None
    target_id, kind = question.get("target_id"), question.get("kind")
    if not isinstance(target_id, str) or not isinstance(kind, str):
        return None
    source = question.get("practice_source")
    frozen = source.get("template_sha256") if isinstance(source, Mapping) else None
    key = (target_id, kind, question.get("variant"), frozen, question.get("answer"),
           question.get("prompt"), question.get("content_revision", question.get("revision")))
    try:
        hash(key)
    except TypeError:
        return None
    if key in _LOCATED:
        return _LOCATED[key]
    located = _locate(question, target_id, frozen)
    _LOCATED[key] = located
    while len(_LOCATED) > _LOCATED_SIZE:
        del _LOCATED[next(iter(_LOCATED))]
    return located


def _locate(question, target_id, frozen):
    from ..learning_content import get_learning_item
    from ..learning_questions import question_template, template_fingerprint
    try:
        item = get_learning_item(target_id)
        located = question_template(item, question) if item is not None else None
        if located is None or not isinstance(located[1], Mapping):
            return None
        lesson, raw = located
        sha = template_fingerprint(raw)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None
    if raw.get("answer") != question.get("answer"):
        return None
    if frozen is not None:
        return sha if frozen == sha else None
    # After a lesson revision a variant number may name another template: then only the same sentence.
    own = (lesson.get("revision") == question.get("content_revision", question.get("revision"))
           or raw.get("prompt") == question.get("prompt"))
    return sha if own else None


def question_row(question):
    """The overlay row of the question's own template, or None; no template lookup without rows."""
    if not isinstance(question, Mapping):
        return None
    target_id, kind = question.get("target_id"), question.get("kind")
    if not isinstance(target_id, str) or not isinstance(kind, str) or _templates(target_id, kind) is None:
        return None
    sha = own_template_sha(question)
    return row(target_id, kind, sha) if sha is not None else None


@content_cache
@lru_cache(maxsize=1024)
def lesson_forms(content_id):
    """overlay.LessonForms of a word lesson (its title, spellings and reading), or None."""
    from ..learning_content import learning_item_view
    item = learning_item_view(content_id) if isinstance(content_id, str) else None
    if not isinstance(item, Mapping) or item.get("family") != "word":
        return None
    spellings = item.get("spellings")
    written = [item.get("title"), *(spellings if isinstance(spellings, (list, tuple)) else ())]
    reading = item.get("reading")
    return overlay.LessonForms(tuple(v for v in written if isinstance(v, str) and v.strip()),
                               reading if isinstance(reading, str) else "")


_NO_ROW = object()


def policy_for(question, *, row_value=_NO_ROW, normal=None):
    """overlay.AnswerPolicy of ``question``: its installed row (or ``row_value`` when the caller
    located it) and, for vocab_recall, its lesson's forms."""
    found = question_row(question) if row_value is _NO_ROW else row_value
    lesson = lesson_forms(question.get("target_id")) if question.get("kind") == overlay.RECALL_KIND else None
    return overlay.effective(question, found, lesson=lesson, normal=normal)


def _fold_normal(text):
    from ..learning_questions import _normal
    return overlay.fold_katakana(_normal(text))


def _readings_of(item):
    values = [item.get("reading"), *(item.get("accepted_readings") or ()), *(item.get("alternative_readings") or ())]
    return {_fold_normal(v) for v in values if isinstance(v, str) and v.strip()}


@content_cache
@lru_cache(maxsize=1024)
def word_readings(content_id):
    """The readings of one word lesson (its reading, accepted and dictionary readings), normalised with
    katakana folded, or an empty set."""
    from ..learning_content import learning_item_view
    item = learning_item_view(content_id) if isinstance(content_id, str) else None
    if not isinstance(item, Mapping) or item.get("family") != "word":
        return frozenset()
    return frozenset(_readings_of(item))


def near_miss_candidates(question, normal):
    """The kana answers a near miss is measured against: the question's (effective) accepted answers and
    the all-kana renderings of its reading forms (answer_policy.kana_answer), normalised with ``normal``."""
    from . import kana_answer
    accepted = question.get("accepted_answers", [question.get("answer")])
    values = [normal(x) for x in (accepted if isinstance(accepted, list) else []) if isinstance(x, str)]
    try:
        values += [normal(x) for x in kana_answer._reading_form_candidates(question)]
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        pass
    return [x for x in dict.fromkeys(values) if near_miss.is_kana(x)]


def graded_near_miss(question, value, normal):
    """Rule P2 for the judge: the near miss of a typed kana answer to a word question that is not strict.

    ``question``: the policy view the judge reads; ``value``: the answer normalised with ``normal``.
    None for grammar questions, strict ones and an answer that is a form of this same lesson the judge
    knows (its readings, the question's alternative answers). Another lesson's word one or two kana
    away IS a near miss: that confusion is judged wrong and shown.
    """
    if (question.get("family") != "word" or question.get("input_mode") != "text" or question.get("strict")
            or not near_miss.is_kana(value)):
        return None
    alternatives = question.get("alternative_answers")
    exempt = {normal(x) for x in (alternatives if isinstance(alternatives, list) else ()) if isinstance(x, str)}
    exempt |= word_readings(question.get("target_id"))
    return near_miss.find(value, near_miss_candidates(question, normal), exempt=exempt)


def shown_near_miss(question, answered_text):
    """Result pages: the near miss of a typed kana answer by P2's distance alone (no exemptions), or None.

    The caller shows it only under a stored WRONG verdict, whatever gave it (this rule, a strict row, a
    known-wrong row; grammar answers too). Display only, from the stored answer and the question snapshot
    when the page is drawn: nothing is stored and the stored verdict never changes.
    """
    if (not isinstance(question, Mapping) or question.get("input_mode") != "text"
            or not isinstance(answered_text, str) or not answered_text.strip()):
        return None
    from ..learning_questions import typed_answer_normal
    try:
        view = overlay.view(question, policy_for(question))
        normal = typed_answer_normal(view)
        return near_miss.find(normal(answered_text), near_miss_candidates(view, normal))
    except (ValueError, KeyError, TypeError, AttributeError, IndexError):
        return None


def known_wrong_feedback(question, answered_text) -> str:
    """Result pages: the feedback of the row's known-wrong answer the learner typed, or "".

    Display only, read when the page is drawn: the stored verdict and the stored question never change.
    """
    if not isinstance(question, Mapping) or not isinstance(answered_text, str) or not answered_text.strip():
        return ""
    found = question_row(question)
    if found is None or not found.get("known_wrong"):
        return ""
    from ..learning_questions import typed_answer_normal
    normal = typed_answer_normal(question)
    policy = overlay.effective(question, found)
    return overlay.row_feedback(policy, normal(answered_text), normal)
