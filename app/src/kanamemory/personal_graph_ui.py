"""Explicit relationship review within feedback maintenance, not lesson chrome."""
from copy import deepcopy

from .personal_graph import replacement_inputs

ACTIONS = {'保留待核': 'pending', '核对后迁到新句': 'reanchor', '撤下关联': 'retire'}


def render_replacements(app, u, service, plan, group, box):
    """Return a read function; editing controls never mutates a learning queue."""
    if not any(o['verb'] == 'replace_example' for o in group['operations']): return lambda: {}
    from .feedback_impact import PREFIX
    impact = app.store.get_setting(PREFIX + plan['impact_id'])
    inputs = replacement_inputs(group, impact['report']['graph_review']['snapshot'])
    controls = {}
    box.add(u.label('替换原句后，逐条确认关联。无法确定的关联留待核对。', size=13))
    for op in group['operations']:
        if op['id'] not in inputs: continue
        details = inputs[op['id']]
        box.add(u.label(op['after'], size=18, bold=True))
        source = next(r['snapshot'] for r in plan['manifest']['resources']
                      if (r['kind'], r['id']) == (op['owner']['kind'], op['owner']['id']))
        field = 'chinese_translation' if op['owner']['kind'] == 'lexical_example' else 'translation'
        translation = next((o['after'] for o in group['operations']
            if o['owner'] == op['owner'] and o['field'] == field), source.get(field, ''))
        box.add(u.label(translation, size=14), u.label('确认此译文仍表达新句；需修改时回到字段修订。', size=12, color=u.MUTED))
        why_translation = u.toga.TextInput(placeholder='译文核对结论与依据')
        translation_evidence = u.toga.TextInput(placeholder='依据编号，如 1,2')
        box.add(why_translation, translation_evidence)
        records = {'references': [], 'lexical_links': []}
        for kind in records:
            for key, base in details[kind].items():
                label = base['target_id'] if kind == 'references' else base['word_id'] + ' · ' + (base.get('sense_id') or '词条')
                pick = u.selection(items=list(ACTIONS), value='保留待核')
                span = u.toga.TextInput(placeholder='新句中的片段：起点:终点；多个用逗号分隔')
                reason = u.toga.TextInput(placeholder='新句为何仍体现此义项／规则，或为何撤下／待核')
                evidence = u.toga.TextInput(placeholder='依据编号，如 1,2（迁入时必填）')
                box.add(u.label(label, size=13, bold=True), pick, span, reason, evidence)
                hints, explanation, reading_scope = {}, None, None
                if kind == 'references':
                    for name, title in [('meaning', '词义提示'), ('reading', '读音提示'), ('rule', '规则提示')]:
                        control = u.toga.TextInput(placeholder=title); control.value = base.get('hint', {}).get(name, '')
                        hints[name] = control; box.add(control)
                    explanation = u.toga.TextInput(placeholder='此处用法说明'); explanation.value = base.get('explanation', '')
                    reading_scope = u.selection(items=['无读音提示', '词典形读音', '片段实际读音'],
                        value={None: '无读音提示', 'lemma': '词典形读音', 'span': '片段实际读音'}[base.get('reading_scope')])
                    box.add(explanation, reading_scope)
                records[kind].append((key, pick, span, reason, evidence, hints, explanation, reading_scope))
        controls[op['id']] = (op, records, why_translation, translation_evidence)
    sources = u.col(); box.add(sources)

    def evidence_list():
        support = service.supports.latest(plan['id'], group['id'])
        if not support: raise ValueError('请先保存这一组字段核据。')
        mapping = {c['binding_id']: c['supporting_evidence_ids'] for c in support['claims']}
        from .feedback_field_review import PREFIX as REVIEW
        receipt = app.store.get_setting(REVIEW + plan['manifest']['field_review_id'])
        evidence = {r['evidence']['id']: r['evidence'] for r in receipt['scope']['evidence']}
        sources.clear()
        for op, *_ in controls.values():
            for index, eid in enumerate(mapping[op['binding_id']]):
                sources.add(u.label(f'新句 {op["replacement_id"][-6:]} · 依据 {index+1}', size=12, bold=True),
                            u.label(evidence[eid]['snapshot']['quote'], size=13))
        return mapping

    status = u.label('', size=12, color=u.MUTED)
    def show_sources(widget=None):
        try: evidence_list(); status.text = '片段位置按字符计数，从 0 开始，终点不含。'
        except ValueError as exc: status.text = str(exc)
    box.add(u.button('查看此新句的核对依据', show_sources, compact=True), status)

    def selected(control, available):
        try:
            numbers = [int(v.strip()) for v in (control.value or '').replace('，', ',').split(',') if v.strip()]
            if len(set(numbers)) != len(numbers) or any(n < 1 or n > len(available) for n in numbers): raise ValueError()
            return [available[n-1] for n in numbers]
        except (ValueError, IndexError) as exc: raise ValueError('依据编号不正确，请查看当前新句的核对依据。') from exc

    def read():
        mapping = evidence_list(); result = {}
        for identity, (op, records, translation_note, translation_ids) in controls.items():
            available = mapping[op['binding_id']]
            current = {'references': {}, 'lexical_links': {}, 'translation_review': {
                'reason': translation_note.value or '', 'evidence_ids': selected(translation_ids, available)}}
            for kind, rows in records.items():
                for key, pick, positions, reason, evidence, hints, explanation, scope in rows:
                    spans = []
                    if ACTIONS[pick.value] == 'reanchor':
                        try:
                            for part in (positions.value or '').replace('，', ',').split(','):
                                start, end = [int(v.strip()) for v in part.split(':')]
                                spans.append({'start': start, 'end': end, 'quote': op['after'][start:end]})
                        except ValueError as exc: raise ValueError('请填写新句中的明确片段位置。') from exc
                    decision = {'action': ACTIONS[pick.value], 'spans': spans, 'reason': reason.value or '',
                                'evidence_ids': selected(evidence, available)}
                    if kind == 'references': decision.update(hint={k: c.value for k, c in hints.items() if c.value},
                        explanation=explanation.value or '',
                        reading_scope={'无读音提示': None, '词典形读音': 'lemma', '片段实际读音': 'span'}[scope.value])
                    current[kind][key] = decision
            result[identity] = current
        return deepcopy(result)
    return read
