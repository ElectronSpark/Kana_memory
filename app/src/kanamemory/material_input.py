"""Bounded text intake; preserve Unicode spelling and explicit source origin."""
from hashlib import sha256
import re

SETTING = "material_input_v1"
SELECTION_SETTING = "material_selection_v1"
MAX_BYTES = 16000
MAX_CHARS = 4000


def decode_text(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        raise ValueError("文本文件最多 16,000 字节，请分段导入。")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("请选择 UTF-8 编码的文本文件。") from None
    if not text.strip() or len(text) > MAX_CHARS or "\x00" in text:
        raise ValueError("请提供 1—4000 字的文本，且不含空字符。")
    return text


def validate_origin(value):
    if (not isinstance(value, dict) or set(value) != {"version", "source_sha256", "kind"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["kind"] not in {"paste", "utf8_file", "share"}
            or not isinstance(value["source_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["source_sha256"]) is None):
        raise ValueError("材料输入来源记录无效。")
    return dict(value)


def save_origin(store, text, kind):
    value = validate_origin({"version": 1, "source_sha256": sha256(text.encode("utf-8")).hexdigest(), "kind": kind})
    store.set_setting(SETTING, value)


def input_kind(store, text):
    value = store.get_setting(SETTING, None)
    if value is None:
        return "paste"
    value = validate_origin(value)
    return value["kind"] if value["source_sha256"] == sha256(text.encode("utf-8")).hexdigest() else "paste"


def validate_selection(value):
    if (not isinstance(value, dict) or set(value) != {"version", "text", "sha256", "spans"}
            or type(value["version"]) is not int or value["version"] != 1
            or not isinstance(value["text"], str)):
        raise ValueError("材料选择范围无效。")
    text = value["text"]
    if (not text.strip() or len(text) > MAX_CHARS or len(text.encode("utf-8")) > MAX_BYTES or "\x00" in text
            or value["sha256"] != sha256(text.encode("utf-8")).hexdigest()
            or not isinstance(value["spans"], list) or not 1 <= len(value["spans"]) <= 8):
        raise ValueError("请选择 1—8 段原文。")
    end = 0
    for span in value["spans"]:
        if (not isinstance(span, dict) or set(span) != {"start", "end", "quote"}
                or type(span["start"]) is not int or type(span["end"]) is not int
                or not end <= span["start"] < span["end"] <= len(text)
                or text[span["start"]:span["end"]] != span["quote"]
                or not span["quote"].strip()):
            raise ValueError("材料选择范围与原文不一致。")
        end = span["end"]
    from copy import deepcopy
    return deepcopy(value)


def select_ranges(text, ranges):
    """Merge neighbouring selected paragraphs without including skipped words."""
    spans = []
    for start, end in sorted(ranges):
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(text):
            raise ValueError("材料选择范围无效。")
        if spans and (start <= spans[-1]["end"] or not text[spans[-1]["end"]:start].strip()):
            spans[-1]["end"] = max(end, spans[-1]["end"])
            spans[-1]["quote"] = text[spans[-1]["start"]:spans[-1]["end"]]
        else:
            spans.append({"start": start, "end": end, "quote": text[start:end]})
    return validate_selection({"version": 1, "text": text,
        "sha256": sha256(text.encode("utf-8")).hexdigest(), "spans": spans})


def selected_text(selection):
    return "\n".join(span["quote"] for span in validate_selection(selection)["spans"])


def selection_key(draft_key="analyze:"):
    return SELECTION_SETTING if draft_key == "analyze:" else SELECTION_SETTING + ":" + sha256(draft_key.encode("utf-8")).hexdigest()


def current_selection(store, text, *, draft_key="analyze:"):
    saved = store.get_setting(selection_key(draft_key), None)
    if saved is None:
        return select_ranges(text, [(0, len(text))])
    saved = validate_selection(saved)
    if saved["text"] != text:
        raise ValueError("原文已修改，请重新选择发送范围。")
    return saved


def paragraph_ranges(text):
    """At most 64 visible chunks; indices count Unicode code points, not bytes."""
    rows = [(m.start(), m.end()) for m in re.finditer(r"[^\r\n]+", text) if m.group().strip()]
    size = max(1, (len(rows) + 63) // 64)
    return [(rows[i][0], rows[min(i + size, len(rows)) - 1][1]) for i in range(0, len(rows), size)]


def original_spans(selection, start, end):
    """Map an output segment back to original ranges; synthetic joins have no anchor."""
    selection = validate_selection(selection)
    length = len(selected_text(selection))
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= length:
        raise ValueError("材料片段范围无效。")
    offset, rows = 0, []
    for span in selection["spans"]:
        stop = offset + len(span["quote"])
        left, right = max(start, offset), min(end, stop)
        if left < right:
            a, b = span["start"] + left - offset, span["start"] + right - offset
            rows.append({"start": a, "end": b, "quote": selection["text"][a:b]})
        offset = stop + 1
    if not rows:
        raise ValueError("分段连接符没有原文锚点。")
    return rows
