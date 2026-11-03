"""An optional third call, only after selecting a concrete differing opinion."""
import asyncio
from copy import deepcopy

from .feedback_revision import FeedbackRevisionStore, generate


def render_revision(app, u, parent, reviews, current, rows, box):
    view = app._feedback_analysis_view; service = FeedbackRevisionStore(reviews)
    saved = service.for_report(parent['id'])
    selected = set(saved['request']['selected']) if saved else set()
    status, results = u.label('', size=12, color=u.MUTED), u.col()

    def active():
        return app.page == 'feedback_analysis' and app._feedback_analysis_view is view

    def open_draft(widget=None):
        if not active(): return
        try:
            job = service.for_report(parent['id'])
            app._feedback_report_exposure(parent['request'])
            results.clear()
            from .feedback_analysis_ui import FIELD_LABELS
            results.add(u.label('修订稿仅供审阅，尚未修改学习内容。', size=12, color=u.MUTED))
            for index, row in enumerate(service.findings(job['id'])):
                title = u.label(f"{index + 1} · {FIELD_LABELS.get(row['field'].split('.')[-1], '内容')}", bold=True, size=14)
                title._feedback_fragment = {'kind': 'feedback_revision', 'job': deepcopy(job), 'finding': row}
                results.add(title, u.label('原文：' + row['before'], size=13))
                if row['decision'] == 'revise': results.add(u.label('建议：' + row['new_value'], size=14))
                else: results.add(u.label('建议保留原文。' if row['decision'] == 'keep' else '尚无法确定。', size=13))
                results.add(u.label(row['reason'], size=12, color=u.MUTED))
                results.add(u.label({'stale': '来源已变化，仅保留历史稿。', 'unresolved': '联动组尚有不确定字段，整组待核。',
                    'needs_evidence': '仍需独立依据和关联影响审查。'}[row['status']], size=12, color=u.MUTED))
            from .feedback_impact_ui import render_impact
            render_impact(app, u, 'revision', job, results)
            from .feedback_field_review_ui import render_field_review
            render_field_review(app, u, 'revision', job, results)
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('feedback_revision', 'reveal', exc)
            status.text = '修订稿显示未完成，请重试；已记录的内容查看仍会保留。'

    def start(widget=None):
        if not active() or view.get('revision_busy'): return
        if not app.ai_connection_status().get('ready'):
            status.text = '请先配置 AI 服务。'; return
        try:
            job = service.prepare(parent['id'], sorted(selected))
            if job['status'] != 'prepared':
                status.text = '本次修订已经请求过，不会重发。'; return
            provider = app.tutor_client()
        except ValueError as exc: status.text = str(exc); return
        except Exception: status.text = '暂时无法准备修订。'; return
        view['revision_busy'] = True
        async def run():
            from .request_outcome import finish_request
            from .diagnostic_events import record_error
            failure = ''
            try:
                config = getattr(provider, 'config', {})
                confirmed = await app.main_window.dialog(u.toga.ConfirmDialog('协调所选不同意见？',
                    '发送所选原文、初次建议、独立复核意见及相互依赖的字段和本地参照，不发送学习历史。'
                    + f"\n服务：{config.get('base_url', '')}\n模型：{provider.model}。这是本报告最后一次修订请求，结果仍须核查。"))
                if not confirmed or not active(): return
                token = service.begin(job['id'], provider.model)
                status.text = '正在整理修订稿，可返回继续学习。'
                result = await finish_request('feedback_revision', generate, provider, job, token, service.finish)
                if not result or result['status'] != 'completed':
                    failure = '修订未完成，请检查已保存报告；不会自动重发。'
            except Exception as exc:
                record_error('feedback_revision', 'save', exc)
                failure = '修订准备或保存未完成，请重新打开报告。'
            finally:
                view['revision_busy'] = False
                latest = getattr(app, '_feedback_analysis_view', None)
                if app.page == 'feedback_analysis' and latest and latest['record']['id'] == view['record']['id']:
                    try:
                        app._render_feedback_reports(latest)
                    except Exception as exc:
                        record_error('feedback_revision', 'refresh', exc)
                        failure = failure or '报告显示未完成，请重新打开已保存报告。'
                    if failure: latest['status'].text = failure
        app.loop.create_task(run())

    if saved and saved['status'] == 'completed':
        box.add(u.button('修订稿已准备 · 查看', open_draft, compact=True))
    elif saved and saved['status'] != 'prepared':
        status.text = {'running': '修订正在进行。', 'transport_unknown': '修订结果不明，不会自动重发。',
                       'failed': '修订未完成，已有报告仍保留。'}.get(saved['status'], '修订尚未完成。')
    else:
        options = [r for r in rows if r['comparison'] == 'differs' and r['answer'] and r['status'] != 'stale']
        if not options: return
        box.add(u.label('不同措辞不代表有错。仅选择需要协调的意见；相关字段会一起处理。', size=12, color=u.MUTED))
        from .feedback_analysis_ui import FIELD_LABELS
        for index, row in enumerate(rows):
            if row not in options: continue
            switch = u.switch(f"协调意见 {index + 1} · {FIELD_LABELS.get(row['field'].split('.')[-1], '内容')}", value=row['binding_id'] in selected)
            def change(widget, key=row['binding_id'], **kw):
                if not active(): return
                if widget.value: selected.add(key)
                else: selected.discard(key)
            switch.on_change = change; box.add(switch)
        box.add(u.button('生成所选修订稿', start, compact=True))
    box.add(status, results)
