"""Optional personal notes are distinct from teaching and never a prompt hint."""
from copy import deepcopy

from .personal_content_notes import PersonalContentNoteStore

NOTE_TITLE = '个人注释 · 待核'


def render_actions(app, u, plan, group, box, active):
    service = PersonalContentNoteStore(app.store, app.feedback_analysis())
    controls = u.col(); status = u.label('', size=12, color=u.MUTED)
    def refresh():
        controls.clear()
        receipt = service.latest('adopt', plan['id'], group['id'])
        if receipt:
            current = service.active(receipt)
            controls.add(u.label('个人注释已保留 · 待核' if current else '个人注释已撤下或被新版替代', size=12))
            if current: controls.add(u.button('撤下这一组个人注释', lambda w: apply('withdraw'), compact=True))
        elif group['status'] == 'personal_note_plan':
            controls.add(u.button('保留为待核个人注释', lambda w: apply('adopt'), compact=True))
        controls.add(status)
    def apply(action):
        if not active(): return
        try: service.commit(service.prepare(plan['id'], group['id'], action=action))
        except Exception as exc:
            status.text = str(exc) if isinstance(exc, ValueError) else '未保存，原内容和进度未改动。'
            return
        status.text = '仅在详情或答后展开查看；不改变教案和判题。'
        try: service.sync(app.feedback_store())
        except Exception: status.text = '已保存，反馈回执待同步。'
        refresh()
    refresh(); box.add(controls)


def append_notes(app, u, children, kind, resource, *, page):
    service = PersonalContentNoteStore(app.store)
    selected = service.for_resource(kind, resource)
    if selected is None: return
    content = u.col()
    opened, exposed = [False], [False]
    def set_opened(value):
        opened[0] = value
        trigger.text = NOTE_TITLE + (' ▾' if value else ' ▸')
    def forget_shown():
        content.clear()
        if hasattr(content, '_feedback_fragment'): delattr(content, '_feedback_fragment')
    def say(text):
        # An empty status label would still reserve a line under the button.
        forget_shown(); content.add(u.label(text, size=12, color=u.MUTED))
    def reveal(widget=None):
        if app.page != page: return
        # Never use a stale page closure to reveal another question's notes.
        if kind == 'question' and getattr(app, '_learning_question', {}).get('id') != resource['id']: return
        if opened[0]:
            forget_shown(); set_opened(False); return
        from .feedback_bindings import current_resource
        binding = {'version': 1, 'resource_kind': kind, 'resource_id': resource['id'], 'example_id': resource.get('example_id', '')}
        current = current_resource(binding, **app.feedback_analysis().resources)
        fresh = service.for_resource(kind, current) if current else None
        if fresh is None or fresh != selected:
            say('注释或原内容已变化，请重新打开。'); return
        # Reopening text already shown on this page is not a new viewing.
        if not exposed[0]:
            try: _expose(app, kind, current)
            except Exception:
                say('显示记录暂未保存，注释仍隐藏。'); return
            exposed[0] = True
        forget_shown(); set_opened(True)
        from .feedback_analysis_ui import FIELD_LABELS
        content._feedback_fragment = {'kind': 'personal_content_note',
            'personal_revision': selected['event']['id'], 'canonical_owner_kind': kind,
            'canonical_owner_id': resource['id'], 'displayed_entries': deepcopy(selected['entries']),
            'qualification': 'personal_note_unverified'}
        for row in selected['entries']:
            content.add(u.label(FIELD_LABELS.get(row['field'].split('.')[-1], '说明'), size=12, color=u.MUTED),
                        u.label(row['text'], size=15))
    trigger = u.button(NOTE_TITLE + ' ▸', reveal, compact=True)
    section = u.col(trigger, content)
    def reset():
        forget_shown(); set_opened(False); exposed[0] = False
    section._personal_note_reset = reset
    children.append(section)


def reset_cached_notes(widget):
    """Cached navigation must not retain text withdrawn on another page."""
    callback = getattr(widget, '_personal_note_reset', None)
    if callback:
        callback(); return
    for child in getattr(widget, 'children', ()) or (): reset_cached_notes(child)
    content = getattr(widget, 'content', None)
    if content is not None: reset_cached_notes(content)


def _expose(app, kind, resource):
    """Record actual viewing for currently reserved abilities, not new scores."""
    targets = set(); items = {}
    if kind == 'lesson': targets.add(resource['id'])
    elif kind in {'reference', 'question'}: targets.add(resource.get('target_id'))
    else:
        from .example_store import example_id_for
        identity = example_id_for(resource['text']) if kind == 'lexical_example' else resource['id']
        targets.update(r['target_id'] for r in app.example_store().references(identity))
        if kind == 'lexical_example':
            from .learning_content import learning_items, learning_item_from_word
            links = [r for r in app.repo.example_links(resource['id']) if r.get('active') is not False and r.get('status') != 'rejected']
            # Include exact dictionary senses even before enrollment (A checks),
            # plus authored/enrolled targets with explicit word/sense bindings.
            for wid in {r['word_id'] for r in links}:
                word = app.repo.get_word(wid)
                if word:
                    for index, _ in enumerate(word.get('senses', (word.get('payload') or {}).get('senses', []))):
                        item = learning_item_from_word(word, index, enrich=False); items[item['id']] = item
            items.update((i['id'], i) for i in learning_items())
            items.update((i['id'], i) for i in app.learning_store.list_items())
            for item in items.values():
                if any(item.get('word_id') == r['word_id'] and (not r.get('sense_id') or item.get('sense_id') == r['sense_id']) for r in links):
                    targets.add(item['id'])
    targets.discard(None)
    pairs = set()
    from .learning_content import get_learning_item
    for identity in targets:
        item = items.get(identity) or app.learning_store.get_item(identity) or get_learning_item(identity)
        if item: pairs.update((identity, ability) for ability in item['abilities'])
    app.diagnostics().expose_visible(pairs)
    by_target = {}
    for identity, ability in pairs: by_target.setdefault(identity, set()).add(ability)
    app._mark_visible_tutor_help(set(by_target), abilities_by_target=by_target, sources={'ai', 'tutor'})
