"""Secondary reference workspace; sources stay out of learning screens."""
import json
from uuid import uuid4

from .navigation import page_view
from .material_reference import MaterialReferenceStore, claims, checks
from . import ui_components as u

LABELS = {'family': '类别', 'domain': '领域', 'title': '写法', 'reading': '读法', 'meaning': '词义',
    'instruction': '首次讲解', 'depends_on': '前置', 'question_templates': '配套题', 'accent': '调号',
    'text': '日文', 'translation': '译文', 'order_translation': '语序译文', 'target_id': '关联目标',
    'spans': '关联片段', 'hint': '理解提示', 'explanation': '关联说明'}
STATES = {'仍待核实': 'pending', '所列参考支持本项': 'supported', '所列参考与本项冲突': 'contradicted'}


class MaterialReferenceUIMixin:
    def save_material_reference_draft(self):
        save = getattr(self, '_material_reference_save', None)
        if getattr(self, 'page', '') in {'material_reference_source', 'material_reference_claim'} and callable(save):
            save()

    @page_view(detail=True, refresh=True)
    def show_material_senses(self, identity):
        from .material_senses import MaterialSenseStore
        service = MaterialSenseStore(self.store, repo=self.repo)
        try:
            rows = service.options(identity)
        except ValueError as exc:
            self.alert(str(exc)); return
        owner = object(); self._material_sense_owner = owner
        children = []
        status = u.label('', size=13, color=u.MUTED)
        def choose(row, source_id):
            if self.page != 'material_senses' or self._material_sense_owner is not owner: return
            try:
                service.choose(row, source_id)
            except ValueError as exc:
                status.text = str(exc); return
            self.show_material_task(identity)
        for row in rows:
            candidate = row['candidate']
            children.append(u.label(candidate['title'], size=22, bold=True, text_role='study'))
            children.append(u.label(candidate['meaning'], size=16))
            for index, option in enumerate(row['options'], 1):
                item, source = option['item'], option['source']
                selected = row['selected'] and row['selected']['source'] == source
                extra = ' / '.join(text for text in item.get('accepted_meanings', []) if text != item['meaning'])
                caption = f"{'✓ ' if selected else ''}{index}. {item['reading']} · {item['meaning']}"
                if extra: caption += '\n' + extra
                children.append(u.button(caption, lambda w, r=row, sid=source['id']: choose(r, sid), multiline=True))
            if not row['options']:
                children.append(u.label('原义项依据已变化，暂保留待核。', size=14, color=u.MUTED))
        self.screen('material_senses', '选择对应词义', *children, status,
            u.button('暂不选择', lambda w: self.show_material_task(identity), compact=True))

    @page_view(detail=True, refresh=True)
    def show_material_references(self, identity, *, offset=0):
        service = MaterialReferenceStore(self.store)
        job = self.material_workflow().get(identity)
        if not job or not job.get('proposal_id'):
            self.alert('材料暂不可用。'); return
        proposal = service.proposal(job['proposal_id']); latest = service.latest(proposal['id'])
        sources = service.sources(proposal['id'])
        children = [u.button('添加参考摘录', lambda w: self.show_material_reference_source(identity)),
            u.label(f'{len(sources)} 份参考 · 逐项核对后才用于学习', size=13, color=u.MUTED)]
        from .material_senses import MaterialSenseStore
        if MaterialSenseStore(self.store, repo=self.repo).options(identity):
            children.insert(0, u.button('选择对应词义', lambda w: self.show_material_senses(identity)))
        titles = {row['id']: row['title'] for row in proposal['items']}
        titles.update({row['id']: row['text'] for row in proposal['example_pack']['examples']})
        rows = claims(proposal)
        for row in rows[offset:offset+12]:
            old = latest.get(row['id']); status = old['status'] if old else 'pending'
            mark = {'supported': '✓', 'contradicted': '!', 'pending': '○'}[status]
            name = titles.get(row['object_id'], '例句关联')[:40]
            children.append(u.button(f"{mark} {name} · {LABELS.get(row['facet'], row['facet'])}",
                lambda w, cid=row['id']: self.show_material_reference_claim(identity, cid), multiline=True))
        if offset: children.append(u.button('上一页', lambda w: self.show_material_references(identity, offset=max(0,offset-12))))
        if offset+12 < len(rows): children.append(u.button('下一页', lambda w: self.show_material_references(identity, offset=offset+12)))
        children.append(u.button('回到材料', lambda w: self.show_material_task(identity)))
        self.screen('material_references', '参考核据', *children)

    @page_view(detail=True, refresh=True)
    def show_material_reference_source(self, identity):
        job = self.material_workflow().get(identity)
        if not job or not job.get('proposal_id'): self.alert('材料暂不可用。'); return
        key = 'material_reference_form_v1:' + job['proposal_id']
        old = self.store.get_setting(key, {})
        label = u.toga.TextInput(placeholder='词典、书名或参考资料名称', value=old.get('label',''))
        locator = u.toga.TextInput(placeholder='网址，或版本与页码', value=old.get('locator',''))
        body = u.toga.MultilineTextInput(placeholder='粘贴可核对的原文摘录', value=old.get('text',''), style=u.Pack(height=220))
        note = u.label('', size=13, color=u.MUTED); owner = object(); self._material_reference_owner = owner
        def active(): return self.page == 'material_reference_source' and self._material_reference_owner is owner
        def save_draft(widget=None):
            if not active(): return
            value = {'label': (label.value or '')[:400], 'locator': (locator.value or '')[:1600], 'text': (body.value or '')[:32000]}
            if self.store.get_setting(key, {}) != value: self.store.set_setting(key, value)
            note.text = '草稿已保存。'
        def save(widget=None):
            if not active(): return
            try:
                MaterialReferenceStore(self.store).add_source(job['proposal_id'], label.value or '', locator.value or '', body.value or '')
                self.store.set_setting(key, {})
            except ValueError as exc: note.text = str(exc); return
            self._material_reference_save = None
            self.show_material_references(identity)
        self._material_reference_save = save_draft
        for control in (label, locator, body): control.on_change = save_draft
        self.screen('material_reference_source', '添加参考', label, locator, body,
            u.label('参考与学习原文分开。尚未发送的 AI 复核会使用这些摘录；已完成的意见保留原版本。', size=12, color=u.MUTED),
            note, u.button('保存参考', save, primary=True), u.button('保存草稿', save_draft, compact=True),
            u.button('返回', lambda w: (save_draft(), self.show_material_references(identity))))

    @page_view(detail=True, refresh=True)
    def show_material_reference_claim(self, identity, claim_id):
        service = MaterialReferenceStore(self.store); job = self.material_workflow().get(identity)
        if not job or not job.get('proposal_id'): self.alert('材料暂不可用。'); return
        expected = service.preview(job['proposal_id'], claim_id); claim = expected['claim']
        draft_key = 'material_reference_claim_form_v1:' + claim_id
        draft = self.store.get_setting(draft_key, {})
        if draft.get('expected', {}).get('proposal_id') != job['proposal_id']: draft = {}
        sources = service.sources(job['proposal_id']); old = service.latest(job['proposal_id']).get(claim_id)
        labels = ['请选择参考'] + [f"{i+1}. {row['label']}" for i,row in enumerate(sources)]
        source = u.selection(items=labels, value=labels[0])
        excerpt = u.toga.MultilineTextInput(placeholder='粘贴所选参考中支持或反对本字段的连续原文', style=u.Pack(height=110))
        state = u.selection(items=list(STATES), value=next(k for k,v in STATES.items() if v == (old['status'] if old else 'pending')))
        reason = u.toga.MultilineTextInput(placeholder='说明具体依据与适用范围', style=u.Pack(height=80))
        comparison = u.toga.MultilineTextInput(placeholder='与独立复核是否相符；有分歧时说明如何依据参考解决', style=u.Pack(height=80))
        notes = {}; children = [u.label(LABELS.get(claim['facet'], claim['facet']), bold=True),
            u.label(claim['value'] if isinstance(claim['value'], str) else json.dumps(claim['value'], ensure_ascii=False, indent=2), size=14), source]
        reference_text = u.label('', size=13)
        def selected(widget=None):
            index = labels.index(source.value)-1
            reference_text.text = sources[index]['text'] if index >= 0 else ''
        source.on_change = selected
        children += [reference_text, excerpt, state, reason]
        for key, label in checks(claim).items():
            control = u.toga.TextInput(placeholder=label)
            if old: control.value = old['checks'][key]
            notes[key] = control; children += [u.label(label, size=12, color=u.MUTED), control]
        report = self.material_verification().get(identity)
        model = next((r for r in (report or {}).get('claims', []) if r['id'] == claim_id), None)
        if model and model.get('model_note'): children.append(u.label(model['model_note'], size=13))
        if old:
            reason.value = old['reason']; comparison.value = old['model_comparison']
            if old['citations']:
                citation = old['citations'][0]
                index = next((i for i,row in enumerate(sources) if row['id'] == citation['source_id']), None)
                if index is not None: source.value = labels[index+1]; excerpt.value = citation['quote']; selected()
        if draft:
            index = next((i for i,row in enumerate(sources) if row['id'] == draft.get('source_id')), None)
            source.value = labels[index+1] if index is not None else labels[0]
            excerpt.value = draft.get('excerpt', '')
            reason.value = draft.get('reason', ''); comparison.value = draft.get('comparison', '')
            state.value = next((k for k,v in STATES.items() if v == draft.get('status')), '仍待核实')
            for key, control in notes.items(): control.value = draft.get('checks', {}).get(key, '')
            selected()
        children.append(comparison)
        note = u.label('', size=13); children.append(note)
        if draft and draft.get('expected') != expected:
            note.text = '核对记录已更新。已恢复未提交草稿，请重新核对后保存。'
        owner = object(); self._material_reference_owner = owner; request_id = uuid4().hex
        def save_draft(widget=None):
            if self.page != 'material_reference_claim' or self._material_reference_owner is not owner: return
            index = labels.index(source.value)-1
            value = {'expected': expected, 'source_id': sources[index]['id'] if index >= 0 else None,
                'excerpt': (excerpt.value or '')[:32000], 'status': STATES[state.value],
                'reason': (reason.value or '')[:4000], 'comparison': (comparison.value or '')[:4000],
                'checks': {k:(c.value or '')[:4000] for k,c in notes.items()}}
            if self.store.get_setting(draft_key, {}) != value: self.store.set_setting(draft_key, value)
        def save(widget=None):
            if self.page != 'material_reference_claim' or self._material_reference_owner is not owner: return
            citations = []; index = labels.index(source.value)-1; quote = excerpt.value or ''
            if index >= 0 and quote:
                row = sources[index]; start = row['text'].find(quote)
                citations = [{'source_id': row['id'], 'start': start, 'end': start+len(quote), 'quote': quote}]
            try:
                service.record(expected, citations=citations, status=STATES[state.value], reason=reason.value or '',
                    check_notes={k:c.value or '' for k,c in notes.items()}, model_comparison=comparison.value or '', request_id=request_id)
            except ValueError as exc: note.text = str(exc); return
            self.store.set_setting(draft_key, {})
            self._material_reference_save = None
            self.show_material_references(identity)
        self._material_reference_save = save_draft
        for control in (excerpt, state, reason, comparison, *notes.values()): control.on_change = save_draft
        source.on_change = lambda w: (selected(w), save_draft(w))
        children += [u.button('保存本项核据', save, primary=True), u.button('返回', lambda w: (save_draft(), self.show_material_references(identity)))]
        self.screen('material_reference_claim', '逐项核对', *children)
