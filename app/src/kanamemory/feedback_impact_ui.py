"""Explicit local impact previews with paged detail and no content activation."""
import asyncio

from .feedback_impact import FeedbackImpactStore, changes_for, calculate

LABELS = {'resource': '内容版本', 'reference': '例句关联', 'anchor': '需重新定位的片段', 'teaching': '教学内容',
    'template': '题目模板', 'relation': '知识关系', 'cache': '派生显示与音频', 'question': '题目记录',
    'history': '历史记录', 'attribution': '部件归因', 'derived': '派生上下文', 'adjustment': '辅助间隔记录', 'profile': '画像依据'}


def render_impact(app, u, phase, source, box):
    if not changes_for(source, phase): return
    view = app._feedback_analysis_view; service = FeedbackImpactStore(app.feedback_analysis())
    status, details = u.label('', size=12, color=u.MUTED), u.col()
    saved = service.get(phase, source['id'])

    def active():
        return app.page == 'feedback_analysis' and app._feedback_analysis_view is view

    def show(report):
        if not active(): return
        details.clear(); r = report['report']
        status.text = ('扫描期间内容或进度有变化，请重新检查。' if report['changed_during_scan'] else
                       '已遍历当前可追溯记录；仍需逐项确认影响。' if r['enumeration_complete'] else
                       '影响范围尚不完整，不能据此采纳修正。')
        details.add(u.label('只检查本地关联，未改内容、答案、草稿或到期时间。', size=12, color=u.MUTED))
        if r['counts']: details.add(u.label(' · '.join(f"{LABELS[k]} {n}" for k, n in r['counts'].items()), size=13))
        details.add(u.label('一题可有多个保存位置；这里统计记录，不作为待做题数。', size=12, color=u.MUTED))
        if r['has_potential']: details.add(u.label('部分旧题缺少字段来源，仅列为可能受影响，不判定其旧成绩有误。', size=12, color=u.MUTED))
        from .semantic_impact import summarize, LABELS as CHANGE_LABELS, ACTIONS
        for classification in summarize(report):
            details.add(u.label(CHANGE_LABELS[classification['category']], size=13, bold=True))
            details.add(u.label(' · '.join(f'{ACTIONS[k]} {n}' for k, n in classification['actions'].items()), size=12))
            if classification['identity_review_required']:
                details.add(u.label('还需确认是否仍为同一义项；不同义项需要独立内容身份。', size=12, color=u.MUTED))
        details.add(u.label('分类只说明已知用途；不据此判定旧答案有错，也不直接改期。', size=12, color=u.MUTED))
        graph = r.get('graph_review')
        if graph:
            g = graph['result']; graph_box = u.col(); shown = [False]
            def show_graph(widget=None):
                if not active() or shown[0]: return
                shown[0] = True
                graph_box.add(u.label(f"结构问题 {len(g['problems'])} · 共享片段 {len(g['overlaps'])}（允许）", size=12))
                reanchor = sum(x['status'] == 'reanchor_and_semantic_review' for x in g['references'])
                graph_box.add(u.label(f"关联记录 {len(g['references'])} · 需重新定位及核对用法 {reanchor}", size=12))
                for route in g['routes']:
                    label = {'catalogue': '当前目录', 'saved': '已保存路线', 'effective': '当前实际路线'}[route['origin']]
                    graph_box.add(u.label(f"{label}：前置关系 {route['edges']}，缺失 {len(route['missing'])}，环 {len(route['cycles'])}", size=12))
                if g['text_replacements_require_new_identity'] or g['lexical_text_identity_review']:
                    graph_box.add(u.label('日语原句改变时需另建例句版本，逐条核对关联，不能照搬旧高亮。', size=12))
                graph_box.add(u.label('结构检查不证明语言用法正确或没有漏关联；历史快照仍保留。', size=12, color=u.MUTED))
                # Only structural counts are visible here. Sentences, target
                # names and quotes stay out of this summary's disclosure scope.
            details.add(u.button('查看图谱与片段检查', show_graph, compact=True), graph_box)
        else:
            details.add(u.label('本快照未完成图谱与片段检查。', size=12, color=u.MUTED))
        page = u.col(); offset = [0]
        def more(widget=None):
            if not active(): return
            stop = min(len(r['rows']), offset[0] + 20)
            for row in r['rows'][offset[0]:stop]:
                certainty = {'declared_field': '字段引用', 'declared_resource': '资源关联', 'potential': '可能关联'}[row['certainty']]
                page.add(u.label(f"{LABELS[row['kind']]} · {certainty}\n{row['locator']}", size=12))
            offset[0] = stop; more_button.enabled = stop < len(r['rows'])
            more_button.text = f"继续查看（{stop}/{len(r['rows'])}）"
        more_button = u.button('展开影响条目', more, compact=True)
        details.add(more_button, page)
        if r['gaps']:
            def gaps(widget=None):
                if active(): details.add(u.label('\n'.join(r['gaps']), size=11, color=u.MUTED))
            details.add(u.button('查看未能追溯的范围', gaps, compact=True))

    def start(widget=None):
        if not active() or view.get('impact_busy'): return
        view['impact_busy'] = True; status.text = '正在检查本地关联，可返回继续学习。'
        async def run():
            saved = False
            try:
                prepared = service.prepare(phase, source['id'])
                report = await asyncio.to_thread(calculate, prepared['path'], prepared['inputs'])
                saved_report = service.finish(prepared, report)
                saved = True
                if active(): show(saved_report)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('feedback_impact', 'refresh' if saved else 'save', exc)
                if active(): status.text = ('影响审查已保存，但显示未完成；请重新打开报告。' if saved else
                    '影响审查未完成，原内容和进度保持不变。')
            finally: view['impact_busy'] = False
        app.loop.create_task(run())

    box.add(u.button('检查修改影响（本地）', start, compact=True))
    if saved: box.add(u.button('查看上次影响快照', lambda w: show(saved), compact=True))
    box.add(status, details)
