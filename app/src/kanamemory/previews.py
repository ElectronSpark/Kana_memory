"""Short, plain-text content previews; no links, lookups, or UI side effects.

Pass a ContentRepository detail record (get_kana/get_word/source). These
functions do not follow its example or source references, so a preview cannot
recursively reference itself. Render their return values as ordinary text,
not ContentText. The dialog's optional details action belongs to its caller.
"""
from __future__ import annotations

from collections.abc import Mapping
import re

from .presentation import CATEGORY_LABELS, payload, short
from .romanization import kana_learning_romaji, romanize_kana

MAX_PREVIEW_CHARACTERS = 800
_URL = re.compile(r"(?:\b[a-z][a-z0-9+.-]*://|\bwww\.)[^\s<>\[\]（）]+", re.IGNORECASE)
_MARKDOWN_LINK = re.compile(r"\[([^\]\n]*)\]\([^\n)]*\)")


def _data(record: Mapping | None) -> dict:
    if not record:
        return {}
    body = payload(record)
    result = dict(body) if isinstance(body, Mapping) else {}
    result.update(record)
    return result


def _clip(value, length: int) -> str:
    text = "" if value is None else str(value)
    text = _MARKDOWN_LINK.sub(r"\1", text)
    text = _URL.sub("", text)
    return short(" ".join(text.split()), length)


def _finish(lines) -> str:
    text = "\n".join(line for line in lines if line)
    # Field limits normally keep previews well below this final guard.
    return text if len(text) <= MAX_PREVIEW_CHARACTERS else text[:MAX_PREVIEW_CHARACTERS - 1] + "…"


def _values(values, length=54) -> str:
    return _clip("；".join(str(value) for value in values if value), length)


def kana_preview(record: Mapping | None) -> str:
    """Summarize explicit spellings, modern romanization, origins and usage."""
    data = _data(record)
    if not data:
        return "假名资料尚未收录。"
    category = data.get("category")
    roman = kana_learning_romaji(data)
    lines = [
        "平假名：" + (_clip(data.get("hiragana"), 40) or "无对应字形／未收录"),
        "片假名：" + (_clip(data.get("katakana"), 40) or "无对应字形／未收录"),
        "类别：" + CATEGORY_LABELS.get(category, "类别尚未整理"),
    ]
    if roman:
        caption = "字形罗马字：" if roman == "wo" and (data.get("hiragana") == "を" or data.get("katakana") == "ヲ") else "现代罗马字："
        lines.append(caption + _clip(roman, 60))
        if caption == "字形罗马字：":
            lines.append("wo 用于区分字形；现代读音通常为 o。")
    elif category in {"long_vowel_mark", "voicing_mark", "iteration_mark"} or data.get("hiragana") == "っ":
        lines.append("罗马字：须结合上下文，不设独立读音。")
    else:
        lines.append("罗马字：尚未收录。")

    origins = [_data(origin) for origin in data.get("origins", [])]
    representatives = []
    for field, label in (("hiragana_origins", "平假名"), ("katakana_origins", "片假名")):
        claims = [claim for origin in origins for claim in origin.get(field, []) if claim.get("han_character")]
        if not claims:
            continue
        claim = claims[0]
        value = label + " ← " + _clip(claim["han_character"], 12)
        if claim.get("certainty") == "explicitly_parenthesized_in_source":
            value += "（原表括注，未定论）"
        elif claim.get("claim_type") == "representative_hypothesis":
            value += "（诸说之一）"
        if len(claims) > 1:
            value += "等"
        representatives.append(value)
    lines.append("字源代表：" + "；".join(representatives) if representatives else "字源：此项尚未收录。")
    contexts = data.get("special_context", [])
    if contexts:
        lines.append("用法：" + _values(contexts, 110))
    elif data.get("mora_count_in_context") is not None:
        lines.append("拍数：" + _clip(data["mora_count_in_context"], 12))
    return _finish(lines)


def compact_kana_preview(record: Mapping | None, *, script: str | None = None) -> str:
    """Keep practice feedback short; extended facts remain on the detail page."""
    data = _data(record)
    if not data:
        return "假名资料尚未收录。"
    roman = kana_learning_romaji(data)
    spellings = " / ".join(f"{caption}：{_clip(data[field], 12)}"
                           for field, caption in (("hiragana", "平"), ("katakana", "片"))
                           if data.get(field) and (script not in {"hiragana", "katakana"} or field == script))
    contextual = data.get("category") in {"long_vowel_mark", "voicing_mark", "iteration_mark"} or data.get("hiragana") == "っ"
    reading = _clip(roman, 24) or ("读音须结合上下文" if contextual else "读音尚未收录")
    return _finish([spellings or "字形尚未收录", reading + " · " + CATEGORY_LABELS.get(data.get("category"), "假名")])


def compact_kana_origin(record: Mapping | None, *, script: str,
                        base_records=()) -> str:
    """Return the active script's origin, without cross-script or guessed links.

    A small/voiced form may inherit an explicitly linked base supplied by the
    caller. Combinations never acquire a single invented source character.
    Short uncertainty marks preserve the source's qualification; detailed
    provenance stays on the detail page.
    """
    if not isinstance(record, Mapping) or script not in {"hiragana", "katakana"}:
        return ""
    data = _data(record)
    if not data.get(script) or data.get("category") in {
            "yoon", "foreign_combination", "foreign_variant"}:
        return ""
    label = "字源"
    inherited = data.get("category") in {
        "dakuten", "handakuten", "foreign_voiced", "small", "rare_legacy_katakana"}
    if inherited:
        enrichment = data.get("enrichment")
        ids = enrichment.get("origin_base_kana_ids", ()) if isinstance(enrichment, Mapping) else ()
        if not isinstance(ids, (list, tuple)) or len(ids) != 1:
            return ""
        base = next((item for item in base_records if isinstance(item, Mapping)
                     and item.get("id") == ids[0] and item.get("id") != data.get("id")), None)
        if base is None:
            return ""
        data = _data(base)
        if not isinstance(data.get(script), str) or not data[script]:
            return ""
        label += "（" + _clip(data[script], 4) + "）"
    origins = data.get("origins")
    if not isinstance(origins, (list, tuple)):
        return ""
    claims = {}
    historical = data.get("category") == "historical_kana"
    for origin in origins:
        if not isinstance(origin, Mapping):
            continue
        rows = _data(origin).get(script + "_origins", ())
        if not isinstance(rows, (list, tuple)):
            continue
        for claim in rows:
            if not isinstance(claim, Mapping):
                continue
            scope = claim.get("variant_scope")
            if scope not in {None, "current_standard_form"} and not (
                    historical and scope == "historical_standard_kana_form"):
                continue
            character = claim.get("han_character")
            if not isinstance(character, str) or not re.fullmatch(
                    r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\U00020000-\U000323af]"
                    r"[\ufe00-\ufe0f\U000e0100-\U000e01ef]?", character):
                continue
            qualifier = ("（待核）" if claim.get("certainty") == "explicitly_parenthesized_in_source"
                         else "（一说）" if claim.get("claim_type") in {
                             "representative_hypothesis", "alternative_hypothesis"} else "")
            previous = claims.get(character, "")
            if not previous or qualifier == "（待核）":
                claims[character] = qualifier
    return label + "：" + _clip("、".join(char + mark for char, mark in claims.items()), 72) if claims else ""


def compact_word_preview(record: Mapping | None) -> str:
    """Show the target word and one short meaning beside the details action."""
    data = _data(record)
    if not data:
        return "词条资料尚未收录。"
    reading = data.get("reading") or data.get("display_reading") or ""
    form = data.get("form") or data.get("display_kanji") or reading
    heading = (_clip(form, 20) or "字形尚未收录") + " · " + (_clip(romanize_kana(reading), 28) if reading else "读音尚未收录")
    senses = data.get("senses", [])
    if senses:
        sense = senses[0]
        chinese = next((s for s in data.get("chinese_senses", []) if s.get("sense_id") == sense.get("sense_id")), None)
        meaning = _sense_preview(sense, 1, chinese)
    else:
        meaning = data.get("chinese_gloss") or data.get("gloss") or "词义尚未收录"
    return _finish([heading, _clip(meaning, 36)])


def word_answer_context(record: Mapping | None, answer: str) -> tuple[str, str]:
    """Keep complementary word facts without printing the answer twice."""
    data = _data(record)
    if not data:
        return "", "词条资料尚未收录。"
    reading = data.get("reading") or data.get("display_reading") or ""
    form = data.get("form") or data.get("display_kanji") or reading
    # The reading is rendered with its pitch annotation in the word context.
    fields, seen = [], {answer.strip(), reading}
    for value in (form, romanize_kana(reading) if reading else ""):
        value = str(value).strip()
        if value and value not in seen:
            fields.append(_clip(value, 56))
            seen.add(value)
    # Reuse the same reviewed sense selection as the existing inline preview.
    meaning = compact_word_preview(record).split("\n", 1)[-1]
    return " · ".join(fields), meaning


def _review_note(data: dict) -> str:
    status = str(data.get("review_status", ""))
    if data.get("practice_approved") is False or data.get("release_ready") is False or "candidate" in status or "unreviewed" in status:
        return _clip(data.get("review_label_zh") or "研究候选，尚未完成语言与教学审核。", 64)
    if data.get("practice_approved") is True:
        return "审核状态：已获练习许可。"
    return "审核状态：尚未提供。"


def _sense_preview(sense: Mapping, number: int, chinese=None) -> str:
    restrictions = []
    for field, label in (("kanji_restrictions", "仅限字形"), ("reading_restrictions", "仅限读法")):
        if sense.get(field):
            restrictions.append(label + "：" + _values(sense[field], 35))
    glosses = [gloss.get("text", "") for gloss in sense.get("japanese_meaning_english_glosses", [])
               if gloss.get("language", "eng") == "eng"]
    if chinese:
        glosses = chinese.get("glosses") or glosses
    prefix = f"{sense.get('sense_number', number)}. "
    if restrictions:
        prefix += "［" + "；".join(restrictions) + "］"
    gloss = _values(glosses, max(24, 150 - len(prefix))) or "英文释义尚未收集"
    return prefix + gloss


def word_preview(record: Mapping | None) -> str:
    """Show the first two dictionary senses and explicit research limitations.

    Romanization is computed from the stored kana reading, never from kanji,
    and is not presented as pitch accent or as an English source-word meaning.
    """
    data = _data(record)
    if not data:
        return "词条资料尚未收录。"
    reading = data.get("reading") or data.get("display_reading")
    form = data.get("form") or data.get("display_kanji") or reading
    lines = [
        "词条：" + (_clip(form, 56) or "字形尚未收录"),
        "读法：" + (_clip(reading, 56) or "尚未收录"),
        "罗马音（按假名）：" + (_clip(romanize_kana(reading), 76) if reading else "读法缺失，未生成"),
    ]
    senses = data.get("senses", [])
    chinese = {s["sense_id"]:s for s in data.get("chinese_senses", [])}
    if senses or data.get("gloss_language") == "eng":
        lines.append("日语词义（中文含模型辅助译文）：" if chinese else "日语词义（英文释义）：")
    if senses:
        lines.extend(_sense_preview(sense, number, chinese.get(sense.get("sense_id"))) for number, sense in enumerate(senses[:2], 1))
        if len(senses) > 2:
            lines.append(f"另有 {len(senses) - 2} 个义项，摘要未展开。")
    elif data.get("gloss"):
        lines.append("词义摘要：" + _clip(data["gloss"], 150))
    else:
        lines.append("词义尚未收集。")
    review = _review_note(data).rstrip("。；")
    if data.get("chinese_gloss_status") == "not_collected" or data.get("chinese_gloss") is None:
        review += "；中文释义尚未收集"
    lines.append(review + "。")
    return _finish(lines)


def source_preview(source: Mapping | None) -> str:
    """Summarize attribution/licensing without URLs or source-reference links."""
    data = _data(source)
    if not data:
        return "来源资料尚未收录。"
    license_text = data.get("license")
    if not license_text or "not_audited" in str(license_text):
        license_label = "许可尚未审核"
    elif "copyright" in str(license_text):
        license_label = "有版权限制；未核实开放内容许可"
    else:
        license_label = _clip(license_text, 100)
    lines = ["来源：" + (_clip(data.get("title"), 110) or "名称尚未收录"), "许可：" + license_label]
    attribution = data.get("attribution") or data.get("copyright") or data.get("author")
    if attribution:
        lines.append("署名：" + _clip(attribution, 130))
    date = data.get("snapshot") or data.get("snapshot_date") or data.get("accessed")
    if date:
        lines.append("资料日期：" + _clip(date, 24))
    status = data.get("redistribution_status")
    if status and ("fact" in status or "reference_only" in status):
        lines.append("使用范围：事实引用；原网页、图片、PDF 或音频不随应用分发。")
    elif status == "excluded_pending_specific_permission":
        lines.append("使用范围：待专项许可，未分发原资料。")
    elif status and ("permitted" in status or "redistributable" in status or "with_attribution" in status):
        lines.append("使用范围：须遵守所列许可和署名要求。")
    else:
        lines.append("再分发范围：须核对具体许可。")
    note = data.get("usage_note") or data.get("usage")
    if note:
        lines.append("说明：" + _clip(note, 150))
    return _finish(lines)
