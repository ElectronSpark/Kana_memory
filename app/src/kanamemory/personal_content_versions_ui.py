"""Explicit group adoption in the maintenance workflow, away from lessons."""
from .personal_content_versions import PersonalContentVersionStore
from .diagnostic_events import record_error


def render_adoption(app, u, plan, group, box, active, *, history_only=False):
    if group['status'] != 'content_review_plan': return
    status = u.label('', size=12, color=u.MUTED)
    service = PersonalContentVersionStore(app.feedback_analysis())
    controls = u.col()
    graph = any(op['verb'] in {'revise_prerequisites', 'revise_reference', 'replace_example'} for op in group['operations'])
    pending = {}
    refresh_pending = [False]
    read_graph = lambda: {}
    def refresh():
        nonlocal read_graph
        controls.clear()
        result = service.latest(plan['id'], group['id'])
        if result:
            current = service.active(result)
            controls.add(u.label('已采纳为个人教学内容' if current else '这一版已撤下或被新版替代', size=12))
            targets = service.formal_recheck_targets(result['id']) if current else []
            if targets:
                controls.add(u.label('旧答案受本次修订影响。缺少可重放的完整调度依据，未重算掌握度或到期时间；可自愿检查当前掌握情况。', size=13))
                def recheck(widget=None):
                    if active() and service.active(result):
                        app.show_diagnostic_scope(scope_ids=targets, intent='active',
                            message='检查内容修订后当前的掌握情况；原始作答与到期时间保留。')
                controls.add(u.button('检查相关内容', recheck, compact=True))
            examples = service.analysis.resources.get('examples')
            if examples and hasattr(examples, 'get_reference'):
                for row in result['resources']:
                    if row.get('kind') != 'reference': continue
                    ref = examples.get_reference(row['id'])
                    if ref:
                        label = u.label('引用：' + ref['target_id'] + ('（未启用）' if ref.get('personal_content_unavailable') else ''), size=12)
                        label._feedback_fragment = {'kind': 'example_text', 'reference': ref,
                            'example_snapshot': examples.get(ref['example_id'])}
                        controls.add(label)
            if current: controls.add(u.button('撤下这一组内容修订', lambda w: apply('withdraw'), compact=True))
        elif not history_only:
            if graph:
                from .personal_graph_ui import render_replacements
                read_graph = render_replacements(app, u, service, plan, group, controls)
                controls.add(u.button('预览关系变更', preview, compact=True))
            reading = any(op['field'].endswith('reading') for op in group['operations'])
            rubric = any(op['owner']['kind'] == 'question' for op in group['operations'])
            controls.add(u.button('确认采纳预览中的关系修订' if graph else '采纳已核对的整题修订' if rubric else '采纳已核实的读法与内容' if reading else '采纳已核实的译文和说明', lambda w: apply('adopt'), compact=True))
    def preview(widget=None):
        if history_only or not active(): return
        try:
            decisions = read_graph()
            prepared = service.prepare(plan['id'], group['id'], graph_review=decisions)
            pending.update(prepared=prepared, decisions=decisions)
            rows = prepared.get('event', prepared.get('saved'))['resources']
            references = sum(len(r.get('graph', {}).get('references', [])) for r in rows)
            retired = sum(len(r.get('graph', {}).get('retired_reference_ids', [])) for r in rows)
            status.text = f'预览已准备：修改 {len(rows)} 个内容对象，保留旧句，迁入 {references} 条引用，处理旧引用 {retired} 条。保留原始作答和正式复习记录；失效归因不再参与辅助安排。'
        except Exception as exc:
            pending.clear()
            status.text = str(exc) if isinstance(exc, ValueError) else '暂不能准备预览；原内容未修改。'
    def apply(action):
        if not active() or history_only and action != 'withdraw': return
        if refresh_pending[0]:
            try:
                refresh()
                refresh_pending[0] = False
            except Exception as exc:
                record_error('content_adoption', 'refresh', exc)
            return  # A committed action must not be repeated to repair its UI.
        try:
            from .personal_content_compensation import prepare, commit, sync
            if action == 'withdraw':
                adopted = service.latest(plan['id'], group['id'])
                result = commit(service, prepare(service, adopted['id']))
            else:
                if graph:
                    if not pending or pending['decisions'] != read_graph(): raise ValueError('请先预览当前关系决定，再确认采纳。')
                    result = service.commit(pending['prepared'])
                else: result = service.commit(service.prepare(plan['id'], group['id']))
        except Exception as exc:
            record_error('content_adoption', 'commit', exc)
            status.text = str(exc) if isinstance(exc, ValueError) else '未完成修改，原内容与进度已保留。'
            return
        refresh_pending[0] = True
        status.text = ('已撤下，恢复前版显示并保留待核状态。' if action == 'withdraw' else '已采纳为个人教学内容。') + '原始作答和正式复习记录已保留，相关辅助记录已重新裁定。'
        status._feedback_fragment = {'kind': 'personal_content_version', 'version_id': result['id'],
            'plan_id': plan['id'], 'group_id': group['id'], 'action': action}
        try: sync(app.store, app.feedback_store())
        except Exception as exc:
            record_error('content_adoption', 'sync', exc)
            status.text += ' 反馈回执待同步。'
        try:
            refresh()
            refresh_pending[0] = False
        except Exception as exc:
            record_error('content_adoption', 'refresh', exc)
            status.text += ' 界面暂未刷新，请返回后重新打开。'
    refresh(); box.add(controls, status)
