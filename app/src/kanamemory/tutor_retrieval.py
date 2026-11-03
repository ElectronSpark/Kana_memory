"""Bounded literal lookup for an explicitly sent learner question.

This is not semantic search or a confidence score. Only titles, readings,
identifiers and Japanese expressions literally present in saved titles match.
No curriculum enrollment, external corpus search or mastery claim is made.
"""
from __future__ import annotations

import json
import re

MAX_MATCHES = 2
MAX_CANDIDATES = 64
_KANA = r'ぁ-ゖァ-ヶー'
_KANA_RUN = re.compile('[' + _KANA + ']+')
_IDS = re.compile(r'(?<![A-Za-z0-9_:-])[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z0-9_-]+){1,8}(?![A-Za-z0-9_:-])')
_QUOTED = re.compile(r'「([^「」\n]{1,160})」|『([^『』\n]{1,160})』|“([^“”\n]{1,160})”|"([^"\n]{1,160})"|‘([^‘’\n]{1,160})’')


def _spans(message, needle, *, single_quoted=False, quoted=()):
    if not needle:
        return []
    if len(needle) == 1 and single_quoted:
        return [(start, end) for text, start, end in quoted if text == needle]
    found = []
    for match in re.finditer(re.escape(needle), message):
        start, end = match.span()
        # A kana reading/form inside a longer kana sequence is not the same
        # lexical item. Quotes make short particles explicit without guessing.
        if (_KANA_RUN.fullmatch(needle) and
                ((start and _KANA_RUN.fullmatch(message[start - 1]))
                 or (end < len(message) and _KANA_RUN.fullmatch(message[end])))):
            continue
        found.append((start, end))
    return found


def _matches(message, row, quoted):
    candidates = []
    fields = [('id', row['content_id'], 1000), ('title', row['title'] or '', 600),
              ('reading', row['reading'] or '', 500)]
    # A form explicitly printed in a grammar heading (e.g. 名词句：です or
    # は：提示话题) is a literal alias. Never extract invented dictionary forms
    # from explanations, answers, meanings or the learner's mistake history.
    if row['family'] == 'grammar':
        for part in re.split(r'[：:（）()／/「」『』\s]+', row['title'] or ''):
            form = part.lstrip('～〜~')
            if form and _KANA_RUN.search(form) and len(form) <= 80:
                fields.append(('title_form', form, 400))
    for field, needle, rank in fields:
        if not isinstance(needle, str) or not needle or len(needle) > 256:
            continue
        for start, end in _spans(message, needle, single_quoted=True, quoted=quoted):
            if field == 'id' and ((start and re.fullmatch(r'[A-Za-z0-9_:-]', message[start - 1]))
                    or (end < len(message) and re.fullmatch(r'[A-Za-z0-9_:-]', message[end]))):
                continue
            explicit = any(text == needle and (a, b) == (start, end) for text, a, b in quoted)
            candidates.append((rank + (250 if explicit else 0), len(needle), -start,
                               field, needle, start, end))
    return max(candidates) if candidates else None


def retrieve_question_material(store, message, *, limit=MAX_MATCHES):
    """Return at most two saved facts and their literal source identities.

    Read only. SQL prefilters scalar title/reading/ID fields; only selected
    matches hydrate rich saved teaching. Merely enrolled or guided lessons and
    fully suspended content do not enter a general question's background.
    Independent failed attempts qualify as *studied*, never as mastered.
    """
    if (not isinstance(message, str) or len(message) > 2000
            or type(limit) is not int or not 1 <= limit <= MAX_MATCHES):
        raise ValueError('Invalid bounded tutor material lookup')
    message = message.strip()
    if not message:
        return []
    queries = store.queries
    if not queries.has_course_history():
        return []
    quoted = []
    for match in _QUOTED.finditer(message):
        group = next(index for index, value in enumerate(match.groups(), 1) if value is not None)
        quoted.append((match.group(group), *match.span(group)))
    terms = list(dict.fromkeys([text for text, _, _ in quoted]
                + _IDS.findall(message) + _KANA_RUN.findall(message)))[:8]
    # Short particles require an explicit quoted mention; otherwise a normal
    # sentence would retrieve every one-character particle it happens to use.
    terms = [term for term in terms if 1 < len(term) <= 160
             or any(term == text for text, _, _ in quoted)]
    rows = queries.tutor_material_candidates(message, terms, MAX_CANDIDATES)
    ranked = [(match, row['content_id']) for row in rows if (match := _matches(message, row, quoted))]
    ranked.sort(key=lambda pair: (pair[0][:3], pair[1]), reverse=True)
    selected, spans = [], []
    for match, identity in ranked:
        _, _, _, field, text, start, end = match
        if any(start >= a and end <= b and (start, end) != (a, b) for a, b in spans):
            continue
        spans.append((start, end))
        row = queries.course_content_json(identity)
        if row is None:
            continue
        from .ai_service import public_item
        from .material_review import reviewed_item
        fact = public_item(reviewed_item(json.loads(row[0])))
        selected.append({'target_id': identity, 'source': 'saved_learning_content',
                         'match_field': field, 'match_text': text, 'fact': fact})
        if len(selected) >= limit:
            break
    return selected
