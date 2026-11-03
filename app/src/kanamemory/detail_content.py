"""Small detail cards, without UI widgets or new linguistic claims.

Only ContentText values are eligible for kana links. Shared editorial warnings
belong to the enclosing page; restrictions that qualify a fact remain here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .kana_links import ContentText
from .presentation import payload
from .reading_audio import MODERN, ReadingAudio, matched_reading_audio


@dataclass(frozen=True)
class DetailLine:
    parts: tuple[str | ContentText, ...]
    role: str = "body"


@dataclass(frozen=True)
class DetailEntry:
    label: str
    lines: tuple[DetailLine, ...]
    # Only an explicitly aligned reading attachment belongs beside this
    # entry's pronunciation. Character-input TTS is a separate demonstration.
    audio: tuple[ReadingAudio, ...] = ()


COMBINATION_CATEGORIES = frozenset({"yoon", "foreign_combination", "foreign_variant"})


def _data(record):
    """Accept raw payloads and ContentRepository's enriched public records."""
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


def _content(value):
    return ContentText(str(value if value is not None else ""))


def _values(values, separator="；"):
    if isinstance(values, str):
        values = [values]
    result = []
    for value in values or ():
        if value is None or value == "":
            continue
        if result:
            result.append(separator)
        result.append(_content(value))
    return tuple(result)


def _field(label, values, role="meta", separator="；"):
    parts = _values(values, separator)
    return DetailLine((label + "：", *parts), role) if parts else None


def is_kana_combination(record) -> bool:
    """The inventory has 36 yoon + 32 foreign combinations + 2 variants.

    Components also exist on voiced and small *single* letters, so they are
    intentionally not a test for combinations.
    """
    return _data(record).get("category") in COMBINATION_CATEGORIES


def sense_entries(record) -> tuple[DetailEntry, ...]:
    data = _data(record)
    chinese = {s["sense_id"]: s for s in data.get("chinese_senses", ())}
    result = []
    for number, sense in enumerate(data.get("senses", ()), 1):
        lines = []
        translation = chinese.get(sense.get("sense_id"), {})
        meanings = _values(translation.get("glosses"))
        if meanings:
            lines.append(DetailLine(meanings, "lead"))
        english = []
        for gloss in sense.get("japanese_meaning_english_glosses", ()):
            if gloss.get("language", "eng") != "eng" or not gloss.get("text"):
                continue
            if english:
                english.append("; ")
            english.append(_content(gloss["text"]))
            qualifiers = _values([gloss.get("type"), gloss.get("gender")], ", ")
            if qualifiers:
                english.extend((" (", *qualifiers, ")"))
        if english:
            lines.append(DetailLine(("英文：", *english), "body"))
        if not meanings and not english:
            lines.append(DetailLine(("释义尚未收集",), "note"))
        for key, label in (("pos_effective", "词性"), ("kanji_restrictions", "仅限字形"),
                           ("reading_restrictions", "仅限读法"), ("field", "领域"),
                           ("misc", "用法标签"), ("dialect_tags", "方言标签")):
            line = _field(label, sense.get(key))
            if line:
                lines.append(line)
        line = _field("说明", sense.get("notes"), "note")
        if line:
            lines.append(line)
        result.append(DetailEntry("义项 " + str(sense.get("sense_number", number)), tuple(lines)))
    if not result:
        lines = (DetailLine((_content(data["gloss"]),), "body"),) if data.get("gloss") else (DetailLine(("词义尚未整理",), "note"),)
        result.append(DetailEntry("词义", lines))
    return tuple(result)


_READING_NOTES = {
    "音节索引所列字音；本索引未区分词义与文白语境，多个字音均保留。": "索引未分词义／文白，保留多音。",
    "普通话代表读音（大陆优先），非全部多音字义项。": "大陆优先的代表音；未覆盖全部义项。",
    "常用粤拼参照，来源未指定广州或香港地点。": "来源未指定广州／香港。",
    "原条目可能分字义、读法或变读；此处保留全部所列音，不指定首选读法。": "保留全部所列音；字义／变读待分，未指定首选。",
}


def dialect_entry(record) -> DetailEntry:
    data = _data(record)
    locality = _data(data.get("locality") or {})
    place = data.get("locality_label") or locality.get("label") or locality.get("label_zh") or data.get("locality_id")
    reading = data.get("romanization") or data.get("ipa_segments") or data.get("transcription")
    lead = (_content(data.get("character") or data.get("hanzi")), " · ", _content(reading) if reading else "原表缺记")
    lines = [DetailLine(lead, "lead"), DetailLine((_content(place),) if place else ("地点待核",), "body")]
    notation = data.get("notation_system")
    tone, number, category = data.get("tone_value"), data.get("tone_number"), data.get("tone_category")
    if notation == "source_broad_IPA_and_Chao_5_level_tone":
        detail = ["宽式 IPA；", "五度调值 ", _content(tone)] if tone is not None else ["宽式 IPA；调值原表缺记"]
    elif notation in {"LSHK_Jyutping", "Jyutping_with_lexi_mf_tone_number"}:
        detail = ["粤拼；末位数字为调号"]
        if number is not None and (not reading or not str(reading).endswith(str(number))):
            detail.extend(("；调号 ", _content(number)))
        if tone is not None:
            detail.extend(("；调值 ", _content(tone)))
    else:
        notation_label = data.get("notation_label")
        if not isinstance(notation_label, str) or not notation_label.strip():
            notation_label = notation
        detail = ["汉语拼音"] if notation == "Hanyu_Pinyin" else ["记音：", _content(notation_label) if notation_label else "体系待核"]
        if tone is not None:
            detail.extend(("；调值 ", _content(tone)))
        if number is not None:
            detail.extend(("；调号 ", _content(number)))
    if category:
        detail.extend(("；调类 ", _content(category)))
    lines.append(DetailLine(tuple(detail), "meta"))
    year = locality.get("reference_publication_year")
    if year is not None:
        lines.append(DetailLine(("资料出版：", _content(year), " 年；非当下个体发音"), "meta"))
    elif locality.get("status") == "survey_attested_survey_date_pending":
        lines.append(DetailLine(("调查年代待核",), "meta"))
    context = data.get("context")
    contexts = {
        "citation_form_single_character; connected_speech_not_inferred": "单字音；连读变调未收录",
        "single_character_citation_form_not_word_or_connected_speech": "单字音；连读变调未收录",
        "representative_dictionary_character_reading_not_all_senses": "字典代表音；未覆盖全部义项",
        "representative_dictionary_reading; locality_and_sense_unspecified": "字典代表音；地点与义项未定",
        "dictionary_citation_form": "字典单字音",
    }
    note = data.get("register_or_reading_note")
    if context:
        lines.append(DetailLine((contexts[context],) if context in contexts else ("适用范围：", _content(context)), "meta"))
    if note:
        lines.append(DetailLine((_content(_READING_NOTES.get(note, note)),), "note"))
    return DetailEntry("", tuple(lines), matched_reading_audio([record], MODERN))


_COMMON_ORIGIN_DISPUTE = "片假名表明示有诸说，这里保留其中一种代表解释。"


def origin_entries(record) -> tuple[DetailEntry, ...]:
    """One card per graphic-origin claim; historical variants stay separate.

    Accept either an individual origin record or a kana's ``origins`` list.
    No historical sound is inferred from the source character.
    """
    data = _data(record)
    if "origins" in data and not any(k in data for k in ("hiragana_origins", "katakana_origins")):
        return tuple(entry for origin in data["origins"] for entry in origin_entries(origin))
    result = []
    dispute = data.get("dispute_note_zh") or ""
    specific_dispute = dispute.removeprefix(_COMMON_ORIGIN_DISPUTE)
    for key, label in (("hiragana_origins", "平假名字源"), ("katakana_origins", "片假名字源")):
        claims = data.get(key, ())
        for index, claim in enumerate(claims):
            lines = [DetailLine((_content(claim.get("han_character")),), "lead")]
            if claim.get("process_zh"):
                lines.append(DetailLine((_content(claim["process_zh"]),), "body"))
            representative = claim.get("certainty") == "representative_explanation_not_exclusive" or claim.get("claim_type") == "representative_hypothesis"
            if representative or (key == "katakana_origins" and dispute.startswith(_COMMON_ORIGIN_DISPUTE)):
                lines.append(DetailLine(("代表解释，非唯一结论。",), "note"))
            if claim.get("formation_period") is not None:
                lines.append(DetailLine(("字形形成时期：", _content(claim["formation_period"])), "meta"))
            result.append(DetailEntry(label + (" · " + str(index + 1) if len(claims) > 1 else ""), tuple(lines)))
    if specific_dispute:
        # A shared disagreement is stated once, after the competing claims.
        result.append(DetailEntry("字源说明", (DetailLine((_content(specific_dispute),), "note"),)))
    for variant in data.get("other_historical_forms", ()):
        parts = (_content(variant.get("glyph")), " ← ", _content(variant.get("han_character")))
        lines = [DetailLine(parts, "lead")]
        if variant.get("reading_label"):
            lines.append(DetailLine(("记音：", _content(variant["reading_label"])), "body"))
        lines.append(DetailLine(("历史异体，不是现行标准假名的另一条直接字源。",), "note"))
        result.append(DetailEntry("历史异体", tuple(lines)))
    return tuple(result)
