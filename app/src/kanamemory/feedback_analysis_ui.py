"""Explicit small outbound manifests; reports remain opt-in during practice."""
import asyncio
from copy import deepcopy

from .navigation import page_view
from .feedback_analysis import FeedbackAnalysisStore, feedback_version, generate
from .feedback_bindings import candidates
from .material_workflow import digest

FIELD_LABELS = {'title': '词形／标题', 'reading': '读法', 'meaning': '含义', 'summary': '概述',
    'rule': '规则', 'example': '示例', 'translation': '译文', 'chinese_translation': '译文',
    'contrast': '对比', 'text': '日语原句', 'explanation': '说明', 'prompt': '题面', 'task': '要求',
    'answer': '答案', 'feedback': '答后说明', 'rubric': '整题答案、变体与选项',
    'prerequisites': '前置学习项', 'link': '引用关系'}
KINDS = {'lesson': '学习项', 'example': '共享例句', 'reference': '引用', 'question': '题目', 'lexical_example': '词库例句'}


class FeedbackAnalysisUIMixin:
    @page_view(replace=True)
    def show_feedback_batch(self, widget=None, *, identity=None):
        from .feedback_batch_ui import show
        show(self, identity)

    def feedback_analysis(self):
        return FeedbackAnalysisStore(self.store, self.feedback_store(), learning=self.learning_store,
                                     repo=self.repo, examples=self.example_store())

    def open_feedback_analysis_detail(self):
        state = self._report_detail
        if self._feedback_dirty(state, detail=True):
            state['message'].text = '先保存修改，再检查这条反馈。'; return
        self.show_feedback_analysis(state['id'])

    @page_view(replace=True)
    def show_feedback_analysis(self, identity):
        from . import ui_components as u
        record = self.feedback_store().get(identity)
        if record is None: return self.show_feedback_manager()
        pool = candidates(record['context'])
        jobs = self.feedback_analysis().reports(identity)
        from .feedback_batch import members
        latest = next((j for j in jobs if self.feedback_analysis().feedbacks_current(j['request'])), None)
        member = next((r for r in members(latest['request']) if r['feedback']['id'] == identity), None) if latest else None
        selected = {b['id'] for b in member['bindings']} if member else set()
        view = {'record': record, 'pool': pool, 'selected': selected, 'opened': False,
                'status': u.label('', size=12, color=u.MUTED), 'items': u.col(), 'reports': u.col()}
        self._feedback_analysis_view = view
        self.screen('feedback_analysis', 'AI 检查反馈', u.label(record['note'], size=15),
            u.label('选择要检查的内容；不确定的建议会保留待核。', size=13, color=u.MUTED),
            u.button('查看内容片段（可能含答案）', lambda w: self.reveal_feedback_fragments(view), compact=True),
            view['items'], view['status'],
            u.button('检查所选片段', lambda w: self.run_feedback_analysis(view), primary=True),
            view['reports'], u.button('返回反馈', lambda w: self.show_feedback_detail(identity), compact=True))
        self._render_feedback_fragments(view)
        self._render_feedback_reports(view)
        from .personal_content_notes import PersonalContentNoteStore
        try: PersonalContentNoteStore(self.store).sync(self.feedback_store())
        except Exception: view['status'].text = '个人注释已保存，反馈回执待同步。'
        from .personal_content_compensation import sync
        try: sync(self.store, self.feedback_store())
        except Exception: view['status'].text = '内容修订已保存，反馈回执待同步。'

    def _feedback_analysis_exposure(self, record, bindings):
        """Opening a report is explicit help, never a new answer or score."""
        from .feedback_exposure import expose
        expose(self, record, bindings)

    def _feedback_report_exposure(self, request):
        from .feedback_batch import members
        records = [(self.feedback_store().get(m['feedback']['id']), m) for m in members(request)]
        if any(r is None or r['context_sha256'] != m['feedback']['context_sha256'] for r, m in records):
            raise ValueError('原反馈上下文已不可用，报告仍隐藏。')
        # Per-member original bindings are required even when a shared field
        # has a different representative ID in the grouped model request.
        for record, member in records: self._feedback_analysis_exposure(record, member['bindings'])

    def reveal_feedback_fragments(self, view):
        if self.page != 'feedback_analysis' or self._feedback_analysis_view is not view: return
        try: self._feedback_analysis_exposure(view['record'], view['pool'])
        except ValueError as exc:
            view['status'].text = str(exc); return
        except Exception:
            view['status'].text = '片段显示记录暂未保存，内容仍隐藏。'; return
        view['opened'] = True; self._render_feedback_fragments(view)

    def _render_feedback_fragments(self, view):
        from . import ui_components as u
        view['items'].clear()
        if not view['pool']:
            view['items'].add(u.label('没有完整且带明确来源的片段；只能整理问题，不能定位内容修改。', size=13, color=u.MUTED))
        for index, binding in enumerate(view['pool']):
            caption = f"{KINDS[binding['resource_kind']]} {index + 1} · {FIELD_LABELS.get(binding['field'].split('.')[-1], '内容')}"
            switch = u.switch(caption, value=binding['id'] in view['selected'])
            def select(widget, key=binding['id'], **kw):
                if self.page != 'feedback_analysis' or self._feedback_analysis_view is not view: return
                if widget.value: view['selected'].add(key)
                else: view['selected'].discard(key)
            switch.on_change = select; view['items'].add(switch)
            if view['opened']: view['items'].add(u.label(binding['value'], size=14))

    def _render_feedback_reports(self, view):
        from . import ui_components as u
        view['reports'].clear()
        for job in self.feedback_analysis().reports(view['record']['id']):
            stale = not self.feedback_analysis().feedbacks_current(job['request'])
            if job['status'] == 'completed':
                label = '旧版反馈的核查报告' if stale else '核查报告已准备'
                slot = u.col()
                def open_report(widget=None, saved=job, box=slot):
                    if self.page != 'feedback_analysis' or self._feedback_analysis_view is not view: return
                    try: self._feedback_report_exposure(saved['request'])
                    except ValueError as exc:
                        view['status'].text = str(exc); return
                    except Exception:
                        view['status'].text = '显示记录暂未保存，报告仍隐藏。'; return
                    result = saved['response']; box.clear()
                    nodes = [u.label(result['summary'], size=15), u.label('核查意见 · 尚未修改内容或复习记录', size=12, color=u.MUTED)]
                    for issue in result['issues']:
                        nodes.append(u.label(issue['claim'], size=14))
                        if issue['suggestion']: nodes.append(u.label('建议稿：' + issue['suggestion'], size=14))
                    if result['maintenance']: nodes.append(u.label(result['maintenance'], size=14))
                    nodes[0]._feedback_fragment = {'kind': 'feedback_analysis_report', 'job': deepcopy(saved)}
                    box.add(*nodes)
                    from .feedback_sources_ui import render_sources
                    render_sources(self, u, saved, box)
                    from .feedback_review_ui import render_review
                    render_review(self, u, saved, box)
                    from .feedback_impact_ui import render_impact
                    render_impact(self, u, 'analysis', saved, box)
                view['reports'].add(u.button(label + ' · 查看', open_report, compact=True), slot)
            else:
                text = {'running': '正在检查；可返回继续学习。', 'prepared': '检查尚未发送。',
                        'transport_unknown': '上次请求结果不明，不会自动重发。', 'failed': '检查未完成，原反馈已保留。',
                        'cancelled': '未发送的检查已取消。'}.get(job['status'], '检查已保存。')
                view['reports'].add(u.label(text, size=12, color=u.MUTED))

    def run_feedback_analysis(self, view):
        if self.page != 'feedback_analysis' or self._feedback_analysis_view is not view or view.get('busy'): return
        if not self.ai_connection_status().get('ready'):
            view['status'].text = '请先配置 AI 服务。'; return
        try:
            service = self.feedback_analysis()
            job = service.create_report(view['record']['id'], sorted(view['selected']))
            if job['status'] != 'prepared': self._render_feedback_reports(view); return
            provider = self.tutor_client()
            config = getattr(provider, 'config', {})
            consent = digest(['feedback_text_and_selected_content_v1', config.get('protocol', ''), config.get('base_url', ''), provider.model,
                              job['id'] if 'group_members' in job['request'] else 'single'])
        except Exception as exc:
            view['status'].text = str(exc) if isinstance(exc, ValueError) else '暂时无法准备核查，请检查 AI 服务设置。'; return
        view['busy'] = True
        async def run():
            from .request_outcome import finish_request
            from .diagnostic_events import record_error
            failure = ''
            try:
                if consent not in getattr(self, '_feedback_analysis_consents', set()):
                    from . import ui_components as u
                    from .feedback_batch import members
                    confirmed = await self.main_window.dialog(u.toga.ConfirmDialog('发送给当前 AI 服务？',
                        f"发送本组 {len(members(job['request']))} 条反馈说明、选定文字片段（可能含原题答案）及对应本地来源；不发送截图、轨迹、设备信息或学习历史。"
                        + f"\n服务：{config.get('base_url', '')}\n模型：{provider.model}。本次最多一次核查请求；结果不会自动修改内容。"))
                    if not confirmed: return
                    if self.page != 'feedback_analysis' or self._feedback_analysis_view is not view: return
                    self._feedback_analysis_consents = {*getattr(self, '_feedback_analysis_consents', set()), consent}
                token = service.begin(job['id'], provider.model)
                view['status'].text = '正在检查；可以返回继续学习。'
                result = await finish_request('feedback_analysis', generate, provider, job, token, service.finish)
                if not result or result['status'] != 'completed':
                    failure = '核查未完成，请重新打开已保存报告检查；不会自动重发。'
            except Exception as exc:
                record_error('feedback_analysis', 'save', exc)
                failure = '核查准备或保存未完成，请重新打开报告检查；不会自动重发。'
            finally:
                view['busy'] = False
                current = getattr(self, '_feedback_analysis_view', None)
                if self.page == 'feedback_analysis' and current and current['record']['id'] == view['record']['id']:
                    try:
                        self._render_feedback_reports(current)
                    except Exception as exc:
                        record_error('feedback_analysis', 'refresh', exc)
                        failure = failure or '报告显示未完成，请重新打开已保存报告。'
                    current['status'].text = failure
        self.loop.create_task(run())
