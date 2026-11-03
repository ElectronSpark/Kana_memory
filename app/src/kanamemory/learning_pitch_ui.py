"""Native pitch controls. Every edit is a draft, never a review event."""
import asyncio
from copy import deepcopy

from . import pitch_practice as pitch


def _width(app):
    try:
        return max(160, float(app.main_window.size.width) - 48)
    except (AttributeError, TypeError, ValueError):
        return 312


def _viewport_height(app, layout):
    """Keep an entire large-font row and the answer controls within reach."""
    try:
        height = float(app.main_window.size.height)
    except (AttributeError, TypeError, ValueError):
        height = 800
    return max(layout['row_content_height'] + 16, min(280, max(240, height * .34)))


def _bind_viewport_gesture(context, viewport, diagram, *, android):
    """Reserve a canvas gesture for the inner scroller, not the outer page."""
    canvas = getattr(diagram, '_pitch_canvas', None) if android and viewport is not None else None
    previous = context.get('touch_binding')
    if previous is not None:
        if previous['canvas'] is canvas and previous['viewport'] is viewport:
            return
        previous['release']()
        previous['canvas'].on_press = None
        previous['canvas'].on_release = None
        context.pop('touch_binding')
    if canvas is None:
        return
    reserved = [None]

    def release(widget=None, x=None, y=None, **kwargs):
        parent, reserved[0] = reserved[0], None
        if parent is not None:
            parent.requestDisallowInterceptTouchEvent(False)

    def press(widget, x=None, y=None, **kwargs):
        release()
        # Starting at the viewport's parent leaves the inner ScrollView free
        # to intercept this drag. Reserving the Canvas parent would block it.
        parent = viewport._impl.native.getParent()
        if parent is not None:
            parent.requestDisallowInterceptTouchEvent(True)
            reserved[0] = parent

    canvas.on_press, canvas.on_release = press, release
    context['touch_binding'] = {'canvas': canvas, 'viewport': viewport, 'release': release}


def _reveal_pitch_row(viewport, diagram, data):
    """Scroll only enough to reveal the active row, in content DIP coordinates."""
    height = viewport.layout.content_height
    if height <= 0:
        return False
    layout = diagram._pitch_layout
    index = (data.get('active_span') or {}).get('start', data.get('cursor'))
    if index is None:
        index = len(data['surface']['units'] if data.get('surface') else data['characters']) - 1
    cells = [cell for row in layout['rows'] for cell in row]
    # Completion and punctuation gaps keep the last relevant row in view.
    cell = next((cell for cell in cells if cell['index'] >= index), cells[-1])
    canvas = getattr(diagram, '_pitch_canvas', None)
    if canvas is not None:
        offset = canvas.layout.absolute_content_top - diagram.layout.absolute_content_top
    else:
        # Desktop widget rendering uses the same fixed row geometry.
        first_row = next(child for child in diagram.children
                         if any(hasattr(node, '_pitch_cell') for node in child.children))
        offset = first_row.layout.absolute_content_top - diagram.layout.absolute_content_top - 8
    top = offset + cell.get('top', 8 + cell['row'] * layout['row_height'])
    bottom = offset + cell['bottom'] if 'bottom' in cell else top + layout['row_content_height']
    position = viewport.vertical_position
    if top < position:
        viewport.vertical_position = max(0, int(top) - 4)
    elif bottom > position + height:
        viewport.vertical_position = max(0, int(bottom - height) + 4)
    horizontal_visible = True
    if data.get('surface'):
        visible_width = viewport.layout.content_width
        horizontal_visible = visible_width > 0
        if visible_width > 0:
            focus = cell.get('focus_x')
            if cell.get('right', 0) - cell.get('left', 0) <= visible_width:
                left, right = cell.get('left', 0), cell.get('right', 0)
            elif focus is not None:
                half_beat = cell.get('beat_width', layout.get('beat_width', 18)) / 2 + 4
                left, right = max(0, focus - half_beat), focus + half_beat
            else:
                left, right = cell.get('left', 0), cell.get('right', 0)
            if right - left <= visible_width:
                if left < viewport.horizontal_position:
                    viewport.horizontal_position = max(0, int(left) - 4)
                elif right > viewport.horizontal_position + visible_width:
                    viewport.horizontal_position = max(0, int(right - visible_width) + 4)
            horizontal_visible = viewport.horizontal_position <= left and right <= viewport.horizontal_position + visible_width
    return (horizontal_visible and viewport.vertical_position <= top
            and bottom <= viewport.vertical_position + height)


def answer_diagram(app, question, *, answer=None, reveal=False):
    from . import ui_components as u
    from .pitch_practice_ui import fixed_pitch_diagram
    data = pitch.fixed_view(question, answer=answer, reveal=reveal)
    return fixed_pitch_diagram(u, **data, width=_width(app), show_counts=reveal)


def _symbol_button(u, symbol, description, handler):
    control = u.button(symbol, handler, flex=1, compact=True, font_size=22)
    control._pitch_action_description = description
    if u.current_platform == "android":
        control._impl.native.setContentDescription(description)
        control._impl.native.setTooltipText(description)
    return control


class LearningPitchUIMixin:
    def learning_pitch_controls(self, question, draft, *, guided=False, on_change=None, on_submit=None, is_active=None):
        """The retained controls own their question, including after feedback."""
        from . import ui_components as u
        from .pitch_practice_ui import fixed_pitch_diagram
        state = pitch.editor_draft(question, draft if draft.get("pitch_state") else {**draft, **pitch.initial_draft(question)})
        holder, message = u.col(), u.label("", size=12, color=u.MUTED)
        context = {"question": question, "draft": state, "guided": guided,
                   "holder": holder, "message": message, "buttons": {}, "ready": False,
                   "locked": False}
        self._learning_pitch_context = context

        def active():
            if is_active is not None:
                return is_active() and not context['locked']
            attr = "_learning_guide" if guided else "_learning_question"
            page = "learning_guide" if guided else "learning_question"
            return self.page == page and getattr(self, attr, None) is question and not context["locked"]

        def render(*, follow_cursor=True):
            view = pitch.state_view(question, context["draft"])
            data = (view["display"] if question["pitch_practice"].get("version") in (2, 3)
                    else pitch.fixed_view(question, context["draft"]))
            update = getattr(context.get("diagram"), "_update_pitch", None)
            updated = update(**data, **({'width': _width(self)} if data.get('surface') else {})) if update else False
            if not updated:
                context["diagram"] = fixed_pitch_diagram(u, **data, width=_width(self),
                    **({'viewport_owned': True} if data.get('surface') else {}))
            diagram = context["diagram"]
            layout = diagram._pitch_layout
            height = _viewport_height(self, layout)
            surface = bool(data.get('surface'))
            overflow_width = surface and layout.get('content_width', 0) > _width(self)
            content_height = layout.get('content_height', (24 if surface else 16)
                                        + len(layout['rows']) * layout['row_height'])
            scrollable = overflow_width or content_height > height
            # A source word may gain more entered beats than written letters.
            # Keep its Canvas in the same layout tree from the first edit:
            # moving an attached Box into ScrollContainer.content clears the
            # descendants' Travertino roots, which the content setter does not
            # rebuild. Width/height changes can then blank the drawing.
            needs_viewport = (question['input_mode'] == 'pitch_fill' and
                              (surface or (len(layout['rows']) > 1 and scrollable)))
            if surface:
                height = min(height, content_height)
            viewport = context.get('viewport')
            if needs_viewport:
                if viewport is None:
                    holder.clear()
                    viewport = u.toga.ScrollContainer(content=diagram, horizontal=surface,
                        vertical=True, style=u.Pack(height=height, margin_bottom=8))
                    context['viewport'] = viewport
                    holder.add(viewport)
                else:
                    if viewport.content is not diagram:
                        viewport.content = diagram
                    if viewport.style.height != height:
                        viewport.style.height = height
            elif holder.children != [diagram]:
                holder.clear()
                if viewport is not None:
                    viewport.content = None
                context['viewport'] = viewport = None
                holder.add(diagram)
            _bind_viewport_gesture(context, viewport if scrollable else None, diagram,
                                   android=u.current_platform == 'android')
            # Native bounds settle after this callback / initial page mount.
            # Bind the deferred scroll to this exact edit, view and question.
            context['render_revision'] = context.get('render_revision', 0) + 1
            revision = context['render_revision']
            pending = context.pop('scroll_handle', None)
            if pending is not None:
                pending.cancel()
            if viewport is not None and follow_cursor and hasattr(viewport, 'layout'):
                async def reveal():
                    for _ in range(4):
                        await asyncio.sleep(.05)
                        if not (active() and getattr(self, '_learning_pitch_context', None) is context
                                and context['render_revision'] == revision
                                and context.get('viewport') is viewport and context.get('diagram') is diagram):
                            return
                        if _reveal_pitch_row(viewport, diagram, data):
                            return
                # Toga Android wakes its cooperative loop via call_soon, used
                # by create_task. A bare call_later from a native button can
                # remain asleep when no other async task is running.
                context['scroll_handle'] = self.loop.create_task(reveal())
            for action, button in context["buttons"].items():
                button.enabled = not context["locked"] and (
                    view.get("can_undo", False) if action == "undo" else
                    view.get("can_merge", False) if action == "merge" else
                    view.get("can_split", False) if action == "split" else view.get("can_input", not view["complete"]))
            context["submit"].enabled = view["complete"] and not context["locked"]

        def save(updated, *, follow_cursor=True):
            if not active():
                return
            # Persist before replacing the visible draft, so a failed write
            # leaves the learner's last saved answer and screen consistent.
            try:
                if on_change is not None:
                    on_change(updated)
                elif guided:
                    self.learning_store.advance_instruction(self._learning_item["id"], "guide",
                        state={"guide_draft": updated, "guide_question": deepcopy(question),
                               "guide_answer": None, "guide_correct": False}, instruction_scope=self._learning_item.get("instruction_scope"))
                    self._learning_guide_result.clear()
                else:
                    self.learning_store.save_draft(question["id"], updated)
                    self._learning_question_state = updated
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('learning_pitch', 'save', exc)
                message.text = "暂未保存，当前输入仍保留：" + str(exc)
                return
            context["draft"] = updated
            message.text = ""
            try:
                render(follow_cursor=follow_cursor)
            except Exception as exc:
                from .diagnostic_events import record_error
                record_error('learning_pitch', 'refresh', exc)
                message.text = "输入已保存，但界面暂未刷新；请返回后继续。"

        def action(value):
            if active():
                try:
                    save(pitch.editor_action(question, context["draft"], value),
                         follow_cursor=value != 'split' or question["pitch_practice"].get("version") == 3)
                except ValueError as exc:
                    message.text = str(exc)

        def submit(widget=None):
            if not active():
                return
            answer = context["draft"].get("answer", "")
            result = pitch.grade_answer(question, answer)
            if result.get("status") in {"empty", "invalid"}:
                message.text = result.get("message", "请完成作答。")
                return
            if on_submit is not None:
                try:
                    on_change(context['draft'])
                    on_submit(answer)
                except Exception as exc:
                    from .diagnostic_events import record_error
                    record_error('learning_pitch', 'callback', exc)
                    message.text = '作答处理未完成，当前输入仍保留；请返回检查已保存的结果。'
                return
            if guided:
                try:
                    self.answer_learning_guide(answer, draft=context["draft"])
                except Exception as exc:
                    from .diagnostic_events import record_error
                    record_error('learning_pitch', 'callback', exc)
                    message.text = '作答处理未完成，当前输入仍保留；请返回检查已保存的结果。'
                    return
                context["locked"] = result.get("correct") is True
                render()
            else:
                # An old single-word snapshot may have asked for a boundary.
                # The editor now gives that boundary; keep the normalized
                # response and reversible draft together at actual submission.
                try:
                    self.learning_store.save_draft(question["id"], context["draft"])
                except Exception as exc:
                    message.text = "暂未保存，当前输入仍保留：" + str(exc)
                    return
                self._learning_question_state = context["draft"]
                self.submit_learning_answer(answer, question_id=question["id"])

        children = [holder]
        if question["input_mode"] == "pitch_fill":
            for value, symbol, description in (("H", "▔", "高音"), ("L", "▁", "低音"),
                                                ("merge", "‿", "与前一个假名合拍"), ("undo", "⌫", "退格")):
                context["buttons"][value] = _symbol_button(u, symbol, description, lambda w, a=value: action(a))
            spec = question["pitch_practice"]
            if spec.get("version") == 2 and spec.get("task_mode") == "pitch_segmentation":
                context["buttons"]["split"] = _symbol_button(u, "│", "在当前位置分词", lambda w: action("split"))
            elif spec.get("version") == 3:
                context["buttons"]["split"] = _symbol_button(u, "│", "完成当前词，进入下一词", lambda w: action("split"))
            controls = [context["buttons"]["H"], context["buttons"]["L"]]
            if spec.get("learner_morae", False):
                controls.append(context["buttons"]["merge"])
            if "split" in context["buttons"]:
                controls.append(context["buttons"]["split"])
            controls.append(context["buttons"]["undo"])
            if u.font_scale() > 1.5:
                children.extend(u.row(*controls[n:n+2]) for n in range(0, len(controls), 2))
            else:
                children.append(u.row(*controls))
        else:
            view = pitch.state_view(question, state)
            for field, caption, options in (("mora_count", "词有几拍", view["mora_options"]),
                                            ("accent", "调号", view["accent_options"])):
                labels = {("%s 拍" % value if field == "mora_count" else "%s 型" % value): value for value in options}
                selected = state["pitch_state"].get(field)
                value = next((label for label, number in labels.items() if number == selected), "请选择")
                selector = u.selection(items=["请选择", *labels], value=value)

                def select(widget, key=field, choices=labels, **kwargs):
                    if context["ready"] and active():
                        save(pitch.set_selection(question, context["draft"], key, choices.get(widget.value)))

                selector.on_change = select
                children.extend([u.label(caption, size=14), selector])
        context["submit"] = u.button("提交", submit, primary=True)
        children.extend([context["submit"], message])
        render()
        context["ready"] = True
        return u.col(*children)
