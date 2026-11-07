"""A small check-only route; same prompts and grading, no implicit enrollment."""
from copy import deepcopy
from uuid import uuid4

from .navigation import page_view
from .retention_check import RetentionCheckStore
from .question_instruction_ui import instruction_widgets
from .question_support import build_prompt_block, learning_text_role, clean_prompt_preferences
from .typography import configure_text_role
from .retention_session_ui import DiagnosticUIMixin
from . import ui_components as u


class RetentionCheckUIMixin(DiagnosticUIMixin):
    def retention_checks(self):
        return RetentionCheckStore(self.store)

    @page_view()
    def show_retention_check_center(self, widget=None, **kwargs):
        active=self.retention_checks().active()
        children=[u.label('检查已学内容，或先试做新内容再决定是否加入。',size=14)]
        diagnostic = self.diagnostics().active()
        if diagnostic:
            children = [u.button('继续掌握检查', lambda w: self.continue_diagnostic(diagnostic['id']), primary=True)]
        elif active:
            count=sum(not r['completed'] for r in active['entries'])
            children.append(u.button(f'继续检查（剩 {count} 项）',lambda w:self.continue_retention_check(active['id']),primary=True))
        else:
            from .retention_schedule import ACTIVITY, dismiss
            activity = self.store.get_setting(ACTIVITY, {})
            if activity.get('cycle_id') and not activity.get('dismissed'):
                children = [u.label('隔了一段时间，可以先用几题找回节奏，也可直接继续原来的学习。', size=14),
                    u.button('由浅到深检查', lambda w: self.show_diagnostic_scope(intent='reentry'), primary=True),
                    u.button('继续原计划', lambda w: (dismiss(self.store), self.continue_learning()), compact=True)]
            finished = self.diagnostics().latest_finished()
            if finished:
                children.append(u.button('上次掌握检查结果', lambda w: self.continue_diagnostic(finished['id'])))
            completed = self.retention_checks().latest_completed()
            if completed:
                children.append(u.button('上次检查结果', lambda w: self.continue_retention_check(completed['id'])))
            children.extend([u.button('试做 3 项新单词',lambda w:self.start_retention_check('word'),primary=True),
                             u.button('试做 3 项新语法',lambda w:self.start_retention_check('grammar'))])
            children.insert(0, u.button('检查已学内容', self.show_diagnostic_scope, primary=True))
            saved_plan = self.diagnostic_ai().latest()
            if saved_plan and saved_plan['status'] != 'applied':
                children.append(u.button('上次 AI 检查建议', lambda w: self.restore_diagnostic_ai(saved_plan['id']), compact=True))
            forms = self.scope_forms().recent()
            if forms:
                children.append(u.label('尚未开始的范围选择', size=13, color=u.MUTED))
                for form in forms:
                    title = (form['message'][:40] or '检查已加入内容') + f" · {len(form['selected'])} 项"
                    children.append(u.button(title, lambda w, identity=form['id']: self.show_diagnostic_scope(form_id=identity), compact=True))
        children.append(u.button('练习设置',self.show_learning_settings,compact=True))
        self.screen('retention_center','先测再决定',*children)

    def start_retention_check(self,family):
        from .learning_content import get_learning_item
        from .learning_questions import build_learning_question
        from .learning_practice_policy import practice_candidates
        from .answer_input import system_japanese_enabled
        enabled=self.learning_preferences()['enabled'];targets=[]
        known=self.store.queries.instruction_ready_ids()
        system_japanese=system_japanese_enabled(self.store)
        enrolled={item['id'] for item in self.learning_store.list_items()}
        recent=self.retention_checks().recent_targets()
        for item in self.learning_items_available():
            if item['family']!=family or item.get('domain')=='pronunciation' or item['id'] in enrolled or item['id'] in recent:
                continue
            local=get_learning_item(item['id'])
            if local is None:continue
            for candidate in practice_candidates(local,enabled,
                    known_background_ids=known,system_japanese=system_japanese):
                q=build_learning_question(local,candidate['kind'],variant=candidate['variant'])
                if q.get('input_mode','choice') in ('choice','text','typing','free_text') and not q.get('audio_text'):
                    targets.append(item['id']);break
            if len(targets)==3:break
        if not targets:
            self.alert('当前没有可用的新内容检查题。请检查题型设置。');return
        try:
            value=self.retention_checks().start(targets,enabled,uuid4().hex)
        except ValueError as exc:
            self.alert(str(exc));return
        self.continue_retention_check(value['id'])

    def continue_retention_check(self,episode_id):
        if self.diagnostics().get(episode_id) is not None:
            return self.continue_diagnostic(episode_id)
        value=self.retention_checks().get(episode_id)
        if value is None:return self.show_retention_check_center()
        self._retention_episode_id=episode_id
        if value['status']=='completed':
            children=[u.label('本次检查已保存，学习计划未改变。',size=15)]
            from .learning_content import get_learning_item
            for row in value['entries']:
                q=row['question']
                item=get_learning_item(q['target_id'])
                if item:
                    children.append(u.button(item['title']+' · 查看与加入',lambda w,cid=q['target_id']:self.show_learning_detail(cid),multiline=True))
            children.extend([u.button('再试一组',self.show_retention_check_center,primary=True),u.button('回到今日',self.show_home)])
            self.screen('retention_done','检查结束',*children);return
        enabled=self.learning_preferences()['enabled']
        row=self.retention_checks().current(episode_id, enabled)
        if row is None:
            self.screen('retention_wait','检查已保存',u.label('剩余内容或题型暂不可用，原题和草稿会保留。'),
                u.button('练习设置',self.show_learning_settings),u.button('返回',self.show_retention_check_center));return
        context={'episode':episode_id,'row':row};self._retention_context=context
        if row['result'] is not None:return self.show_retention_check_result(context)
        q=row['question'];children=[u.button('显示答案',lambda w:self.submit_retention_check(context,'',revealed=True),compact=True),
            build_prompt_block(self,q,clean_prompt_preferences(self.store.get_setting('prompt_display_v1',{})))]
        children[-1]._feedback_fragment.update({'kind':'retention_check','episode_id':episode_id,'entry':deepcopy(row),'purpose':'check_only'})
        children.extend(instruction_widgets(u,q))
        if q.get('input_mode','choice')=='choice':
            children.extend(u.button(answer,lambda w,a=answer:self.submit_retention_check(context,a),multiline=True) for answer in q.get('choices',[]))
        else:
            def save(widget=None,**kwargs):
                if self.page=='retention_question' and self._retention_context is context:
                    self.retention_checks().save_draft(episode_id,row['id'],field.value or '')
            from .answer_input_ui import answer_controls
            from .answer_input import available, system_japanese_enabled
            field,controls=answer_controls(u,q,row['draft'],save,
                lambda:self.page=='retention_question' and self._retention_context is context,
                system_japanese=system_japanese_enabled(self.store))
            context['field']=field
            children.extend(controls)
            if available(q,system_japanese=system_japanese_enabled(self.store)):
                children.append(u.button('提交',lambda w:self.submit_retention_check(context,field.value or ''),primary=True))
            else:
                def skip(widget=None):
                    if self.page=='retention_question' and self._retention_context is context:
                        self.retention_checks().skip_unavailable_input(episode_id,row['id'])
                        self.continue_retention_check(episode_id)
                children.extend([u.label('这道旧题需要额外的日文输入。可跳过，草稿保留且不计错。',size=13,color=u.MUTED),
                    u.button('跳过这题',skip,primary=True),u.button('作答设置',self.show_learning_settings,compact=True)])
        context['status']=u.label('',size=12,color=u.MUTED);children.append(context['status'])
        self.screen('retention_question','检查掌握',*children)

    def submit_retention_check(self,context,answer,*,revealed=False):
        if self.page!='retention_question' or self._retention_context is not context:return
        from .answer_input_ui import prepare_submission
        ready, answer = prepare_submission(context['row']['question'], answer, context['status'], revealed=revealed, field=context.get('field'))
        if not ready:return
        try:
            self.retention_checks().submit(context['episode'],context['row']['id'],answer,self.learning_preferences()['enabled'],revealed=revealed)
        except ValueError as exc:
            context['status'].text=str(exc);return
        self.continue_retention_check(context['episode'])

    def show_retention_check_result(self,context):
        from .answer_feedback import build_answer_feedback
        from .answer_result_ui import entered_answer_row, distinct_explanation
        from .answer_autoplay import autoplay_answer
        row=context['row'];q=row['question'];result=row['result']
        children=[build_answer_feedback(result),u.label(q['answer'],size=20,text_role=learning_text_role(q,'answer'))]
        text=self._learning_answer_audio_text(q)
        if text:children[-1]=u.row(children[-1],u.speaker_button(lambda w:self.speak_text(text)))
        if result['answered_text'] and result['correct'] is not True and result['answered_text']!=q['answer']:
            children.append(entered_answer_row(u, result['answered_text'], text_role=learning_text_role(q, 'answer')))
        explanation = distinct_explanation(q.get('feedback'), q['answer'])
        if explanation: children.append(u.label(explanation, size=15))
        children.append(u.label('本次只作检查，不调整复习。' if not result['assisted'] else '这次使用了帮助，只记录练习。',size=12,color=u.MUTED))
        def advance(widget=None,**kwargs):
            if self.page!='retention_feedback' or self._retention_context is not context:return
            self.retention_checks().advance(context['episode'],row['id']);self.continue_retention_check(context['episode'])
        children.append(u.button('继续',advance,primary=True))
        self.screen('retention_feedback','检查结果',*children)
        self.retention_checks().expose_many([{'id':q['target_id'],'abilities':[q['ability']]}],'feedback')
        autoplay_answer(self,text,'retention:'+row['id'])
