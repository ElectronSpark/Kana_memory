"""User-attributed field review, opened deliberately inside feedback reports."""
from copy import deepcopy
from uuid import uuid4

from .feedback_field_review import FeedbackFieldReviewStore
from .feedback_sources import SCOPE
from .diagnostic_events import record_error

CHOICES = {'待核': 'pending', '不采用': 'reject', '仅作个人待核注释': 'display_note', '依据支持（已人工核对）': 'supported_by_user'}
SCOPES = dict(zip(SCOPE, ('词形', '义项或用法', '时代', '地区', '语域')))
RELATIONSHIPS = {'独立性未确定': 'unknown', '与原内容同源': 'same_lineage', '已核对来源相互独立': 'independent_user_asserted'}


def render_field_review(app, u, phase, source, box):
    service = FeedbackFieldReviewStore(app.feedback_analysis()); view = app._feedback_analysis_view
    panel, status = u.col(), u.label('', size=12, color=u.MUTED)
    def active(): return app.page == 'feedback_analysis' and app._feedback_analysis_view is view

    def open_review(widget=None):
        if not active(): return
        try:
            expected = service.preview(phase, source['id']); scope = expected['scope']
            parent = app.feedback_analysis().get(scope['parent_id'])
            app._feedback_report_exposure(parent['request'])
        except ValueError as exc:
            status.text = str(exc)
            panel.clear()
            # A successful adoption makes the original proposal stale. Its
            # saved plan must remain reachable for inspection and withdrawal.
            from .content_correction_plan_ui import render_plan
            try:
                render_plan(app, u, phase, source, panel, history_only=True)
            except Exception:
                status.text += ' 已保存计划暂不可用，请稍后重试。'
            return
        except Exception: status.text = '核对范围暂不可用，内容仍隐藏。'; return
        previous = service.latest(phase, source['id'])
        saved = {r['binding_id']: r for r in previous['decisions']} if previous and previous['scope'] == scope else {}
        controls, request_id = [], uuid4().hex
        panel.clear()
        caption = u.label('保存你的核对决定；不会直接修改内容或学习记录。相互依赖的字段作为一组保留。', size=12, color=u.MUTED)
        caption._feedback_fragment = {'kind': 'feedback_field_review', 'scope': deepcopy(scope), 'receipt_id': previous['id'] if previous else None}
        panel.add(caption)
        from .feedback_analysis_ui import FIELD_LABELS
        for index, change in enumerate(scope['changes']):
            bid = change['binding']['id']; old = saved.get(bid, {})
            label = FIELD_LABELS.get(change['binding']['field'].split('.')[-1], '内容')
            pick = u.selection(items=list(CHOICES), value=next(k for k, v in CHOICES.items() if v == old.get('decision', 'pending')))
            reason = u.toga.MultilineTextInput(placeholder='核对理由：摘录如何支持或反对这项修改；有分歧时说明原因', style=u.Pack(height=90, margin_bottom=6))
            reason.value = old.get('reason', '')
            detail = u.col(); evidence = []
            old_evidence = {r['evidence_id']: r for r in old.get('evidence_reviews', [])}
            checks = {k: u.switch('已核对' + SCOPES[k], value=old.get('scope_checked', {}).get(k, False)) for k in SCOPE}
            compare = u.switch('已比较原文、新稿和复核意见', value=old.get('comparison_checked', False))
            detail.add(u.label('原文：' + change['binding']['value'], size=13), u.label('建议：' + change['new_value'], size=14))
            if change['model_answer'] is not None: detail.add(u.label('独立意见：' + change['model_answer'], size=13))
            if change['model_note']: detail.add(u.label(change['model_note'], size=12, color=u.MUTED))
            for number, row in enumerate(scope['evidence']):
                e = row['evidence']
                if e['id'] not in change['evidence_ids']: continue
                selected = u.switch(f'使用摘录 {number + 1}', value=e['id'] in old.get('evidence_ids', []))
                data = e['snapshot']
                evaluation = old_evidence.get(e['id'], {})
                scope_inputs = {k: u.toga.TextInput(placeholder=SCOPES[k] + '的适用范围', style=u.Pack(margin_bottom=4)) for k in SCOPE}
                for k, control in scope_inputs.items(): control.value = evaluation.get('scope', data['scope']).get(k, '')
                relationship = u.selection(items=list(RELATIONSHIPS), value=next(k for k, v in RELATIONSHIPS.items() if v == evaluation.get('relationship', 'unknown')))
                independence = u.toga.TextInput(placeholder='本次核对来源关系的理由', style=u.Pack(margin_bottom=6))
                independence.value = evaluation.get('reason', '')
                evidence.append((e['id'], selected, scope_inputs, relationship, independence))
                detail.add(selected, u.label(data['quote'], size=13), u.label(data['url'] or '用户粘贴资料', size=11, color=u.MUTED),
                    u.label(' · '.join(SCOPES[k] + '：' + (data['scope'][k] or '未确定') for k in SCOPE), size=12),
                    u.label('来源独立性：' + (row['relationship_reason'] if row['relationship'] == 'independent_user_asserted' else
                        '同源' if row['relationship'] == 'same_lineage' else '未确定'), size=12, color=u.MUTED))
                detail.add(u.label('本次人工核对（原资料记录保留）', size=12, color=u.MUTED), *scope_inputs.values(), relationship, independence)
            if not evidence: detail.add(u.label('尚无该字段的外部摘录；可保留待核或个人注释。', size=12, color=u.MUTED))
            detail.add(*checks.values(), compare, reason)
            holder = u.col(); opened = [False]
            def expand(widget=None, holder=holder, detail=detail, opened=opened):
                if not active() or opened[0]: return
                holder.add(detail); opened[0] = True
            panel.add(u.label(f'{index + 1} · {label}', size=14, bold=True), pick,
                u.button('原文、新稿与依据', expand, compact=True), holder)
            controls.append((bid, pick, reason, evidence, checks, compare))

        def save(widget=None):
            if not active(): return
            rows = [{'binding_id': bid, 'decision': CHOICES[pick.value], 'reason': reason.value or '',
                'evidence_ids': [eid for eid, control, _, _, _ in evidence if control.value],
                'evidence_reviews': [{'evidence_id': eid, 'scope': {k: v.value or '' for k, v in inputs.items()},
                    'relationship': RELATIONSHIPS[rel.value], 'reason': reason.value or ''}
                    for eid, control, inputs, rel, reason in evidence if control.value],
                'scope_checked': {k: control.value for k, control in checks.items()}, 'comparison_checked': compare.value}
                for bid, pick, reason, evidence, checks, compare in controls]
            try:
                receipt = service.record(expected, rows, request_id=request_id)
            except ValueError as exc: status.text = str(exc); return
            except Exception as exc:
                record_error('feedback_field', 'save', exc)
                status.text = '核对决定未保存，可重试。'
                return
            pending = sum(g['status'] == 'pending' for g in receipt['groups'])
            status.text = f'核对决定已保存。{pending} 组仍待核；内容和学习记录未改动。'
            try:
                open_review()
            except Exception as exc:
                record_error('feedback_field', 'refresh', exc)
                status.text = '核对决定已保存，但界面暂未刷新；请返回后重新打开。'
        panel.add(u.button('保存核对决定', save, compact=True))
        if previous and previous['scope'] == scope:
            from .content_correction_plan_ui import render_plan
            render_plan(app, u, phase, source, panel)
    box.add(u.button('核对字段与依据', open_review, compact=True), status, panel)
