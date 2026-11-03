"""Explicit blind-review requests and opt-in findings inside feedback reports."""
import asyncio
from copy import deepcopy

from .feedback_review import FeedbackReviewStore, proposals, generate


def render_review(app, u, parent, box):
    if not proposals(parent): return
    view = app._feedback_analysis_view
    service = FeedbackReviewStore(app.feedback_analysis())
    saved = service.for_report(parent['id'])
    status, results = u.label('', size=12, color=u.MUTED), u.col()

    def active():
        return app.page == 'feedback_analysis' and app._feedback_analysis_view is view

    def open_findings(widget=None):
        if not active(): return
        try:
            app._feedback_report_exposure(parent['request'])
            current = service.for_report(parent['id'])
            rows = service.findings(current['id']) if current else []
            results.clear()
            from .feedback_analysis_ui import FIELD_LABELS
            labels = {'agrees': '模型答案与建议稿一致，仍缺独立依据。',
                      'differs': '模型提出了不同答案；这还不能证明原文或建议稿有误。', 'unknown': '依据不足，保留待核。'}
            for index, row in enumerate(rows):
                caption = FIELD_LABELS.get(row['field'].split('.')[-1], '内容')
                node = u.label(f'{index + 1} · {caption}', size=14, bold=True)
                node._feedback_fragment = {'kind': 'feedback_independent_review', 'job': deepcopy(current), 'finding': row}
                results.add(node, u.label('来源已变化，仅保留旧报告。' if row['status'] == 'stale' else labels[row['comparison']], size=13))
                if row['answer']: results.add(u.label(row['answer'], size=14))
                if row['note']: results.add(u.label(row['note'], size=12, color=u.MUTED))
            if current and current['request']['evidence']:
                results.add(u.button('查看核据参照', lambda w: show_sources(current), compact=True))
            if current and rows:
                from .feedback_field_review_ui import render_field_review
                render_field_review(app, u, 'analysis', parent, results)
                from .feedback_revision_ui import render_revision
                render_revision(app, u, parent, service, current, rows, results)
        except ValueError as exc: status.text = str(exc)
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('feedback_review', 'reveal', exc)
            status.text = '复核意见显示未完成，请重试；已记录的内容查看仍会保留。'

    def show_sources(current):
        if not active(): return
        try:
            stale = any(r['status'] == 'stale' for r in service.findings(current['id']))
            pairs = {(e['object_id'], ability) for e in current['request']['evidence'] if e['kind'] == 'dictionary_sense' for ability in ('understanding', 'reading')}
            app.diagnostics().expose_visible(pairs)
            mapping = {cid: {'understanding', 'reading'} for cid, _ in pairs}
            app._mark_visible_tutor_help(set(mapping), abilities_by_target=mapping, sources={'ai', 'tutor'})
            results.add(u.label('参照的适用范围与独立性仍须核查，获取成功不代表修正正确。', size=12, color=u.MUTED))
            if stale: results.add(u.label('以下为历史参照，当前来源或反馈已变化。', size=12, color=u.MUTED))
            for source in current['request']['evidence']:
                data = source['snapshot']
                if source['kind'] == 'public_excerpt':
                    results.add(u.label(data['quote'], size=14), u.label(data['url'] or '用户粘贴资料', size=11, color=u.MUTED))
                    continue
                results.add(u.label(' · '.join(str(data.get(k, '')) for k in ('title', 'reading', 'meaning')), size=14),
                    u.label(source['object_id'] + '\n版本：' + source['revision'], size=11, color=u.MUTED))
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('feedback_review', 'reveal', exc)
            status.text = '参照显示未完成，请重试；已记录的内容查看仍会保留。'

    def start(widget=None):
        if not active() or view.get('review_busy'): return
        if not app.ai_connection_status().get('ready'):
            status.text = '请先配置 AI 服务。'; return
        try:
            job = service.prepare(parent['id'])
            if job['status'] != 'prepared':
                status.text = '本次复核已经请求过；不会重复发送。'; return
            provider = app.tutor_client()
        except ValueError as exc: status.text = str(exc); return
        except Exception: status.text = '暂时无法准备复核。'; return
        view['review_busy'] = True
        async def run():
            from .request_outcome import finish_request
            from .diagnostic_events import record_error
            failure = ''
            try:
                config = getattr(provider, 'config', {})
                confirmed = await app.main_window.dialog(u.toga.ConfirmDialog('独立复核这些建议？',
                    '发送必要原文语境、精确义项的本地词典参照及已选资料摘录（包括来源地址和适用范围）；不发送首轮结论、建议稿或学习历史。'
                    + f"\n服务：{config.get('base_url', '')}\n模型：{provider.model}。最多一次请求，意见不自动修改内容。"))
                if not confirmed or not active(): return
                token = service.begin(job['id'], provider.model)
                status.text = '正在独立复核；可返回继续学习。'
                result = await finish_request('feedback_review', generate, provider, job, token, service.finish)
                if not result or result['status'] != 'completed':
                    failure = '复核未完成，请检查已保存报告；不会自动重发。'
            except Exception as exc:
                record_error('feedback_review', 'save', exc)
                failure = '复核准备或保存未完成，请重新打开报告。'
            finally:
                view['review_busy'] = False
                current_view = getattr(app, '_feedback_analysis_view', None)
                if app.page == 'feedback_analysis' and current_view and current_view['record']['id'] == view['record']['id']:
                    # Rebuild collapsed reports: a late reply must not expose an answer.
                    try:
                        app._render_feedback_reports(current_view)
                    except Exception as exc:
                        record_error('feedback_review', 'refresh', exc)
                        failure = failure or '报告显示未完成，请重新打开已保存报告。'
                    if failure: current_view['status'].text = failure
        app.loop.create_task(run())

    if saved and saved['status'] == 'completed':
        box.add(u.button('独立复核已准备 · 查看', open_findings, compact=True))
    elif not saved or saved['status'] == 'prepared':
        box.add(u.button('独立复核建议', start, compact=True))
    else:
        status.text = {'running': '独立复核正在进行。', 'transport_unknown': '上次复核结果不明，不会自动重发。',
                       'failed': '独立复核未完成，原报告仍保留。'}.get(saved['status'], '复核尚未完成。')
    box.add(status, results)
