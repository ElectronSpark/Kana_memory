"""Small, platform-independent presentation helpers; no linguistic guesses."""
from __future__ import annotations

import json
import unicodedata

from .kana_links import ContentText

CATEGORY_LABELS = {
    "basic": "清音与拨音", "dakuten": "浊音", "handakuten": "半浊音",
    "foreign_voiced": "外来浊音", "yoon": "拗音", "small": "小假名",
    "foreign_combination": "外来语组合", "foreign_variant": "外来语变体",
    "historical_kana": "历史假名", "rare_legacy_katakana": "罕见片假名",
    "long_vowel_mark": "长音符", "voicing_mark": "浊音符号", "iteration_mark": "重复符号",
}


def payload(record):
    value = record.get("payload", {}) if record else {}
    return json.loads(value) if isinstance(value, str) else value


def wrapped(text, columns=44):
    """Explicit line breaks keep native labels usable on narrow phone screens."""
    lines = []
    for paragraph in str(text or "").splitlines() or [""]:
        line, width = "", 0
        for char in paragraph:
            size = 0 if unicodedata.combining(char) else 2 if unicodedata.east_asian_width(char) in "WF" else 1
            if width + size > columns:
                lines.append(line)
                line, width = "", 0
            line += char
            width += size
        lines.append(line)
    return "\n".join(lines)


def short(text, length=36):
    text = str(text or "").replace("\n", " ")
    return text if len(text) <= length else text[:length - 1] + "…"


def _flatten_parts(parts: list[str | ContentText]) -> str:
    return "".join(part.text if isinstance(part, ContentText) else part for part in parts)


def _join_parts(groups: list[list[str | ContentText]], separator: str) -> list[str | ContentText]:
    result: list[str | ContentText] = []
    for index, group in enumerate(groups):
        if index:
            result.append(separator)
        result.extend(group)
    return result


def _content_values(values, separator: str) -> list[str | ContentText]:
    return _join_parts([[ContentText(str(value))] for value in values], separator)


def kana_origin_parts(record, *, historical_available=False) -> list[str | ContentText]:
    """Keep sourced explanations distinct from translated interface templates."""
    data = payload(record)
    blocks = []
    for key, label in (("hiragana_origins", "平假名"), ("katakana_origins", "片假名")):
        for origin in data.get(key, []):
            blocks.append([label + " ← ", ContentText(str(origin["han_character"])), "\n",
                           ContentText(str(origin.get("process_zh", "")))])
    if data.get("dispute_note_zh"):
        blocks.append([ContentText(data["dispute_note_zh"])])
    # Only the few explicitly collected historical variants are shown, with a
    # distinction from the origins of today's standard letter.
    for variant in data.get("other_historical_forms", []):
        blocks.append(["历史异体：", ContentText(str(variant.get("glyph", ""))), " ← ",
                       ContentText(str(variant["han_character"])), "（记 ",
                       ContentText(str(variant.get("reading_label", ""))),
                       "）；不是现行平假名的另一条直接字源。"])
    if not historical_available:
        blocks.append(["形成期汉语／日语读音：尚待逐字考证。现代读音不代替古音。"])
    return _join_parts(blocks, "\n\n")


def kana_origin_text(record):
    return _flatten_parts(kana_origin_parts(record))


def dialect_parts(record) -> list[str | ContentText]:
    data = payload(record)
    if "locality_label" in record:
        location = ContentText(str(record["locality_label"]))
    elif "locality_id" in data:
        location = ContentText(str(data["locality_id"]))
    else:
        location = "地点待核"
    raw_reading = data.get("romanization") or data.get("ipa_segments") or data.get("transcription")
    reading = ContentText(str(raw_reading)) if raw_reading else "原表缺记"
    notation = data.get("notation_system")
    tone = data.get("tone_value")
    if notation == "source_broad_IPA_and_Chao_5_level_tone":
        detail = ["记音：资料宽式 IPA；"]
        detail += ["五度调值 ", ContentText(str(tone))] if tone is not None else ["调值原表缺记"]
    elif notation in {"LSHK_Jyutping", "Jyutping_with_lexi_mf_tone_number"}:
        detail = ["记音：粤拼；末位数字是调号"]
    elif notation == "Hanyu_Pinyin":
        detail = ["记音：汉语拼音"]
    else:
        detail = ["记音：", ContentText(str(notation)) if notation else "体系待核"]
        if tone is not None:
            detail += ["；调值 ", ContentText(str(tone))]
    category = data.get("tone_category")
    if category:
        detail += ["；调类 ", ContentText(str(category))]
    note = data.get("register_or_reading_note") or ""
    result = [ContentText(str(data.get("character", record.get("hanzi", "")))), " · ",
              location, "\n", reading, "\n", *detail]
    if note:
        result += ["\n", ContentText(str(note))]
    return result


def dialect_text(record):
    return _flatten_parts(dialect_parts(record))


def origin_source_ids(record):
    data = payload(record)
    found = list(data.get("source_ids", []))
    found.extend(data.get("corroborating_source_ids", []))
    for field in ("hiragana_origins", "katakana_origins", "other_historical_forms"):
        for claim in data.get(field, []):
            found.extend(claim.get("source_ids", []))
    return list(dict.fromkeys(found))


def word_senses_parts(record) -> list[str | ContentText]:
    data = payload(record)
    chinese = {s["sense_id"]:s for s in data.get("chinese_senses", [])}
    output = []
    for i, sense in enumerate(data.get("senses", []), 1):
        glosses = sense.get("japanese_meaning_english_glosses", [])
        meanings = [g.get("text", "") for g in glosses if g.get("language", "eng") == "eng"]
        pos = sense.get("pos_effective", [])
        notes = sense.get("notes", [])
        meaning_parts = _content_values(meanings, "; ") if "; ".join(meanings) else ["英文释义未收集"]
        translation = chinese.get(sense.get("sense_id"))
        lines = [[f"{sense.get('sense_number', i)}. ", *_content_values(translation["glosses"], "；")], ["英文原释：", *meaning_parts]] if translation else [[f"{sense.get('sense_number', i)}. ", *meaning_parts]]
        if ", ".join(pos):
            lines.append(["词性：", *_content_values(pos, ", ")])
        # These are semantic restrictions, not optional bibliography. Omitting
        # them incorrectly assigns (for example) Ω's ohm sense to every spelling.
        for field, label in (("kanji_restrictions", "仅限字形"), ("reading_restrictions", "仅限读法"),
                             ("field", "领域"), ("misc", "用法标签"), ("dialect_tags", "方言标签")):
            values = sense.get(field, [])
            if values:
                lines.append([label + "：", *_content_values(values, "; ")])
        if "; ".join(notes):
            lines.append(["说明：", *_content_values(notes, "; ")])
        output.append(_join_parts(lines, "\n"))
    parts = _join_parts(output, "\n\n")
    if not parts:
        parts = [ContentText(record["gloss"])] if "gloss" in record else ["词义尚未整理"]
    if data.get("chinese_gloss_status") == "not_collected":
        parts = ["日语义项的英文释义；中文释义尚未收集。\n\n", *parts]
    elif chinese:
        parts = ["中文释义含模型辅助翻译，尚未人工逐项审校；英文原释保留供对照。\n\n", *parts]
    return parts


def word_senses(record):
    return _flatten_parts(word_senses_parts(record))
