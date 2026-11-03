"""Explicit batch selection, one in-flight diagnosis, durable opt-in continuation."""
import asyncio

from .feedback_batch import FeedbackBatchStore, members
from .feedback_bindings import candidates
from .feedback_analysis import generate
from .material_workflow import digest

STATES = {'prepared': '等待发送', 'running': '正在检查', 'completed': '报告已准备',
    'failed': '未收到有效报告，不会自动重发', 'transport_unknown': '结果不明，不会自动重发', 'cancelled': '已取消未发送的检查'}


def show(app, identity=None):
    from . import ui_components as u
    service = FeedbackBatchStore(app.feedback_analysis())
    try:
        saved = service.get(identity) if identity else None
        ids = saved['feedback_ids'] if saved else sorted(getattr(app, '_feedback_selected', set()))
        if not 1 <= len(ids) <= 10: raise ValueError('请先选择1至10条反馈。')
        records = [app.feedback_store().get(i) for i in ids]
        if any(r is None for r in records) and not saved: raise ValueError('部分反馈已删除，请重新选择。')
    except ValueError as exc: app.alert(str(exc)); return
    view = {'id': saved['id'] if saved else None, 'records': records, 'selected': {}, 'status': u.label('', size=12, color=u.MUTED),
        'selection': u.col(), 'jobs': u.col(), 'archive': u.switch('报告完成后归档相应反馈', value=saved['archive_after'] if saved else False)}
    app._feedback_batch_view = view
    def active(): return app.page == 'feedback_batch' and app._feedback_batch_view is view
    def refresh(widget=None):
        if active(): render_jobs(app, view)
    def pause(widget=None):
        if active() and view['id']:
            service.configure(view['id'], paused=True)
            view['status'].text = '已停止后续请求；当前已发送请求会保留结果。'
    children = [u.label('先选要检查的片段；涉及同一内容的反馈会合并。报告完成后仍需审阅。', size=13),
        view['selection'], view['status'], view['archive'],
        u.button('继续批量检查', lambda w: run(app, view), primary=True),
        u.button('停止后续请求', pause, compact=True), u.button('刷新进度', refresh, compact=True),
        view['jobs'], u.button('返回反馈记录', lambda w: app.show_feedback_manager(), compact=True)]
    app.screen('feedback_batch', '批量 AI 检查', *children)
    if saved: render_jobs(app, view); return
    from .feedback_analysis_ui import FIELD_LABELS, KINDS
    for index, record in enumerate(records):
        pool = candidates(record['context']); selected = view['selected'][record['id']] = {b['id'] for b in pool}
        row = u.col(u.label(f"反馈 {index + 1} · {record['note']}", size=14))
        for n, binding in enumerate(pool):
            switch = u.switch(f"{KINDS[binding['resource_kind']]} {n+1} · {FIELD_LABELS.get(binding['field'].split('.')[-1], '内容')}", value=True)
            def choose(widget, key=binding['id'], selection=selected, **kw):
                if not active() or view['id']: return
                if widget.value: selection.add(key)
                else: selection.discard(key)
            switch.on_change = choose; row.add(switch)
        preview = u.col()
        def reveal(widget=None, r=record, bindings=pool, selection=selected, box=preview):
            if not active(): return
            picked = [b for b in bindings if b['id'] in selection]
            try: app._feedback_analysis_exposure(r, picked)
            except Exception: view['status'].text = '显示记录暂未保存，片段仍隐藏。'; return
            box.clear()
            for b in picked: box.add(u.label(b['value'], size=13))
        row.add(u.button('查看片段（可能含答案）', reveal, compact=True), preview)
        view['selection'].add(row)


def render_jobs(app, view):
    if not view['id']: return
    from . import ui_components as u
    service = FeedbackBatchStore(app.feedback_analysis()); jobs = service.jobs(view['id'])
    view['jobs'].clear()
    for n, job in enumerate(jobs):
        ms = members(job['request'])
        view['jobs'].add(u.label(f"核查组 {n+1} · {len(ms)} 条反馈 · {STATES[job['status']]}", size=13))
        if not app.feedback_analysis().feedbacks_current(job['request']):
            view['jobs'].add(u.label('原反馈已变化；保留历史任务，需重新选择。', size=12, color=u.MUTED))
        # No model summary or content names before the ordinary exposure gate.
        present = next((m['feedback']['id'] for m in ms if app.feedback_store().get(m['feedback']['id'])), None)
        if present:
            view['jobs'].add(u.button('打开核查组', lambda w, fid=present: app.show_feedback_analysis(fid), compact=True))


def run(app, view):
    if app.page != 'feedback_batch' or app._feedback_batch_view is not view: return
    if getattr(app, '_feedback_batch_running', False):
        view['status'].text = '已有批次正在处理，可以返回继续学习。'; return
    if not app.ai_connection_status().get('ready'):
        view['status'].text = '请先配置 AI 服务。'; return
    service = FeedbackBatchStore(app.feedback_analysis())
    try:
        batch = service.get(view['id']) if view['id'] else service.prepare({i: sorted(v) for i, v in view['selected'].items()})
        view['id'] = batch['id']; view['selection'].clear()
        jobs = service.jobs(batch['id']); render_jobs(app, view)
        pending = [j for j in jobs if j['status'] == 'prepared']
        if not pending:
            service.configure(batch['id'], archive_after=bool(view['archive'].value))
            archived = service.archive_completed(batch['id'])
            view['status'].text = (f'已归档 {len(archived)} 条已完成且未变更的反馈。' if archived else
                '没有尚未发送的检查；现有报告可继续审阅。')
            return
        provider = app.tutor_client(); config = getattr(provider, 'config', {})
        consent = digest(['feedback_batch_v1', batch['id'], config.get('protocol', ''), config.get('base_url', ''), provider.model])
    except Exception as exc:
        view['status'].text = str(exc) if isinstance(exc, ValueError) else '暂时无法准备批量检查。'; return
    app._feedback_batch_running = True
    async def work():
        from .request_outcome import finish_request
        from .diagnostic_events import record_error
        failure = ''
        try:
            if consent not in getattr(app, '_feedback_batch_consents', set()):
                from . import ui_components as u
                ok = await app.main_window.dialog(u.toga.ConfirmDialog('批量发送给当前 AI 服务？',
                    f"发送 {len(batch['feedback_ids'])} 条反馈说明、选定片段（可能含答案）及对应本地来源；不发送截图、轨迹、设备信息或学习历史。"
                    f"\n服务：{config.get('base_url', '')}\n模型：{provider.model}\n本次最多 {len(pending)} 次诊断请求，逐组发送；后续复核、修订需另行点击。每组全流程最多3次模型请求。"))
                if not ok or app.page != 'feedback_batch' or app._feedback_batch_view is not view: return
                app._feedback_batch_consents = {*getattr(app, '_feedback_batch_consents', set()), consent}
            service.configure(batch['id'], paused=False, archive_after=bool(view['archive'].value))
            service.archive_completed(batch['id'])  # complete a prior result->archive interruption
            for job in service.jobs(batch['id']):
                if service.get(batch['id'])['paused']: break
                if job['status'] != 'prepared': continue
                if service.analysis.current_request(job['request']) != job['request']:
                    service.analysis.cancel(job['id']); continue
                token = service.analysis.begin(job['id'], provider.model)
                result = await finish_request('feedback_batch', generate, provider, job, token, service.analysis.finish)
                if result is None:
                    raise RuntimeError('Feedback result persistence unavailable')
                service.archive_completed(batch['id'])
                current = getattr(app, '_feedback_batch_view', {})
                if app.page == 'feedback_batch' and current.get('id') == batch['id']: render_jobs(app, current)
        except Exception as exc:
            record_error('feedback_batch', 'save', exc)
            failure = '已暂停后续请求。原任务保留，可稍后继续检查。'
            try:
                service.configure(batch['id'], paused=True)
            except Exception as failure_error:
                record_error('feedback_batch', 'record_failure', failure_error)
                failure = '后续请求已停止，但暂停状态未保存；请重新打开检查。'
        finally:
            app._feedback_batch_running = False
            current = getattr(app, '_feedback_batch_view', {})
            if app.page == 'feedback_batch' and current.get('id') == batch['id']:
                try: render_jobs(app, current)
                except Exception as exc:
                    record_error('feedback_batch', 'refresh', exc)
                    failure = failure or '任务列表未刷新，请重新打开检查已保存的结果。'
                if failure: current['status'].text = failure
    app.loop.create_task(work())
