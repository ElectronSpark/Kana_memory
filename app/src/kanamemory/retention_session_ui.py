"""Native diagnostic navigation over the durable session service."""
from copy import deepcopy
import asyncio
from time import monotonic
from uuid import uuid4

from .navigation import page_view
from .answer_result_ui import answer_text
from .retention_session import DiagnosticStore, BUDGETS, LIVE
from .question_presentation import question_instruction
from .question_support import build_prompt_block, clean_prompt_preferences, learning_text_role
from .retention_ai_ui import DiagnosticAIUIMixin
from .retention_dialogue_ui import DiagnosticDialogueUIMixin

DIAGNOSTIC_PAGES = {'diagnostic_scope', 'diagnostic_question', 'diagnostic_feedback', 'diagnostic_wait', 'diagnostic_done', 'diagnostic_dialogue'}
RUNNING_PAGES = {'diagnostic_question', 'diagnostic_feedback', 'diagnostic_dialogue'}
PURPOSE_LABELS = {'只检查，不改复习间隔': 'check_only', '用有效作答调整复习': 'check_and_adjust'}
BUDGET_LABELS = {'快速 · 6 项 / 5 分钟': 'quick', '深入 · 12 项 / 10 分钟': 'deep'}


def ui():
    from . import ui_components as app
    return app


def result_view(result):
    view = deepcopy(result)
    if result.get('assessment', {}).get('assessment_eligible'):
        view.update(result.get('observed_judgement', {}))
    return view


class DiagnosticUIMixin(DiagnosticAIUIMixin, DiagnosticDialogueUIMixin):
    def diagnostics(self):
        return DiagnosticStore(self.store, self.learning_store, repo=self.repo, examples=self.example_store())

    def _diagnostic_scope_items(self):
        rows = self.store.queries.diagnostic_scope_rows()
        return [dict(row) for row in rows]

    @page_view()
    def show_diagnostic_scope(self, widget=None, *, scope_ids=None, intent='active', message='', form_id=None, **kwargs):
        if self.page == 'diagnostic_scope': self._save_diagnostic_scope()
        u = ui(); active = self.diagnostics().active()
        if active: return self.continue_diagnostic(active['id'])
        saved = self.scope_forms().get(form_id) if form_id else None
        if saved is None and scope_ids is None and not message and intent == 'active':
            saved = next((v for v in self.scope_forms().recent() if v['intent'] == intent), None)
        if saved and saved['episode_id']: return self.continue_diagnostic(saved['episode_id'])
        if saved:
            scope_ids, intent, message = saved['restriction'], saved['intent'], saved['message']
        items = self._diagnostic_scope_items()
        if scope_ids is not None: items = [x for x in items if x['content_id'] in scope_ids]
        # Alternate stronger and weaker old items across vocabulary/grammar.
        # This is transparent sampling, not an inferred new mastery score.
        pools = []
        for family in ('word', 'grammar'):
            pool = sorted((x for x in items if x['family'] == family), key=lambda x: (x['reviews'] > 0, x['stability'], x['content_id']))
            ordered = []
            while pool:
                ordered.append(pool.pop())
                if pool: ordered.append(pool.pop(0))
            pools.append(ordered)
        recommended = []
        while any(pools):
            for pool in pools:
                if pool: recommended.append(pool.pop(0)['content_id'])
        context = {'items': items, 'selected': set(recommended[:6]), 'recommended': recommended,
                   'expanded': set(), 'exposed': set(), 'request_id': uuid4().hex, 'intent': intent, 'offsets': {}}
        self._diagnostic_scope = context
        purpose = u.selection(items=list(PURPOSE_LABELS), value=next(iter(PURPOSE_LABELS)))
        budget = u.selection(items=list(BUDGET_LABELS), value=next(iter(BUDGET_LABELS)))
        context.update(purpose=purpose, budget=budget, tree=u.col(), status=u.label('', size=12, color=u.MUTED))
        context['count'] = u.label('', size=14)
        saved = saved or self.scope_forms().create(restriction=scope_ids, intent=intent, selected=recommended[:6], message=message)
        self._restore_scope_controls(context, saved)
        def change_budget(widget=None, **kw):
            if self._diagnostic_scope is not context or context.get('restoring'): return
            self._scope_changed(context)
            self._render_diagnostic_scope()
        budget.on_change = change_budget
        purpose.on_change = lambda w, **kw: self._scope_changed(context)
        self._render_diagnostic_scope()
        ai_controls = self.diagnostic_ai_controls(context, message=message)
        self.screen('diagnostic_scope', '检查掌握情况', purpose, budget, context['count'], context['tree'],
            context['status'], u.button('开始检查', lambda w: self.start_diagnostic(context), primary=True),
            *ai_controls,
            u.button('返回', self.show_retention_check_center))
        if saved['ai_plan']:
            job = self.diagnostic_ai().get(saved['ai_plan'])
            if job and job['status'] == 'completed':
                self._show_diagnostic_ai_result(context, job, self._diagnostic_ai_values(context))

    def _render_diagnostic_scope(self):
        u = ui(); context = self._diagnostic_scope; box = context['tree']; box.clear()
        limit = BUDGETS[BUDGET_LABELS[context['budget'].value]][0]
        context['count'].text = f"已选 {len(context['selected'])} 项 / 最多 {limit} 项"
        missing = context['selected'] - {item['content_id'] for item in context['items']}
        if missing:
            def clear_missing(widget=None):
                context['selected'].difference_update(missing)
                self._scope_changed(context); self._render_diagnostic_scope()
            box.add(u.button(f'取消 {len(missing)} 项当前不可用的选择', clear_missing, compact=True))
        if not context['items']:
            box.add(u.label('还没有已加入的内容。可以返回，先试做新内容。', size=14)); return
        groups = {'单词': [], '语法': [], '读音规则': []}
        for item in context['items']:
            key = '读音规则' if item['domain'] == 'pronunciation' else '单词' if item['family'] == 'word' else '语法'
            groups[key].append(item)
        for title, items in groups.items():
            if not items: continue
            selected = sum(x['content_id'] in context['selected'] for x in items)
            mark = '☑' if selected == len(items) else '◩' if selected else '☐'
            def toggle(widget=None, name=title):
                if self._diagnostic_scope is not context: return
                if name in context['expanded']: context['expanded'].remove(name)
                else: context['expanded'].add(name)
                self._scope_changed(context)
                self._render_diagnostic_scope()
            box.add(u.button(f"{'▾' if title in context['expanded'] else '▸'} {mark} {title}  {selected}/{len(items)}", toggle, multiline=True))
            if title not in context['expanded']: continue
            offset = context['offsets'].get(title, 0)
            visible = items[offset:offset + 30]
            exposed = set()
            for item in visible:
                cid = item['content_id']
                if item['family'] == 'grammar':
                    exposed.update((cid, ability) for ability in ('understanding', 'application'))
                else:
                    # Seeing a spelling reveals the written answer for use /
                    # orthography, but not the meaning or an unseen reading.
                    exposed.add((cid, 'use'))
                    if not any('\u3400' <= c <= '\u9fff' or '\uf900' <= c <= '\ufaff'
                               or '\U00020000' <= c <= '\U000323af' or c in '々〆〇'
                               for c in str(item['title'])):
                        exposed.add((cid, 'reading'))
            try:
                self.diagnostics().expose_visible(exposed)
            except Exception:
                context['status'].text = '显示记录暂未保存，请重试。'
                continue
            context['exposed'].update(exposed)
            for item in visible:
                cid = item['content_id']
                switch = u.toga.Switch(str(item['title']), value=cid in context['selected'], style=u.Pack(margin_left=14, margin_bottom=6))
                def choose(widget, target=cid, **kw):
                    if self._diagnostic_scope is not context or context.get('restoring'): return
                    if widget.value and target not in context['selected'] and len(context['selected']) >= limit:
                        context['restoring'] = True
                        try: widget.value = False
                        finally: context['restoring'] = False
                        context['status'].text = '已到本轮数量上限，可取消一项或选择深入检查。'; return
                    if widget.value: context['selected'].add(target)
                    else: context['selected'].discard(target)
                    self._scope_changed(context)
                    context['count'].text = f"已选 {len(context['selected'])} 项 / 最多 {limit} 项"
                switch.on_change = choose; box.add(switch)
            if len(items) > 30:
                def move(widget=None, name=title, next_offset=offset + 30, total=len(items)):
                    context['offsets'][name] = 0 if next_offset >= total else next_offset
                    self._scope_changed(context)
                    self._render_diagnostic_scope()
                box.add(u.button('下一页' if offset + 30 < len(items) else '回到第一页', move, compact=True))
        self._scope_changed(context)

    def start_diagnostic(self, context):
        if self.page != 'diagnostic_scope' or self._diagnostic_scope is not context: return
        ids = [x['content_id'] for x in context['items'] if x['content_id'] in context['selected']]
        try:
            self._save_diagnostic_scope(context)
            value = self.diagnostics().start(ids, self.learning_preferences()['enabled'], context['request_id'],
                purpose=PURPOSE_LABELS[context['purpose'].value], budget=BUDGET_LABELS[context['budget'].value],
                intent=context['intent'], exposed_units=context['exposed'],
                _after_start=lambda db, episode: self.scope_forms().started(context['form_id'], episode))
        except ValueError as exc:
            context['status'].text = str(exc); return
        self.continue_diagnostic(value['id'])

    def _stop_diagnostic_timer(self):
        handle = getattr(self, '_diagnostic_timer', None)
        if handle is not None: handle.cancel()
        self._diagnostic_timer = None

    def _flush_diagnostic_time(self, *, pause=False, at=None):
        self._save_diagnostic_input()
        start = getattr(self, '_diagnostic_tick_at', None)
        context = getattr(self, '_diagnostic_context', None)
        if start is None or context is None: return None
        elapsed = min(600000, max(0, round(((monotonic() if at is None else at) - start) * 1000)))
        value = self.diagnostics().consume(context['episode'], elapsed, pause=pause)
        self._stop_diagnostic_timer()
        self._diagnostic_tick_at = None
        return value

    def _arm_diagnostic_timer(self, context):
        self._stop_diagnostic_timer()
        if getattr(self, '_diagnostic_background', False): return
        if getattr(self, '_diagnostic_tick_at', None) is None:
            self._diagnostic_tick_at = monotonic()
        if not callable(getattr(self.loop, 'call_later', None)): return
        async def watch():
            while self.page in RUNNING_PAGES and self._diagnostic_context is context:
                await asyncio.sleep(5)
                if self.page not in RUNNING_PAGES or self._diagnostic_context is not context or self._diagnostic_background:
                    return
                now = monotonic(); previous = self._diagnostic_tick_at
                if previous is None: return
                try:
                    self._save_diagnostic_input()
                    value = self.diagnostics().consume(context['episode'], min(600000, max(0, round((now - previous) * 1000))))
                except Exception:
                    context['status'].text = '检查时间暂未保存，请稍后重试。'; continue
                self._diagnostic_tick_at = now
                self._diagnostic_budget_label(context, value)
                if value['status'] == 'budget' and context['row']['result'] is None and self.page != 'diagnostic_dialogue':
                    self._diagnostic_timer = None
                    self.continue_diagnostic(context['episode']); return
        # create_task also wakes Toga Android's cooperative event loop.
        self._diagnostic_background = False
        self._diagnostic_timer = self.loop.create_task(watch())

    def diagnostic_visibility(self, foreground, at=None):
        self._diagnostic_background = not foreground
        if foreground:
            from .retention_schedule import visit
            visit(self.store)
        if self.page not in RUNNING_PAGES: return
        if not foreground:
            self._diagnostic_pause_at = monotonic() if at is None else at
            self._stop_diagnostic_timer()
            try: self._flush_diagnostic_time(pause=True, at=self._diagnostic_pause_at)
            except Exception: self._diagnostic_context['status'].text = '暂停记录暂未保存。'
        else:
            context = getattr(self, '_diagnostic_context', None)
            if context:
                try: self._flush_diagnostic_time(pause=True, at=getattr(self, '_diagnostic_pause_at', None))
                except Exception:
                    context['status'].text = '暂停记录暂未保存，请稍后重试。'; return
                if self.page == 'diagnostic_dialogue':
                    self.show_diagnostic_dialogue(context['episode'], context['row']['id'])
                else:
                    self.continue_diagnostic(context['episode'])

    def _save_diagnostic_input(self):
        if self.page == 'diagnostic_dialogue':
            if not self._save_diagnostic_dialogue(): raise ValueError('提问草稿未保存。')
            return
        context = getattr(self, '_diagnostic_context', None)
        if self.page in RUNNING_PAGES and context and context.get('field') is not None and context['row']['result'] is None:
            self.diagnostics().save_draft(context['episode'], context['row']['id'], {'answer': context['field'].value or ''})

    def diagnostic_leaving(self, next_page=None):
        if self.page == 'diagnostic_scope':
            try: self._save_diagnostic_scope()
            except Exception as exc:
                self.alert('范围草稿未保存：' + str(exc)); return False
        if self.page not in RUNNING_PAGES: return True
        try:
            self._flush_diagnostic_time(pause=next_page not in RUNNING_PAGES)
            return True
        except Exception as exc:
            self.alert('检查进度暂未保存：' + str(exc)); return False

    def diagnostic_back(self, widget=None, **kwargs):
        if self.page == 'diagnostic_dialogue':
            self.diagnostic_dialogue_back(); return True
        if self.page in RUNNING_PAGES:
            context = self._diagnostic_context
            async def confirm():
                approved = await self.main_window.dialog(ui().toga.ConfirmDialog('暂停检查？', '题目和输入会保留，可从首页继续。'))
                if approved and self.page in RUNNING_PAGES and self._diagnostic_context is context:
                    self._diagnostic_home()
            self.loop.create_task(confirm())
            return True
        if self.diagnostic_leaving():
            self.back_stack.discard(DIAGNOSTIC_PAGES)
            self.show_retention_check_center()
        return True

    def _diagnostic_budget_label(self, context, value):
        answers = sum(r['result'] is not None for r in value['entries'])
        _, cap, milliseconds = BUDGETS[value['budget']]
        seconds = max(0, (milliseconds - value['used_ms']) // 1000)
        purpose = '只检查' if value['purpose'] == 'check_only' else '允许调整复习'
        context['budget'].text = f'{answers}/{cap} · {seconds // 60}:{seconds % 60:02d} · {purpose}'

    def diagnostic_permission_control(self, context):
        u = ui(); value = self.diagnostics().get(context['episode'])
        if value.get('material_id'):
            return u.col()
        switch = u.toga.Switch('调整复习', value=value['purpose'] == 'check_and_adjust', style=u.Pack(margin_bottom=6))
        def change(widget, **kwargs):
            if self.page not in RUNNING_PAGES or self._diagnostic_context is not context: return
            saved = False
            try:
                self._flush_diagnostic_time()
                self.diagnostics().set_purpose(context['episode'], 'check_and_adjust' if widget.value else 'check_only')
                saved = True
                self.continue_diagnostic(context['episode'])
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('diagnostic_permission', 'refresh' if saved else 'save', exc)
                context['status'].text = ('设置已保存，但界面未刷新；请返回后继续检查。' if saved else
                                          '设置未保存：' + str(exc))
                self._arm_diagnostic_timer(context)
        switch.on_change = change
        return switch

    def replace_diagnostic_question(self, identity):
        from .diagnostic_replacement import replace
        u = ui(); service = self.diagnostics(); value = service.get(identity)
        old = next((r for r in value['entries'] if r.get('content_retirement')), None) if value else None
        if old is None: return self.continue_diagnostic(identity)
        try:
            replace(service, identity, old['id'], self.learning_preferences()['enabled'])
        except (ValueError, KeyError) as exc:
            self.screen('diagnostic_wait', '原题与草稿已保存', u.label(str(exc)),
                u.button('返回检查', lambda w: self.continue_diagnostic(identity)),
                u.button('题型设置', self.show_learning_settings),
                u.button('结束本轮', lambda w: self.finish_diagnostic(identity)))
            return
        self.continue_diagnostic(identity)

    @page_view(replace=True)
    def continue_diagnostic(self, identity):
        u = ui()
        self._flush_diagnostic_time(pause=True)
        service = self.diagnostics(); value = service.get(identity)
        if value is None: return self.show_retention_check_center()
        row = service.current(identity, self.learning_preferences()['enabled'])
        if row is None:
            if value['status'] in LIVE:
                retired = any(r.get('content_retirement') for r in value['entries'])
                message = ('内容修订涉及的未答题已保留，尚未评分。可结束本轮后，按当前内容继续检查。'
                           if retired else '剩余内容依据或题型暂不可用，题目和草稿已保留。')
                self.screen('diagnostic_wait', '检查已保存', u.label(message),
                    *([u.button('替换待核题并继续', lambda w: self.replace_diagnostic_question(identity), primary=True)] if retired else []),
                    u.button('题型设置', self.show_learning_settings), u.button('暂停', self.show_retention_check_center),
                    u.button('结束本轮', lambda w: self.finish_diagnostic(identity)))
                return
            return self.show_diagnostic_summary(value)
        row = service.present(identity, row['id'])
        context = {'episode': identity, 'row': row, 'budget': u.label('', size=12, color=u.MUTED), 'status': u.label('', size=12, color=u.MUTED)}
        self._diagnostic_context = context
        self._diagnostic_budget_label(context, service.get(identity))
        if row['result'] is not None:
            self.show_diagnostic_result(context)
            self._arm_diagnostic_timer(context)
            return
        q = row['question']
        if q.get('input_mode') == 'multi_cloze':
            from .multi_cloze_ui import prompt_view
            prompt = prompt_view(self, u, q, row['draft'],
                clean_prompt_preferences(self.store.get_setting('prompt_display_v1', {})))
        else:
            prompt = build_prompt_block(self, q, clean_prompt_preferences(self.store.get_setting('prompt_display_v1', {})))
        prompt._feedback_fragment.update({'kind': 'diagnostic', 'episode_id': identity, 'entry': deepcopy(row), 'purpose': row['purpose']})
        children = [context['budget'], self.diagnostic_permission_control(context), u.row(u.button('显示答案', lambda w: self.submit_diagnostic(context, '', revealed=True), compact=True), self.diagnostic_help_controls(context)), prompt]
        instruction = question_instruction(q)
        if instruction: children.append(u.label(instruction, size=13, color=u.MUTED))
        if q.get('audio_text'):
            async def play(widget=None):
                if self.page != 'diagnostic_question' or self._diagnostic_context is not context: return
                try:
                    await self.platform.play(q['audio_text'])
                    success = True
                except Exception:
                    success = False
                if self.page == 'diagnostic_question' and self._diagnostic_context is context:
                    if success:
                        try: service.save_draft(identity, row['id'], {'audio_ok': True})
                        except Exception:
                            context['status'].text = '播放记录未保存，请重试。'
                    else: context['status'].text = '播放未完成，可重试。'
            children.append(u.speaker_button(play))
        if q.get('input_mode') in {'pitch_fill', 'pitch_identify'}:
            children.append(self.learning_pitch_controls(q, row['draft'],
                on_change=lambda draft: service.save_draft(identity, row['id'], draft),
                on_submit=lambda answer: self.submit_diagnostic(context, answer),
                is_active=lambda: self.page == 'diagnostic_question' and self._diagnostic_context is context))
        elif q.get('input_mode') == 'multi_cloze':
            from .multi_cloze_ui import controls as cloze_controls

            def submit_cloze(answer):
                self.submit_diagnostic(context, answer)
                return self.page != 'diagnostic_question' or self._diagnostic_context is not context

            children.append(cloze_controls(self, u, q, row['draft'],
                lambda draft: service.save_draft(identity, row['id'], draft),
                submit_cloze,
                lambda: self.page == 'diagnostic_question' and self._diagnostic_context is context, prompt=prompt))
        elif q.get('input_mode') == 'reorder':
            children.append(self.diagnostic_word_blocks(context))
        elif q.get('input_mode', 'choice') == 'choice':
            children.extend(u.button(answer, lambda w, a=answer: self.submit_diagnostic(context, a), multiline=True) for answer in q.get('choices', []))
        else:
            from .answer_input_ui import answer_controls
            from .answer_input import system_japanese_enabled
            def save(widget=None, **kw):
                if self.page != 'diagnostic_question' or self._diagnostic_context is not context: return
                try: service.save_draft(identity, row['id'], {'answer': field.value or ''})
                except Exception as exc: context['status'].text = '输入未保存：' + str(exc)
            field, controls = answer_controls(u, q, row['draft'].get('answer', ''), save,
                lambda: self.page == 'diagnostic_question' and self._diagnostic_context is context,
                system_japanese=system_japanese_enabled(self.store))
            context['field'] = field; children.extend(controls)
            children.append(u.button('提交', lambda w: self.submit_diagnostic(context, field.value or ''), primary=True))
        children.extend([context['status'], u.button('暂停检查', self.diagnostic_back, compact=True)])
        self.screen('diagnostic_question', '检查掌握', *children)
        self._arm_diagnostic_timer(context)

    def diagnostic_word_blocks(self, context):
        u = ui(); row = context['row']; q = row['question']
        used = list(row['draft'].get('used', []))
        composed = u.label(''.join(q['choices'][i] for i in used) or '点击词块组成句子', size=20)
        buttons = []
        def edit(index=None):
            if self.page != 'diagnostic_question' or self._diagnostic_context is not context: return
            from .word_blocks import proposed_blocks
            proposed = proposed_blocks(q['choices'], {'used': used}, index)
            if proposed is None: return
            draft = {key: proposed[key] for key in ('used', 'answer')}
            try:
                self.diagnostics().save_draft(context['episode'], row['id'], draft)
            except Exception as exc:
                context['status'].text = '输入未保存：' + str(exc); return
            used[:] = proposed['used']
            composed.text = draft['answer'] or '点击词块组成句子'
            for i, button in enumerate(buttons): button.enabled = i not in used
        for index, token in enumerate(q.get('choices', [])):
            button = u.button(token, lambda w, i=index: edit(i), multiline=True, text_role='study')
            button.enabled = index not in used; buttons.append(button)
        return u.col(composed, *buttons, u.button('撤回末项', lambda w: edit(), compact=True),
                     u.button('提交', lambda w: self.submit_diagnostic(context, ''.join(q['choices'][i] for i in used)), primary=True))

    def submit_diagnostic(self, context, answer, *, revealed=False):
        if self.page != 'diagnostic_question' or self._diagnostic_context is not context: return
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(context['row']['question'], answer, context['status'], revealed=revealed)
        if not ready:return
        try:
            value = self._flush_diagnostic_time()
            if value and value['status'] == 'budget': return self.continue_diagnostic(context['episode'])
            self.diagnostics().submit(context['episode'], context['row']['id'], answer,
                                      self.learning_preferences()['enabled'], revealed=revealed)
        except Exception as exc:
            context['status'].text = str(exc); self._arm_diagnostic_timer(context); return
        self.continue_diagnostic(context['episode'])

    def show_diagnostic_result(self, context):
        from .answer_feedback import build_answer_feedback
        from .answer_result_ui import entered_answer_row, distinct_explanation
        from .answer_autoplay import autoplay_answer
        u = ui(); row = context['row']; q = row['question']; result = row['result']; viewed = result_view(result)
        text = self._learning_answer_audio_text(q)
        value = self.diagnostics().get(context['episode'])
        if value.get('material_id') and q.get('family') == 'word':
            from .material_eligibility import pretest_targets
            item = next((item for item in pretest_targets(self.store, value['material_id'],
                repo=self.repo, examples=self.example_store())
                if item['id'] == q['target_id'] and item['revision'] == q['revision']), None)
            text = item.get('reading', '') if item else ''
        from .pitch_practice import is_pitch_question
        from .learning_pitch_ui import answer_diagram
        from .pitch_practice import answer_label
        pitch = is_pitch_question(q)
        answer = (answer_diagram(self, q, answer=result['answered_text']) if pitch and viewed.get('correct') is True
                  else answer_diagram(self, q, reveal=True) if pitch
                  else u.label(answer_text(q), size=20, text_role=learning_text_role(q, 'answer')))
        def expose_answer_audio():
            abilities = {(q['target_id'], q['ability'])}
            if q.get('family') == 'word':
                abilities.add((q['target_id'], 'reading'))
            self.diagnostics().expose_visible(abilities)

        def play_answer(widget=None):
            if (self.page != 'diagnostic_feedback' or self._diagnostic_context is not context
                    or self._page_token != view_token):
                return
            try:
                expose_answer_audio()
            except Exception:
                context['status'].text = '播放记录未保存，请重试。'
                return
            self.speak_text(text)

        children = [context['budget'], self.diagnostic_permission_control(context), build_answer_feedback(viewed), u.row(answer, u.speaker_button(play_answer)) if text else answer]
        if viewed.get('correct') is not True and result['answered_text'] and result['answered_text'] != q['answer']:
            entered = answer_label(q, result['answered_text']) if pitch else answer_text(q, result['answered_text'])
            children.append(entered_answer_row(u, entered, text_role=learning_text_role(q, 'answer')))
        explanation = distinct_explanation(q.get('feedback'), answer_text(q))
        if explanation: children.append(u.label(explanation, size=15))
        formal = (result.get('assessment', {}).get('settlement_channel') == 'formal'
                  and value['purpose'] == 'check_and_adjust' and not row['credit_revoked'])
        children.append(u.label('这次是接受帮助后的练习。' if result['assisted'] else '按本次作答调整此项复习。' if formal else '检查已保存，复习间隔不变。', size=12, color=u.MUTED))
        detail = (lambda w: self.show_material_candidate_facts(value['material_id'], q['target_id'])) if q['target_id'].startswith('material-candidate:') else (lambda w: self.show_learning_detail(q['target_id']))
        children.append(u.row(u.button('详细', detail, compact=True), self.diagnostic_help_controls(context)))
        def advance(rating=None):
            if self.page != 'diagnostic_feedback' or self._diagnostic_context is not context: return
            try:
                self._flush_diagnostic_time()
                self.diagnostics().advance(context['episode'], row['id'], self.learning_preferences()['enabled'], rating=rating)
            except Exception as exc:
                context['status'].text = str(exc); self._arm_diagnostic_timer(context); return
            self.continue_diagnostic(context['episode'])
        if formal and viewed.get('correct') is True:
            children.append(u.row(*[u.button(label, lambda w, n=number: advance(n), compact=True, flex=1)
                for number, label in ((1, '再学'), (2, '困难'), (3, '记住了'), (4, '轻松'))]))
        else: children.append(u.button('继续', lambda w: advance(), primary=True))
        children.extend(self.diagnostic_mid_controls(context))
        children.extend([context['status'], u.button('暂停检查', self.diagnostic_back, compact=True)])
        self.screen('diagnostic_feedback', '检查结果', *children)
        view_token = self._page_token
        if autoplay_answer(self, text, 'diagnostic:' + row['id']):
            expose_answer_audio()

    def finish_diagnostic(self, identity):
        self._flush_diagnostic_time(pause=True)
        self.diagnostics().stop(identity)
        self.continue_diagnostic(identity)

    def show_diagnostic_summary(self, value):
        u = ui(); children = []
        covered = {r['question']['target_id'] for r in value['entries'] if r['result'] is not None}
        children.append(u.label(f"本次检查了 {len(covered)}/{len(value['scope_ids'])} 项", size=19, bold=True))
        adjusted = sum(bool((r['settlement'] or {}).get('scheduled')) for r in value['entries'])
        children.append(u.label(f'已按有效首答调整 {adjusted} 项复习。' if adjusted else '复习间隔未改变。', size=13, color=u.MUTED))
        titles = {r['content_id']: r['title'] for r in self._diagnostic_scope_items()}
        material = self.material_study().for_diagnostic(value['id']) if value['intent'] == 'material' else None
        if material:
            titles.update({row['item']['id']: row['item']['title'] for row in material.get('lessons', [])})
            from .material_eligibility import pretest_targets
            titles.update({item['id']: item['title'] for item in pretest_targets(self.store, material['id'],
                           repo=self.repo, examples=self.example_store())})
        for cid in value['scope_ids']:
            rows = [r for r in value['entries'] if r['question']['target_id'] == cid and r['result'] is not None]
            label = '尚未检查'
            if rows:
                first = rows[0]; result = result_view(first['result'])
                if not first['result'].get('assessment', {}).get('assessment_eligible'): label = '暂不确定'
                elif result.get('outcome') == 'revealed': label = '已看答案，掌握待确认'
                elif result.get('correct') is None: label = '暂不确定'
                elif not first['result']['assisted'] and result.get('correct') is True:
                    label = '认识，产出待确认' if first['question'].get('input_mode', 'choice') == 'choice' else '能独立应用'
                elif any(result_view(r['result']).get('correct') is True for r in rows): label = '帮助后完成'
                else: label = '需要补学'
            from .learning_content import get_learning_item
            title = titles.get(cid) or (get_learning_item(cid) or {}).get('title', '本次检查内容')
            target_label = u.label(str(title) + ' · ' + label, size=15)
            children.append(u.row(target_label, u.button('问 AI',
                lambda w, entry=rows[-1]['id']: self.show_diagnostic_dialogue(value['id'], entry), compact=True))
                if rows else target_label)
        for blocked in value['blocked']:
            if blocked['reason'] == 'continue_original':
                children.append(u.button('继续原来已开始的题', self.continue_learning, multiline=True))
        if material:
            children.append(u.button('回到这批材料', lambda w: self.show_material_task(material['id']), primary=True))
            if self.material_study().teaching_targets(material['id']):
                children.append(u.button('看相关讲解', lambda w: self.show_material_study(material['id'])))
            children.append(u.button('回到今日', self._diagnostic_home))
        else:
            children.extend([u.button('继续原练习', self.continue_learning, primary=True),
                             u.button('检查其他内容', self.show_retention_check_center),
                             u.button('回到今日', self._diagnostic_home)])
        self.screen('diagnostic_done', '检查结束', *children)

    def _diagnostic_home(self, widget=None):
        if not self.diagnostic_leaving(): return
        self._returning_home = True
        try: self.show_home()
        finally: self._returning_home = False
