"""Editable, resumable scope and optional model-selected target suggestions."""
import asyncio

from .retention_scope_form import ScopeFormStore, request_id
from .retention_scope_ai import ScopeAIStore, generate, snapshot


class ScopeUIMixin:
    def scope_forms(self):
        return ScopeFormStore(self.store)

    def scope_ai(self):
        return ScopeAIStore(self.diagnostics())

    def _save_diagnostic_scope(self, context=None):
        from .retention_session_ui import PURPOSE_LABELS, BUDGET_LABELS
        context = context or getattr(self, '_diagnostic_scope', None)
        if not context or context.get('restoring') or not context.get('form_id'):
            return None
        current = self.scope_forms().get(context['form_id'])
        if current and current['episode_id']:
            return current  # Leaving after an atomic start must not edit its frozen form.
        changes = {'selected': sorted(context['selected']), 'purpose': PURPOSE_LABELS[context['purpose'].value],
            'budget': BUDGET_LABELS[context['budget'].value], 'expanded': sorted(context['expanded']),
            'offsets': context['offsets'], 'exposed': sorted([list(pair) for pair in context['exposed']]),
            'message': context['ai_message'].value or '' if 'ai_message' in context else context.get('message_value', '')}
        saved = self.scope_forms().edit(context['form_id'], changes)
        context['request_id'] = request_id(saved)
        if current and current['revision'] != saved['revision']:
            if context.get('ai_button') is not None and not context.get('ai_busy'):
                context['ai_button'].enabled = True
        return saved

    def _scope_changed(self, context):
        if self._diagnostic_scope is not context or context.get('restoring'):
            return
        try:
            self._save_diagnostic_scope(context)
        except ValueError as exc:
            context['status'].text = str(exc)

    def _restore_scope_controls(self, context, saved):
        from .retention_session_ui import PURPOSE_LABELS, BUDGET_LABELS
        context['restoring'] = True
        try:
            context.update(form_id=saved['id'], selected=set(saved['selected']), expanded=set(saved['expanded']),
                offsets=dict(saved['offsets']), exposed={tuple(row) for row in saved['exposed']},
                request_id=request_id(saved), intent=saved['intent'], message_value=saved['message'])
            context['purpose'].value = next(k for k, v in PURPOSE_LABELS.items() if v == saved['purpose'])
            context['budget'].value = next(k for k, v in BUDGET_LABELS.items() if v == saved['budget'])
            if 'ai_message' in context:
                context['ai_message'].value = saved['message']
        finally:
            context['restoring'] = False

    def scope_suggestion_controls(self, context):
        from . import ui_components as u
        context['scope_box'] = u.col(); context['scope_busy'] = False
        context['scope_button'] = u.button('按要求建议范围', lambda w: self.request_scope_suggestion(context), compact=True)
        context['ai_message'].on_change = lambda w, **kw: self._scope_changed(context)
        self.scope_ai().recover()
        saved = self.scope_forms().get(context['form_id'])
        if saved['scope_plan']:
            job = self.scope_ai().get(saved['scope_plan'])
            if job: self._show_scope_suggestion(context, job)
        return [context['scope_button'], context['scope_box']]

    def request_scope_suggestion(self, context):
        from .ai_service import AIServiceError
        if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context or context['scope_busy']:
            return
        if not self.ai_connection_status().get('ready'):
            context['status'].text = '请先配置 AI 服务，也可直接手动选择。'; return
        try:
            form = self._save_diagnostic_scope(context)
            service = self.scope_ai(); job = service.create(form, self.learning_preferences()['enabled'])
            self.scope_forms().edit(form['id'], {'scope_plan': job['id']})
            if job['status'] != 'prepared':
                return self._show_scope_suggestion(context, job)
            client = self.tutor_client(); token = service.begin(job['id'], client.model)
        except (ValueError, AIServiceError) as exc:
            context['status'].text = getattr(exc, 'user_message', str(exc)); return
        context['scope_busy'] = True; context['scope_button'].enabled = False
        context['status'].text = '正在建议范围，也可继续手动选择。'
        self.loop.create_task(self._run_scope_suggestion(context, job, client, token))

    async def _run_scope_suggestion(self, context, job, client, token):
        from .request_outcome import finish_request
        try:
            saved = await finish_request('diagnostic_scope', generate, client, job, token, self.scope_ai().finish)
        except asyncio.CancelledError:
            if self.page == 'diagnostic_scope' and self._diagnostic_scope is context:
                context['status'].text = '范围建议已中断，可以继续手动选择；不会自动重发。'
            raise
        finally:
            context['scope_busy'] = False
            if self.page == 'diagnostic_scope' and self._diagnostic_scope is context:
                context['scope_button'].enabled = True
        if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context: return
        if saved: self._show_scope_suggestion(context, saved)
        else: context['status'].text = '范围建议未完成，可以继续手动选择；不会自动重发。'

    def _show_scope_suggestion(self, context, job):
        from . import ui_components as u
        box = context['scope_box']; box.clear()
        if job['adoption']:
            box.add(u.label('建议已加入当前范围，可继续修改。', size=12, color=u.MUTED)); return
        if job['status'] != 'completed':
            box.add(u.label('上次建议未完成，可以继续手动选择。', size=12, color=u.MUTED)); return
        form = self.scope_forms().get(context['form_id'])
        try:
            if snapshot(self.diagnostics(), form, self.learning_preferences()['enabled']) != job['request']:
                raise ValueError('stale')
        except ValueError:
            box.add(u.label('要求或内容已变化，未采用旧建议。', size=12, color=u.MUTED)); return
        if not job['response']['targets']:
            box.add(u.label('暂没有合适建议，可以改写要求或手动选择。', size=12, color=u.MUTED)); return
        ids = [row['id'] for row in job['response']['targets']]
        options = {row['id']: row for row in job['request']['candidates']}
        exposed = set()
        for cid in ids:
            option = options[cid]
            if option['family'] == 'grammar':
                exposed.update((cid, a) for a in ('understanding', 'application'))
            elif not any('\u3400' <= c <= '\u9fff' for c in option['title']):
                exposed.add((cid, 'reading'))
        try:
            self.diagnostics().expose_visible(exposed)
            context['exposed'].update(exposed); self._save_diagnostic_scope(context)
        except Exception:
            context['status'].text = '显示记录未保存，请稍后重新打开。'; return
        box.add(u.label('建议检查：' + '、'.join(options[cid]['title'] for cid in ids), size=14))
        def adopt(widget=None):
            if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context: return
            try:
                self._save_diagnostic_scope(context)
                saved = self.scope_ai().adopt(job['id'], self.learning_preferences()['enabled'])
                self._restore_scope_controls(context, saved)
                self._render_diagnostic_scope()
                box.clear(); box.add(u.label('范围已更新，点击“开始检查”即可。', size=12, color=u.MUTED))
            except ValueError as exc:
                context['status'].text = str(exc)
        box.add(u.button('采用此范围', adopt, compact=True))
