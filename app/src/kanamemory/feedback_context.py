"""Read-only, versioned evidence for a page report, not a learner backup.

The tree describes attached widgets (including content below the viewport).
Semantic fragments preserve the exact plans and corpus revisions used to build
them. Hidden solutions belong only to the maintenance payload, never the UI
preview. This module deliberately has no dependency on Toga or Android at import.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
import json
import re
from pathlib import PurePath

FORMAT = "kanamemory-feedback-context"
VERSION = 1
MAX_WIDGETS = 12000
MAX_DEPTH = 48
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_STRING_BYTES = 128 * 1024
MAX_CONTAINER_ITEMS = 2000
MAX_VALUES = 60000
MAX_TARGETS = 200
MAX_WARNINGS = 200

_MISSING = object()
_SECRET_KEYS = frozenset({"apikey", "password", "secret", "authorization", "credential",
    "credentials", "accesstoken", "refreshtoken", "bearertoken", "clientsecret", "privatekey"})
_SECRET_TEXT = re.compile(r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|Bearer\s+[A-Za-z0-9_.-]{12,})\b")
_SENSITIVE_LABEL = re.compile(r"api[ _-]?key|password|密钥|密码|访问令牌", re.I)
_STYLE_FIELDS = ("font_family", "font_size", "font_weight", "font_style", "color",
    "background_color", "width", "height", "flex", "padding", "padding_top", "padding_bottom",
    "padding_left", "padding_right", "margin", "margin_top", "margin_bottom", "margin_left",
    "margin_right", "direction", "align_items", "text_align", "visibility", "display")
_SETTING_KEYS = ("practice_preferences", "practice_queue_kinds", "word_drill_preferences",
    "learning_preferences_v1", "prompt_display_v1", "pitch_display_v1", "answer_input_preferences_v1")
_QUESTION_PAGES = frozenset({"question", "word_question", "ink_question", "feedback", "kana_introduction"})
_SESSION_PAGES = _QUESTION_PAGES | {"session_wait", "session_done", "kana_intro_wait", "dependency_wait"}
_LEARNING_QUESTION_PAGES = frozenset({"learning_question", "learning_feedback", "learning_feedback_detail"})


def canonical_json(value):
    """Portable UTF-8 hash input; callers must exclude the outer sha256 field."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def context_sha256(context):
    """Refresh after adding screenshot metadata, without hashing its own hash."""
    return sha256(canonical_json({key: value for key, value in context.items() if key != "sha256"}).encode("utf-8")).hexdigest()


def _secret_key(key):
    compact = re.sub(r"[^a-z]", "", str(key).lower())
    return compact in _SECRET_KEYS or compact.endswith("apikey") or compact.endswith("password")


class _Capture:
    def __init__(self):
        self.warnings = []
        self.redactions = []
        self.redaction_count = 0
        self.warning_count = 0
        self.text_bytes = 0
        self.values = 0
        self.widget_count = 0
        self.fragments = []
        self.seen_widgets = {}

    def warn(self, path, reason, **detail):
        self.warning_count += 1
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append({"path": path, "reason": reason, **detail})

    def redact(self, path):
        self.redaction_count += 1
        if len(self.redactions) < MAX_WARNINGS:
            self.redactions.append(path)
        return "[redacted]"

    def attr(self, obj, key, path, default=None):
        if obj is None:
            return default
        try:
            return getattr(obj, key, default)
        except Exception as exc:
            self.warn(path, "attribute_unavailable", error_type=type(exc).__name__)
            return default

    def text_value(self, value, path):
        """Flatten Android styled text, never an arbitrary object's repr.

        TextView.getText() can yield SpannedString/Editable after typography
        spans were applied. Check the actual Java interface before converting;
        the source widget and its spans must remain untouched.
        """
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        try:
            from java import jclass
        except ImportError:
            return value
        try:
            if isinstance(value, jclass("java.lang.CharSequence")):
                return str(value)
        except Exception as exc:
            self.warn(path, "native_text_unavailable", error_type=type(exc).__name__)
        return value

    def copy(self, value, path, depth=0, ancestors=None):
        self.values += 1
        if self.values > MAX_VALUES:
            self.warn(path, "value_limit", limit=MAX_VALUES)
            return {"capture_omitted": "value_limit"}
        if depth > MAX_DEPTH:
            self.warn(path, "depth_limit", limit=MAX_DEPTH)
            return {"capture_omitted": "depth_limit"}
        if value is None or type(value) in (bool, int):
            return value
        if isinstance(value, float):
            if isfinite(value):
                return value
            self.warn(path, "non_finite_number")
            return None
        if isinstance(value, str):
            if _SECRET_TEXT.search(value):
                value = _SECRET_TEXT.sub("[redacted]", value)
                self.redact(path)
            raw = value.encode("utf-8", errors="replace")
            limit = min(MAX_STRING_BYTES, max(0, MAX_TEXT_BYTES - self.text_bytes))
            if len(raw) > limit:
                self.warn(path, "text_limit", original_bytes=len(raw), captured_bytes=limit)
                value = raw[:limit].decode("utf-8", errors="ignore")
                raw = value.encode("utf-8")
            self.text_bytes += len(raw)
            return value
        ancestors = set() if ancestors is None else ancestors
        if id(value) in ancestors:
            self.warn(path, "data_cycle")
            return {"capture_omitted": "data_cycle"}
        ancestors = ancestors | {id(value)}
        if is_dataclass(value) and not isinstance(value, type):
            value = {field.name: getattr(value, field.name) for field in fields(value)}
        if isinstance(value, Mapping):
            result = {}
            for index, (key, item) in enumerate(value.items()):
                if index >= MAX_CONTAINER_ITEMS or self.values > MAX_VALUES:
                    self.warn(path, "container_limit", original_items=len(value), captured_items=index)
                    break
                if not isinstance(key, (str, int, float, bool)):
                    self.warn(path, "unsupported_key", type=type(key).__name__)
                    continue
                key = self.copy(str(key), path + "/<key>", depth + 1, ancestors)
                if not isinstance(key, str):
                    break
                child_path = path + "/" + str(key)
                result[key] = self.redact(child_path) if _secret_key(key) else self.copy(item, child_path, depth + 1, ancestors)
            return result
        if isinstance(value, (list, tuple, set, frozenset)):
            values = value
            if isinstance(value, (set, frozenset)):
                # The only app sets captured are simple identifiers/indices.
                values = sorted(value, key=lambda x: (type(x).__name__, str(x) if isinstance(x, (str, int)) else ""))
            result = []
            for index, item in enumerate(values):
                if index >= MAX_CONTAINER_ITEMS or self.values > MAX_VALUES:
                    self.warn(path, "container_limit", original_items=len(value), captured_items=index)
                    break
                result.append(self.copy(item, f"{path}/{index}", depth + 1, ancestors))
            return result
        self.warn(path, "unsupported_type", type=type(value).__name__)
        return {"capture_omitted": "unsupported_type", "type": type(value).__name__}

    def read(self, obj, method, path, *args):
        fn = self.attr(obj, method, path, None)
        if not callable(fn):
            self.warn(path, "read_method_unavailable", method=method)
            return None
        try:
            return fn(*args)
        except Exception as exc:
            self.warn(path, "read_failed", error_type=type(exc).__name__)
            return None

    def native(self, widget, path):
        impl = self.attr(widget, "_impl", path + "/native")
        native = self.attr(impl, "native", path + "/native")
        if native is None:
            return {"visibility_known": False, "viewport_visible": None}
        result = {"visibility_known": False, "viewport_visible": None}
        for name, method in (("visibility_code", "getVisibility"), ("shown", "isShown"),
                ("x", "getX"), ("y", "getY"), ("width", "getWidth"), ("height", "getHeight"),
                ("scroll_x", "getScrollX"), ("scroll_y", "getScrollY")):
            fn = self.attr(native, method, path + "/native/" + name)
            if callable(fn):
                try:
                    value = fn()
                    if isinstance(value, (int, float, bool)):
                        result[name] = self.copy(value, path + "/native/" + name)
                except Exception as exc:
                    self.warn(path + "/native/" + name, "native_measurement_unavailable", error_type=type(exc).__name__)
        if "visibility_code" in result:
            result["visibility_known"] = True
            result["visible"] = result["visibility_code"] == 0
        fn = self.attr(native, "getGlobalVisibleRect", path + "/native/viewport")
        if callable(fn):
            try:
                from java import jclass
                rect = jclass("android.graphics.Rect")()
                result["viewport_visible"] = bool(fn(rect))
                result["visible_bounds_px"] = [int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)]
            except ImportError:
                pass  # Non-Android fake/native backends have no global Rect.
            except Exception as exc:
                self.warn(path + "/native/viewport", "native_measurement_unavailable", error_type=type(exc).__name__)
        return result

    def widget(self, widget, path="root", depth=0, parent_visible=True):
        if widget is None:
            self.warn(path, "missing_widget")
            return None
        if id(widget) in self.seen_widgets:
            self.warn(path, "repeated_widget", first_path=self.seen_widgets[id(widget)])
            return {"path": path, "reference_path": self.seen_widgets[id(widget)]}
        if self.widget_count >= MAX_WIDGETS or depth > MAX_DEPTH:
            self.warn(path, "widget_limit", max_widgets=MAX_WIDGETS, max_depth=MAX_DEPTH)
            return {"path": path, "capture_omitted": "widget_limit"}
        self.seen_widgets[id(widget)] = path
        self.widget_count += 1
        node = {"path": path, "type": type(widget).__name__, "attached": True}
        placeholder = self.text_value(self.attr(widget, "placeholder", path + "/placeholder", ""), path + "/placeholder")
        sensitive = ("password" in type(widget).__name__.lower()
            or self.attr(widget, "_feedback_sensitive", path + "/sensitive", False) is True
            or isinstance(placeholder, str) and bool(_SENSITIVE_LABEL.search(placeholder)))
        for name in ("text", "value", "placeholder", "enabled", "readonly", "multiline"):
            value = self.attr(widget, name, path + "/" + name, _MISSING)
            if value is not _MISSING:
                if name in {"text", "value", "placeholder"}:
                    value = self.text_value(value, path + "/" + name)
                node[name] = self.redact(path + "/" + name) if sensitive and name in {"value", "text"} else self.copy(value, path + "/" + name)
        options = self.attr(widget, "items", path + "/items", _MISSING)
        if options is not _MISSING and options is not None:
            try:
                values = []
                for index, option in enumerate(options):
                    if index >= MAX_CONTAINER_ITEMS:
                        self.warn(path + "/items", "option_limit", limit=MAX_CONTAINER_ITEMS)
                        break
                    if type(option).__module__.startswith("toga.sources"):
                        option = self.attr(option, "value", path + "/items/" + str(index))
                    values.append(option)
                node["items"] = self.redact(path + "/items") if sensitive else self.copy(values, path + "/items")
            except Exception as exc:
                self.warn(path + "/items", "options_unavailable", error_type=type(exc).__name__)
        for name in ("_text_role", "_study_ranges"):
            value = self.attr(widget, name, path + "/" + name, _MISSING)
            if value is not _MISSING:
                node[name.removeprefix("_")] = self.copy(value, path + "/" + name)
        image = self.attr(widget, "image", path + "/image")
        image_path = self.attr(image, "path", path + "/image/path")
        if isinstance(image_path, (str, PurePath)):
            # App resource identities are useful; local user's folder names are
            # not. Never read or export an unrelated image file here.
            normalized = str(image_path).replace("\\", "/")
            if "/resources/" in normalized:
                node["image_asset"] = self.copy("resources/" + normalized.split("/resources/", 1)[1], path + "/image_asset")
            else:
                node["image_asset"] = {"filename": self.copy(normalized.rsplit("/", 1)[-1], path + "/image_asset"), "path_redacted": True}
        style = self.attr(widget, "style", path + "/style")
        styles = {}
        # Pack's old padding names are margin aliases, not CSS insets. Preserve
        # export keys without triggering deprecated getters. Other style objects
        # and dictionaries keep their own padding semantics; no Toga import.
        pack = any(cls.__module__ == "toga.style.pack" and cls.__name__ == "Pack"
                   for cls in type(style).__mro__)
        for name in _STYLE_FIELDS:
            attribute = name.replace("padding", "margin", 1) if pack and name.startswith("padding") else name
            value = style.get(name, _MISSING) if isinstance(style, Mapping) else self.attr(style, attribute, path + "/style/" + name, _MISSING)
            if value is _MISSING or value is None:
                continue
            if not isinstance(value, (str, bool, int, float, list, tuple)):
                # Pack's immutable Color/constant values have a stable display
                # representation. Arbitrary application objects do not.
                if type(value).__module__.startswith(("travertino", "toga")):
                    value = str(value)
                else:
                    continue
            styles[name] = self.copy(value, path + "/style/" + name)
        if styles:
            node["style"] = styles
        node["native"] = self.native(widget, path)
        visible = parent_visible and node["native"].get("visible", True) and styles.get("visibility") != "hidden" and styles.get("display") != "none"
        node["visible"] = visible
        fragment = self.attr(widget, "_feedback_fragment", path + "/fragment")
        if fragment is not None:
            data = self.copy(fragment, f"fragments/{len(self.fragments)}/data")
            node["fragment_index"] = len(self.fragments)
            self.fragments.append({"widget_path": path, "visible": visible, "data": data,
                "sha256": sha256(canonical_json(data).encode("utf-8")).hexdigest()})
        # Compatible with previously constructed views when a report is opened
        # during an in-process upgrade or a standalone view test.
        elif self.attr(widget, "_lesson_view", path + "/plan") is not None or self.attr(widget, "_prompt_support", path + "/plan") is not None:
            data = self.copy({"kind": "render_plan", "lesson": self.attr(widget, "_lesson_view", path),
                "prompt": self.attr(widget, "_prompt_support", path)}, f"fragments/{len(self.fragments)}/data")
            node["fragment_index"] = len(self.fragments)
            self.fragments.append({"widget_path": path, "visible": visible, "data": data,
                "sha256": sha256(canonical_json(data).encode("utf-8")).hexdigest()})
        children = self.attr(widget, "children", path + "/children", ())
        try:
            children = list(children or ())
        except Exception as exc:
            self.warn(path, "children_unavailable", error_type=type(exc).__name__)
            children = []
        content = self.attr(widget, "content", path + "/content")
        if content is not None and all(content is not child for child in children):
            children.append(content)
        if children:
            node["children"] = []
            for index, child in enumerate(children):
                if self.widget_count >= MAX_WIDGETS:
                    self.warn(path, "widget_children_omitted", remaining_children=len(children) - index)
                    break
                node["children"].append(self.widget(child, f"{path}/{index}", depth + 1, visible))
        return node


def _question_record(value):
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value):
        return {field.name: getattr(value, field.name) for field in fields(value)}
    return None


def _question_capture(c, question, path):
    record = _question_record(question)
    if record is None:
        c.warn(path, "question_snapshot_unavailable")
        return {}
    hidden = {key: record.pop(key) for key in ("answer", "accepted_answers", "alternative_answers",
        "known_wrong", "feedback", "explanation", "guide", "audio_text", "optional_audio_text", "support") if key in record}
    # Metadata may carry a kana answer or generated canonical ordering. It is
    # evidence for maintainers, not part of the public question prompt preview.
    if "metadata" in record:
        hidden["metadata"] = record.pop("metadata")
    return {"question": c.copy(record, path + "/question"),
        "maintainer_only": {"not_ui_content": True, "hidden_solution_and_metadata": c.copy(hidden, path + "/maintainer_only")}}


def _walk_ids(value, ids, sources, depth=0):
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"target_id", "content_id"} and isinstance(item, str):
                ids.add(item)
            if key == "source_ids" and isinstance(item, list):
                sources.update(x for x in item if isinstance(x, str))
            _walk_ids(item, ids, sources, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _walk_ids(item, ids, sources, depth + 1)


def _capture_content(c, app, page, key):
    result = {"capture_strategy": "rendered_widgets_and_fragments"}
    repo = c.attr(app, "repo", "content/repository")
    route, identity = (key + [None, None])[:2] if isinstance(key, list) else (None, None)
    method = {"kana_detail": "get_kana", "word_detail": "get_word", "card_detail": "get_kana"}.get(page)
    expected_route = {"kana_detail": "show_kana_detail", "word_detail": "show_word_detail", "card_detail": "show_card_detail"}.get(page)
    if method and route == expected_route and isinstance(identity, str):
        record = c.read(repo, method, "content/selected", identity)
        result["selected"] = c.copy(record, "content/selected")
        result["selected_id"] = identity
        result["snapshot_origin"] = "read_only_bundled_repository"
    if page in _QUESTION_PAGES and repo is not None:
        question = _question_record(c.attr(app, "question", "content/question"))
        if question and isinstance(question.get("content_id"), str):
            content_id = question["content_id"]
            method = "get_word" if str(question.get("kind", "")).startswith("word_") else "get_kana"
            result["question_content"] = c.copy(c.read(repo, method, "content/question_content", content_id), "content/question_content")
    if page in _LEARNING_QUESTION_PAGES:
        q = c.attr(app, "_learning_question", "content/question")
        target = q.get("target_id", q.get("content_id")) if isinstance(q, Mapping) else None
        if isinstance(target, str):
            item = c.read(c.attr(app, "learning_store", "content/learning_item"), "get_item", "content/learning_item", target)
            if item is None:
                try:
                    from .learning_content import get_learning_item
                    item = get_learning_item(target)
                except Exception as exc:
                    c.warn("content/learning_item", "content_read_failed", error_type=type(exc).__name__)
            result["learning_item"] = c.copy(item, "content/learning_item")
    title = c.attr(c.attr(app, "title_label", "content/page_title"), "text", "content/page_title", "") or c.attr(app, "title", "content/page_title", "")
    if page in {"learning_familiarity", "learning_intro", "learning_guide"} or page == "learning_prerequisite" and title == "接续说明":
        item = c.attr(app, "_learning_item", "content/current_learning_item")
        if isinstance(item, Mapping):
            result["learning_item"] = c.copy(item, "content/current_learning_item")
    if page == "learning_familiarity":
        controls = c.attr(app, "_learning_familiarity_controls", "content/familiarity_selections", {})
        if isinstance(controls, Mapping):
            values = {ability: c.attr(control, "value", "content/familiarity_selections/" + str(ability))
                      for ability, control in controls.items()}
            result["familiarity_selections"] = c.copy(values, "content/familiarity_selections")
    if page == "learning_detail" and route == "show_learning_detail":
        item = c.attr(app, "_learning_detail_item", "content/current_learning_item")
        if isinstance(item, Mapping) and item.get("id") == identity:
            result["learning_item"] = c.copy(item, "content/current_learning_item")
    if page == "example_detail":
        result["example_selection"] = c.copy(c.attr(app, "_example_detail_state", "content/example_selection"), "content/example_selection")
    if page == "example_editor":
        for name in ("_example_editor_target", "_example_editor_existing"):
            result[name.removeprefix("_")] = c.copy(c.attr(app, name, "content/" + name), "content/" + name)
        result["target_id"] = result.get("example_editor_target")
    if page == "words":
        words = c.attr(app, "current_words", "content/visible_words", ())
        if isinstance(words, (list, tuple)):
            result["visible_words"] = c.copy(words, "content/visible_words")
        result["offset"] = c.copy(c.attr(app, "word_offset", "content/offset", 0), "content/offset")
    control_fields = {
        "words": ("word_query", "word_collection"), "kana": ("kana_filter",),
        "learning_library": ("learning_library_query", "learning_library_category"),
        "example_library": ("example_query",), "example_picker": ("example_target_query",),
        "cards": ("card_query", "card_state", "card_kind", "card_research"),
    }
    for name in control_fields.get(page, ()):
        control = c.attr(app, name, "content/filter_values/" + name)
        value = c.attr(control, "value", "content/filter_values/" + name)
        result.setdefault("filter_values", {})[name] = c.copy(value, "content/filter_values/" + name)
    # The exact composed plans in fragments take precedence over any newly
    # queried records; they retain ordering, hints and highlight spans.
    result["rendered_fragment_indices"] = list(range(len(c.fragments)))
    return result


def _capture_practice(c, app, page):
    result = {}
    if page in _SESSION_PAGES:
        for name in ("session_id", "question_index", "session_right", "script", "use_full_keyboard"):
            result[name] = c.copy(c.attr(app, name, "practice/" + name), "practice/" + name)
        queue = c.attr(app, "questions", "practice/queue", ())
        from .domain import _QuestionSequence
        if isinstance(queue, (list, tuple, _QuestionSequence)):
            result["queue_length"] = len(queue)
            result["queue_scope"] = "current_question_only; other queued answers are not exported"
        store = c.attr(app, "store", "practice/lane")
        result["lane"] = c.copy(c.attr(store, "practice_lane", "practice/lane", "main"), "practice/lane")
        db = c.attr(store, "_db", "practice/session")
        session_id = result.get("session_id")
        if db is not None and isinstance(session_id, str):
            try:
                row = db.execute("SELECT session_id,started_at_utc,timezone,random_seed,policy_version "
                    "FROM study_session WHERE session_id=?", (session_id,)).fetchone()
                result["session"] = c.copy(dict(row) if row else None, "practice/session")
            except Exception as exc:
                c.warn("practice/session", "session_read_failed", error_type=type(exc).__name__)
    if page in _QUESTION_PAGES:
        result.update(_question_capture(c, c.attr(app, "question", "practice/question"), "practice"))
        for name in ("answer_revealed", "answered_text", "answer_correct", "question_audio_ok",
                     "word_answer", "romaji_answer", "word_romaji_parts", "word_romaji_position",
                     "word_romaji_choices", "word_romaji_current", "word_romaji_plan", "word_romaji_unit_mode"):
            value = c.attr(app, name, "practice/draft/" + name, _MISSING)
            if value is not _MISSING:
                result.setdefault("draft", {})[name] = c.copy(value, "practice/draft/" + name)
        index = c.attr(app, "question_index", "practice/index")
        for name in ("_assisted_attempts", "_answered_attempts"):
            indices = c.attr(app, name, "practice/" + name, ())
            if isinstance(indices, (set, list, tuple)):
                session_id = c.attr(app, "session_id", "practice/session_id")
                result[name.removeprefix("_")] = index in indices or (session_id, index) in indices
    if page in _LEARNING_QUESTION_PAGES:
        q = c.attr(app, "_learning_question", "practice/question")
        result.update(_question_capture(c, q, "practice"))
        for name in ("_learning_question_state", "_learning_parts", "_learning_used", "_learning_audio_ok"):
            value = c.attr(app, name, "practice/" + name, _MISSING)
            if value is not _MISSING:
                result[name.removeprefix("_")] = c.copy(value, "practice/" + name)
        if isinstance(q, Mapping) and q.get("id"):
            saved = c.read(c.attr(app, "learning_store", "practice/saved"), "get_question", "practice/saved", q["id"])
            if isinstance(saved, Mapping):
                result["saved_state"] = c.copy({key: saved.get(key) for key in ("question_id", "status", "draft", "result", "not_before_utc")}, "practice/saved_state")
    if page == "learning_guide":
        result.update(_question_capture(c, c.attr(app, "_learning_guide", "practice/guide"), "practice"))
    if page in {"free_ink", "ink_question"}:
        result["ink"] = {name: c.copy(c.attr(app, name, "practice/ink/" + name), "practice/ink/" + name)
            for name in ("strokes", "_active_stroke", "ink_width", "ink_height", "_ink_view_width", "_ink_view_height", "_ink_revision")}
    # Input controls in widget_tree are authoritative for unsaved keystrokes;
    # state above distinguishes the last persisted draft from the live view.
    return result


def _capture_progress(c, app, ids):
    store = c.attr(app, "store", "learner_progress")
    learning = c.attr(app, "learning_store", "learner_progress")
    selected = sorted(ids)
    if len(selected) > MAX_TARGETS:
        c.warn("learner_progress", "target_limit", original_items=len(selected), limit=MAX_TARGETS)
        selected = selected[:MAX_TARGETS]
    result = {"scope": "targets referenced by this page only", "targets": {}}
    for target in selected:
        record = {}
        if learning is not None:
            item = c.read(learning, "get_item", "learner_progress/" + target, target)
            record["enrolled"] = item is not None
            if isinstance(item, Mapping):
                record["learning"] = c.copy({key: item.get(key) for key in (
                    "content_id", "stage", "introduction_id", "checkpoint", "exposure_group",
                    "introduced_at_utc", "retrieval_at_utc", "units")}, "learner_progress/" + target)
        db = c.attr(store, "_db", "learner_progress/kana_cards")
        if db is not None:
            try:
                rows = db.execute("SELECT card_id,content_id,content_version,skill,script,scheduler_state_json,"
                    "scheduler_version,due_at_utc,introduced_at_utc,review_count,suspended FROM study_card WHERE content_id=?",
                    (target,)).fetchall()
                record["kana_cards"] = c.copy([dict(row) for row in rows], "learner_progress/" + target + "/kana_cards")
            except Exception as exc:
                c.warn("learner_progress/" + target, "card_read_failed", error_type=type(exc).__name__)
        result["targets"][target] = record
    return result


def sanitize_feedback_fragment(value):
    """Redact and bound maintenance evidence before attaching it to a widget."""
    capture = _Capture()
    result = capture.copy(value, "fragment")
    if isinstance(result, dict):
        result["sanitization"] = {"redaction_count": capture.redaction_count,
                                  "warnings": capture.warnings}
    return result


def capture_feedback_context(app, *, overlay=None):
    """Capture the current page without changing navigation, drafts or schedules.

    Consumers must inspect completeness, not infer that a screenshot/tree is a
    complete content backup. Preview only page/environment/widget_tree; content,
    fragments and practice may contain unshown solutions for maintainers.
    """
    c = _Capture()
    page = c.attr(app, "page", "page/name", "unknown")
    title = ""
    for name in ("title_label", "page_title"):
        label = c.attr(app, name, "page/title")
        title = c.text_value(c.attr(label, "text", "page/title", ""), "page/title")
        if title:
            break
    if not title:
        title = c.attr(app, "title", "page/title", "")
        if not isinstance(title, str):
            title = c.text_value(c.attr(title, "text", "page/title", ""), "page/title")
    key = c.copy(c.attr(app, "_view_key", "page/view_key"), "page/view_key")
    result = {"format": FORMAT, "version": VERSION,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "page": {"name": c.copy(page, "page/name"), "title": c.copy(title, "page/title"), "view_key": key},
        "environment": {}, "preview_policy": "Only page, environment, visible widget_tree and completeness; semantic payload may include hidden solutions."}
    scroll = c.attr(app, "_page_scroll", "page/scroll")
    if scroll is not None:
        for name in ("position", "horizontal_position", "vertical_position"):
            value = c.attr(scroll, name, "page/scroll/" + name, _MISSING)
            if value is not _MISSING:
                result["page"].setdefault("scroll_position", {})[name] = c.copy(value, "page/scroll/" + name)
    try:
        from .build_info import VERSION as app_version, BUILD_NUMBER, BUILD_DATE
        result["environment"].update(app_version=app_version, build_number=BUILD_NUMBER, build_date=BUILD_DATE)
    except ImportError:
        c.warn("environment/build", "build_information_unavailable")
    try:
        from .text_layout import font_scale
        result["environment"]["font_scale"] = c.copy(font_scale(), "environment/font_scale")
    except Exception as exc:
        c.warn("environment/font_scale", "font_scale_unavailable", error_type=type(exc).__name__)
    window = c.attr(app, "main_window", "environment/window")
    size = c.attr(window, "size", "environment/window_size")
    if size is not None:
        result["environment"]["window_size"] = {name: c.copy(c.attr(size, name, "environment/window_size/" + name), "environment/window_size/" + name) for name in ("width", "height")}
    for name in ("model_state",):
        value = c.attr(app, name, "environment/" + name, _MISSING)
        if value is not _MISSING:
            result["environment"][name] = c.copy(value, "environment/" + name)
    platform = c.attr(app, "platform", "environment/platform")
    if callable(c.attr(platform, "feedback_environment", "environment/native")):
        result["environment"]["native"] = c.copy(c.read(platform, "feedback_environment", "environment/native"), "environment/native")
    root = None
    for name in ("root", "content_slot", "body"):
        root = c.attr(app, name, "widget_tree")
        if root is not None:
            break
    result["widget_tree"] = c.widget(root)
    result["fragments"] = c.fragments
    result["content"] = _capture_content(c, app, page, key)
    result["practice"] = _capture_practice(c, app, page)
    if page == "report_list":
        selected = sorted(getattr(app, "_feedback_selected", ()))
        result["feedback_manager"] = c.copy({
            "applied_filter": getattr(app, "_feedback_manager_state", {}),
            "draft_filter": {key: control.value for key, control in getattr(app, "_feedback_manager_controls", {}).items()},
            "selected_ids": selected[:MAX_CONTAINER_ITEMS], "selected_count": len(selected),
            "truncated": len(selected) > MAX_CONTAINER_ITEMS,
            "visible_ids": [identity for identity, _ in getattr(app, "_feedback_page_switches", ())],
            "export": {**app._feedback_export_settings(),
                "pending": bool(getattr(app, "_feedback_export_pending", False)),
                **getattr(app, "_feedback_export_state", {})},
        }, "feedback_manager")
    if page == "report_editor":
        # Reporting the feedback editor is valid, but its title alone cannot
        # reproduce the lesson which prompted the original report. Retain one
        # bounded origin, without recursively accumulating editor captures.
        editor = c.attr(app, "_report_editor", "related_page_context", {})
        origin = editor.get("context") if isinstance(editor, Mapping) else None
        if isinstance(origin, Mapping):
            origin = origin.get("related_page_context", origin)
            if isinstance(origin, Mapping):
                result["related_page_context"] = c.copy({
                    key: value for key, value in origin.items()
                    if key != "related_page_context"
                }, "related_page_context")
    ids, source_ids = set(), set()
    _walk_ids(c.fragments, ids, source_ids)
    _walk_ids(result["content"], ids, source_ids)
    _walk_ids(result["practice"].get("question"), ids, source_ids)
    if result["content"].get("selected_id"):
        ids.add(result["content"]["selected_id"])
    result["learner_progress"] = _capture_progress(c, app, ids)
    source_map = c.attr(app, "source_map", "content/sources", {})
    if isinstance(source_map, Mapping):
        result["content"]["sources"] = c.copy({key: source_map[key] for key in sorted(source_ids) if key in source_map}, "content/sources")
    unresolved_sources = source_ids - set(result["content"].get("sources", {}))
    if unresolved_sources:
        try:
            from .learning_content import learning_sources
            sources = {source["id"]: source for source in learning_sources() if source["id"] in unresolved_sources}
            result["content"].setdefault("sources", {}).update(c.copy(sources, "content/learning_sources"))
            unresolved_sources -= set(sources)
        except Exception as exc:
            c.warn("content/sources", "learning_sources_unavailable", error_type=type(exc).__name__)
    if unresolved_sources:
        result["content"]["unresolved_source_ids"] = sorted(unresolved_sources)
    result["settings"] = {}
    store = c.attr(app, "store", "settings")
    if store is not None:
        for name in _SETTING_KEYS:
            result["settings"][name] = c.copy(c.read(store, "get_setting", "settings/" + name, name, None), "settings/" + name)
    result["settings"]["active_practice_preferences"] = c.copy(c.attr(app, "_practice_preferences", "settings/active_practice_preferences", {}), "settings/active_practice_preferences")
    events = c.attr(app, "_feedback_events", "recent_events", ())
    if isinstance(events, (list, tuple)):
        result["recent_events"] = c.copy(events[-12:], "recent_events")
    from .diagnostic_events import error_events
    result["diagnostic_events"] = error_events()
    if overlay is not None:
        result["overlay"] = c.copy(overlay, "overlay")
    result["completeness"] = {"complete": c.warning_count == 0, "warnings": c.warnings,
        "warning_count": c.warning_count, "redactions": c.redactions, "redaction_count": c.redaction_count, "widget_count": c.widget_count,
        "text_bytes": c.text_bytes, "scope": "attached widget tree plus page-related semantic snapshots; viewport visibility is unknown when the backend cannot measure it",
        "limits": {"widgets": MAX_WIDGETS, "depth": MAX_DEPTH, "text_bytes": MAX_TEXT_BYTES,
            "string_bytes": MAX_STRING_BYTES, "container_items": MAX_CONTAINER_ITEMS, "values": MAX_VALUES,
            "progress_targets": MAX_TARGETS, "warning_details": MAX_WARNINGS}}
    from .feedback_bindings import capture_bindings
    result['fragment_bindings'] = capture_bindings(result)
    result["sha256"] = context_sha256(result)
    return result
