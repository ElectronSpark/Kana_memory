"""Prepare whole domain groups after field review, with explicit disclosure."""
import asyncio

from .content_correction_plan import ContentCorrectionPlanStore, LABELS, REQUIREMENTS
from .feedback_bindings import field_value
from .feedback_impact import FeedbackImpactStore, calculate

IDENTITIES = {'尚不能确定': 'unresolved', '仍为同一义项': 'same_sense', '实际是不同义项': 'different_sense'}


def render_plan(app, u, phase, source, box, *, history_only=False):
    service = ContentCorrectionPlanStore(app.feedback_analysis()); view = app._feedback_analysis_view
    status, panel = u.label('', size=12, color=u.MUTED), u.col()
    def active(): return app.page == 'feedback_analysis' and app._feedback_analysis_view is view
    receipt = service.fields.latest(phase, source['id'])
    saved = service.latest(phase, source['id'])
    if history_only and not saved or not history_only and not receipt: return
    identities = {}
    for c in ([] if history_only else receipt['scope']['changes']):
        b = c['binding']
        if b['resource_kind'] != 'lesson' or b['field'] not in {'title', 'meaning'}: continue
        pick = u.selection(items=list(IDENTITIES), value='尚不能确定')
        reason = u.toga.TextInput(placeholder='判断理由（不确定时可留空）')
        identities[b['id']] = (pick, reason)
        box.add(u.label('本次词形或含义修改是否仍指同一义项？', size=13), pick, reason)

    def show(saved):
        if not active(): return
        try:
            parent = app.feedback_analysis().get(saved['manifest']['parent_id'])
            app._feedback_report_exposure(parent['request'])
        except Exception: status.text = '显示记录暂未保存，计划仍隐藏。'; return
        panel.clear(); owners = {(r['kind'], r['id']): r['snapshot'] for r in saved['manifest']['resources']}
        panel.add(u.label(f"修改计划已保存 · {len(saved['groups'])} 组", size=13, bold=True))
        for index, group in enumerate(saved['groups']):
            label = {'pending': '仍待核对', 'rejected': '不采用', 'personal_note_plan': '待核个人注释', 'content_review_plan': '内容修订待验证'}[group['status']]
            detail = u.col(); opened = [False]
            def expand(widget=None, g=group, target=detail, state=opened):
                if not active() or state[0]: return
                state[0] = True
                for op in g['operations']:
                    before = field_value(owners[(op['owner']['kind'], op['owner']['id'])], op['field'])
                    target.add(u.label(LABELS[op['verb']], size=13, bold=True),
                        u.label('原：' + before, size=13), u.label('新：' + op['after'], size=14))
                    if op['replacement_id']:
                        target.add(u.label(f"新建例句身份；保留旧句。需重核 {len(op['reference_checks'])} 条共享引用、{len(op['lexical_link_checks'])} 条词库关联。", size=12))
                if not history_only:
                    for requirement in g['requirements']: target.add(u.label(REQUIREMENTS[requirement], size=12, color=u.MUTED))
                    from .personal_content_notes_ui import render_actions
                    render_actions(app, u, saved, g, target, active)
                    from .feedback_field_support_ui import render_support
                    render_support(app, u, saved, g, target, active)
                from .personal_content_versions_ui import render_adoption
                render_adoption(app, u, saved, g, target, active, history_only=history_only)
            panel.add(u.button(f"第 {index+1} 组 · {label} · {len(group['operations'])} 项修改", expand, compact=True), detail)
        panel.add(u.label('保留原作答、草稿和正式到期；计划不产生新成绩。', size=12, color=u.MUTED))

    def start(widget=None):
        if history_only or not active() or view.get('correction_plan_busy'): return
        choices = {bid: {'choice': IDENTITIES[p.value], 'reason': r.value or ''} for bid, (p, r) in identities.items()}
        try:
            current = service.fields.latest(phase, source['id'])
            if not current or current['id'] != receipt['id']: raise ValueError('核对决定已改变，请重新打开。')
            impact_store = FeedbackImpactStore(app.feedback_analysis()); prepared = impact_store.prepare(phase, source['id'])
        except Exception as exc:
            status.text = str(exc) if isinstance(exc, ValueError) else '暂时无法准备修改计划。'; return
        view['correction_plan_busy'] = True; status.text = '正在检查当前影响，可以返回继续学习。'
        async def work():
            saved = False
            try:
                result = await asyncio.to_thread(calculate, prepared['path'], prepared['inputs'])
                impact = impact_store.finish(prepared, result)
                plan = service.save(service.prepare(phase, source['id'], impact, identities=choices))
                saved = True
                if active(): status.text = ''; show(plan)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('feedback_plan', 'refresh' if saved else 'save', exc)
                if active(): status.text = ('计划已保存，但显示未完成；请重新打开报告。' if saved else
                    str(exc) if isinstance(exc, ValueError) else '计划未准备完成，原内容与进度未改动。')
            finally: view['correction_plan_busy'] = False
        app.loop.create_task(work())

    if not history_only: box.add(u.button('准备修改计划（本地）', start, compact=True))
    box.add(status)
    if saved: box.add(u.button('查看已保存修改计划', lambda w: show(saved), compact=True))
    box.add(panel)
