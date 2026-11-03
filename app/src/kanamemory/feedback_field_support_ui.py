"""Semantic field reviews stay in the secondary correction workflow."""
from uuid import uuid4

from .feedback_field_support import FeedbackFieldSupportStore, CHECKS, category, empty_decision

STATES = {'待核': 'pending', '依据支持这项新稿': 'supported', '依据反对这项新稿': 'contradicted'}
ROLES = {'尚不能确定': 'unknown', '支持新稿': 'supports', '反对新稿': 'opposes', '仅提供背景': 'context', '不适用于本处': 'irrelevant'}
COMPARISONS = {'仍有未解决分歧': 'unresolved', '复核意见与依据相符': 'compatible', '分歧已由所列依据解决': 'resolved'}


def render_support(app, u, plan, group, box, active):
    service = FeedbackFieldSupportStore(app.feedback_analysis())
    panel, status = u.col(), u.label('', size=12, color=u.MUTED)
    def open_review(widget=None):
        if not active(): return
        try:
            expected = service.preview(plan['id'], group['id'])
            app._feedback_report_exposure(app.feedback_analysis().get(plan['manifest']['parent_id'])['request'])
            saved = service.latest(plan['id'], group['id'])
        except Exception as exc:
            status.text = str(exc) if isinstance(exc, ValueError) else '核据暂不可用，正文仍隐藏。'; return
        panel.clear(); controls = []; request_id = uuid4().hex
        previous = {r['binding_id']: r for r in saved['decisions']} if saved else {}
        scope = expected['receipt']['scope']; evidence = {r['evidence']['id']: r['evidence'] for r in scope['evidence']}
        from .feedback_analysis_ui import FIELD_LABELS
        panel.add(u.label('记录你如何依据资料核对新稿。此处只确认字段依据，采纳内容和题目判分另行检查。', size=12, color=u.MUTED))
        for c in scope['changes']:
            bid = c['binding']['id']
            if bid not in group['binding_ids']: continue
            old = previous.get(bid, empty_decision(c))
            pick = u.selection(items=list(STATES), value=next(k for k, v in STATES.items() if v == old['status']))
            panel.add(u.label(FIELD_LABELS.get(c['binding']['field'].split('.')[-1], '内容'), bold=True),
                      u.label('新稿：' + c['new_value'], size=14), pick)
            reason = u.toga.MultilineTextInput(placeholder='说明资料如何支持或反对新稿', style=u.Pack(height=80))
            reason.value = old['reason']; panel.add(reason)
            checks = {}
            for key, label in CHECKS[category(c['binding'])].items():
                control = u.toga.TextInput(placeholder=label + '：核对结论')
                control.value = old['checks'][key]; checks[key] = control
                panel.add(u.label(label, size=12, color=u.MUTED), control)
            sources = []; old_sources = {r['id']: r for r in old['evidence']}
            for index, eid in enumerate(c['evidence_ids']):
                source = old_sources[eid]; snapshot = evidence[eid]['snapshot']
                role = u.selection(items=list(ROLES), value=next(k for k, v in ROLES.items() if v == source['role']))
                note = u.toga.TextInput(placeholder='这份摘录对当前新稿意味着什么'); note.value = source['reason']
                panel.add(u.label(f'摘录 {index+1}', size=12, bold=True), u.label(snapshot['quote'], size=13), role, note)
                sources.append((eid, role, note))
            if c['model_answer'] is not None: panel.add(u.label('独立复核：' + c['model_answer'], size=13))
            comparison = u.selection(items=list(COMPARISONS), value=next(k for k, v in COMPARISONS.items() if v == old['comparison']['status']))
            explanation = u.toga.TextInput(placeholder='相符或解决分歧的具体理由'); explanation.value = old['comparison']['reason']
            panel.add(comparison, explanation)
            controls.append((bid, pick, reason, checks, sources, comparison, explanation))
        def save(widget=None):
            if not active(): return
            decisions = [{'binding_id': bid, 'status': STATES[p.value], 'reason': why.value or '',
                'checks': {k: c.value or '' for k, c in checks.items()},
                'evidence': [{'id': eid, 'role': ROLES[r.value], 'reason': n.value or ''} for eid, r, n in sources],
                'comparison': {'status': COMPARISONS[comparison.value], 'reason': explanation.value or ''}}
                for bid, p, why, checks, sources, comparison, explanation in controls]
            try:
                result = service.record(expected, decisions, request_id=request_id)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('feedback_support', 'save', exc)
                status.text = str(exc) if isinstance(exc, ValueError) else '核据未保存，可重试。'
                return
            try:
                count = sum(r['status'] == 'supported' for r in result['claims'])
                status.text = f'字段核据已保存 · {count}/{len(result["claims"])} 项有依据；内容和评分未改动。'
                panel._feedback_fragment = {'kind': 'feedback_field_support', 'receipt_id': result['id'], 'claims': result['claims']}
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('feedback_support', 'refresh', exc)
                status.text = '核据已保存，但显示未完成；请重新打开报告。'
        panel.add(u.button('保存字段核据', save, compact=True))
    box.add(u.button('核对新稿的事实依据', open_review, compact=True), status, panel)
