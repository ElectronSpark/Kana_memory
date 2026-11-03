"""Record explicit feedback help without changing answers, queues or due dates."""
import json

from .feedback_bindings import candidates, intact


def origin_questions(context):
    practice = context.get('practice') or {}
    rows = []
    if isinstance(practice.get('question'), dict):
        if not intact(context, 'practice', practice):
            raise ValueError('原题快照不完整，请先回原题查看答案。')
        rows.append(practice['question'])
    for index, wrapper in enumerate(context.get('fragments', [])):
        data = wrapper.get('data', wrapper)
        q = data.get('question') or (data.get('entry') or {}).get('question')
        if isinstance(q, dict):
            if not intact(context, f'fragments/{index}/data', data):
                raise ValueError('原题快照不完整，请先回原题查看答案。')
            rows.append(q)
    page = (context.get('page') or {}).get('name', '')
    if (page.endswith('_question') or page in {'question', 'ink_question', 'learning_guide'}) and not rows:
        raise ValueError('原题暂不可定位，报告仍隐藏。')
    return rows


def mark_legacy_heads(store, questions):
    """Only an actually presented, unfinished head of the same kana/word."""
    targets = {(q['content_id'], q.get('script')) for q in questions if q.get('content_id')}
    changed = []
    with store._db:
        store._db.execute('BEGIN IMMEDIATE')
        for row in store._db.execute('SELECT * FROM session_checkpoint').fetchall():
            state = json.loads(row['state_json'])
            if not state or state.get('feedback_pending') or state.get('page') in {'session_wait', 'dependency_wait'}:
                continue
            q = store._session_snapshot(row['session_id'])[row['question_index']]
            if (q['content_id'], q.get('script')) not in targets: continue
            assisted = set(json.loads(row['assisted_indices_json'])) | {row['question_index']}
            state['answer_revealed'] = True
            store._db.execute('UPDATE session_checkpoint SET state_json=?,assisted_indices_json=? WHERE session_id=?',
                (json.dumps(state, ensure_ascii=False), json.dumps(sorted(assisted)), row['session_id']))
            changed.append((row['session_id'], row['question_index']))
    return changed


def expose(app, record, bindings):
    """The frozen report cannot supply a new target or a write address."""
    from .learning_questions import KIND_ABILITIES
    from .feedback_bindings import current_resource
    context = record['context']
    pool = {b['id']: b for b in candidates(context)}
    if any(pool.get(b['id']) != b for b in bindings):
        raise ValueError('报告片段与原反馈不一致，内容仍隐藏。')
    origins = origin_questions(context)  # also applies to note-only requests
    pairs = set()
    for q in origins:
        if q.get('target_id') and q.get('kind') in KIND_ABILITIES:
            pairs.add((q['target_id'], KIND_ABILITIES[q['kind']]))
    resources = app.feedback_analysis().resources
    for b in bindings:
        target, kind, field = b['target_id'], b['resource_kind'], b['field']
        if kind == 'question':
            q = next((q for q in origins if q.get('id') == b['resource_id']), None)
            if not q or q.get('target_id', '') != target: raise ValueError('原题归属需要确认。')
            continue
        current = current_resource(b, **resources)
        if current is None: raise ValueError('原内容暂不可定位，报告仍隐藏。')
        actual_target = current.get('target_id') or (current['id'] if kind == 'lesson' else '')
        if actual_target != target: raise ValueError('内容归属已变化，报告仍隐藏。')
        targets = [(target, current.get('family') or current.get('target', {}).get('family'))] if target else []
        # A reference explanation belongs only to that reference's target.
        if kind in {'example', 'lexical_example'}:
            targets = [(r['target_id'], r.get('target', {}).get('family'))
                       for r in app.example_store().references(b['resource_id'])]
        for cid, family in targets:
            if family is None:
                item = app.learning_store.get_item(cid)
                family = item.get('family') if item else None
            abilities = ('reading',) if field.endswith('reading') else ('understanding', 'application') if family == 'grammar' else ('understanding',)
            pairs.update((cid, a) for a in abilities)
    app.diagnostics().expose_visible(pairs)
    by_target = {}
    for cid, ability in pairs: by_target.setdefault(cid, set()).add(ability)
    # DiagnosticStore already persists both its own reservation and ordinary
    # course drafts. Do not route its reserved question through save_draft.
    app._mark_visible_tutor_help(set(by_target), abilities_by_target=by_target, sources={'ai', 'tutor'})
    keys = mark_legacy_heads(app.store, origins)
    if keys: app._assisted_attempts.update(keys)
    if (getattr(app, 'session_id', None), getattr(app, 'question_index', None)) in keys:
        app.answer_revealed = True
