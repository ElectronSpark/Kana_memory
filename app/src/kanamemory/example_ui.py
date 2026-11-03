"""Shared examples are teaching material, never automatic quiz hints or ratings."""
from __future__ import annotations

import json
import weakref
from copy import deepcopy

from .navigation import page_view
from .example_highlight import overlap_regions
from .typography import configure_study_ranges, configure_text_role
from .lexical_example_ui import LexicalExampleUIMixin


def ui():
    from . import ui_components as app
    return app


class ExampleUIMixin(LexicalExampleUIMixin):
    def natural_translation_text(self, text, translation=''):
        from .natural_translations import natural_translation_for
        return natural_translation_for(text, translation)

    def translation_display_values(self, text, translation='', *, resource=None):
        # Deduplicate hints against both the immutable source and its display.
        from .natural_translations import natural_translation_note
        if resource and resource.get('personal_revision'): return (translation,)
        return (translation, self.natural_translation_text(text, translation),
                natural_translation_note(text, translation))

    def natural_translation_label(self, text, translation='', *, prefix='', resource=None, **style):
        from .natural_translations import natural_translation_note, natural_translation_pending
        u = ui()
        personal = (resource or {}).get('personal_revision')
        if (resource or {}).get('personal_withdrawal_id'):
            result = u.label(prefix + '译文待重新核对', **style)
            result._feedback_fragment = {'kind': 'personal_content_hold', 'resource_id': resource['id'],
                'withdrawal_id': resource['personal_withdrawal_id']}
            return result
        displayed = translation if personal else self.natural_translation_text(text, translation)
        note = natural_translation_note(text, translation) if displayed != translation else ''
        pending = natural_translation_pending(text, translation) if displayed == translation and not personal else None
        style.setdefault('wrap', False)
        style.setdefault('text_role', 'ui')
        if note or pending:
            # Keep the pure translation prominent. The old teaching detail is
            # a separate, naturally wrapping line, never an answer/audio input.
            main_style = {**style, 'margin_bottom': 2}
            result = u.col(u.label(prefix + displayed, **main_style),
                u.label(note or '原译·待核', size=12, color=u.MUTED, wrap=False, text_role='ui', margin_bottom=0),
                margin_bottom=style.get('margin_bottom', 8))
        else:
            result = u.label(prefix + displayed, **style)
        result._feedback_fragment = {'kind': 'natural_translation', 'source_text': text,
            'source_translation': translation, 'display_translation': displayed,
            'teaching_note': note, 'overlaid': displayed != translation, 'prefix': prefix,
            'status': 'pending' if pending else 'overlaid' if displayed != translation else 'original',
            'reason': pending['reason'] if pending else '', 'retained_original': displayed == translation}
        if personal:
            result._feedback_fragment.update(status='personal', personal_revision=personal, resource_id=resource['id'])
        return result

    def natural_translation_review_rows(self, text, translation=''):
        """Secondary source pages only; no review reasons in the main example."""
        from .natural_translations import natural_translation_pending
        pending = natural_translation_pending(text, translation)
        if not pending:
            return []
        u = ui()
        result = u.col(u.label('自然译文待核，暂用原译', size=13, color=u.MUTED),
                       u.label(pending['reason'], size=13, color=u.MUTED, wrap=False))
        result._feedback_fragment = {'kind': 'natural_translation_review', 'source_text': text,
            'source_translation': translation, **pending}
        return [result]

    def order_translation_rows(self, text, translation='', *, explicit=None, **presentation):
        from .order_translation_ui import order_translation_rows
        return order_translation_rows(self, text, translation, explicit=explicit, **presentation)

    def annotated_sentence(self, text, *, size=20, highlight_spans=(), links=(), on_select=None,
                           available_width=None, audio=False, audio_text=None, reading_override=None, reading_pending=False,
                           annotation_exclusions=()):
        """Teaching-only view; questions must use their separate support gate."""
        from .sentence_annotation_ui import build_sentence_block
        from .question_support import clean_prompt_preferences, PROMPT_DISPLAY_SETTING
        u = ui()
        slot = u.col()
        ref = weakref.ref(slot)
        corrected = reading_override is not None or reading_pending
        spoken = reading_override if corrected else audio_text if isinstance(audio_text, str) and audio_text.strip() else text
        def render():
            view = ref()
            if view is None:
                return
            width = available_width
            if width is None:
                try:
                    width = max(80, float(self.main_window.size.width) - 64)
                except (AttributeError, TypeError, ValueError):
                    width = 296
            # The speaker shares the first row with the sentence. Reserve its
            # fixed native width before wrapping furigana/highlighted segments.
            playable = audio and bool(text.strip()) and not reading_pending
            if playable:
                width = max(80, width - 58)
            prefs = clean_prompt_preferences(self.store.get_setting(PROMPT_DISPLAY_SETTING, {}))
            annotated = prefs if not corrected else {**prefs, 'furigana': False, 'number': False, 'line': False}
            block = build_sentence_block(self, text, size=size, highlight_spans=highlight_spans,
                links=links, on_select=on_select, available_width=width, preferences=annotated,
                annotation_exclusions=annotation_exclusions)
            view.clear()
            if playable:
                speaker = u.speaker_button(lambda w: self.speak_text(spoken), description='朗读例句：' + text)
                speaker._example_audio_text = spoken
                view.add(u.row(u.col(block, flex=1), speaker))
            else:
                view.add(block)
            if reading_pending:
                view.add(u.label('读法待核', size=12, color=u.MUTED))
            elif corrected and prefs['furigana'] and reading_override:
                # A sentence reading has no validated token alignment or pitch.
                view.add(u.label(reading_override, size=max(12, size*.7), text_role='study'))
            for name in ('_sentence_original_text', '_sentence_annotation_plan', '_example_highlights',
                         '_example_link_regions', '_example_select'):
                if hasattr(block, name):
                    setattr(view, name, getattr(block, name))
            fragment = dict(getattr(view, '_feedback_fragment', {}))
            fragment['sentence_annotation'] = deepcopy(block._feedback_fragment)
            if corrected:
                fragment['reading_revision'] = {'reading': reading_override, 'pending': reading_pending, 'alignment': 'sentence_only'}
            if playable:
                fragment['audio_text'] = spoken
            view._feedback_fragment = fragment
        slot._render_sentence = render
        self._sentence_views = [r for r in getattr(self, '_sentence_views', []) if r() is not None] + [ref]
        render()
        return slot

    def refresh_sentence_annotations(self):
        for ref in tuple(getattr(self, '_sentence_views', [])):
            view = ref()
            if view is not None:
                view._render_sentence()

    def sentence_source_rows(self, text):
        from .sentence_annotations import annotation_pending, annotation_sources
        u = ui()
        rows = []
        for pending in annotation_pending(text):
            status = '读法、调号待核' if pending['reading_pending'] else '调号待核'
            rows.append(u.label(pending['surface'] + ' · ' + status, size=14, bold=True))
            rows.append(u.label(pending['reason'], size=13, color=u.MUTED))
        for source in annotation_sources(text):
            rows.append(u.label(source['title'], size=14))
            if source.get('note'):
                rows.append(u.label(source['note'], size=13, color=u.MUTED))
            if source.get('url'):
                rows.append(u.label(source['url'], size=12, color=u.MUTED))
        return rows

    def example_targets_available(self, *, source_text=None, reference_ids=None):
        """Read-only dictionary/kana references do not become enrolled lessons."""
        from .ai_materials import ai_reference_targets
        from .content import ContentRepository
        items = {item['id']: item for item in self.learning_items_available()}
        repo = getattr(self, 'repo', None)
        if isinstance(repo, ContentRepository):
            for item in ai_reference_targets(repo, source_text=source_text, target_ids=reference_ids):
                items.setdefault(item['id'], item)
        return list(items.values())

    def open_example_target(self, item):
        if item.get('family') == 'kana':
            self.show_kana_detail(item.get('kana_id', item['id']))
        else:
            self.show_learning_detail(item)

    def example_store(self):
        from .example_store import ExampleStore, SETTING
        db = getattr(self.store, '_db', None)
        transaction = db is not None and db.in_transaction
        token = ((id(self.store), id(db), db.total_changes,
                  db.execute('PRAGMA data_version').fetchone()[0],
                  id(self.learning_store), id(getattr(self, 'repo', None)))
                 if db is not None and not transaction else None)
        # One screen can request this service for every visible example. Avoid
        # copying the entire course catalogue again while the database is
        # unchanged. Own writes, external commits and replaced stores all miss
        # this shortcut; progress itself is still read by each service query.
        if token is not None and token == getattr(self, '_example_access_token', None):
            return self._example_service
        value = self.store.get_setting(SETTING, None)
        reference_ids = {reference['target_id'] for reference in (value or {}).get('references', [])}
        items = self.example_targets_available(reference_ids=reference_ids)
        if transaction:
            # total_changes does not decrease on rollback. Never publish a
            # catalogue containing uncommitted lessons or custom references.
            self._example_access_token = None
            return ExampleStore(self.store, self.learning_store, items,
                lexical_review=getattr(getattr(self, 'repo', None), "lexical_review", None))
        # Restores and imports can replace the setting without changing the
        # StudyStore object. A content key prevents retaining a stale index.
        from .material_workflow import digest
        key = (id(self.store), tuple((x['id'], x.get('revision')) for x in items),
               json.dumps(value, ensure_ascii=False, sort_keys=True),
               digest(self.store.get_setting('material_usage_v1', {})),
               self.store.get_setting('personal_content_version_generation_v1', 0))
        if getattr(self, '_example_cache_key', None) != key:
            self._example_service = ExampleStore(self.store, self.learning_store, items,
                lexical_review=getattr(getattr(self, "repo", None), "lexical_review", None))
            self._example_cache_key = key
        self._example_access_token = token
        return self._example_service

    def example_text(self, example, reference=None, *, interactive=False):
        u = ui()
        from .personal_reading import presentation, annotation_exclusions
        all_refs = self.example_store().references(example['id'])
        refs = all_refs if interactive else []
        regions = overlap_regions(refs)
        # Only the example-level reading describes the complete sentence;
        # reference hints may describe one word and must never replace it.
        exclusions = annotation_exclusions(all_refs + ([reference] if reference else []))
        widget = self.annotated_sentence(example['text'], audio=not exclusions or bool(example.get('personal_reading')),
            audio_text=example.get('reading'), **presentation(example), annotation_exclusions=exclusions,
            highlight_spans=(reference or {}).get('spans', []), links=regions,
            on_select=lambda ids: self._render_example_detail(example['id'],
                (reference or {}).get('id'), choices=ids))
        widget._feedback_fragment = {**widget._feedback_fragment, 'kind': 'example_text', 'example_id': example.get('id'),
            'example_snapshot': deepcopy(example), 'reference': deepcopy(reference),
            'linked_references': deepcopy(refs), 'overlap_regions': deepcopy(regions),
            'interactive': interactive}
        return widget

    def example_hint_rows(self, hints, *, already_shown=()):
        u = ui()
        names = {'meaning': '词义', 'reading': '读法', 'rule': '用法'}
        if isinstance(already_shown, str):
            already_shown = (already_shown,)
        shown = {text.strip() for text in already_shown}
        surface = any(h.get('surface') for h in hints)
        rows = []
        for hint in hints:
            if (not hint.get('text') or hint['text'].strip() in shown
                    or surface and hint['kind'] == 'reading'):
                continue
            name = '基本形读法' if hint.get('scope') == 'lemma' else names.get(hint['kind'], '')
            widget = u.label(name + '：' + hint['text'], size=13, color=u.MUTED)
            widget._feedback_fragment = {'kind': 'example_hint', 'hint': deepcopy(hint)}
            rows.append(widget)
        return rows

    def example_background_hints(self, example_id, reference):
        """Only weak lexical support needed to read the current example.

        Competing rules and overlapping senses are chosen explicitly, never
        presented as if all interpretations were simultaneously applicable.
        """
        u = ui()
        refs = self.example_store().references(example_id, current_target_id=reference['target_id'])
        rows, seen = [], set()
        for ref in sorted(refs, key=lambda r: r['spans'][0]['start']):
            if (ref['target_id'] == reference['target_id'] or ref['target_id'] in seen
                    or ref['target'].get('family') != 'word'
                    or any(a['start'] < b['end'] and b['start'] < a['end']
                           for a in ref['spans'] for b in reference['spans'])):
                continue
            surface = ref.get('hint', {}).get('rule', '')
            support = [(surface if h['kind'] == 'reading' and ref.get('surface_hint') else
                        ('基本形读法：' + h['text'] if h.get('scope') == 'lemma' else h['text']))
                       for h in ref['hints'] if h['kind'] in {'meaning', 'reading'}]
            if support:
                quote = '／'.join(dict.fromkeys(s['quote'] for s in ref['spans']))
                support = [value for value in support if value != quote]
                if not support:
                    continue
                caption = quote + ' · ' + ' · '.join(dict.fromkeys(support))
                label = u.label(caption, size=12, color=u.MUTED, wrap=False)
                configure_study_ranges(label, [(0, len(quote))])
                label._feedback_fragment = {'kind': 'example_background_hint', 'example_id': example_id,
                    'reference': deepcopy(ref), 'displayed_support': deepcopy(support)}
                rows.append(label)
                seen.add(ref['target_id'])
                if len(rows) == 2:
                    break
        return rows

    def example_cards(self, item, *, limit=2):
        u = ui()
        entries = self.example_store().for_item(item['id'])
        if not entries:
            instruction = item.get('instruction', {})
            result = u.col(self.annotated_sentence(instruction.get('example', ''), size=20,
                audio=instruction.get('example_text_role', 'study') == 'study'),
                self.natural_translation_label(instruction.get('example', ''), instruction.get('translation', ''), color=u.MUTED),
                *self.order_translation_rows(instruction.get('example', ''), instruction.get('translation', ''),
                    explicit=instruction.get('order_translation')),
                u.button('添加例句', lambda w: self.show_example_editor(item['id']), compact=True))
            result._feedback_fragment = {'kind': 'example_fallback', 'target_id': item['id'],
                'content_revision': item.get('revision'), 'instruction': deepcopy(instruction)}
            return result
        children = []
        for index, example in enumerate(entries[:limit]):
            ref = example['reference']
            children.append(u.col(self.example_text(example, ref),
                self.natural_translation_label(example['text'], example.get('translation', ''), resource=example, size=14, color=u.MUTED),
                *self.order_translation_rows(example['text'], example.get('translation', ''),
                    explicit=example.get('order_translation')),
                *self.example_hint_rows(example['hints'], already_shown=self.translation_display_values(example['text'], example.get('translation', ''), resource=example)),
                *self.example_background_hints(example['id'], ref),
                u.button('例句详情', lambda w, eid=example['id'], rid=ref['id']:
                         self.show_example_detail(eid, reference_id=rid), compact=True),
                padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[index % 3]))
        children.append(u.row(
            u.button(f"全部例句（{len(entries)}）", lambda w: self.show_item_examples(item['id']), flex=1, compact=True),
            u.button('添加例句', lambda w: self.show_example_editor(item['id']), flex=1, compact=True)))
        result = u.col(*children)
        result._feedback_fragment = {'kind': 'example_collection', 'target_id': item['id'],
            'ordered_example_ids': [e['id'] for e in entries[:limit]], 'total': len(entries), 'offset': 0,
            'sort': 'learner_progress_weighted', 'limit': limit}
        return result

    @page_view(detail=True)
    def show_item_examples(self, target_id, *, offset=0):
        self._render_item_examples(target_id, offset)

    def _render_item_examples(self, target_id, offset=0):
        u = ui()
        self._item_examples_state = (target_id, offset)
        service = self.example_store()
        items = {x['id']: x for x in self.example_targets_available(reference_ids={target_id})}
        entries = service.for_item(target_id)
        children = [u.label(items[target_id]['title'], size=21, bold=True)]
        children[0]._feedback_fragment = {'kind': 'example_collection', 'target_id': target_id,
            'ordered_example_ids': [e['id'] for e in entries[offset:offset + 8]],
            'total': len(entries), 'offset': offset, 'sort': 'learner_progress_weighted', 'limit': 8}
        for index, entry in enumerate(entries[offset:offset + 8]):
            ref = entry['reference']
            children.append(u.col(self.example_text(entry, ref),
                self.natural_translation_label(entry['text'], entry.get('translation', ''), resource=entry, color=u.MUTED),
                *self.order_translation_rows(entry['text'], entry.get('translation', ''),
                    explicit=entry.get('order_translation')),
                *self.example_hint_rows(entry['hints'], already_shown=self.translation_display_values(entry['text'], entry.get('translation', ''), resource=entry)),
                *self.example_background_hints(entry['id'], ref),
                u.button('例句详情', lambda w, eid=entry['id'], rid=ref['id']:
                         self.show_example_detail(eid, reference_id=rid), compact=True),
                padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[index % 3]))
        if offset:
            children.append(u.button('上一页', lambda w: self._render_item_examples(target_id, offset - 8)))
        if offset + 8 < len(entries):
            children.append(u.button('下一页', lambda w: self._render_item_examples(target_id, offset + 8)))
        children.append(u.button('添加例句', lambda w: self.show_example_editor(target_id)))
        self.screen('item_examples', '例句', *children)

    @page_view(detail=True)
    def show_example_detail(self, example_id, *, reference_id=None):
        self._render_example_detail(example_id, reference_id)

    def _render_example_detail(self, example_id, reference_id=None, *, choices=None):
        u = ui()
        service = self.example_store()
        example = service.get(example_id)
        if not example:
            self.alert('这条例句暂不可用。')
            return
        refs = service.references(example_id)
        # Inline links also carry a single owner. Open that reference directly;
        # only actual ambiguity needs a chooser after filtering stale IDs.
        linked = [r for r in refs if r['id'] in (choices or ())]
        if len(linked) == 1:
            reference_id = linked[0]['id']
        choices = [r['id'] for r in linked] if len(linked) > 1 else None
        selected = next((r for r in refs if r['id'] == reference_id), refs[0] if refs else None)
        self._example_detail_state = (example_id, selected['id'] if selected else None)
        children = [self.example_text(example, selected, interactive=True)]
        children[0]._feedback_fragment.update(selected_reference_id=selected['id'] if selected else None,
            choice_reference_ids=deepcopy(choices), display_mode='overlap_choices' if choices else 'all_references')
        if example.get('personal_replacement_id'):
            replacement = example['personal_replacement_id']
            children.extend([u.label('此处保留原句记录，当前教学使用修订版。', size=12, color=u.MUTED),
                             u.button('查看修订后的例句', lambda w: self.show_example_detail(replacement), compact=True)])
        if example.get('translation'):
            children.append(self.natural_translation_label(example['text'], example['translation'], resource=example, size=16))
        children.extend(self.order_translation_rows(example['text'], example.get('translation', ''),
            explicit=example.get('order_translation')))
        if not example.get('personal_reading') and example.get('reading') and example['reading'] != example['text']:
            children.append(u.label(example['reading'], text_role='study'))
        if selected:
            hints = service.display_hints(selected, current_target_id=selected['target_id'])
            explanation = '' if selected.get('personal_withdrawal_id') else selected.get('explanation', '')
            shown = {*(value.strip() for value in self.translation_display_values(example['text'], example.get('translation', ''), resource=example)),
                     *(h['text'].strip() for h in hints)}
            # Old default word explanations join already visible focus, sense
            # and translation. Keep their immutable data, omit only this exact
            # redundancy in the view; contextual/custom explanations survive.
            focus = '／'.join(s['quote'] for s in selected['spans'])
            default_word = (focus + '：' + selected['target'].get('meaning', '')
                            + '。' + example.get('translation', ''))
            if selected['target'].get('family') == 'word' and explanation == default_word:
                explanation = ''
            else:
                explanation = '\n'.join(line for line in explanation.splitlines() if line.strip() not in shown)
            children.extend([u.label(selected['target']['title'], bold=True),
                             *self.example_hint_rows(hints, already_shown=self.translation_display_values(example['text'], example.get('translation', '')))])
            children.extend(self.example_background_hints(example_id, selected))
            if explanation:
                children.append(u.label(explanation, size=15))
                children[-1]._feedback_fragment = {'kind': 'example_explanation', 'example_id': example_id,
                    'reference_id': selected['id'], 'target_id': selected['target_id'], 'displayed_explanation': explanation}
            children.append(u.label(selected['status']['label'], size=13, color=u.MUTED))
            if not selected['target'].get('unavailable'):
                children.append(u.button('详细', lambda w: self.open_example_target(selected['target']), compact=True))
        if choices:
            children.append(u.label('这个片段关联多项内容，请选择：', size=14))
            candidates = linked
        else:
            candidates = refs
        candidates = [r for r in candidates if not selected or r['id'] != selected['id']]
        if candidates and not choices:
            children.append(u.label('关联学习项', size=16, bold=True))
        for ref in candidates:
            children.append(u.button(ref['target']['title'] + ' · ' + ref['status']['label'],
                lambda w, rid=ref['id']: self._render_example_detail(example_id, rid), multiline=True))
        # Explicit fragment buttons also work with screen readers and desktop
        # backends that cannot invoke Android inline spans.
        if not choices:
            for region in overlap_regions(refs):
                if len(region['reference_ids']) > 1:
                    text = example['text'][region['start']:region['end']]
                    children.append(u.button('「' + text + '」的关联用法',
                        lambda w, ids=region['reference_ids']: self._render_example_detail(example_id,
                            selected['id'] if selected else None, choices=ids), compact=True))
        else:
            children.append(u.button('显示全部关联', lambda w: self._render_example_detail(example_id, reference_id)))
        from .personal_content_notes_ui import append_notes
        append_notes(self, u, children, 'example', example, page='example_detail')
        if selected: append_notes(self, u, children, 'reference', selected, page='example_detail')
        children.extend([u.button('关联其他学习项', lambda w: self.show_example_target_picker(example_id)),
            u.button('资料', lambda w: self.show_example_sources(example_id), compact=True)])
        self.screen('example_detail', '例句', *children)

    @page_view(detail=True)
    def show_example_sources(self, example_id):
        u = ui()
        from .learning_content import learning_sources
        example = self.example_store().get(example_id)
        sources = {x['id']: x for x in learning_sources()}
        children = [u.label('例句资料', size=18), *self.sentence_source_rows(example['text']),
                    *self.natural_translation_review_rows(example['text'], example.get('translation', ''))]
        snapshots = example.get('lexical_provenance', [])
        snapshot_ids = {snapshot['review_id'] for snapshot in snapshots}
        local_review_ids = set()
        identities = dict.fromkeys([*example.get('source_ids', []), *example.get('lexical_review_ids', []),
                                    *[snapshot['review_id'] for snapshot in snapshots]])
        for key in identities:
            if key in example.get('lexical_review_ids', []) or key in snapshot_ids:
                repo = getattr(self, 'repo', None)
                local = repo.get_example(key) if repo and getattr(repo, 'lexical_review', None) else None
                if local and local.get('review'):
                    local_review_ids.add(key)
                    children.append(u.button('词库例句 · 审校与来源',
                        lambda w, eid=key: self.show_lexical_example_sources(eid), compact=True))
                elif key not in snapshot_ids:
                    children.append(u.label('词库例句来源：' + key, size=13, color=u.MUTED))
                continue
            source = sources.get(key, {'title': key})
            children.append(u.label(source.get('title', key)))
            if source.get('url'):
                children.append(u.label(source['url'], size=12, color=u.MUTED))
        for snapshot in snapshots:
            if snapshot['review_id'] not in local_review_ids:
                children.extend(self._lexical_provenance_children(snapshot,
                    shown_review_pair=(example['text'], example.get('translation', ''))))
        if not example.get('source_ids') and not snapshots:
            children.append(u.label('本机添加的例句。'))
        self.screen('example_sources', '资料', *children)

    @page_view(detail=True)
    def show_example_target_picker(self, example_id=None, *, query='', offset=0):
        self._render_example_target_picker(example_id, query, offset)

    def _render_example_target_picker(self, example_id=None, query='', offset=0):
        u = ui()
        self.example_target_query = u.toga.TextInput(value=query,
            placeholder='搜索要关联的词义、语法或读音规则', style=u.Pack(margin_bottom=6))
        search = lambda w: self._render_example_target_picker(example_id, self.example_target_query.value or '')
        self.example_target_query.on_confirm = search
        items = [x for x in self.learning_items_available() if not query.strip() or query.strip().casefold() in
                 ' '.join(str(x.get(k, '')) for k in ('title', 'meaning', 'reading')).casefold()]
        children = [self.example_target_query, u.button('搜索', search, compact=True)]
        for item in items[offset:offset + 12]:
            children.append(u.button(item['title'], lambda w, target=item['id']:
                                     self.show_example_editor(target, example_id=example_id), multiline=True))
        if offset:
            children.append(u.button('上一页', lambda w: self._render_example_target_picker(example_id, query, offset - 12)))
        if offset + 12 < len(items):
            children.append(u.button('下一页', lambda w: self._render_example_target_picker(example_id, query, offset + 12)))
        self.screen('example_picker', '选择学习项', *children)

    @page_view(detail=True)
    def show_example_editor(self, target_id, *, example_id=None):
        u = ui()
        item = next(x for x in self.learning_items_available() if x['id'] == target_id)
        example = self.example_store().get(example_id) if example_id else None
        self._example_editor_target = target_id
        self._example_editor_existing = example
        self._example_editor_fields = {}
        children = [u.label(item['title'], size=20, bold=True)]
        fields = [('text', '日文例句', example['text'] if example else ''),
            ('translation', '中文意思', example.get('translation', '') if example else ''),
            ('focus', '要强调的片段', ''),
            ('meaning', '简短词义提示', item.get('meaning', '') if item['family'] == 'word' else ''),
            ('reading', '当前词的读法提示', item.get('reading', '') if item['family'] == 'word' else ''),
            ('rule', '本例表达的用法提示', ''), ('explanation', '本例的解释', '')]
        for key, caption, value in fields:
            if example and key in {'text', 'translation'}:
                children.append(self.annotated_sentence(value, size=18) if key == 'text' else
                    self.natural_translation_label(example['text'], value, size=14))
                if key == 'translation':
                    children.extend(self.order_translation_rows(example['text'], value,
                        explicit=example.get('order_translation')))
                continue
            control = u.toga.TextInput(value=value, placeholder=caption, style=u.Pack(margin_bottom=8))
            if key in {'text', 'focus', 'reading'}:
                configure_text_role(control, 'study')
            self._example_editor_fields[key] = control
            children.extend([u.label(caption, size=13, color=u.MUTED), control])
        self._example_editor_preview = u.col()
        children.extend([u.button('预览强调和提示', self.preview_example_edit, primary=True), self._example_editor_preview])
        self.screen('example_editor', '添加例句' if not example else '添加关联', *children)

    def preview_example_edit(self, widget=None):
        u = ui()
        values = {key: control.value or '' for key, control in self._example_editor_fields.items()}
        existing = self._example_editor_existing
        if existing:
            values.update(text=existing['text'], translation=existing.get('translation', ''))
        text, focus = values.get('text', ''), values.get('focus', '')
        if not text.strip() or (not existing and not values.get('translation', '').strip()) or not focus.strip():
            self.alert('请填写日文、中文意思和要强调的片段。')
            return
        starts, cursor = [], 0
        while focus and (index := text.find(focus, cursor)) >= 0:
            starts.append(index)
            cursor = index + 1
        if not starts:
            self.alert('强调片段必须与例句中的文字完全一致。')
            return
        if not any(values.get(k, '').strip() for k in ('meaning', 'rule')):
            self.alert('请补充一条简短词义或用法提示。')
            return
        self._example_editor_preview.clear()
        occurrence = u.selection(items=[f'第 {n + 1} 处' for n in range(len(starts))], style=u.Pack(margin_bottom=8))
        self._example_editor_preview.add(u.label('选择要强调的位置：', size=14), occurrence)
        rendered = u.col()
        self._example_editor_preview.add(rendered)
        def draw(w=None):
            index = int(occurrence.value.split()[1]) - 1
            span = {'start': starts[index], 'end': starts[index] + len(focus), 'quote': focus}
            rendered.clear()
            example = {'id': existing['id'] if existing else '', 'text': text}
            rendered.add(self.example_text(example, {'spans': [span]}), self.natural_translation_label(text, values['translation']),
                *self.order_translation_rows(text, values['translation'],
                    explicit=(existing or {}).get('order_translation')),
                *self.example_hint_rows([{'kind': k, 'text': values.get(k, '')} for k in ('meaning', 'reading', 'rule')],
                                        already_shown=self.translation_display_values(text, values['translation'])))
        occurrence.on_change = draw
        draw()
        target_id = self._example_editor_target
        def save(w):
            # Saving exactly the preview avoids later form edits changing the
            # anchors behind the user's review; duplicate saves are idempotent.
            index = int(occurrence.value.split()[1]) - 1
            span = {'start': starts[index], 'end': starts[index] + len(focus), 'quote': focus}
            try:
                result = self.example_store().add_example(text, values['translation'], target_id, [span],
                    {k: values.get(k, '') for k in ('meaning', 'reading', 'rule')},
                    explanation=values.get('explanation', ''), example_id=existing['id'] if existing else None)
                self._example_cache_key = None
                reference = next(r for r in self.example_store().references(result['id'])
                    if r['target_id'] == target_id and r['spans'] == [span]
                    and r['hint'] == {k: values.get(k, '') for k in ('meaning', 'reading', 'rule')})
                self.show_example_detail(result['id'], reference_id=reference['id'])
            except ValueError as exc:
                self.alert('未能保存：' + str(exc))
        self._example_editor_preview.add(u.button('保存例句' if not existing else '保存关联', save, primary=True))

    @page_view()
    def show_example_library(self, widget=None, *, query='', offset=0):
        self._render_example_library(query, offset)

    def _render_example_library(self, query='', offset=0):
        u = ui()
        self._example_library_state = (query, offset)
        service = self.example_store()
        self.example_query = u.toga.TextInput(value=query, placeholder='搜索日文或中文例句', style=u.Pack(margin_bottom=6))
        search = lambda w: self._render_example_library(self.example_query.value or '')
        self.example_query.on_confirm = search
        page = service.examples_page(query, offset=offset, limit=10)
        ordered, total = page['examples'], page['total']
        children = [self.example_query, u.button('搜索', search, compact=True),
            u.row(u.button('添加例句', lambda w: self.show_example_target_picker(), flex=1),
                  u.button('导入／导出', self.show_example_exchange, flex=1)),
            u.button('解析日文并添加', lambda w: self.show_ai_form('analyze'), compact=True),
            u.label(f'共 {total} 条', size=13, color=u.MUTED)]
        children[-1]._feedback_fragment = {'kind': 'example_library', 'query': query, 'offset': offset,
            'ordered_example_ids': [e['id'] for e in ordered], 'total': total}
        for entry in ordered:
            card = u.col(self.example_text(entry), self.natural_translation_label(entry['text'], entry.get('translation', ''), resource=entry, size=14),
                *self.order_translation_rows(entry['text'], entry.get('translation', ''),
                    explicit=entry.get('order_translation')),
                u.button('例句详情', lambda w, eid=entry['id']: self.show_example_detail(eid), compact=True),
                padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[len(children) % 3])
            card._feedback_fragment = {'kind': 'example_library_entry', 'example_id': entry['id'],
                'example_snapshot': deepcopy(entry)}
            children.append(card)
        if offset:
            children.append(u.button('上一页', lambda w: self._render_example_library(query, offset - 10)))
        if offset + 10 < total:
            children.append(u.button('下一页', lambda w: self._render_example_library(query, offset + 10)))
        if getattr(getattr(self, 'repo', None), 'lexical_review', None):
            children.append(u.button('词库例句与审校', self.show_lexical_example_library, compact=True))
        self.screen('example_library', '例句库', *children)

    @page_view()
    def show_example_exchange(self, widget=None):
        u = ui()
        self._example_json_input = u.toga.MultilineTextInput(placeholder='粘贴例句 JSON 数据', style=u.Pack(height=220, margin_bottom=8))
        self._example_import_preview = u.col()
        self.screen('example_exchange', '导入／导出例句',
            u.button('导出我的例句与关联', self.export_examples),
            u.button('读取 JSON 文件', self.read_examples), self._example_json_input,
            u.button('检查并预览', self.preview_example_import, primary=True), self._example_import_preview)

    async def export_examples(self, widget=None):
        from .example_store import serialize_pack
        try:
            data = serialize_pack(self.example_store().export_pack())
            if await self.platform.export_backup(data, 'KanaMemory-examples-v1.json'):
                self.alert('已导出例句与关联。')
        except Exception as exc:
            self.alert('导出失败：' + str(exc))

    async def read_examples(self, widget=None):
        from .example_store import MAX_IMPORT_BYTES
        token = self._page_token
        try:
            data = await self.platform.import_backup()
            if data is not None and token == self._page_token:
                if len(data) > MAX_IMPORT_BYTES:
                    raise ValueError('单次导入文本不能超过 32 MiB。')
                self._example_json_input.value = data.decode('utf-8-sig')
                self.preview_example_import()
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('example_import', 'read', exc)
            if token == self._page_token:
                self.alert('读取失败：' + str(exc))

    def preview_example_import(self, widget=None):
        from .example_store import parse_pack, validate_pack
        u = ui()
        try:
            raw = self._example_json_input.value or ''
            candidate = parse_pack(raw, known_targets={x['id'] for x in self.learning_items_available()},
                                   require_status=True)
            # LLM/external proposals remain inert until this visible preview
            # is explicitly accepted; never interpret prose as instructions.
            if candidate['status'] == 'draft':
                candidate['status'] = 'reviewed'
                validate_pack(candidate)
        except (ValueError, TypeError, AttributeError) as exc:
            self.alert('例句格式有误：' + str(exc))
            return
        self._example_import_preview.clear()
        self._example_import_preview.add(u.label(f"{len(candidate['examples'])} 条例句 · {len(candidate['references'])} 个关联", bold=True))
        preview_rows = u.col()
        self._example_import_preview.add(preview_rows)
        def render(offset=0):
            preview_rows.clear()
            for ex in candidate['examples'][offset:offset + 5]:
                refs = [r for r in candidate['references'] if r['example_id'] == ex['id']]
                for ref in refs:
                    target = next(x for x in self.learning_items_available() if x['id'] == ref['target_id'])
                    preview_rows.add(u.label(target['title'], bold=True), self.example_text(ex, ref),
                        self.natural_translation_label(ex['text'], ex.get('translation', ''), color=u.MUTED),
                        *self.order_translation_rows(ex['text'], ex.get('translation', ''),
                            explicit=ex.get('order_translation')),
                        *self.example_hint_rows([{'kind': k, 'text': t} for k, t in ref['hint'].items()],
                                                already_shown=self.translation_display_values(ex['text'], ex.get('translation', ''))),
                        u.label(ref.get('explanation', ''), size=13))
            if offset:
                preview_rows.add(u.button('上一页', lambda w: render(offset - 5)))
            if offset + 5 < len(candidate['examples']):
                preview_rows.add(u.button('下一页', lambda w: render(offset + 5)))
        render()
        def save(w):
            try:
                self.example_store().import_pack(candidate)
                self._example_cache_key = None
                self.show_example_library()
            except ValueError as exc:
                self.alert('未能导入：' + str(exc))
        self._example_import_preview.add(u.button('确认添加', save, primary=True))
