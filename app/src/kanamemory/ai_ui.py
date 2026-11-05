"""Explicit, resumable AI authoring and practice, separate from SRS queues."""
from __future__ import annotations

from .material_request_outcome import record_failure, refresh_failure

import asyncio
from copy import deepcopy
from datetime import datetime
from hashlib import sha256
import json
from uuid import uuid4

from .ai_service_ui import AIServiceUIMixin
from .tutor_ui import TutorUIMixin
from .retention_check_ui import RetentionCheckUIMixin
from .material_study_ui import MaterialStudyUIMixin
from .material_reference_ui import MaterialReferenceUIMixin
from .ai_ui_state import AI_FORM_SETTING, AI_PREVIEW_SETTING
from .navigation import page_view
from .question_presentation import question_instruction
from .question_support import build_prompt_block, clean_prompt_preferences, learning_text_role, prompt_audio_text
from .typography import configure_text_role

AI_PRACTICE_PAGES = {"ai_question", "ai_feedback", "ai_practice_done", "retention_question", "retention_feedback", "retention_done", "retention_wait"}


def ui():
    from . import ui_components as app
    return app


class AIUIMixin(MaterialReferenceUIMixin, MaterialStudyUIMixin, RetentionCheckUIMixin, TutorUIMixin, AIServiceUIMixin):
    def material_workflow(self):
        from .material_workflow import MaterialWorkflowStore
        service = MaterialWorkflowStore(self.store)
        service.recover_interrupted()
        return service

    def material_verification(self):
        from .material_verification import MaterialVerificationStore
        return MaterialVerificationStore(self.store)

    def material_revision(self):
        from .material_revision import MaterialRevisionStore
        return MaterialRevisionStore(self.store)

    @page_view(detail=True, refresh=True)
    def show_material_usage(self, proposal_id):
        from .material_usage import read, allowed, flags, MaterialUsageStore
        from .material_workflow import digest
        u = ui(); service = self.ai_material_store(read_only=True)
        proposal = service.get(proposal_id)
        if not proposal:
            self.alert('材料已不可用。'); return
        state = read(self.store); permission = flags(state, proposal)
        source = u.switch('整份来源', value=True)
        controls = {group: {} for group in ('items', 'examples', 'references')}
        children = [u.label('停止使用不会删除原材料或学习记录。其他有效来源仍可使用。', size=14), source]
        children[0]._feedback_fragment = {'kind': 'material_usage', 'proposal_id': proposal_id, 'generation': state['generation']}
        objects = {'items': proposal['items'], **{g: proposal['example_pack'][g] for g in ('examples', 'references')}}
        labels = {'items': '学习项', 'examples': '例句', 'references': '关联'}
        for group, rows in objects.items():
            for row in rows:
                name = row.get('title') or row.get('text') or '／'.join(s['quote'] for s in row.get('spans', []))
                active = allowed(state, proposal, group, row['id'], _flags=permission)
                control = u.switch(labels[group] + ' · ' + name + ('  ✓' if active else '  −'), value=False)
                def select_part(widget, **kwargs):
                    if widget.value: source.value = False
                control.on_change = select_part
                controls[group][row['id']] = control; children.append(control)
        def select_source(widget, **kwargs):
            if widget.value:
                for group in controls.values():
                    for control in group.values(): control.value = False
        source.on_change = select_source
        managed = state['managed']['examples']
        if any(proposal['decisions']['examples'][r['id']] == 'kept' and r['id'] not in managed
               for r in proposal['example_pack']['examples']):
            children.append(u.label('部分旧例句缺少来源归属记录，其例句库副本会保留。', size=12, color=u.MUTED))
        request_ids = {False: uuid4().hex, True: uuid4().hex}
        def change(enabled):
            selection = {'source': bool(source.value), **{g: [cid for cid, c in rows.items() if c.value] for g, rows in controls.items()}}
            try:
                MaterialUsageStore(self.store).change(proposal_id, selection, enabled=enabled,
                    request_id=request_ids[enabled], expected_generation=state['generation'], expected_usage_sha256=digest(state))
            except ValueError as exc:
                self.alert(str(exc)); return
            self.show_material_usage(proposal_id)
        children.extend([u.button('停止使用所选', lambda w: change(False)),
                         u.button('恢复使用所选', lambda w: change(True)),
                         u.button('管理例句库副本', lambda w: self.show_material_source_copies(proposal_id), compact=True),
                         u.button('返回材料', lambda w: self.show_ai_proposal(proposal_id))])
        self.screen('material_usage', '材料使用设置', *children)

    @page_view(detail=True, refresh=True)
    def show_material_source_copies(self, proposal_id, *, offset=0):
        from .material_source_resolution import MaterialSourceStore
        from .material_usage import read, allowed
        from .material_workflow import digest
        u = ui(); service = MaterialSourceStore(self.store)
        try: candidates = service.candidates(proposal_id)
        except ValueError as exc: self.alert(str(exc)); return
        state = read(self.store)
        known = {p['id']: p for p in self.ai_material_store(read_only=True).list_proposals()}
        children = [u.label('只决定副本今后如何使用，不判断内容是否正确。随包与词库中的同句不受影响。', size=14)]
        controls, shown = {}, candidates[offset:offset + 12]
        for row in shown:
            obj = row['snapshot']
            name = obj.get('text') or '／'.join(s['quote'] for s in obj.get('spans', []))
            control = u.switch(('正文 · ' if row['group'] == 'examples' else '关联 · ') + name, value=False)
            controls[(row['group'], row['id'])] = control
            status = '尚未确定副本用法' if row['external'] is None else '独立保留副本' if row['external'] else '随来源使用'
            card = [control, u.label(status, size=13, color=u.MUTED)]
            for owner in row['owners']:
                proposal = known[owner['id']]
                label = datetime.fromisoformat(proposal['created_at_utc']).astimezone().strftime('%m-%d %H:%M')
                label += ' · ' + ('仍在使用' if allowed(state, proposal, row['group'], row['id']) else '已停止使用')
                card.append(u.button(label, lambda w, pid=proposal['id']: self.show_ai_proposal(pid), compact=True))
            children.append(u.col(*card, padding=8, margin_bottom=6, background_color=u.ENTRY_BACKGROUNDS[len(children) % 3]))
        if shown:
            children.append(u.label('“随来源使用”会在列出的来源全部停用后隐藏该副本；“独立保留”则继续显示。', size=13))
            request_ids = {False: uuid4().hex, True: uuid4().hex}
            def decide(external):
                chosen = [row for row in shown if controls[(row['group'], row['id'])].value]
                if not chosen: self.alert('请选择需要处理的副本。'); return
                expected = {'proposal_id': proposal_id, 'generation': state['generation'], 'usage_sha256': digest(state), 'rows': chosen}
                try: service.decide(expected, external=external, request_id=request_ids[external])
                except ValueError as exc: self.alert(str(exc)); return
                self.show_material_source_copies(proposal_id, offset=offset)
            children.extend([u.button('独立保留所选副本', lambda w: decide(True)),
                             u.button('所选副本随来源使用', lambda w: decide(False))])
        else: children.append(u.label('没有需要处理的个人副本。', size=14))
        if offset: children.append(u.button('上一页', lambda w: self.show_material_source_copies(proposal_id, offset=max(0, offset - 12))))
        if offset + 12 < len(candidates): children.append(u.button('下一页', lambda w: self.show_material_source_copies(proposal_id, offset=offset + 12)))
        children.append(u.button('返回使用设置', lambda w: self.show_material_usage(proposal_id)))
        children[0]._feedback_fragment = {'kind': 'material_source_copies', 'proposal_id': proposal_id, 'rows': shown, 'usage_sha256': digest(state)}
        self.screen('material_source_copies', '例句库副本', *children)

    @page_view(detail=True, refresh=True)
    def show_material_revision(self, identity):
        from .material_revision import conflict_claims
        u = ui()
        report = self.current_material_verification(identity)
        record = self.material_revision().get(identity)
        children = [u.label("只针对选定分歧修订一次，原材料保留。", size=14)]
        children[0]._feedback_fragment = {'kind': 'material_revision', 'workflow_id': identity,
            'request_sha256': record['request_sha256'] if record else None,
            'child_id': record['child_id'] if record else None}
        if record:
            issues = {row['claim_id']: row for row in record['request']['issues']}
            for change in (record['response'] or {}).get('changes', []):
                issue = issues[change['claim_id']]
                children.append(u.col(u.label(issue['context'].get('title') or issue['context'].get('text') or '关联内容', bold=True),
                    u.label(change['note'], size=14),
                    u.label('原内容：' + self._material_field_text(issue['before']), size=13, color=u.MUTED),
                    u.label('建议：' + self._material_field_text(json.loads(change['value_json'])), size=15),
                    padding=8, margin_bottom=6, background_color=u.ENTRY_BACKGROUNDS[len(children) % 3]))
            if record['child_id']:
                children.append(u.button('查看修订稿', lambda w: self.show_material_task(record['child_id']), primary=True))
            elif record['response'] is not None:
                children.append(u.label('本次没有得到可用修订，原材料仍可保留。', size=14))
            else:
                children.append(u.label('这次修订调用已记录；请在材料任务中查看状态。', size=14))
        else:
            controls = {}
            for index, (claim, finding) in enumerate(conflict_claims(report)):
                if index >= 32:
                    children.append(u.label('还有其他待核字段；本次仅处理所选分歧。', size=13)); break
                control = u.toga.Switch(self._material_field_text(claim['value']), value=index < 8,
                    style=u.Pack(margin_bottom=4))
                controls[claim['id']] = control
                children.append(u.col(control, u.label(finding['note'], size=13), padding=6))
            if controls:
                children.append(u.button('生成一次修订稿', lambda w: self.start_material_revision(identity,
                    [cid for cid, control in controls.items() if control.value]), primary=True))
            else: children.append(u.label('没有可修订的具体分歧。', size=14))
        children.append(u.button('返回原材料', lambda w: self.show_material_task(identity)))
        self.screen('material_revision', '材料修订', *children)

    @staticmethod
    def _material_field_text(value):
        if isinstance(value, dict):
            return '\n'.join(AIUIMixin._material_field_text(v) for v in value.values())
        if isinstance(value, list):
            return '\n'.join(AIUIMixin._material_field_text(v) for v in value)
        return '待核' if value is None else str(value)

    def start_material_revision(self, identity, claim_ids):
        if getattr(self, '_ai_request_operation', None) is not None:
            self.alert('请等待当前材料任务完成。'); return
        if not self.ai_connection_status().get('ready'):
            self.alert('请先配置 AI 服务。'); return
        try:
            record = self.material_revision().begin(identity, claim_ids, self.ai_material_store(read_only=True),
                repo=self.repo, examples=self.example_store(), budget_context=self.material_budget_context())
        except ValueError as exc:
            self.alert(str(exc)); return
        operation = object()
        run = {'id': identity, 'token': record['token'], 'operation': operation, 'stage': 'revision'}
        self._ai_request_operation, self._ai_material_run = operation, run
        self.show_material_task(identity)
        self._ai_request_task = self.loop.create_task(self._run_material_revision(record, run, self._page_token))

    async def _run_material_revision(self, record, run, page_token):
        response = None
        provider = None
        completed = False
        phase = 'save'
        try:
            provider = self.ai_client()
            from .material_budget import authorize, settle
            authorize(self.store, run['id'], run['token'], provider)
            phase = 'request'
            response = await asyncio.to_thread(provider.revise_material, deepcopy(record['request']))
            phase = 'save'
            settle(self.store, run['id'], run['token'], getattr(provider, 'last_usage', {}))
            job = self.material_workflow().get(run['id'])
            parent = self.ai_material_store(read_only=True).get(job['proposal_id'])
            reference_ids = self._ai_material_reference_ids(parent)
            issues = {row['claim_id']: row for row in record['request']['issues']}
            for change in response.get('changes', []):
                if issues.get(change.get('claim_id'), {}).get('facet') == 'target_id':
                    target = json.loads(change['value_json'])
                    if isinstance(target, str): reference_ids.add(target)
            result = self.material_revision().complete(run['id'], run['token'], response,
                self.ai_material_store(reference_ids=reference_ids), model=getattr(provider, 'model', 'configured-provider'),
                repo=self.repo, examples=self.example_store())
            completed = bool(result)
            if result and self.page == 'material_task' and self._page_token == page_token:
                self.show_material_revision(run['id'])
        except asyncio.CancelledError:
            if not completed:
                record_failure(self.material_workflow, run, code='revision_interrupted', delivery_unknown=True, operation='material_revision')
            if self.page == 'material_task' and self._page_token == page_token:
                refresh_failure(self.show_material_task, run['id'], self.alert, operation='material_revision')
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('material_revision', 'refresh' if completed else phase, exc)
            if not completed:
                record_failure(self.material_workflow, run, code='revision_unfinished', delivery_unknown=response is None, operation='material_revision')
            if completed and self.page == 'material_task' and self._page_token == page_token:
                self.alert('修订结果已保存，但页面未刷新；请重新打开材料任务。')
            elif self.page == 'material_task' and self._page_token == page_token:
                refresh_failure(self.show_material_task, run['id'], self.alert, operation='material_revision')
        finally:
            try:
                if provider is not None:
                    from .material_budget import settle
                    settle(self.store, run['id'], run['token'], getattr(provider, 'last_usage', {}))
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error("material_revision", "save", exc)
            finally:
                if getattr(self, '_ai_request_operation', None) is run['operation']:
                    self._ai_request_operation = None; self._ai_material_run = None
                    if self.page == 'ai_form': self._set_ai_form_busy(False)

    def current_material_verification(self, identity):
        job = self.material_workflow().get(identity)
        proposal = self.ai_material_store(read_only=True).get(job["proposal_id"]) if job and job["proposal_id"] else None
        return (self.material_verification().current(identity, proposal, repo=self.repo, examples=self.example_store())
                if proposal else None)

    def check_material_locally(self, identity):
        job = self.material_workflow().get(identity)
        proposal = self.ai_material_store(read_only=True).get(job["proposal_id"]) if job else None
        if proposal is None:
            self.alert("该批材料已不可用。")
            return None
        report = self.material_verification().prepare(job, proposal, repo=self.repo, examples=self.example_store())
        self.show_material_task(identity)
        return report

    def start_material_review(self, identity):
        if getattr(self, "_ai_request_operation", None) is not None:
            self.alert("请等待当前材料任务完成。")
            return
        if not self.ai_connection_status().get("ready"):
            self.alert("请先配置 AI 服务。")
            return
        report = self.material_verification().get(identity) or self.check_material_locally(identity)
        if report is None:
            return
        try:
            from .material_reference import refresh_unsent_review
            refresh_unsent_review(self.store, identity)
            report = self.material_verification().get(identity)
            attempt = self.material_workflow().begin(identity, "review", budget_context=self.material_budget_context())
        except ValueError as exc:
            self.alert(str(exc))
            return
        operation = object()
        run = {"id": identity, "token": attempt, "operation": operation, "stage": "review"}
        self._ai_request_operation, self._ai_material_run = operation, run
        self.show_material_task(identity)
        token = self._page_token
        self._ai_request_task = self.loop.create_task(self._run_material_review(report, run, token))

    async def _run_material_review(self, report, run, page_token):
        response = None
        provider = None
        completed = False
        phase = 'save'
        try:
            provider = self.ai_client()
            from .material_budget import authorize, settle
            authorize(self.store, run['id'], run['token'], provider)
            phase = 'request'
            response = await asyncio.to_thread(provider.verify_material, deepcopy(report["review_request"]))
            phase = 'save'
            settle(self.store, run['id'], run['token'], getattr(provider, 'last_usage', {}))
            result = self.material_workflow().complete_review(run["id"], run["token"], response,
                model=getattr(provider, "model", "configured-provider"))
            completed = bool(result)
            if result and self.page == "material_task" and self._page_token == page_token:
                self.show_material_task(run["id"])
        except asyncio.CancelledError:
            if not completed and getattr(self, "_ai_material_run", None) is run:
                record_failure(self.material_workflow, run, code="review_interrupted", delivery_unknown=True, operation="material_review")
            if self.page == "material_task" and self._page_token == page_token:
                refresh_failure(self.show_material_task, run["id"], self.alert, operation="material_review")
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('material_review', 'refresh' if completed else phase, exc)
            if not completed:
                record_failure(self.material_workflow, run, code="review_unfinished", delivery_unknown=response is None, operation="material_review")
            if completed and self.page == 'material_task' and self._page_token == page_token:
                self.alert('核验结果已保存，但页面未刷新；请重新打开材料任务。')
            elif self.page == "material_task" and self._page_token == page_token:
                refresh_failure(self.show_material_task, run["id"], self.alert, operation="material_review")
        finally:
            try:
                if provider is not None:
                    from .material_budget import settle
                    settle(self.store, run['id'], run['token'], getattr(provider, 'last_usage', {}))
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error("material_review", "save", exc)
            finally:
                if getattr(self, "_ai_request_operation", None) is run["operation"]:
                    self._ai_request_operation = None
                    self._ai_material_run = None
                    if self.page == "ai_form":
                        self._set_ai_form_busy(False)

    def clear_ai_secret_input(self):
        control = getattr(self, "ai_key_input", None)
        if control is not None:
            control.value = ""

    def ai_material_store(self, *, reference_ids=(), read_only=False):
        from .ai_materials import AIMaterialStore
        if read_only:
            return AIMaterialStore(self.store)
        return AIMaterialStore(self.store,
            self.example_targets_available(reference_ids=set(reference_ids)), self.example_store())

    @staticmethod
    def _ai_material_reference_ids(material):
        """Resolve only supplied identities; stage still validates the full payload."""
        pack = material.get("example_pack") if isinstance(material, dict) else None
        refs = pack.get("references") if isinstance(pack, dict) else None
        return {ref["target_id"] for ref in refs if isinstance(ref, dict)
                and isinstance(ref.get("target_id"), str)} if isinstance(refs, list) else set()

    def ai_practice_store(self):
        from .ai_practice_store import AIPracticeStore
        return AIPracticeStore(self.store)

    def _ai_scope(self, *, request=False):
        from .ai_questions import build_learning_scope
        return build_learning_scope(self.learning_store, self.learning_preferences()["enabled"],
                                    limit=24 if request else None)

    def _ai_target(self, item_id):
        if not item_id:
            return None
        saved = self.learning_store.get_item(item_id)
        if saved:
            # Keep the taught content version, without copying dates, checkpoints
            # or scheduler units into an example-generation request.
            for key in ("stage", "introduction_id", "checkpoint", "exposure_group",
                        "introduced_at_utc", "retrieval_at_utc", "units"):
                saved.pop(key, None)
            return saved
        return next((item for item in
            self.example_targets_available(reference_ids={item_id}) if item["id"] == item_id), None)

    def resume_ai_input(self, widget=None, **kwargs):
        from .ai_ui_state import validate_ai_form_value
        try:
            value = validate_ai_form_value(self.store.get_setting(AI_FORM_SETTING, {})) or {}
        except ValueError:
            self.alert("上次输入暂不可用，请重新选择任务。")
            return
        last = value.get("last")
        if last:
            self.show_ai_form(last["mode"], target_id=last["target_id"], input_id=last.get("input_id"))

    def _ai_unfinished_input(self):
        from .ai_ui_state import validate_ai_form_value
        try:
            value = validate_ai_form_value(self.store.get_setting(AI_FORM_SETTING, {})) or {}
        except ValueError:
            return False
        saved = value.get("drafts", {}).get(value.get("last", {}).get("key"), {})
        return bool(saved and not saved.get("request_finished") and
                    (saved.get("source_text", "").strip() or saved.get("topic", "").strip() or saved.get("request_id")))

    @page_view()
    def show_ai_center(self, widget=None, **kwargs):
        u = ui()
        scope = self._ai_scope()
        counts = self.ai_practice_store().counts(self.learning_preferences()["enabled"], scope)
        pending = self.ai_material_store(read_only=True).list_proposals(status="pending")
        jobs = self.material_workflow().list()
        children = [u.button("针对性练习", lambda w: self.show_tutor_center(), primary=True)]
        shared = self.shared_material_rows()
        if shared or getattr(self.platform, "name", None) == "android":
            children.append(u.button(f"分享文本（{len(shared)} 份）" if shared else "分享文本", self.show_material_share_inbox))
        if jobs:
            children.append(u.button("材料任务", self.show_material_tasks))
        children.append(u.button("先测再决定",self.show_retention_check_center))
        if counts["remaining"]:
            children.append(u.button(f"继续 AI 练习（剩 {counts['remaining']} 题）", self.continue_ai_practice, primary=True))
        preview = self.store.get_setting(AI_PREVIEW_SETTING, None)
        if preview:
            children.append(u.button("上次生成的题目", self.show_ai_question_preview))
        if self._ai_unfinished_input():
            children.append(u.button("继续上次输入", self.resume_ai_input))
        children.extend([
            u.button("按已学内容出题", lambda w: self.show_ai_form("questions"), primary=not counts["remaining"]),
            u.button("生成例句", self.show_ai_target_picker),
            u.button("解析日文", lambda w: self.show_ai_form("analyze")),
            u.button(f"待确认内容（{len(pending)} 批）", self.show_ai_inbox),
            u.label("新内容由你决定保留或舍弃，保留后可另行加入学习。", size=12, color=u.MUTED),
            u.button("AI 服务", self.show_ai_service_settings, compact=True),
        ])
        self.screen("ai_center", "AI 学习助手", *children)

    @page_view()
    def show_ai_target_picker(self, widget=None, *, query="", offset=0, **kwargs):
        self._render_ai_target_picker(query, offset)

    def _render_ai_target_picker(self, query="", offset=0):
        u = ui()
        self._ai_target_query = u.toga.TextInput(value=query, placeholder="搜索假名、单词、语法或读音规则", style=u.Pack(margin_bottom=8))
        search = lambda w: self._render_ai_target_picker(self._ai_target_query.value or "")
        self._ai_target_query.on_confirm = search
        items = [item for item in self.example_targets_available() if not query.strip() or query.strip().casefold() in
                 " ".join(str(item.get(key, "")) for key in ("title", "meaning", "reading")).casefold()]
        children = [self._ai_target_query, u.button("搜索", search, compact=True)]
        for item in items[offset:offset + 12]:
            children.append(u.button(item["title"] + " · " + item.get("meaning", ""),
                lambda w, target=item["id"]: self.show_ai_form("examples", target_id=target), multiline=True))
        if offset:
            children.append(u.button("上一页", lambda w: self._render_ai_target_picker(query, offset - 12)))
        if offset + 12 < len(items):
            children.append(u.button("下一页", lambda w: self._render_ai_target_picker(query, offset + 12)))
        self.screen("ai_target_picker", "为哪一项生成例句", *children)

    @page_view()
    def show_ai_form(self, mode, *, target_id=None, input_id=None):
        u = ui()
        self.cancel_ai_generation(navigation=True)
        if mode not in {"questions", "examples", "analyze"}:
            raise ValueError("Unknown AI activity")
        if mode == "examples" and not target_id:
            self.show_ai_target_picker()
            return
        target = self._ai_target(target_id)
        if mode == "examples" and target is None:
            self.alert("该学习项暂不可用。")
            return
        key = mode + ":" + (target_id or "")
        if input_id is not None:
            if mode != "analyze" or target_id is not None:
                raise ValueError("分享材料只支持原文解析。")
            from .material_share import MaterialShareStore, draft_key
            try:
                MaterialShareStore(self.store).open_draft(input_id)
            except ValueError as exc:
                self.alert(str(exc))
                return
            key = draft_key(input_id)
        raw = self.store.get_setting(AI_FORM_SETTING, {})
        saved = raw.get("drafts", {}).get(key, {}) if isinstance(raw, dict) else {}
        default_count = str(max(1, min(10, target.get("minimum_examples", 10) - target.get("retained_body_example_count", target.get("retained_example_count", 0))))) \
            if target and target.get("instruction_status") == "instruction_missing" else "3"
        self._ai_form_context = {"mode": mode, "target_id": target_id, "key": key}
        if input_id is not None:
            self._ai_form_context["input_id"] = input_id
        self._ai_form_fields = {}
        children = []
        if target:
            children.append(u.label(target["title"], size=21, bold=True,
                                    text_role="study" if target["family"] == "word" else "ui"))
        if mode == "questions":
            scope = self._ai_scope(request=True)
            children.append(u.label(f"当前可用 {len(scope['targets'])} 项已学内容", size=14, color=u.MUTED))
            children.append(u.label("生成补充练习；本次作答暂不改变复习间隔。", size=12, color=u.MUTED))
        if mode == "analyze":
            source = configure_text_role(u.toga.MultilineTextInput(value=saved.get("source_text", ""),
                placeholder="粘贴日文例句或段落（最多 4000 字）", on_change=self.save_ai_form,
                style=u.Pack(height=190, margin_bottom=8)), "study")
            self._ai_form_fields["source_text"] = source
            self._ai_source_scope_label = u.label("", size=12, color=u.MUTED)
            self.refresh_material_selection()
            children.extend([source, u.button("导入文本文件", self.read_material_file, compact=True),
                             u.button("选择发送范围", self.show_material_selection, compact=True), self._ai_source_scope_label,
                             u.label("分句后建议相关学习项；已有内容会优先关联。", size=12, color=u.MUTED)])
        self._ai_form_fields["count"] = u.toga.TextInput(value=saved.get("count", default_count),
            placeholder="1—8" if mode == "analyze" else "1—10", on_change=self.save_ai_form, style=u.Pack(margin_bottom=8))
        self._ai_form_fields["topic"] = u.toga.TextInput(value=saved.get("topic", ""),
            placeholder="可选：日常生活、旅行等", on_change=self.save_ai_form, style=u.Pack(margin_bottom=8))
        children.extend([u.label("题数" if mode == "questions" else "例句数" if mode == "examples" else "最多建议的新学习项", size=13),
                         self._ai_form_fields["count"], u.label("主题或侧重点（可不填）", size=13), self._ai_form_fields["topic"]])
        self._ai_form_status = u.label("", size=13, color=u.MUTED)
        self._ai_generate_button = u.button("生成题目" if mode == "questions" else "生成例句" if mode == "examples" else "解析并建议",
            lambda w, context=self._ai_form_context: self.start_ai_generation(form_context=context), primary=True)
        self._ai_cancel_button = u.button("取消生成",
            lambda w, context=self._ai_form_context: self.cancel_ai_generation(form_context=context), compact=True)
        self._ai_cancel_button.enabled = False
        children.extend([self._ai_generate_button, self._ai_cancel_button, self._ai_form_status])
        self._ai_connection_slot = u.col()
        self.refresh_ai_form_connection()
        children.append(self._ai_connection_slot)
        self.screen("ai_form", {"questions": "按已学内容出题", "examples": "生成例句", "analyze": "解析日文"}[mode], *children)
        if getattr(self, "_ai_request_operation", None) is not None:
            self._set_ai_form_busy(True)
            self._ai_form_status.text = "材料仍在生成，结果会保存到材料任务。"

    def refresh_ai_form_connection(self):
        """Refresh only the local connection notice on a restored input form."""
        u = ui()
        self.refresh_material_selection()
        slot = getattr(self, "_ai_connection_slot", None)
        if slot is None:
            return
        status = self.ai_connection_status()
        slot.clear()
        if not status.get("ready"):
            slot.add(u.label(status.get("message") or "请先连接 AI 服务。", size=13, color=u.MUTED),
                     u.button("配置 AI 服务", self.show_ai_service_settings))
        elif self._ai_form_status.text == "请先配置 AI 服务。":
            # This is a local configuration gate, not generation/request progress.
            self._ai_form_status.text = ""

    def save_ai_form(self, widget=None, **kwargs):
        context = getattr(self, "_ai_form_context", None)
        fields = getattr(self, "_ai_form_fields", {})
        if (self.page != "ai_form" or not context or "count" not in fields or "topic" not in fields
                or widget is not None and all(widget is not control for control in fields.values())):
            return
        raw = self.store.get_setting(AI_FORM_SETTING, {})
        raw = dict(raw) if isinstance(raw, dict) else {}
        drafts = dict(raw.get("drafts", {}))
        from .ai_ui_state import MAX_FORM_DRAFTS
        if context["key"] not in drafts and len(drafts) >= MAX_FORM_DRAFTS:
            self._ai_form_status.text = "输入草稿已满，请先处理或舍弃已有分享草稿。"
            return False
        if context.get("input_id"):
            from .material_share import MaterialShareStore
            shared = MaterialShareStore(self.store).get(context["input_id"])
            if shared is None or shared["status"] == "discarded":
                self._ai_form_status.text = "这份草稿已舍弃。"
                return False
        saved = dict(drafts.pop(context["key"], {}))
        values = {key: str(control.value or "")[:4000 if key == "source_text" else 200]
                  for key, control in fields.items()}
        if "source_text" in fields and len(str(fields["source_text"].value or "")) > 4000:
            if getattr(self, "_ai_form_status", None) is not None:
                self._ai_form_status.text = "原文超过 4000 字，尚未保存；请先分段。"
            return False
        if any(saved.get(key) != value for key, value in values.items()):
            for key in ("request_id", "request_fingerprint", "request_finished"):
                saved.pop(key, None)
        saved.update(values)
        drafts[context["key"]] = saved
        self.store.set_setting(AI_FORM_SETTING, {"last": deepcopy(context), "drafts": drafts})
        self.refresh_material_selection()

    def _ai_request(self):
        context = self._ai_form_context
        fields = self._ai_form_fields
        value = str(fields["count"].value or "").strip()
        limit = 8 if context["mode"] == "analyze" else 10
        if not value.isdecimal() or not 1 <= int(value) <= limit:
            raise ValueError(f"数量请输入 1—{limit}。")
        source = str(fields["source_text"].value or "") if "source_text" in fields else ""
        if context["mode"] == "analyze" and (not source.strip() or len(source) > 4000):
            raise ValueError("请提供 1—4000 字的日文。")
        selection = None
        if context["mode"] == "analyze":
            from .material_input import current_selection, selected_text
            selection = current_selection(self.store, source, draft_key=context["key"])
            source = selected_text(selection)
        topic = str(fields["topic"].value or "").strip()
        if len(topic) > 200:
            raise ValueError("侧重点请控制在 200 字以内。")
        items = self.example_targets_available(source_text=source) if context["mode"] == "analyze" else []
        target = deepcopy(self._ai_target(context["target_id"]))
        if context["mode"] == "examples" and target is None:
            raise ValueError("该学习项暂不可用，请重新选择。")
        scope = self._ai_scope(request=True) if context["mode"] == "questions" else None
        if scope is not None and not scope["targets"]:
            raise ValueError("还没有符合当前题型的独立作答记录，请先完成一些本地学习。")
        if context["mode"] == "questions" and self.store.get_setting(AI_PREVIEW_SETTING, None):
            raise ValueError("上次生成的题目尚待确认，请先从 AI 中心继续或舍弃。")
        request = {"mode": context["mode"], "count": int(value), "topic": topic, "source_text": source,
                   "target": target, "scope": scope}
        if context["mode"] == "analyze":
            # Prefer literal forms from this input, then already learned rows.
            # Do not upload the whole catalogue or any learner event history.
            enrolled = {item["id"] for item in self.learning_store.list_items()}
            def relevance(item):
                match = max((len(str(item[key])) for key in ("title", "reading", "hiragana", "katakana")
                             if item.get(key) and str(item[key]) in source), default=0)
                return (-match, item["id"] not in enrolled, item["id"])
            items.sort(key=relevance)
            request["catalog"] = [{key: item.get(key) for key in ("id", "revision", "family", "title", "reading", "meaning", "reference_only")}
                                  for item in items[:100]]
        if self.save_ai_form() is False:
            raise ValueError("输入草稿尚未保存，请处理提示后重试。")
        raw = self.store.get_setting(AI_FORM_SETTING, {})
        saved = raw["drafts"][context["key"]]
        fingerprint = sha256(json.dumps({"request": request, "source_selection": selection}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        old_job = (self.material_workflow().for_request(saved["request_id"])
                   if context["mode"] != "questions" and saved.get("request_id") else None)
        if (saved.get("request_fingerprint") != fingerprint or saved.get("request_finished")
                or old_job and old_job["status"] in {"failed", "cancelled", "transport_unknown"}):
            saved["request_id"] = uuid4().hex
        saved.update(request_fingerprint=fingerprint, request_finished=False)
        request["request_id"] = saved["request_id"]
        self.store.set_setting(AI_FORM_SETTING, raw)
        return request

    def _set_ai_form_busy(self, busy):
        self._ai_generate_button.enabled = not busy
        self._ai_cancel_button.enabled = busy
        for control in self._ai_form_fields.values():
            control.enabled = not busy

    def _finish_ai_form_request(self, request_id, *, update_view=True):
        raw = self.store.get_setting(AI_FORM_SETTING, {})
        for saved in raw.get("drafts", {}).values():
            if saved.get("request_id") == request_id:
                saved["request_finished"] = True
        self.store.set_setting(AI_FORM_SETTING, raw)
        if update_view:
            self._set_ai_form_busy(False)
            self._ai_form_status.text = "生成完成，内容已保存为待确认建议。"

    def start_ai_generation(self, widget=None, *, form_context=None, **kwargs):
        if form_context is not None and form_context is not self._ai_form_context:
            return
        if self.page != "ai_form" or getattr(self, "_ai_request_operation", None) is not None:
            return
        if getattr(self, "_material_file_busy", False):
            self._ai_form_status.text = "请先完成文本文件导入。"
            return
        try:
            request = self._ai_request()
        except ValueError as exc:
            self._ai_form_status.text = str(exc)
            return
        if request["mode"] != "questions":
            from .ai_materials import AIMaterialStore
            # A process may stop after staging but before marking the request
            # complete. Recover that local proposal without another API call.
            saved = next((row for row in AIMaterialStore(self.store).list_proposals()
                          if row["request_id"] == request["request_id"]), None)
            if saved:
                self._finish_ai_form_request(request["request_id"])
                job = self.material_workflow().for_request(request["request_id"])
                if job:
                    self.show_material_task(job["id"])
                else:
                    self.show_ai_proposal(saved["id"])
                return
        if not self.ai_connection_status().get("ready"):
            self._ai_form_status.text = "请先配置 AI 服务。"
            return
        operation, token = object(), self._page_token
        material_run = None
        if request["mode"] != "questions":
            try:
                workflow = self.material_workflow()
                from .material_input import input_kind, current_selection
                selection = current_selection(self.store, str(self._ai_form_fields["source_text"].value or ""), draft_key=self._ai_form_context["key"]) if request["mode"] == "analyze" else None
                kind = input_kind(self.store, selection["text"]) if selection else "target"
                if selection and self._ai_form_context.get("input_id"):
                    from .material_share import MaterialShareStore
                    shared = MaterialShareStore(self.store).get(self._ai_form_context["input_id"])
                    if shared and shared["text"] == selection["text"]:
                        kind = "share"
                job = workflow.create(request, source_selection=selection,
                    input_kind=kind)
                attempt = workflow.begin(job["id"], budget_context=self.material_budget_context())
                material_run = {"id": job["id"], "token": attempt, "operation": operation}
            except ValueError as exc:
                self._ai_form_status.text = str(exc)
                return
        self._ai_material_run = material_run
        self._ai_request_operation = operation
        self._set_ai_form_busy(True)
        self._ai_form_status.text = "正在生成…"
        self._ai_form_status._feedback_fragment = {"kind": "ai_request", "request": deepcopy(request)}
        self._ai_request_task = self.loop.create_task(self._run_ai_generation(request, operation, token, material_run=material_run))

    async def _run_ai_generation(self, request, operation, token, *, material_run=None):
        response = None
        provider = None
        completed = False
        phase = 'save'
        try:
            provider = self.ai_client()
            if material_run is not None:
                from .material_budget import authorize
                authorize(self.store, material_run['id'], material_run['token'], provider)
            phase = 'request'
            response = await asyncio.to_thread(provider.generate, request)
            phase = 'save'
            if material_run is not None:
                from .material_budget import settle
                settle(self.store, material_run['id'], material_run['token'], getattr(provider, 'last_usage', {}))
            visible = self.page == "ai_form" and self._page_token == token and self._ai_request_operation is operation
            if material_run is not None:
                saved = self.material_workflow().complete_generation(material_run["id"], material_run["token"], response,
                    self.ai_material_store(reference_ids=self._ai_material_reference_ids(response)))
                if saved is None:
                    return
                target = saved["proposal_id"]
            elif not visible:
                return
            elif request["mode"] == "questions":
                from .ai_questions import validate_question_proposals
                scope = self._ai_scope()
                questions = validate_question_proposals(response.get("questions"), scope,
                    self.learning_preferences()["enabled"], self.ai_practice_store().all_questions())
                if not questions:
                    raise ValueError("没有通过检查的题目。")
                self.store.set_setting(AI_PREVIEW_SETTING, {"request_id": request["request_id"], "questions": questions})
                target = None
            else:
                proposal = self.ai_material_store(reference_ids=self._ai_material_reference_ids(response)).stage(
                    "text" if request["mode"] == "analyze" else "examples",
                    request["source_text"], response.get("items", []), response.get("example_pack"),
                    response.get("segments", []), request["request_id"])
                target = proposal["id"]
            completed = True
            self._finish_ai_form_request(request["request_id"], update_view=visible)
            if self.page == "ai_form":
                self._set_ai_form_busy(False)
            self._ai_request_operation = None
            self._ai_material_run = None
            if visible:
                if material_run is not None:
                    self.show_material_task(material_run["id"])
                elif target:
                    self.show_ai_proposal(target)
                else:
                    self.show_ai_question_preview()
        except asyncio.CancelledError:
            if not completed and material_run is not None and getattr(self, "_ai_material_run", None) is material_run:
                # Explicit cancellation has already fenced its attempt. A
                # process/lifecycle interruption has unknown remote delivery.
                record_failure(self.material_workflow, material_run,
                    code="request_interrupted", delivery_unknown=True, operation="ai_generation")
            return
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error('ai_generation', 'refresh' if completed else phase, exc)
            if completed:
                if self.page == 'ai_form' and self._page_token == token:
                    self._ai_form_status.text = '生成结果已保存，但页面未完成刷新；请从 AI 中心继续，无需重新生成。'
                return
            if material_run is not None:
                record_failure(self.material_workflow, material_run,
                    code="validation_error" if isinstance(exc, (ValueError, TypeError)) else "generation_error",
                    delivery_unknown=response is None, operation="ai_generation")
            if self.page == "ai_form" and self._page_token == token and self._ai_request_operation is operation:
                from .feedback_context import sanitize_feedback_fragment
                validation_failure = isinstance(exc, (ValueError, TypeError))
                code = getattr(exc, "code", None)
                if not isinstance(code, str) or code not in {"busy", "credentials", "device", "incomplete", "invalid_request", "model",
                                "network", "quota", "refusal", "response_format", "response_size", "sensitive_input"}:
                    code = "validation_error" if validation_failure else "generation_error"
                evidence = {"kind": "ai_generation_failure", "request": request, "error_code": code}
                if validation_failure:
                    evidence["validation_error"] = str(exc)[:2000]
                if response is not None:
                    evidence["response_candidate"] = response
                # Raw provider output never becomes a stored UI fragment:
                # redact secrets and enforce feedback limits before attachment.
                self._ai_form_status._feedback_fragment = sanitize_feedback_fragment(evidence)
                self._ai_form_status.text = getattr(exc, "user_message", None) or (
                    "生成内容未通过检查，请重试或调整输入。" if validation_failure else
                    "生成未完成，请检查 AI 服务后重试。已有内容和练习保持不变。")
                if validation_failure and response is not None:
                    detail = self._ai_form_status._feedback_fragment.get("validation_error", "")
                    if isinstance(detail, str) and detail:
                        self._ai_form_status.text = "生成内容未通过检查：\n" + detail[:220]
        finally:
            try:
                if material_run is not None and provider is not None:
                    from .material_budget import settle
                    settle(self.store, material_run['id'], material_run['token'], getattr(provider, 'last_usage', {}))
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error("ai_generation", "save", exc)
            finally:
                if getattr(self, "_ai_request_operation", None) is operation:
                    self._ai_request_operation = None
                    self._ai_material_run = None
                    if self.page == "ai_form":
                        self._set_ai_form_busy(False)

    def cancel_ai_generation(self, widget=None, *, form_context=None, navigation=False, shutdown=False, **kwargs):
        if form_context is not None and form_context is not self._ai_form_context:
            return
        if getattr(self, "_ai_request_operation", None) is None:
            return
        material_run = getattr(self, "_ai_material_run", None)
        if material_run:
            if navigation:
                return
            workflow = self.material_workflow()
            if shutdown:
                workflow.fail(material_run["id"], material_run["token"], code="app_closed", delivery_unknown=True)
            else:
                workflow.cancel(material_run["id"])
            self._ai_material_run = None
        self._ai_request_operation = None
        task = getattr(self, "_ai_request_task", None)
        if task is not None and not task.done():
            task.cancel()
        if self.page == "ai_form":
            self._ai_form_status.text = "已取消，输入已保留。"
            self._set_ai_form_busy(False)

    def cancel_material_task(self, identity):
        run = getattr(self, '_ai_material_run', None)
        if run and run['id'] == identity:
            self.cancel_ai_generation()
        else:
            self.material_workflow().cancel(identity)
        self.show_material_task(identity)

    @page_view()
    def show_material_tasks(self, widget=None, *, offset=0, **kwargs):
        u = ui()
        jobs = self.material_workflow().list()
        children = [u.button("解析日文", lambda w: self.show_ai_form("analyze"))]
        for job in jobs[offset:offset + 12]:
            children.append(u.button(datetime.fromisoformat(job["created_at"]).astimezone().strftime("%m-%d %H:%M") + " · " + self._material_task_status(job),
                lambda w, identity=job["id"]: self.show_material_task(identity), multiline=True))
        if offset:
            children.append(u.button("上一页", lambda w: self.show_material_tasks(offset=max(0, offset - 12))))
        if offset + 12 < len(jobs):
            children.append(u.button("下一页", lambda w: self.show_material_tasks(offset=offset + 12)))
        if not jobs:
            children.append(u.label("暂无材料任务。"))
        self.screen("material_tasks", "材料任务", *children)

    @staticmethod
    def _material_task_status(job):
        labels = {"queued": "等待生成", "running": "正在生成", "awaiting_review": "材料待核验",
                  "completed": "核验已记录", "failed": "任务未完成", "cancelled": "已取消",
                  "transport_unknown": "上次请求结果不明"}
        return ("文本解析" if job["request"]["mode"] == "analyze" else "生成例句") + " · " + labels[job["status"]]

    @page_view(detail=True, refresh=True)
    def show_material_task(self, identity):
        u = ui()
        workflow = self.material_workflow()
        job = workflow.get(identity)
        if job is None:
            self.alert("该材料任务已不可用。")
            return
        self._material_task_id = identity
        children = [u.label(self._material_task_status(job), size=18, bold=True)]
        children[0]._feedback_fragment = {"kind": "material_workflow", "workflow_id": job["id"],
            "request_hash": job["request_hash"], "status": job["status"], "proposal_id": job["proposal_id"]}
        from .material_budget import totals as budget_totals
        budget = workflow.budget(job.get('revision_of', identity))
        if budget:
            spent = budget_totals(budget)
            children.append(u.label(f"调用额度 {spent['tokens']:,}/{budget['limits']['tokens']:,} token · "
                f"费用估算 ${spent['microusd'] / 1000000:.3f}/${budget['limits']['microusd'] / 1000000:.2f}"
                + ('（含预留）' if spent['unknown'] else ''), size=13, color=u.MUTED))
        if job["proposal_id"]:
            proposal = self.ai_material_store(read_only=True).get(job["proposal_id"])
            if proposal:
                children.extend([u.label(f"{len(proposal['items'])} 个学习项 · {len(proposal['example_pack']['examples'])} 条例句"),
                    u.label("保留材料不会自动加入学习或调整复习。", size=13, color=u.MUTED),
                    u.button("查看材料", lambda w: self.show_ai_proposal(job["proposal_id"]), primary=True)])
                children.append(u.button('使用设置', lambda w: self.show_material_usage(job['proposal_id']), compact=True))
                children.append(u.button('参考核据', lambda w: self.show_material_references(identity), compact=True))
            report = self.current_material_verification(identity)
            if report is None:
                children.append(u.button("核对本地资料", lambda w: self.check_material_locally(identity)))
            else:
                supported = sum(row["status"] == "supported" for row in report["claims"])
                children.extend(self.material_study_actions(identity))
                children.extend([u.label(f"{supported} 项已有核据 · {len(report['claims']) - supported} 项待核", size=14),
                    u.button("核验详情", lambda w: self.show_material_verification(identity), compact=True)])
                if job["status"] == "awaiting_review" and not job.get('revision_of') and report["review_request"]["questions"]:
                    children.extend([u.button("AI 复核待核内容", lambda w: self.start_material_review(identity)),
                        u.label("仅发送本批原文及相关资料；复核意见不会直接改变学习进度。", size=12, color=u.MUTED)])
                from .material_revision import conflict_claims
                revision = self.material_revision().get(identity)
                if revision:
                    children.append(u.button('查看修订记录', lambda w: self.show_material_revision(identity)))
                elif job['status'] == 'completed' and conflict_claims(report) and not job.get('revision_of'):
                    children.append(u.button('修订分歧字段', lambda w: self.show_material_revision(identity)))
                if job.get('revision_of'):
                    children.append(u.button('对照原材料', lambda w: self.show_material_revision(job['revision_of'])))
        elif job["status"] == "transport_unknown":
            children.append(u.label("结果尚未收到，未自动重发。原文和已使用的调用记录已保留。", size=14))
        if job["status"] == "running":
            children.append(u.button("刷新", lambda w: self.show_material_task(identity)))
            children.append(u.button("取消本次调用", lambda w: self.cancel_material_task(identity), compact=True))
        children.append(u.button("返回材料任务", self.show_material_tasks))
        self.screen("material_task", "材料任务", *children)

    @page_view(detail=True, refresh=True)
    def show_material_verification(self, identity, *, offset=0):
        u = ui()
        report = self.current_material_verification(identity)
        if report is None:
            self.alert("尚无核验记录。")
            return
        labels = {"family": "类别", "domain": "领域", "title": "写法", "reading": "读法", "meaning": "词义",
            "instruction": "讲解", "depends_on": "前置内容", "question_templates": "配套题目", "accent": "调号",
            "text": "日文", "translation": "译文", "order_translation": "语序译文", "target_id": "关联学习项",
            "spans": "关联片段", "hint": "提示", "explanation": "关联说明"}
        states = {"supported": "已有核据", "unknown": "待核", "needs_review": "仍需核实", "contradicted": "有冲突", "stale": "依据已变更"}
        children = [u.label("模型意见作为核查线索，不等于已证实正确。", size=13, color=u.MUTED)]
        for claim in report["claims"][offset:offset + 12]:
            row = [u.label(labels.get(claim["facet"], claim["facet"]) + " · " + states[claim["status"]], bold=True, size=15)]
            if claim["model_note"]:
                row.append(u.label(claim["model_note"], size=13))
            children.append(u.col(*row, padding=8, margin_bottom=6, background_color=u.ENTRY_BACKGROUNDS[len(children) % 3]))
        if offset:
            children.append(u.button("上一页", lambda w: self.show_material_verification(identity, offset=max(0, offset - 12))))
        if offset + 12 < len(report["claims"]):
            children.append(u.button("下一页", lambda w: self.show_material_verification(identity, offset=offset + 12)))
        children.append(u.button("返回材料任务", lambda w: self.show_material_task(identity)))
        self.screen("material_verification", "核验详情", *children)

    @page_view()
    def show_ai_inbox(self, widget=None, *, show_all=False, offset=0, **kwargs):
        u = ui()
        proposals = self.ai_material_store(read_only=True).list_proposals(status=None if show_all else "pending")
        children = [u.button("只看待确认" if show_all else "查看全部记录", lambda w: self.show_ai_inbox(show_all=not show_all), compact=True)]
        if not proposals:
            children.append(u.label("暂无待确认内容。" if not show_all else "暂无生成记录。"))
        for proposal in proposals[offset:offset + 12]:
            name = "文本解析" if proposal["mode"] == "text" else "生成例句"
            children.append(u.button(f"{name} · {len(proposal['items'])} 个学习项 · {len(proposal['example_pack']['examples'])} 条例句",
                lambda w, identity=proposal["id"]: self.show_ai_proposal(identity), multiline=True))
        if offset:
            children.append(u.button("上一页", lambda w: self.show_ai_inbox(show_all=show_all, offset=offset - 12)))
        if offset + 12 < len(proposals):
            children.append(u.button("下一页", lambda w: self.show_ai_inbox(show_all=show_all, offset=offset + 12)))
        self.screen("ai_inbox", "待确认内容", *children)

    @page_view(detail=True, refresh=True)
    def show_ai_proposal(self, proposal_id):
        self._render_ai_proposal(proposal_id)

    def _render_ai_proposal(self, proposal_id):
        u = ui()
        service = self.ai_material_store(read_only=True)
        proposal = service.get(proposal_id)
        if proposal is None:
            self.alert("这批内容已不可用。")
            return
        self._ai_proposal_id = proposal_id
        self._ai_review_controls = {"items": {}, "examples": {}, "references": {}}
        # One fresh snapshot per render, never cached across keep/discard/import.
        retained = {item["id"]: item for item in service.retained_items()}
        children = [u.label("选择要保留或舍弃的内容。保留不会自动加入学习计划。", size=13, color=u.MUTED)]
        children[0]._feedback_fragment = {"kind": "ai_proposal", "proposal": deepcopy(proposal)}
        children.append(u.button('使用设置', lambda w: self.show_material_usage(proposal_id), compact=True))
        job = self.material_workflow().for_request(proposal["request_id"])
        if job:
            from .material_workflow import original_segments
            children[0]._feedback_fragment["original_segments"] = original_segments(job, proposal)
        statuses = {"pending": "待确认", "kept": "已保留", "discarded": "已舍弃"}
        for item in proposal["items"]:
            state = proposal["decisions"]["items"].get(item["id"], "pending")
            card = [u.label(item["title"], size=20, bold=True, text_role="study" if item["family"] == "word" else "ui"),
                    u.label(item.get("meaning", item.get("instruction", {}).get("summary", "")), size=14),
                    u.label(statuses[state], size=12, color=u.MUTED),
                    u.button("讲解与配套题", lambda w, identity=item["id"]:
                             self.show_ai_candidate((proposal_id, identity)), compact=True)]
            if state == "pending":
                choice = u.switch("选择此学习项", value=True)
                self._ai_review_controls["items"][item["id"]] = choice
                card.append(choice)
            if state == "kept":
                card.extend(self._ai_retained_target_actions(item["id"], retained))
            children.append(u.col(*card, padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[0]))
        known = {item["id"]: item for item in self.example_targets_available(
            reference_ids=self._ai_material_reference_ids(proposal))}
        known.update({item["id"]: item for item in proposal["items"]})
        pack = proposal["example_pack"]
        reference_service = self.ai_material_store(reference_ids=self._ai_material_reference_ids(proposal))
        eligible_refs = reference_service.eligible_reference_ids(proposal)
        active_refs = {r["id"] for e in pack["examples"] for r in self.example_store().references(e["id"])}
        activate = []
        for identity in self._ai_related_targets(proposal, retained):
            item = known.get(identity)
            if item:
                card = [u.label(item["title"], size=20, bold=True,
                                text_role="study" if item["family"] in {"word", "kana"} else "ui")]
                card.extend(self._ai_retained_target_actions(identity, retained))
                children.append(u.col(*card, padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[0]))
        for example in pack["examples"]:
            state = proposal["decisions"]["examples"].get(example["id"], "pending")
            refs = [ref for ref in pack["references"] if ref["example_id"] == example["id"]]
            spans = [span for ref in refs for span in ref["spans"]]
            card = [self.example_text(example, {"spans": spans}), self.natural_translation_label(example['text'], example.get('translation', ''), size=14),
                    *self.order_translation_rows(example['text'], example.get('translation', ''),
                        explicit=example.get('order_translation'))]
            for ref in refs:
                target = known.get(ref["target_id"], {})
                quote = "／".join(span["quote"] for span in ref["spans"])
                card.append(u.label(quote + " · " + target.get("title", "关联用法"), size=13, color=u.GREEN))
                card.extend(self.example_hint_rows([{"kind": key, "text": value} for key, value in ref.get("hint", {}).items()],
                                                  already_shown=self.translation_display_values(example['text'], example.get('translation', ''))))
                if ref.get("explanation"):
                    card.append(u.label(ref["explanation"], size=13))
                ref_state = proposal["decisions"]["references"][ref["id"]]
                if ref_state == "pending" and state != "discarded":
                    choice = u.switch("保留此关联" + ("" if ref["id"] in eligible_refs else "（待核）"), value=ref["id"] in eligible_refs)
                    self._ai_review_controls["references"][ref["id"]] = choice
                    card.append(choice)
                elif ref_state == "kept":
                    card.append(u.label("关联已启用" if ref["id"] in active_refs else "关联已保留，尚未启用", size=12, color=u.MUTED))
                    if ref["id"] not in active_refs and ref["id"] in eligible_refs:
                        activate.append(ref["id"])
            card.append(u.label(statuses[state], size=12, color=u.MUTED))
            if state == "pending":
                choice = u.switch("选择此例句", value=True)
                self._ai_review_controls["examples"][example["id"]] = choice
                def body_changed(widget, related=tuple(r["id"] for r in refs), controls=self._ai_review_controls, **kw):
                    if controls is not self._ai_review_controls or widget.value:
                        return
                    for rid in related:
                        if rid in controls["references"]:
                            controls["references"][rid].value = False
                choice.on_change = body_changed
                card.append(choice)
            children.append(u.col(*card, padding=10, margin_bottom=10, background_color=u.ENTRY_BACKGROUNDS[1]))
        if any(self._ai_review_controls.values()):
            children.extend([u.button("保留所选", lambda w: self.decide_ai_proposal(True, proposal_id=proposal_id), primary=True),
                             u.button("舍弃所选", lambda w: self.decide_ai_proposal(False, proposal_id=proposal_id))])
        if activate:
            children.append(u.button(f"启用已核关联（{len(activate)}）",
                lambda w: self.activate_ai_material_references(proposal_id, activate)))
        children.append(u.button("返回待确认内容", self.show_ai_inbox))
        self.screen("ai_proposal", "确认新内容", *children)

    def _ai_related_targets(self, proposal, retained):
        """Recover targets from durable references, including old proposals."""
        shown = {item["id"] for item in proposal["items"]}
        referenced = list(dict.fromkeys(ref["target_id"] for ref in proposal["example_pack"]["references"]))
        requested = set()
        raw = self.store.get_setting(AI_FORM_SETTING, {})
        for key, draft in (raw.get("drafts", {}) if isinstance(raw, dict) else {}).items():
            if key.startswith("examples:") and draft.get("request_id") == proposal["request_id"]:
                requested.add(key.split(":", 1)[1])
        return [identity for identity in referenced if identity not in shown and
                (identity in retained or identity in requested or len(referenced) == 1)]

    def _ai_retained_target_actions(self, item_id, retained_items):
        u = ui()
        retained = retained_items.get(item_id)
        actions = []
        if retained:
            count, minimum = retained["retained_example_count"], retained["minimum_examples"]
            if self.learning_store.get_item(item_id):
                actions.append(u.label("已加入学习计划。", size=13, color=u.MUTED))
            elif not retained["instruction_missing"]:
                from .assessment_policy import teaching_allowed
                if teaching_allowed(retained, store=self.store):
                    actions.append(u.button("加入学习计划", lambda w: self.enroll_ai_item(item_id), primary=True))
                else:
                    actions.append(u.label("例句已齐备，内容仍待核验。", size=13, color=u.MUTED))
            else:
                body_count = retained.get("retained_body_example_count", count)
                pending_count = retained.get("pending_example_count", 0)
                text = f"已保留 {body_count} 条例句"
                if pending_count:
                    text += f"；{pending_count} 条关联待核"
                actions.append(u.label(text + "。", size=12, color=u.MUTED))
                if body_count < minimum:
                    actions.append(u.button("继续补充例句", lambda w: self.show_ai_form("examples", target_id=item_id)))
        actions.append(u.button("查看学习项", lambda w: self.open_ai_target(item_id), compact=True))
        return actions

    def open_ai_target(self, item_id):
        item = self._ai_target(item_id)
        if item:
            self.open_example_target(item)
        else:
            self.alert("该学习项暂不可用。")

    @page_view(detail=True, refresh=True)
    def show_ai_candidate(self, selection):
        """Inspect a staged lesson without retaining it or creating a card."""
        from .structured_lesson_ui import teaching_block
        from .learning_ui import KIND_LABELS
        u = ui()
        proposal_id, item_id = selection
        proposal = self.ai_material_store(read_only=True).get(proposal_id)
        item = next((row for row in (proposal or {}).get("items", []) if row["id"] == item_id), None)
        if item is None:
            self.alert("这条建议暂不可用。")
            return
        title = u.label(item["title"], size=22, bold=True,
                        text_role="study" if item["family"] == "word" else "ui")
        title._feedback_fragment = {"kind": "ai_candidate_lesson", "proposal_id": proposal_id,
                                    "item": deepcopy(item)}
        children = [title]
        if item.get("reading"):
            children.append(u.label(item["reading"], size=17, text_role="study"))
        children.append(teaching_block(u, item))
        instruction = item["instruction"]
        children.extend([self.annotated_sentence(instruction["example"], size=18),
                         self.natural_translation_label(instruction['example'], instruction['translation'], size=14),
                         *self.order_translation_rows(instruction['example'], instruction['translation'],
                             explicit=instruction.get('order_translation'))])
        if instruction.get("prerequisite"):
            children.append(u.label("先了解：" + instruction["prerequisite"], size=14))
        if item.get("depends_on"):
            known = {row["id"]: row for row in self.learning_items_available() + proposal["items"]}
            children.append(u.label("前置知识：" + "、".join(known.get(cid, {}).get("title", cid)
                                  for cid in item["depends_on"]), size=13, color=u.MUTED))
        for kind, variants in item["question_templates"].items():
            children.append(u.label(KIND_LABELS.get(kind, "配套题"), size=17, bold=True))
            for index, question in enumerate(variants):
                parts = [u.label(question.get("task", ""), size=12, color=u.MUTED),
                         u.label(question["prompt"], size=18, text_role="study")]
                if question.get("choices"):
                    parts.append(u.label("选项：" + " ／ ".join(question["choices"]), size=14))
                parts.extend([u.label("答案：" + question["answer"], size=15, color=u.GREEN),
                              u.label(question["feedback"], size=14)])
                from .learning_questions import learning_order_translation_source
                reference = question.get('order_translation')
                source = learning_order_translation_source(dict(question, kind=kind)) if isinstance(reference, dict) else None
                if source:
                    parts.extend(self.order_translation_rows(source, '',
                        explicit=reference, show_source=True))
                alternatives = [answer for answer in question.get("accepted_answers", []) if answer != question["answer"]]
                if alternatives:
                    parts.append(u.label("也接受：" + " ／ ".join(alternatives), size=13))
                if question.get("known_wrong"):
                    parts.append(u.label("判为错误：" + " ／ ".join(question["known_wrong"]), size=13))
                if question.get("strict"):
                    parts.append(u.label("此题按完整写法匹配。", size=12, color=u.MUTED))
                children.append(u.col(*parts, padding=10, margin_bottom=8,
                                      background_color=u.ENTRY_BACKGROUNDS[index % 3]))
        self.screen("ai_candidate", "待确认学习项", *children)

    def decide_ai_proposal(self, keep, *, proposal_id=None):
        if self.page != "ai_proposal" or proposal_id is not None and proposal_id != self._ai_proposal_id:
            return
        selected = {kind: [identity for identity, control in controls.items() if control.value]
                    for kind, controls in self._ai_review_controls.items()}
        if not any(selected.values()):
            self.alert("请先选择要处理的内容。")
            return
        try:
            service = self.ai_material_store(read_only=True)
            if keep:
                proposal = service.get(self._ai_proposal_id)
                service = self.ai_material_store(reference_ids=self._ai_material_reference_ids(proposal))
            method = service.keep if keep else service.discard
            method(self._ai_proposal_id, item_ids=selected["items"], example_ids=selected["examples"], reference_ids=selected["references"])
        except ValueError as exc:
            self.alert(str(exc))
            return
        self._example_cache_key = None
        self._render_ai_proposal(self._ai_proposal_id)

    def activate_ai_material_references(self, proposal_id, reference_ids):
        proposal = self.ai_material_store(read_only=True).get(proposal_id)
        if proposal is None:
            return
        try:
            self.ai_material_store(reference_ids=self._ai_material_reference_ids(proposal)).activate_references(proposal_id, reference_ids)
        except ValueError as exc:
            self.alert(str(exc))
            return
        self._example_cache_key = None
        self._render_ai_proposal(proposal_id)

    def enroll_ai_item(self, item_id):
        saved = self.learning_store.get_item(item_id)
        if saved:
            self.show_learning_detail(saved)
            return
        service = self.ai_material_store(read_only=True)
        readiness = service.ready_for_learning(item_id)
        if not readiness["ready"]:
            self.alert("\n".join(readiness["missing"]))
            return
        item = next((item for item in self.learning_items_available() if item["id"] == item_id), None)
        if item:
            self.enroll_learning(item)

    @page_view()
    def show_ai_question_preview(self, widget=None, **kwargs):
        u = ui()
        preview = self.store.get_setting(AI_PREVIEW_SETTING, None)
        if not preview:
            self.show_ai_form("questions")
            return
        questions = preview["questions"]
        self._ai_question_preview = preview
        children = [u.label(f"已生成 {len(questions)} 题", size=20, bold=True),
                    u.label("开始后追加到已有 AI 练习，未完成题目和草稿会保留。", size=13, color=u.MUTED)]
        # The full candidate is maintenance evidence only; the visible preview
        # intentionally avoids solutions or question text before retrieval.
        children[0]._feedback_fragment = {"kind": "ai_question_candidates", "candidate": deepcopy(preview)}
        children.extend([u.button("开始练习", self.accept_ai_questions, primary=True),
                         u.button("舍弃这一批", self.discard_ai_questions)])
        self.screen("ai_question_preview", "题目已准备好", *children)

    def accept_ai_questions(self, widget=None, **kwargs):
        preview = self.store.get_setting(AI_PREVIEW_SETTING, None)
        if not preview:
            return
        try:
            self.ai_practice_store().append(preview["questions"], self._ai_scope(), self.learning_preferences()["enabled"])
        except ValueError:
            self.alert("学习状态或题型已变化，请重新生成符合当前设置的题目。")
            return
        self.store.set_setting(AI_PREVIEW_SETTING, None)
        self.continue_ai_practice()

    def discard_ai_questions(self, widget=None, **kwargs):
        self.store.set_setting(AI_PREVIEW_SETTING, None)
        self.show_ai_center()

    @page_view(replace=True)
    def continue_ai_practice(self, widget=None, **kwargs):
        u = ui()
        enabled, scope = self.learning_preferences()["enabled"], self._ai_scope()
        current = self.ai_practice_store().current(enabled, scope)
        if current is None:
            counts = self.ai_practice_store().counts(enabled, scope)
            self.screen("ai_practice_done", "本次 AI 练习已保存",
                u.label("其余题目暂不符合当前学习范围或题型设置。" if counts["remaining"] else
                        "内容修订涉及的未答题与草稿已保存，未计作完成。" if counts.get('retired') else "这一批已完成。"),
                u.button("再生成一组", lambda w: self.show_ai_form("questions"), primary=True),
                u.button("回到今日", self.leave_ai_practice))
            return
        self._ai_current = current
        question = current["question"]
        if current.get("result"):
            self.show_ai_feedback(current["result"])
            return
        # Give the action real row width. An empty flex filler can consume the
        # whole Android row while an intrinsic-width compact button collapses.
        actions = [u.button("显示答案", lambda w, qid=current["id"]: self.reveal_ai_answer(question_id=qid),
                            compact=True, font_size=12, flex=1)]
        actions.append(u.button("问 AI", lambda w, qid=current["id"]: self.open_tutor_question(
            source="ai", question_id=qid), compact=True, font_size=12, flex=1))
        if prompt_audio_text(question):
            actions.append(u.speaker_button(lambda w, qid=current["id"]: self.play_ai_prompt(question_id=qid), "朗读题干"))
        instruction = question_instruction(question)
        children = []
        children.append(build_prompt_block(self, question,
            clean_prompt_preferences(self.store.get_setting("prompt_display_v1", {})), size=20))
        children[-1]._feedback_fragment.update({"kind": "ai_question", "question": deepcopy(question),
                                          "question_id": current["id"], "draft": deepcopy(current.get("draft", {}))})
        if instruction:
            children.append(u.label(instruction, size=13, color=u.MUTED))
        children.append(u.row(*actions))
        if question.get("input_mode", "choice") == "choice":
            children.extend(u.button(choice, lambda w, answer=choice, qid=current["id"]: self.submit_ai_answer(answer, question_id=qid),
                                     multiline=True, text_role=learning_text_role(question, "choice")) for choice in question.get("choices", []))
        else:
            from .answer_input_ui import answer_controls
            from .answer_input import system_japanese_enabled
            self._ai_answer_input, controls = answer_controls(u, question,
                current.get("draft", {}).get("answer", ""), self.save_ai_answer,
                lambda: self.page == "ai_question" and self._ai_current["id"] == current["id"],
                system_japanese=system_japanese_enabled(self.store))
            children.extend([*controls, u.button("提交", lambda w: self.submit_ai_answer(self._ai_answer_input.value), primary=True)])
        self._ai_question_status = u.label("", size=12, color=u.MUTED)
        children.append(self._ai_question_status)
        self.screen("ai_question", "AI 练习", *children)
        if current.get('draft', {}).get('presented') is not True:
            self.ai_practice_store().save_draft(current['id'], {'presented': True})
            self._ai_current.setdefault('draft', {})['presented'] = True

    def save_ai_answer(self, widget=None, **kwargs):
        if self.page == "ai_question":
            self.ai_practice_store().save_draft(self._ai_current["id"], {"answer": self._ai_answer_input.value or ""})

    def submit_ai_answer(self, answer, *, question_id=None, revealed=False):
        if self.page != "ai_question" or question_id is not None and question_id != self._ai_current["id"]:
            return
        if not str(answer or "").strip() and not revealed:
            self._ai_question_status.text = "请先作答。"
            return
        try:
            result = self.ai_practice_store().submit(self._ai_current["id"], answer,
                self.learning_preferences()["enabled"], self._ai_scope(), revealed=revealed)
        except ValueError:
            self._ai_question_status.text = "当前题型或学习范围已变化，请返回 AI 中心继续。"
            return
        self.show_ai_feedback(result)

    def reveal_ai_answer(self, widget=None, *, question_id=None, **kwargs):
        self.submit_ai_answer(self._ai_current.get("draft", {}).get("answer", ""), question_id=question_id, revealed=True)

    def show_ai_feedback(self, result):
        from .answer_feedback import build_answer_feedback
        from .answer_result_ui import entered_answer_row, distinct_explanation
        from .answer_autoplay import autoplay_answer
        u = ui()
        question = self._ai_current["question"]
        children = [u.label(result.get("answer", question["answer"]), size=20,
                            flex=1, text_role=learning_text_role(question, "answer"))]
        children[0]._feedback_fragment = {"kind": "ai_answer_result", "question": deepcopy(question), "result": deepcopy(result)}
        audio_text = self._learning_answer_audio_text(question)
        if audio_text:
            def play_answer(widget=None, **kwargs):
                if self.page == "ai_feedback" and self._ai_current["question"] is question:
                    self.speak_text(audio_text)
            children[0] = u.row(children[0], u.speaker_button(play_answer, "朗读答案对应的日语"))
        children.insert(0, build_answer_feedback(result))
        if result.get("answered_text") and not result.get("correct") and result["answered_text"] != result.get("answer", question["answer"]):
            children.append(entered_answer_row(u, result["answered_text"], text_role=learning_text_role(question, "answer")))
        explanation = distinct_explanation(result.get("explanation"), result.get("answer", question["answer"]))
        if explanation and explanation != str(result.get("answer", question["answer"])).strip():
            children.append(u.label(explanation, size=15))
        from .ai_questions import order_translation_source
        reference = question.get('order_translation')
        source = order_translation_source(question) if isinstance(reference, dict) else None
        if source:
            children.extend(self.order_translation_rows(source, '',
                explicit=reference, show_source=True))
        target = self.learning_store.get_item(question["target_id"])
        actions = [u.button("问 AI", lambda w, qid=self._ai_current["id"]: self.open_tutor_question(
            source="ai", question_id=qid, after_answer=True), compact=True, flex=1)]
        if target:
            actions.append(u.button("详细", lambda w: self.show_learning_detail(target), compact=True, flex=1))
        children.append(u.row(*actions))
        children.append(u.button("下一题", lambda w, qid=self._ai_current["id"]: self.finish_ai_question(question_id=qid), primary=True))
        self.screen("ai_feedback", "训练反馈", *children)
        autoplay_answer(self, audio_text, ("ai", self._ai_current["id"]))

    def finish_ai_question(self, widget=None, *, question_id=None, **kwargs):
        if self.page != "ai_feedback" or question_id is not None and question_id != self._ai_current["id"]:
            return
        self.ai_practice_store().complete(self._ai_current["id"])
        self.continue_ai_practice()

    def play_ai_prompt(self, widget=None, *, question_id=None, **kwargs):
        if self.page != "ai_question" or question_id is not None and question_id != self._ai_current["id"]:
            return
        text = prompt_audio_text(self._ai_current["question"])
        if not text:
            return
        self.speak_text(text)

    def leave_ai_practice(self, widget=None, **kwargs):
        self.platform.stop()
        self.back_stack.pages.clear()
        self._practice_context = False
        self._view_key = None
        self._returning_home = True
        try:
            self.show_home()
        finally:
            self._returning_home = False
        self.back_stack.pages.clear()

    def request_ai_practice_exit(self):
        if self.page in {"ai_practice_done", "retention_done", "retention_wait"}:
            AIUIMixin.leave_ai_practice(self)
            return True
        if getattr(self, "_leave_confirmation_active", False):
            return True
        self._leave_confirmation_active = True
        token = self._page_token
        async def confirm():
            try:
                confirmed = await self.main_window.dialog(ui().toga.ConfirmDialog("返回首页？", "练习和已输入的答案已保存，可以稍后继续。"))
                if confirmed and self._page_token == token:
                    AIUIMixin.leave_ai_practice(self)
            finally:
                self._leave_confirmation_active = False
        self.loop.create_task(confirm())
        return True
