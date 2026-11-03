"""Continuous material preview and explicit enrollment, outside normal queues."""
from .navigation import page_view
from .material_study import MaterialStudyStore
from .material_share_ui import MaterialShareUIMixin


def ui():
    from . import ui_components as app
    return app


class MaterialStudyUIMixin(MaterialShareUIMixin):
    def refresh_material_selection(self):
        if getattr(self, "_ai_form_context", {}).get("mode") != "analyze":
            return
        label = getattr(self, "_ai_source_scope_label", None)
        source = getattr(self, "_ai_form_fields", {}).get("source_text")
        if label is None or source is None:
            return
        text = str(source.value or "")
        if not text.strip():
            label.text = ""
            return
        from .material_input import current_selection, selected_text
        try:
            selection = current_selection(self.store, text, draft_key=self._ai_form_context["key"])
            label.text = ("发送全文" if selected_text(selection) == text else
                          f"只发送所选 {len(selection['spans'])} 段，共 {len(selected_text(selection))} 字")
        except ValueError as exc:
            label.text = str(exc)

    @page_view()
    def show_material_selection(self, widget=None, **kwargs):
        if self.page != "ai_form" or self._ai_form_context["mode"] != "analyze":
            return
        if getattr(self, "_ai_request_operation", None) is not None or getattr(self, "_material_file_busy", False):
            return
        from .ai_ui import AI_FORM_SETTING
        from .material_input import current_selection, select_ranges, paragraph_ranges, selection_key
        form_context = self._ai_form_context.copy()
        text = str(self._ai_form_fields["source_text"].value or "")
        try:
            full = select_ranges(text, [(0, len(text))])
        except ValueError as exc:
            self._ai_form_status.text = str(exc)
            return
        self.save_ai_form()
        try:
            saved = current_selection(self.store, text, draft_key=form_context["key"])
        except ValueError:
            saved = {"spans": []}
        u = ui()
        context = self._material_selection_context = object()
        rows, controls = [], []
        for start, end in paragraph_ranges(text):
            checked = any(span["start"] <= start and end <= span["end"] for span in saved["spans"])
            check = u.toga.Switch("", value=checked)
            controls.append((check, start, end))
            rows.append(u.row(check, u.label(text[start:end], size=17, flex=1, text_role="study")))
        status = u.label("", size=12, color=u.MUTED)
        def apply(full_text=False):
            if self.page != "material_selection" or self._material_selection_context is not context:
                return
            current = self.store.get_setting(AI_FORM_SETTING, {}).get("drafts", {}).get(form_context["key"], {}).get("source_text")
            if current != text:
                status.text = "原文已修改，请返回重新选择。"
                return
            try:
                selected = full if full_text else select_ranges(text, [(a, b) for control, a, b in controls if control.value])
                self.store.set_setting(selection_key(form_context["key"]), selected)
            except ValueError as exc:
                status.text = str(exc)
                return
            self.show_ai_form("analyze", input_id=form_context.get("input_id"))
        self.screen("material_selection", "选择发送范围", *rows, status,
            u.button("使用所选段落", lambda w: apply(), primary=True),
            u.button("使用全文", lambda w: apply(True), compact=True))

    async def read_material_file(self, widget=None, **kwargs):
        if self.page != "ai_form" or self._ai_form_context["mode"] != "analyze":
            return
        if getattr(self, "_ai_request_operation", None) is not None:
            self._ai_form_status.text = "请等待当前材料任务完成后再导入。"
            return
        context, token = self._ai_form_context, self._page_token
        if getattr(self, "_material_file_busy", False):
            return
        self._material_file_busy = True
        phase = "read"
        try:
            from .material_input import decode_text, save_origin
            data = await self.platform.import_material_text()
            if (data is None or self.page != "ai_form" or self._page_token != token
                    or self._ai_form_context is not context):
                return
            phase = "decode"
            text = decode_text(data)
            self._ai_form_fields["source_text"].value = text
            phase = "save"
            if self.save_ai_form() is False:
                return  # Preserve the form's precise rejection and its input.
            save_origin(self.store, text, "utf8_file")
            self._ai_form_status.text = "文本已导入，可编辑后再解析。"
        except Exception as exc:
            from .diagnostic_events import record_error
            record_error("material_input", phase, exc)
            if self.page == "ai_form" and self._page_token == token and self._ai_form_context is context:
                self._ai_form_status.text = (
                    "文本已读入，但保存未完成；当前输入已保留，请重试。" if phase == "save" else
                    "未能导入。请选择 UTF-8 文本，最多 4000 字、16,000 字节。" if phase == "decode" else
                    "未能读取文件，请重试。")
        finally:
            self._material_file_busy = False

    def material_study(self):
        return MaterialStudyStore(self.store, self.learning_store, repo=self.repo, examples=self.example_store())

    def material_study_actions(self, identity):
        u = ui()
        try:
            targets = self.material_study().teaching_targets(identity)
            from .material_eligibility import pretest_targets
            checks = pretest_targets(self.store, identity, repo=self.repo, examples=self.example_store())
        except ValueError:
            return []
        from .material_senses import MaterialSenseStore
        sense_actions = ([u.button('选择对应词义', lambda w: self.show_material_senses(identity))]
            if MaterialSenseStore(self.store, repo=self.repo).options(identity) else [])
        if not targets and not checks:
            return sense_actions
        state = self.material_study().get(identity)
        from .material_pretest import get
        receipt = get(self.store, identity) or state
        episode = self.diagnostics().get(receipt['diagnostic_id']) if receipt and receipt.get('diagnostic_id') else None
        check_label = ("查看检查结果" if episode and episode["status"] in {"completed", "stopped", "budget"}
                       else "继续材料检查" if episode else "先检查，再决定")
        children = [*sense_actions, u.label(f"可检查 {len(checks)} 项相关内容", size=14),
                u.button(check_label,
                    lambda w: self.start_material_pretest(identity))]
        if targets:
            children.extend([u.button("继续看讲解" if state else "先看讲解", lambda w: self.show_material_study(identity)),
                             *self.material_enrollment_actions(identity)])
            extension = self.material_study().extension_preview(identity) if state else None
            if extension:
                def append_lessons(widget=None):
                    try:
                        self.material_study().extend(identity, extension)
                    except ValueError as exc:
                        self.alert(str(exc)); return
                    self.show_material_study(identity)
                children.append(u.button(f"追加新核实内容（{len(extension['added_target_ids'])} 项）", append_lessons))
        if receipt:
            from .material_pretest import added_targets
            extra = added_targets(self.material_study(), identity)
            if extra:
                children.append(u.button(f"只检查新增内容（{len(extra)} 项）",
                    lambda w: self.start_material_pretest(identity, append=True)))
        return children

    @page_view(detail=True, refresh=True)
    def show_material_candidate_facts(self, identity, target_id):
        from .material_qualification import targets
        item = next((item for item in targets(self.store, identity, repo=self.repo, examples=self.example_store())
                     if item['id'] == target_id), None)
        u = ui()
        if item is None:
            self.alert('相关依据已变化，原检查记录已保留。')
            return
        self.diagnostics().expose_visible([(item['id'], ability) for ability in item['abilities']])
        children = [u.label(item['title'], size=24, text_role='study'), u.label(item['meaning'], size=17)]
        if item['reading']:
            def speak(widget=None):
                current = next((row for row in targets(self.store, identity, repo=self.repo, examples=self.example_store())
                                if row['id'] == target_id and row['reading'] == item['reading']), None)
                if current and self.page == 'material_candidate_facts':
                    self.speak_text(current['reading'])
            children.append(u.row(u.label(item['reading'], size=18, text_role='study'), u.speaker_button(speak)))
        self.screen('material_candidate_facts', '核对内容', *children,
                    u.button('回到材料', lambda w: self.show_material_task(identity), compact=True))

    def material_enrollment_actions(self, identity):
        u = ui()
        ready = self.material_study().enrollment_readiness(identity)
        state = self.material_study().get(identity)
        same_targets = state is None or set(ready['target_ids']) <= set(state['target_ids'])
        if ready['ready'] and same_targets:
            return [u.button("加入学习计划", lambda w: self.show_material_enrollment(identity), compact=True)]
        if any(row.get('reason') == 'not_retained' for row in ready['blocked']):
            job = self.material_workflow().get(identity)
            return [u.button('选择保留材料', lambda w: self.show_ai_proposal(job['proposal_id']), compact=True)]
        from .material_admission import MaterialAdmissionStore
        if any(row['abilities'] for row in MaterialAdmissionStore(self.material_study()).options(identity)):
            return [u.button('加入已核能力', lambda w: self.show_material_ability_admission(identity), compact=True)]
        return [u.label("可先检查和查看已核说明；正式加入前还需补齐相应能力的教案与已核例句。", size=13, color=u.MUTED)]

    @page_view(detail=True, refresh=True)
    def show_material_ability_admission(self, identity):
        from .material_admission import MaterialAdmissionStore
        u = ui()
        service = MaterialAdmissionStore(self.material_study())
        options = service.options(identity)
        context = self._material_admission_context = object()
        controls, rows, pending = {}, [], {}
        status = u.label('', size=14, color=u.MUTED)
        labels = {'understanding': '词义', 'reading': '读音'}
        def active():
            return self.page == 'material_enrollment' and self._material_admission_context is context
        def refresh(widget=None):
            if widget is not None and not active():
                return
            selected = {cid: [ability for ability, control in choices.items() if control.value]
                        for cid, choices in controls.items()}
            selected = {cid: values for cid, values in selected.items() if values}
            try:
                preview = service.preview(identity, selected)
            except ValueError as exc:
                pending.clear()
                status.text = str(exc)
                confirm_button.enabled = False
                return
            pending['preview'] = preview
            prerequisites = len(set(preview['to_enroll']) - {edge['canonical_id'] for edge in preview['mappings']})
            status.text = ('会先学习所需前置内容。' if prerequisites else '') + '已有记录和复习时间保持不变。'
            confirm_button.enabled = True
        for option in options:
            item = option['item']
            choices = {}
            for ability in option['abilities']:
                control = u.switch(labels[ability], value=True)
                control.on_change = refresh
                choices[ability] = control
            if not choices:
                continue
            controls[option['candidate_id']] = choices
            waiting = [labels[a] for a in item['abilities'] if a in labels and a not in choices]
            rows.append(u.col(u.label(item['title'], size=21, bold=True, text_role='study'),
                u.label(item['meaning'], size=16), *choices.values(),
                *([u.label('、'.join(waiting) + '暂不加入', size=12, color=u.MUTED)] if waiting else [])))
        self._material_admission_controls = controls
        def confirm(widget=None):
            if not active() or 'preview' not in pending:
                return
            try:
                service.enroll(pending['preview'])
            except ValueError as exc:
                status.text = str(exc)
                return
            self.continue_learning()
        confirm_button = u.button('加入并继续学习', confirm, primary=True)
        refresh()
        # The visible meaning itself is an exposure, still owned by the
        # candidate. Accepted mappings project its actual timestamp read-only.
        self.diagnostics().expose_visible([(cid, 'understanding') for cid in controls])
        self.screen('material_enrollment', '选择已核能力',
            u.label('使用已核实的课程教案学习所选能力。', size=15), *rows, status,
            confirm_button, u.button('暂不加入', lambda w: self.show_material_task(identity)))

    def start_material_pretest(self, identity, *, append=False):
        try:
            episode = self.material_study().start_pretest(identity, self.learning_preferences()["enabled"], append=append)
        except ValueError as exc:
            self.alert(str(exc))
            return
        self.continue_diagnostic(episode["id"])

    @page_view(detail=True, refresh=True)
    def show_material_study(self, identity):
        u = ui()
        try:
            state = self.material_study().present(identity)
        except ValueError as exc:
            try:
                revised = self.material_study().lesson_revision_preview(identity)
            except ValueError:
                revised = None
            if revised:
                owner = object(); self._material_lesson_revision_owner = owner
                def refresh(widget=None):
                    if self.page != 'material_study' or self._material_lesson_revision_owner is not owner: return
                    try:
                        self.material_study().refresh_lesson(identity, revised)
                    except ValueError as error:
                        self.alert(str(error)); return
                    self.show_material_study(identity)
                self.screen('material_study', '讲解已更新',
                    u.label(revised['item']['title'], size=22, bold=True, text_role='study'),
                    u.button('使用已核实的新讲解', refresh, primary=True),
                    u.button('稍后继续', lambda w: self.show_material_task(identity), compact=True))
            else:
                self.alert(str(exc))
            return
        self._material_study_id = identity
        row = next((row for row in state["lessons"] if row["completed_at"] is None), None)
        if row is None:
            self.screen("material_study", "本批讲解已看完",
                u.button("检查相关内容", lambda w: self.start_material_pretest(identity), primary=True),
                *self.material_enrollment_actions(identity),
                u.button("回到材料", lambda w: self.show_material_task(identity)))
            return
        item = row["item"]
        from .structured_lesson_ui import teaching_block
        index = sum(row["completed_at"] is not None for row in state["lessons"]) + 1
        def speak(text):
            current = self.material_study().get(identity)
            pending = next((row for row in current["lessons"] if row["completed_at"] is None), None) if current else None
            if self.page != "material_study" or pending is None or pending["item"]["id"] != item["id"]:
                return
            self.material_study().present(identity, surface="tts")
            self.speak_text(text)
        def advance(widget=None):
            if self.page != "material_study" or self._material_study_id != identity:
                return
            try:
                self.material_study().advance(identity, item["id"])
            except ValueError as exc:
                self.alert(str(exc))
                return
            self.show_material_study(identity)
        caption = u.label(f"{index}/{len(state['lessons'])}", size=13, color=u.MUTED)
        caption._feedback_fragment = {"kind": "material_prelearn", "workflow_id": identity,
            "target_id": item["id"], "revision": item["revision"], "lesson_sha256": row["sha256"]}
        if item.get("dictionary_only"):
            lesson = u.col(u.label(item['title'], size=24, bold=True, text_role="study"),
                *([u.row(u.label(item['reading'], size=18, flex=1, text_role="study"),
                      u.speaker_button(lambda w: speak(item['reading'])))] if item.get('reading') else []),
                u.label(item['meaning'], size=17),
                *([] if item.get('material_fact_scope') else [u.label("这是所列词典义项；原句中的具体用法另行核验。", size=12, color=u.MUTED)]))
        else:
            lesson = teaching_block(u, item, speak=speak)
        self.screen("material_study", "先看讲解", caption, lesson,
            u.button("继续", advance, primary=True),
            u.button("稍后继续", lambda w: self.show_material_task(identity), compact=True))

    @page_view(detail=True, refresh=True)
    def show_material_enrollment(self, identity):
        u = ui()
        try:
            preview = self.material_study().enrollment_preview(identity)
        except ValueError as exc:
            self.alert(str(exc))
            return
        self._material_enrollment = {"id": identity, "preview": preview}
        context = self._material_enrollment
        added = len(preview["to_enroll"])
        prerequisites = len(set(preview["to_enroll"]) - set(preview["target_ids"]))
        message = f"将加入 {added} 项" + (f"，其中 {prerequisites} 项是所需前置内容。" if prerequisites else "。")
        if not added:
            message = "相关内容已在学习计划中，原记录和到期时间保持不变。"
        labels = {'understanding': '理解', 'reading': '读音', 'use': '提取与使用',
                  'listening': '听辨', 'application': '应用'}
        controls, rows = {}, []
        for item in self.material_study().available_targets(identity):
            if item['id'] not in preview['target_ids']:
                continue
            existing = self.learning_store.get_item(item['id'])
            admitted = set(existing['enrolled_abilities']) if existing else set()
            choices = {}
            for ability in item['abilities']:
                control = u.switch(labels.get(ability, ability), value=not existing or ability in admitted)
                control.enabled = ability not in admitted
                choices[ability] = control
            controls[item['id']] = choices
            rows.append(u.col(u.label(item['title'], size=19, bold=True,
                                     text_role='study' if item['family'] == 'word' else 'ui'),
                              *choices.values()))
        self._material_ability_controls = controls
        if not added and any(control.enabled for choices in controls.values() for control in choices.values()):
            message = '选择要补充学习的能力。'
        status = u.label('', size=13, color=u.MUTED)
        def confirm(widget=None):
            if self.page != "material_enrollment" or self._material_enrollment is not context:
                return
            try:
                selected = {cid: [ability for ability, control in choices.items() if control.value]
                            for cid, choices in controls.items()}
                self.material_study().enroll(identity, {**preview, 'abilities': selected})
            except ValueError as exc:
                status.text = str(exc)
                return
            self.continue_learning()
        self.screen("material_enrollment", "加入相关内容", u.label(message, size=16),
            *rows, status,
            u.button("加入并继续学习" if added else "确认并继续学习", confirm, primary=True),
            u.button("暂不加入", lambda w: self.show_material_task(identity)))
