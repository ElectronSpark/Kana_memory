"""The installed answer overlay row of a question's own template, and the lesson forms of rule T8.

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
from . import overlay

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
