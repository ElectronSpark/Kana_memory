"""Optional AI plan beside the immediately usable local diagnostic controls."""
import asyncio

from .retention_ai import DiagnosticAIStore, REASONS, generate
from .retention_scope_ui import ScopeUIMixin
from .request_outcome import finish_request


class DiagnosticAIUIMixin(ScopeUIMixin):
    def diagnostic_ai(self):
        return DiagnosticAIStore(self.diagnostics())

    def diagnostic_ai_controls(self, context, *, message=''):
        from . import ui_components as u
        field = u.toga.TextInput(value=message, placeholder='想重点检查什么？（可选）',
                                style=u.Pack(margin_top=8, margin_bottom=6))
        context.update(ai_message=field, ai_box=u.col(), ai_busy=False, ai_plan=None)
        context['ai_button'] = u.button('AI 建议选题', lambda w: self.request_diagnostic_ai(context), compact=True)
        return [field, *self.scope_suggestion_controls(context), context['ai_button'], context['ai_box']]

    def _diagnostic_ai_values(self, context):
        from .retention_session_ui import PURPOSE_LABELS, BUDGET_LABELS
        return {'scope': [row['content_id'] for row in context['items'] if row['content_id'] in context['selected']],
            'enabled': self.learning_preferences()['enabled'], 'message': context['ai_message'].value or '',
            'purpose': PURPOSE_LABELS[context['purpose'].value], 'budget': BUDGET_LABELS[context['budget'].value],
            'intent': context['intent']}

    def request_diagnostic_ai(self, context):
        from .ai_service import AIServiceError
        if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context or context['ai_busy']:
            return
        if not self.ai_connection_status().get('ready'):
            context['status'].text = '请先配置 AI 服务，也可直接开始本地检查。'
            return
        try:
            self._save_diagnostic_scope(context)
            values = self._diagnostic_ai_values(context)
            job = self.diagnostic_ai().create(request_id=context['request_id'], **values)
            context['ai_plan'] = job['id']
            self.scope_forms().edit(context['form_id'], {'ai_plan': job['id']})
            if job['status'] == 'completed':
                return self._show_diagnostic_ai_result(context, job, values)
            client = self.tutor_client()
            token = self.diagnostic_ai().begin(job['id'], client.model)
        except ValueError as exc:
            context['status'].text = str(exc)
            return
        except AIServiceError as exc:
            context['status'].text = exc.user_message
            return
        context['ai_busy'] = True
        context['ai_button'].enabled = False
        context['status'].text = '正在准备建议；仍可直接开始本地检查。'
        self.loop.create_task(self._run_diagnostic_ai(context, job, values, client, token))

    async def _run_diagnostic_ai(self, context, job, values, client, token):
        try:
            saved = await finish_request('diagnostic_plan', generate, client, job, token, self.diagnostic_ai().finish)
        except asyncio.CancelledError:
            if self.page == 'diagnostic_scope' and self._diagnostic_scope is context:
                context['status'].text = '本次建议已中断，可以继续本地检查；不会自动重发。'
            raise
        finally:
            context['ai_busy'] = False
            if self.page == 'diagnostic_scope' and self._diagnostic_scope is context:
                context['ai_button'].enabled = True
        if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context:
            return  # Durable response remains available; no late navigation.
        if saved and saved['status'] == 'completed':
            self._show_diagnostic_ai_result(context, saved, values)
        else:
            context['status'].text = '本次建议未完成，可以直接使用本地安排；不会自动重发。'

    def _show_diagnostic_ai_result(self, context, job, values):
        from . import ui_components as u
        if self._diagnostic_ai_values(context) != values:
            context['status'].text = '范围或设置已变化，未采用旧建议。'
            return
        box = context['ai_box']; box.clear()
        reasons = list(dict.fromkeys(REASONS[pick['reason']] for pick in job['response']['picks']))
        box.add(u.label(' · '.join(reasons), size=13, color=u.MUTED))
        context['status'].text = '建议已准备好，原学习记录未改变。'
        def apply(widget=None):
            if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context:
                return
            if self._diagnostic_ai_values(context) != values:
                context['status'].text = '范围或设置已变化，未采用旧建议。'
                return
            try:
                episode = self.diagnostic_ai().apply(job['id'], self.learning_preferences()['enabled'],
                    exposed=context['exposed'], _after_start=lambda db, episode: self.scope_forms().started(context['form_id'], episode))
            except ValueError as exc:
                context['status'].text = str(exc)
                return
            self.continue_diagnostic(episode['id'])
        box.add(u.button('按建议开始检查', apply, compact=True))

    def restore_diagnostic_ai(self, identity):
        from .retention_session_ui import PURPOSE_LABELS, BUDGET_LABELS
        job = self.diagnostic_ai().get(identity)
        if not job:
            return
        if 'session' in job['request']:
            return self.continue_diagnostic(job['request']['session']['episode_id'])
        if job['episode_id']:
            return self.continue_diagnostic(job['episode_id'])
        request = job['request']
        self.show_diagnostic_scope(scope_ids=request['scope_ids'], intent=request['intent'], message=request['message'])
        if self.page != 'diagnostic_scope':
            return
        context = self._diagnostic_scope
        context['purpose'].value = next(k for k, v in PURPOSE_LABELS.items() if v == request['purpose'])
        context['budget'].value = next(k for k, v in BUDGET_LABELS.items() if v == request['budget'])
        context['selected'] = set(request['scope_ids'])
        context['ai_plan'] = identity
        context['ai_button'].enabled = False
        self._render_diagnostic_scope()
        self._save_diagnostic_scope(context)
        self.scope_forms().edit(context['form_id'], {'ai_plan': identity})
        if job['status'] == 'completed':
            self._show_diagnostic_ai_result(context, job, self._diagnostic_ai_values(context))
        else:
            context['status'].text = '上次建议未完成；保留了范围，可以直接开始本地检查。'

    def diagnostic_from_tutor(self, widget=None):
        if self.page != 'tutor_chat':
            return
        self.save_tutor_chat_input()
        self.show_diagnostic_scope(message=self._tutor_chat_input.value or '')

    def diagnostic_mid_controls(self, context):
        from . import ui_components as u
        from .retention_ai_mid import identity, snapshot
        service = self.diagnostic_ai()
        service.recover()
        job = service.get(identity(context['episode']))
        box = u.col(); context['mid_ai_box'] = box
        if job:
            self._show_diagnostic_mid(context, job)
            return [box]
        try:
            snapshot(service.diagnostics, service.diagnostics.get(context['episode']), self.learning_preferences()['enabled'])
        except ValueError:
            return []
        box.add(u.button('AI 调整后续顺序', lambda w: self.request_diagnostic_mid(context), compact=True))
        return [box]

    def request_diagnostic_mid(self, context):
        from .ai_service import AIServiceError
        if self.page != 'diagnostic_feedback' or self._diagnostic_context is not context:
            return
        if not self.ai_connection_status().get('ready'):
            context['status'].text = '请先配置 AI 服务，也可继续本地安排。'
            return
        try:
            self._flush_diagnostic_time()
            service = self.diagnostic_ai()
            job = service.create_mid(context['episode'], self.learning_preferences()['enabled'])
            if job['status'] != 'prepared':
                self._show_diagnostic_mid(context, job)
                return
            client = self.tutor_client()
            token = service.begin(job['id'], client.model)
            self._show_diagnostic_mid(context, service.get(job['id']))
            self.loop.create_task(self._run_diagnostic_mid(context, job, client, token))
        except (ValueError, AIServiceError) as exc:
            context['status'].text = getattr(exc, 'user_message', str(exc))
        finally:
            if self.page == 'diagnostic_feedback' and self._diagnostic_context is context:
                self._arm_diagnostic_timer(context)

    async def _run_diagnostic_mid(self, context, job, client, token):
        try:
            saved = await finish_request('diagnostic_mid', generate, client, job, token, self.diagnostic_ai().finish)
        except asyncio.CancelledError:
            if self.page == 'diagnostic_feedback' and self._diagnostic_context is context:
                self._show_diagnostic_mid(context, {'status': 'failed'})
            raise
        if self.page == 'diagnostic_feedback' and self._diagnostic_context is context:
            self._show_diagnostic_mid(context, saved or {'status': 'failed'})

    def _show_diagnostic_mid(self, context, job):
        from . import ui_components as u
        from .retention_ai_mid import snapshot
        box = context['mid_ai_box']; box.clear()
        state = job['status']
        if state == 'prepared':
            box.add(u.button('AI 调整后续顺序', lambda w: self.request_diagnostic_mid(context), compact=True))
            return
        if state != 'completed':
            text = ('正在准备建议，也可继续练习。' if state == 'running' else
                    '已调整后续顺序。' if state == 'applied' else '本次建议未完成，继续使用本地安排。')
            box.add(u.label(text, size=12, color=u.MUTED))
            return
        try:
            current = snapshot(self.diagnostics(), self.diagnostics().get(context['episode']), self.learning_preferences()['enabled'])
            if current != job['request']:
                raise ValueError('stale')
        except ValueError:
            box.add(u.label('本轮作答或设置已变化，继续使用本地安排。', size=12, color=u.MUTED))
            return
        def apply(widget=None):
            if self.page != 'diagnostic_feedback' or self._diagnostic_context is not context:
                return
            try:
                self._flush_diagnostic_time()
                self.diagnostic_ai().apply(job['id'], self.learning_preferences()['enabled'])
                self._show_diagnostic_mid(context, self.diagnostic_ai().get(job['id']))
            except ValueError as exc:
                context['status'].text = str(exc)
            finally:
                if self.page == 'diagnostic_feedback' and self._diagnostic_context is context:
                    self._arm_diagnostic_timer(context)
        box.add(u.button('采用后续顺序建议', apply, compact=True))
