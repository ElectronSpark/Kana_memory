"""Immediate editable diagnostic questions with durable, bounded AI replies."""
import asyncio
from copy import deepcopy

from .navigation import page_view
from .retention_dialogue import DiagnosticDialogueStore, generate
from .tutor_suggestions import suggestion_options


class DiagnosticDialogueUIMixin:
    def diagnostic_dialogue(self):
        return DiagnosticDialogueStore(self.diagnostics())

    def diagnostic_help_controls(self, context):
        from . import ui_components as u
        def open_help(widget=None):
            if self.page in {'diagnostic_question', 'diagnostic_feedback'} and self._diagnostic_context is context:
                self.show_diagnostic_dialogue(context['episode'], context['row']['id'])
        return u.button('问 AI', open_help, compact=True)

    @page_view(replace=True)
    def show_diagnostic_dialogue(self, episode, entry):
        from . import ui_components as u
        self._flush_diagnostic_time()
        service = self.diagnostic_dialogue(); service.recover()
        value = self.diagnostics().get(episode)
        if value is None: return self.show_retention_check_center()
        row = self.diagnostics()._entry(value, entry)
        if not row['presented']: return self.continue_diagnostic(episode)
        if value['status'] == 'paused':
            row = self.diagnostics().present(episode, entry)
            value = self.diagnostics().get(episode)
        context = {'episode': episode, 'row': row, 'budget': u.label('', size=12, color=u.MUTED),
                   'status': u.label('', size=12, color=u.MUTED)}
        self._diagnostic_context = context
        form = service.form(episode, entry)
        view = {'form': form, 'context': context, 'busy': False, 'history': u.col(), 'starters': u.col()}
        self._diagnostic_dialogue = view
        field = u.toga.MultilineTextInput(value=form['draft'], placeholder='直接写下你想问的问题',
                                         style=u.Pack(height=96, margin_bottom=6))
        view['field'] = field
        field._feedback_fragment = {'kind': 'diagnostic_dialogue_input', 'episode_id': episode,
                                    'entry_id': entry, 'form_id': form['id'], 'question': deepcopy(row['question'])}
        field.on_change = lambda w, **kw: self._save_diagnostic_dialogue(view)
        view['send'] = u.button('发送', lambda w: self.request_diagnostic_dialogue(view), primary=True)
        children = [context['budget'], field, view['send'], context['status']]
        if row['result'] is not None:
            children.append(u.button('讲一下这题', lambda w: self._diagnostic_explain(view), compact=True))
        children += [view['history'], view['starters'],
            u.button('返回检查', lambda w: self.diagnostic_dialogue_back(), compact=True)]
        self.screen('diagnostic_dialogue', '问 AI', *children)
        self._diagnostic_budget_label(context, value)
        self._arm_diagnostic_timer(context)
        self._render_diagnostic_dialogue(view)
        # Input is already mounted. Only safe local starter IDs may arrive here.
        if self.ai_connection_status().get('ready'):
            self.request_diagnostic_dialogue(view, mode='starters')

    def _save_diagnostic_dialogue(self, view=None):
        view = view or getattr(self, '_diagnostic_dialogue', None)
        if self.page != 'diagnostic_dialogue' or view is None or self._diagnostic_dialogue is not view: return True
        try:
            view['form'] = self.diagnostic_dialogue().save_form(view['form'], view['field'].value or '')
            return True
        except ValueError:
            view['context']['status'].text = '提问草稿未保存，请缩短输入后重试。'
            return False
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('diagnostic_dialogue', 'save', exc)
            view['context']['status'].text = '提问草稿未保存，请重试；当前输入已保留。'
            return False

    def _diagnostic_explain(self, view):
        if self.page != 'diagnostic_dialogue' or self._diagnostic_dialogue is not view: return
        if (view['field'].value or '').strip():
            view['context']['status'].text = '输入框已有问题，可直接发送。'; return
        view['field'].value = '用一句话讲解这题的关键；必要时给一个简短例子。'
        if self._save_diagnostic_dialogue(view): self.request_diagnostic_dialogue(view)

    def _render_diagnostic_dialogue(self, view):
        if self.page != 'diagnostic_dialogue' or self._diagnostic_dialogue is not view: return
        from . import ui_components as u
        service = self.diagnostic_dialogue(); f = view['form']
        jobs = service.jobs(f['episode'], f['entry'])
        view['busy'] = any(j['status'] == 'running' and j['request']['mode'] == 'ask' for j in jobs)
        view['send'].enabled = not view['busy']
        view['history'].clear(); view['starters'].clear()
        for job in [j for j in jobs if j['request']['mode'] == 'ask'][-2:]:
            view['history'].add(u.label(job['request']['message'], size=13, color=u.MUTED))
            if job['status'] == 'completed':
                try: response = service.visible(job['id'])
                except Exception:
                    view['history'].add(u.label('旧讲解依据已变化，暂不显示。', size=12, color=u.MUTED)); continue
                node = u.label(response['text'], size=15)
                node._feedback_fragment = {'kind': 'diagnostic_dialogue', 'job': deepcopy(job)}
                view['history'].add(node)
            else:
                label = '正在回答；可以返回检查。' if job['status'] == 'running' else '这次未取得回复，问题已保存；不会自动重发。'
                view['history'].add(u.label(label, size=12, color=u.MUTED))
        starter = next((j for j in jobs if j['request']['mode'] == 'starters' and j['status'] == 'completed'), None)
        if starter:
            options = suggestion_options(after_answer=starter['request']['context']['after_answer'])
            for identity in starter['response']['ids']:
                def use(widget=None, text=options[identity]):
                    if self.page != 'diagnostic_dialogue' or self._diagnostic_dialogue is not view: return
                    if (view['field'].value or '').strip():
                        view['context']['status'].text = '输入框已有问题，清空后可选用建议。'; return
                    view['field'].value = text; self._save_diagnostic_dialogue(view)
                view['starters'].add(u.button(options[identity], use, compact=True, multiline=True))

    def request_diagnostic_dialogue(self, view, *, mode='ask'):
        if self.page != 'diagnostic_dialogue' or self._diagnostic_dialogue is not view: return
        if mode == 'ask' and view['busy']: return
        if not self.ai_connection_status().get('ready'):
            view['context']['status'].text = '请先配置 AI 服务；仍可返回本地检查。'; return
        if not self._save_diagnostic_dialogue(view): return
        try:
            client = self.tutor_client()
            job = self.diagnostic_dialogue().create_dialogue(view['form'], mode)
            if job['status'] != 'prepared':
                self._render_diagnostic_dialogue(view); return
            token = self.diagnostic_dialogue().begin(job['id'], client.model)
        except Exception:
            if mode == 'ask': view['context']['status'].text = '本轮 AI 暂不可用或额度已用完，问题已保留。'
            return
        if mode == 'ask':
            view['busy'] = True; view['send'].enabled = False
            view['context']['status'].text = '正在回答；可继续输入或返回检查。'
        self.loop.create_task(self._run_diagnostic_dialogue(view, job, client, token))

    async def _run_diagnostic_dialogue(self, view, job, client, token):
        from .request_outcome import finish_request
        try:
            saved = await finish_request('diagnostic_dialogue', generate, client, job, token, self.diagnostic_dialogue().finish)
        except asyncio.CancelledError:
            current = getattr(self, '_diagnostic_dialogue', None)
            if (job['request']['mode'] == 'ask' and self.page == 'diagnostic_dialogue'
                    and current and current['form']['id'] == view['form']['id']):
                current['context']['status'].text = '回复已中断，问题已保存；不会自动重发。'
            raise
        finally:
            if job['request']['mode'] == 'ask':
                view['busy'] = False
                current = getattr(self, '_diagnostic_dialogue', None)
                if (self.page == 'diagnostic_dialogue' and current
                        and current['form']['id'] == view['form']['id']):
                    current['busy'] = False
                    current['send'].enabled = True
        current = getattr(self, '_diagnostic_dialogue', None)
        if self.page != 'diagnostic_dialogue' or current is None or current['form']['id'] != view['form']['id']: return
        # Foreground restoration may replace widgets while this call is alive.
        # Refresh the matching thread, preserving its newer editable draft.
        view = current
        if job['request']['mode'] == 'ask':
            view['send'].enabled = True
            view['context']['status'].text = '' if saved and saved['status'] == 'completed' else '回复未完成，问题已保存。'
            # Never erase text typed while the network request was pending.
            if saved and saved['status'] == 'completed' and view['form']['revision'] == job['request']['form_revision']:
                view['field'].value = ''; self._save_diagnostic_dialogue(view)
        self._render_diagnostic_dialogue(view)

    def diagnostic_dialogue_back(self):
        view = getattr(self, '_diagnostic_dialogue', None)
        if self.page != 'diagnostic_dialogue' or view is None: return
        if not self._save_diagnostic_dialogue(view): return
        self._flush_diagnostic_time()
        self.continue_diagnostic(view['form']['episode'])
