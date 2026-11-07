"""Sentence review status is visible; provenance stays on a secondary page."""
from __future__ import annotations

from copy import deepcopy

from .example_store import example_id_for
from .navigation import page_view
from .presentation import payload
from .previews import _sense_preview
from .typography import configure_study_ranges
from . import ui_components as u

STATES = {"全部": "all", "存疑": "uncertain", "已修正": "corrected", "未发现问题": "reviewed"}
STATUS_LABELS = {"reviewed": "未发现问题", "corrected": "已修正", "uncertain": "待核",
                 "rejected": "已撤下", "confirmed": "已确认"}


def _selected_meanings(word, link):
    data = {**(payload(word) or {}), **word}
    selected = set(link.get("sense_ids", []))
    chinese = {s["sense_id"]: s for s in data.get("chinese_senses", [])}
    return "；".join(_sense_preview(sense, number, chinese.get(sense.get("sense_id")))
                    for number, sense in enumerate(data.get("senses", []), 1)
                    if sense.get("sense_id") in selected)


class LexicalExampleUIMixin:
    def lexical_example_card(self, example, word_id, index=0):
        from .personal_content_versions import resolve
        example = resolve(self.store, example, kind='lexical_example')
        review = example["review"]
        links = review.get("matched_associations", [])
        spans = [s for a in links if a["status"] != "rejected" for s in a["spans"]]
        from .personal_reading import presentation, spoken_text
        text = self.annotated_sentence(example["text"], size=19, highlight_spans=spans,
            available_width=self._lexical_sentence_width(), **presentation(example))
        text.style.flex = 1
        text._feedback_fragment = {**text._feedback_fragment, "kind": "lexical_example", "example_id": example["id"],
            "example_snapshot": deepcopy(example), "word_id": word_id, "spans": spans}
        children = [u.row(text, *([] if example.get('personal_reading', {}).get('pending') else [u.speaker_button(
                         lambda w: self.speak_text(spoken_text(example)), description="播放日语：" + example["text"])])),
                    self.natural_translation_label(example['text'], example['chinese_translation'], resource=example, size=15),
                    *self.order_translation_rows(example['text'], example['chinese_translation'],
                        explicit=example.get('order_translation'))]
        if review["state"] == "uncertain":
            children.append(u.label("有待核内容", size=12, color=u.MUTED))
        children.append(u.button("例句详情", lambda w: self.show_lexical_example_detail(example["id"]), compact=True))
        return self.entry_card(*children, index=index)

    @page_view(detail=True)
    def show_lexical_example_detail(self, identity):
        from .personal_content_versions import resolve
        example = resolve(self.store, self.repo.get_example(identity), kind='lexical_example')
        if not example or not example.get("review"):
            self.alert("这条例句的审校资料暂不可用。")
            return
        review = example["review"]
        spans = [s for a in review["associations"] if a["status"] in {"confirmed", "corrected"} for s in a["spans"]]
        from .personal_reading import presentation, spoken_text
        text = self.annotated_sentence(example["text"], size=22, highlight_spans=spans,
            available_width=self._lexical_sentence_width(), **presentation(example))
        text.style.flex = 1
        text._feedback_fragment = {**text._feedback_fragment, "kind": "lexical_example", "example_id": identity,
            "example_snapshot": deepcopy(example), "spans": spans}
        children = [u.row(text, *([] if example.get('personal_reading', {}).get('pending') else [u.speaker_button(
                         lambda w: self.speak_text(spoken_text(example)), description="播放日语：" + example["text"])])),
                    self.natural_translation_label(example['text'], example['chinese_translation'], resource=example, size=16),
                    *self.order_translation_rows(example['text'], example['chinese_translation'],
                        explicit=example.get('order_translation'))]
        for field, caption in (("japanese", "日语待核"), ("chinese_translation", "中文译文待核")):
            decision = review[field]
            if decision["status"] == "uncertain" and not (field == 'chinese_translation' and example.get('personal_revision')):
                children.append(u.label(caption, size=14, color=u.MUTED))
        seen = set()
        for link in review["associations"]:
            if link["status"] == "rejected" or link["entry_id"] in seen:
                continue
            seen.add(link["entry_id"])
            word = self.repo.get_word(link["entry_id"])
            if not word:
                continue
            meaning = _selected_meanings(word, link)
            label = word["form"] + (" · " + meaning if meaning else "")
            if link["status"] == "uncertain":
                label += "（关联待核）"
            elif not link.get("resolved_reading"):
                label += "（具体读法待核）"
            block = u.label(label, size=15)
            configure_study_ranges(block, [(0, len(word["form"]))])
            children.extend([block, u.button("词条", lambda w, wid=word["id"]: self.open_content_word(wid), compact=True)])
        shared_id = example.get('personal_shared_example_id') or example_id_for(example["text"])
        if self.example_store().get(shared_id):
            children.append(u.button("关联学习项", lambda w: self.show_example_detail(shared_id)))
        from .personal_content_notes_ui import append_notes
        append_notes(self, u, children, 'lexical_example', example, page='lexical_example_detail')
        children.append(u.button("审校与来源", lambda w: self.show_lexical_example_sources(identity), compact=True))
        self.screen("lexical_example_detail", "例句", *children)

    @page_view(detail=True)
    def show_lexical_example_sources(self, identity):
        example = self.repo.get_example(identity)
        if not example or not example.get("review"):
            self.alert("这条例句的审校资料暂不可用。")
            return
        review, source = example["review"], example["payload"]
        children = [u.label("审校与来源", size=19, bold=True), *self.sentence_source_rows(example['text']),
            u.label("模型逐句语义审校，未代替母语编辑认证。存疑关联不会加入课程例句。", size=13, color=u.MUTED)]
        children[0]._feedback_fragment = {"kind": "lexical_review", "example_id": identity,
            "review": deepcopy(review), "original": deepcopy(source)}
        if review.get("notes"):
            children.append(u.label(review["notes"], size=14))
        jp = review["japanese"]
        children.extend([u.label("原始日语 · " + STATUS_LABELS[jp["status"]], bold=True),
                         u.label(source["text"], text_role="study", size=17)])
        if jp.get("reason"):
            children.append(u.label(jp["reason"], size=14))
        # A corrected Chinese translation belongs to the current display text,
        # not necessarily to the historical Japanese retained above.
        current_text = example["text"]
        if current_text != source["text"]:
            children.extend([u.label("本版日语", size=13),
                             u.label(current_text, text_role="study", size=17)])
        children.append(self.natural_translation_label(current_text, review['chinese_translation']['text'], prefix='随包审校中文：', size=15))
        children.extend(self.natural_translation_review_rows(current_text, review['chinese_translation']['text']))
        children.extend(self.order_translation_rows(current_text, review["chinese_translation"]["text"],
            explicit=example.get('order_translation')))
        if review["chinese_translation"].get("reason"):
            children.append(u.label("中文译文待核：" + review["chinese_translation"]["reason"], size=14, color=u.MUTED))
        for decision in review["translations"]:
            original = source.get("translations", [])[decision["index"]]
            children.extend([u.label("原译文 · " + STATUS_LABELS[decision["status"]], size=13, bold=True),
                             u.label(original["text"], size=15)])
            if decision.get("text") and decision["text"] != original["text"]:
                children.append(u.label("修正：" + decision["text"], size=15))
            if decision.get("reason"):
                children.append(u.label(decision["reason"], size=14, color=u.MUTED))
            children.extend(self._lexical_source_credit(original))
        for link in review["associations"]:
            children.append(u.label("关联 · " + STATUS_LABELS[link["status"]] + " · " + link["reason"], size=14))
            if link["status"] in {"confirmed", "corrected"} and not link.get("resolved_reading"):
                children.append(u.label("具体读法未定，暂不接入课程例句。", size=13, color=u.MUTED))
        children.extend(self._lexical_source_credit(source))
        if source.get("content_origin") == "ai_generated":
            children.append(u.label("原例句由模型编写；本版保留原始文本与审校记录。", size=13, color=u.MUTED))
        for url in review.get("sources", []):
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                children.append(u.button("核对资料", lambda w, address=url: self.open_url(address, title="核对资料", summary="例句审校依据"), compact=True))
        self.screen("lexical_example_sources", "审校与来源", *children)

    def _lexical_source_credit(self, source):
        children = []
        credit = " · ".join(dict.fromkeys(x for x in (source.get("attribution"), source.get("license")) if x))
        if credit:
            children.append(u.label(credit, size=12, color=u.MUTED))
        for key, title in (("attribution_url", "原资料"), ("license_url", "许可说明")):
            url = source.get(key)
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                children.append(u.button(title, lambda w, address=url, caption=title:
                    self.open_url(address, title=caption, summary=source.get("text", "")), compact=True))
        return children

    def _lexical_provenance_children(self, snapshot, *, shown_review_pair=None):
        """Render portable source credits only on the shared example's source page."""
        source = snapshot["original"]
        children = [u.label("词库例句来源 · " + snapshot["review_id"], size=15, bold=True),
                    u.label("原始日语", size=13), u.label(source["text"], text_role="study", size=17)]
        children[0]._feedback_fragment = {"kind": "lexical_provenance", "snapshot": deepcopy(snapshot)}
        children.extend(self._lexical_source_credit(source))
        reviewed = snapshot.get("reviewed_text")
        if reviewed and reviewed != source["text"]:
            children.extend([u.label("审校后日语", size=13), u.label(reviewed, text_role="study", size=17)])
        for translation in source.get("translations", []):
            children.extend([u.label("原译文" + (" · " + translation["language"] if translation.get("language") else ""), size=13),
                             u.label(translation["text"], size=15)])
            children.extend(self._lexical_source_credit(translation))
        if snapshot.get("chinese_translation"):
            # Portable legacy snapshots may omit the reviewed Japanese. Do not
            # assume their corrected translation still belongs to the old text.
            if reviewed:
                children.append(self.natural_translation_label(reviewed, snapshot['chinese_translation'],
                    prefix='审校后中文：', size=15))
                if (reviewed, snapshot['chinese_translation']) != shown_review_pair:
                    children.extend(self.natural_translation_review_rows(reviewed, snapshot['chinese_translation']))
                children.extend(self.order_translation_rows(reviewed, snapshot["chinese_translation"]))
            else:
                children.append(u.label("审校后中文：" + snapshot["chinese_translation"], size=15))
        if snapshot.get("notes"):
            children.append(u.label(snapshot["notes"], size=14))
        for url in snapshot.get("sources", []):
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                children.append(u.button("核对资料", lambda w, address=url:
                    self.open_url(address, title="核对资料", summary="例句审校依据"), compact=True))
        return children

    @page_view()
    def show_lexical_example_library(self, widget=None, *, query="", state="all", offset=0):
        self._render_lexical_example_library(query, state, offset)

    def _render_lexical_example_library(self, query="", state="all", offset=0):
        self.lexical_example_query = u.toga.TextInput(value=query, placeholder="搜索词库例句或中文", style=u.Pack(margin_bottom=6))
        self.lexical_example_state = u.selection(items=list(STATES), style=u.Pack(margin_bottom=6))
        self.lexical_example_state.value = next(k for k, v in STATES.items() if v == state)
        search = lambda w: self._render_lexical_example_library(self.lexical_example_query.value or "", STATES[self.lexical_example_state.value])
        self.lexical_example_query.on_confirm = search
        self.lexical_example_state.on_change = search
        self._lexical_example_library_state = (query, state, offset)
        from .personal_content_versions import changed_resources, resolve
        from .personal_graph import affected_lexical_ids
        overrides = {}
        changed = set(changed_resources(self.store)) | {('lexical_example', identity) for identity in affected_lexical_ids(self.store)}
        for kind, identity in changed:
            if kind != 'lexical_example': continue
            current = resolve(self.store, self.repo.get_example(identity), kind=kind)
            if current:
                overrides[identity] = {'search_text': '\n'.join([current['text'], current.get('chinese_translation', ''), current.get('reading', '')]),
                    'state': 'uncertain' if current.get('personal_content_unavailable') else current['review_state']}
        page = self.repo.reviewed_example_page(query, state, offset, display_overrides=overrides)
        children = [self.lexical_example_query, self.lexical_example_state, u.button("搜索", search, compact=True),
                    u.label(f"共 {page['total']} 条", size=13, color=u.MUTED)]
        children[-1]._feedback_fragment = {"kind": "lexical_example_library", "query": query,
            "state": state, "offset": offset, "total": page["total"],
            "ordered_example_ids": [e["id"] for e in page["rows"]]}
        for index, example in enumerate(page["rows"]):
            example = resolve(self.store, example, kind='lexical_example')
            from .personal_reading import presentation
            rows = [self.annotated_sentence(example['text'], audio=True, **presentation(example)), self.natural_translation_label(example['text'], example['chinese_translation'], resource=example, size=14),
                    *self.order_translation_rows(example['text'], example['chinese_translation'],
                        explicit=example.get('order_translation'))]
            if example['review_state'] == 'uncertain':
                rows.append(u.label('待核', size=12, color=u.MUTED))
            rows.append(u.button('例句详情', lambda w, eid=example['id']: self.show_lexical_example_detail(eid), compact=True))
            children.append(self.entry_card(*rows, index=index))
        if offset:
            children.append(u.button("上一页", lambda w: self._render_lexical_example_library(query, state, max(0, offset - 10))))
        if offset + 10 < page["total"]:
            children.append(u.button("下一页", lambda w: self._render_lexical_example_library(query, state, offset + 10)))
        self.screen("lexical_example_library", "词库例句", *children)

    def _lexical_sentence_width(self):
        try:
            return max(80, float(self.main_window.size.width) - 112)
        except (AttributeError, TypeError, ValueError):
            return 248
