"""Individual evidence cards; presentation only, without linguistic inference."""
from __future__ import annotations

from .detail_content import DetailEntry, DetailLine
from .enrichment_presentation import HISTORICAL_SYSTEM_LABELS
from .kana_links import ContentText
from .presentation import payload
from .reading_audio import MIDDLE_CHINESE, matched_reading_audio
from .content_pack import load_namespace


# Display once above the cards on every reference page, including later pages.
# Individual records retain their identity and specific limits, not this prose.
REFERENCE_PAGE_NOTE = (
    "英语释义、发音及普通话候选均针对英语源词，未对齐日语义项。"
    "候选含地域、旧式用语，常用程度待核；借入记载不等于纯音译。"
    "同形汉语参照不证明日语词义或词源。分词发音不代表整句重音与连读。"
)
PITCH_NOTE = "东京式辞书形：0 为无下降；n 为第 n 拍后下降。复合、活用及句中语调可能变化；合成音不用于校定音调。"
ARPABET_NOTE = "ARPABET 元音后的 0 / 1 / 2 分别表示非重读／主重音／次重音；变体未逐项对齐词性或日语义项。"
ETYMOLOGY_NOTE = "保留来源的语源记载及各自适用字形、读法，尚未完成逐项教学审核。"
_HISTORICAL_COMMON = "供历史比较，不是该假名形成或首次传入时唯一实际读音；不能按字形关系直接等同汉语与日语音值。"
_CMU_NOTE = "CMU vowel suffixes 0/1/2 denote unstressed/primary/secondary stress; alternatives are not aligned to POS or Japanese senses."
_SAME_FORM_WARNING = "这是同形汉语词的资料，不代表日语释义，也不证明日语词源；拼音来自词条本身。"
_BORROWING_SCOPE = "Two dictionaries supply a borrowing claim and Mandarin lexical attestation. This does not assert that every Japanese sense shares the same Mandarin usage, nor that the spelling has no semantic component."
_SOURCES = load_namespace("presentation_metadata", {}).get("detail_sources", {})


def _data(record):
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


def _c(value):
    return ContentText(str(value if value is not None else ""))


def _joined(values, separator=" / "):
    if isinstance(values, str):
        values = [values]
    parts = []
    for value in values or ():
        if value is None or value == "":
            continue
        if parts:
            parts.append(separator)
        parts.append(_c(value))
    return tuple(parts)


def _field(label, values, role="meta", separator=" / "):
    parts = _joined(values, separator)
    return DetailLine((label + "：", *parts), role) if parts else None


def _entry(label, *lines, audio=()):
    return DetailEntry(label, tuple(line for line in lines if line is not None), audio)


def _source(record):
    names = []
    for source in record.get("source_ids") or ():
        if source.startswith("wiktionary-zh-japanese-"):
            name = "中文维基词典·日语"
        elif source.startswith("wiktionary-en-japanese-"):
            name = "英语维基词典·日语"
        elif source.startswith("enwiktionary-english:"):
            name = "英语维基词典·英语"
        elif source.startswith("enwiktionary-chinese:"):
            name = "英语维基词典·汉语"
        else:
            name = _SOURCES.get(source, source)
        if name not in names:
            names.append(name)
    # Source labels are interface metadata, not linkable learning text.
    return DetailLine(("来源：" + "；".join(names),), "meta") if names else None


def history_entries(record) -> tuple[DetailEntry, ...]:
    """One historical claim per card, then one note per distinct convention."""
    data = _data(record)
    direct = data.get("historical_reading_references") or ()
    components = data.get("component_historical_reading_references") or ()
    records = direct or components
    if not records and "reading" in data and "claim_type" in data:
        records = [data]
        direct = records
    result, conventions = [], {}
    category_labels = {"initial": "声母", "rhyme": "韵", "rhyme_group": "摄", "tone": "声调",
                       "division": "等", "openness": "开合", "place": "发音部位", "voicing": "清浊"}
    for item in records:
        system = item.get("system") or item.get("notation_system") or ""
        system_label = HISTORICAL_SYSTEM_LABELS.get(system, system)
        limitation = item.get("limitation_zh") or ""
        # Remove only the known shared sentence, never an arbitrary prefix.
        limitation = limitation.removeprefix(_HISTORICAL_COMMON).strip()
        scope = {"katakana": "仅适用于片假名。", "hiragana": "仅适用于平假名。"}.get(item.get("script_scope"))
        if item.get("script_scope") and not scope:
            scope = "适用文字：" + str(item["script_scope"])
        source_char = item.get("source_hanzi") or item.get("character") or data.get("hiragana") or data.get("katakana")
        lead = _joined([source_char, item.get("reading")], " · ")
        lines = [DetailLine(lead, "lead"), _field("时期", item.get("period")),
                 DetailLine((system_label,), "meta") if system_label else None,
                 DetailLine((scope,), "note") if scope else None]
        categories = item.get("phonological_categories") or {}
        if categories:
            parts = []
            for key, value in categories.items():
                if parts:
                    parts.append("；")
                parts.extend((category_labels.get(key, key) + " ", _c(value)))
            lines.append(DetailLine(tuple(parts), "body"))
        if item.get("fanqie") and item["fanqie"] not in (item.get("reading") or ""):
            lines.append(_field("反切", item["fanqie"], "body"))
        if item.get("query_character") and item["query_character"] != source_char:
            lines.append(_field("查询字形", item["query_character"]))
        lines += [_field("原表拼音栏", item.get("source_modern_pinyin")),
                  _field("限制", limitation, "note"), _source(item)]
        result.append(_entry("历史读音参照" if direct else "构件字的历史参照", *lines,
                             audio=matched_reading_audio([item], MIDDLE_CHINESE)))
        convention = item.get("reading_convention_zh")
        if convention:
            conventions.setdefault((system_label, convention), None)
    for (system_label, convention) in conventions:
        result.append(_entry("记音说明", DetailLine((system_label,), "meta"), DetailLine((_c(convention),), "note")))
    return tuple(result)


def japanese_entries(record, *, include_pitch=True) -> tuple[DetailEntry, ...]:
    data = _data(record)
    japanese = data.get("japanese_enrichment") or {}
    pitch = data.get("pitch_accent") or japanese.get("pitch_accent") or ()
    result = []
    if include_pitch:
        for item in pitch:
            raw = item.get("raw")
            if raw is None:
                raw = ", ".join(map(str, item.get("positions") or ()))
            lines = [DetailLine((_c(item.get("reading")), " [", _c(raw), "]"), "lead"),
                     _field("来源字形", item.get("source_form"))]
            if item.get("source_reading") != item.get("reading"):
                lines.append(_field("来源读法", item.get("source_reading")))
            if item.get("scope") not in {None, "citation_form_not_sentence_sandhi"}:
                lines.append(_field("范围", item["scope"], "note"))
            if item.get("system") not in {None, "Tokyo_pitch_drop_after_mora_zero_unaccented"}:
                lines.append(_field("体系", item["system"]))
            lines.append(_source(item))
            result.append(_entry("日语音调", *lines))
        result.append(_entry("音调说明" if pitch else "日语音调",
                             DetailLine((PITCH_NOTE if pitch else "所查开放资料中尚无可精确匹配的标注。",), "note")))
    for item in japanese.get("pronunciations") or ():
        if not item.get("ipa"):
            continue
        result.append(_entry("日语 IPA", DetailLine((_c(item["ipa"]),), "lead"),
                             _field("字形", item.get("form"), "body"),
                             _field("适用读法", item.get("applies_to_readings")),
                             _field("地点／用法", [*(item.get("tags") or ()), *(item.get("raw_tags") or ())]),
                             _field("说明", item.get("note"), "note"), _source(item)))
    evidence = japanese.get("etymology_evidence") or ()
    for item in evidence:
        lines = [DetailLine(_joined([item.get("form"), *item.get("applies_to_readings", ())], " · "), "lead"),
                 _field("记载", item.get("text"), "body"), _field("原文语言", item.get("language"))]
        for relation in item.get("relations") or ():
            if relation.get("text") and relation["text"] not in (item.get("text") or ""):
                lines.append(_field("来源所列关系", relation["text"], "body"))
        if item.get("classification") not in {None, "source_statement_only_not_automatic_borrowing_classification"}:
            lines.append(_field("范围", item["classification"], "note"))
        lines.append(_source(item))
        result.append(_entry("语源记载", *lines))
    if evidence:
        result.append(_entry("语源说明", DetailLine((ETYMOLOGY_NOTE,), "note")))
    return tuple(result)


def _english_pronunciation(lemma, pronunciation, pos=(), component=None):
    lines = [DetailLine((_c(pronunciation.get("value")),), "lead"),
             _field("英语源词", lemma, "body")]
    if component:
        lines.append(_field("分词", component, "body"))
    lines += [_field("词性", pos), _field("记音", pronunciation.get("notation")),
              _field("地域／口音", pronunciation.get("variety"))]
    qualifiers = pronunciation.get("template_qualifiers") or {}
    # Numbered qualifiers refer to positions in the source template. Preserve
    # those indices rather than asserting each qualifier applies to this IPA.
    extra = [f"{k}={v}" for k, v in qualifiers.items()
             if not (k in {"a", "aa"} and v == pronunciation.get("variety"))]
    lines.append(_field("原模板其他限定（序号沿用来源，逐音对应待核）", extra))
    if pronunciation.get("dictionary_spelling") not in {None, component or lemma}:
        lines.append(_field("词典字形", pronunciation["dictionary_spelling"]))
    if pronunciation.get("note") not in {None, _CMU_NOTE}:
        lines.append(_field("说明", pronunciation["note"], "note"))
    lines.append(_source(pronunciation))
    return _entry("英语分词发音" if component else "英语发音", *lines)


def reference_entries(record) -> tuple[DetailEntry, ...]:
    """Slice into pages of eight, displaying REFERENCE_PAGE_NOTE on each page."""
    data = _data(record)
    english = data.get("english_enrichment") or {}
    japanese = data.get("japanese_enrichment") or {}
    result, has_arpabet = [], False
    for entry in english.get("english_entries") or ():
        lemma = entry.get("lemma", "")
        pronunciations = entry.get("pronunciations") or ()
        for item in pronunciations:
            result.append(_english_pronunciation(lemma, item, entry.get("pos")))
            has_arpabet |= item.get("notation") == "ARPABET"
        if not pronunciations:
            result.append(_entry("英语表达读音", DetailLine((_c(lemma),), "lead"),
                                 DetailLine(("整个英语表达的读音尚未取得。",), "note")))
            for component in entry.get("component_pronunciations") or ():
                for item in component.get("pronunciations") or ():
                    result.append(_english_pronunciation(lemma, item, component=component.get("word")))
                    has_arpabet |= item.get("notation") == "ARPABET"
        senses = entry.get("senses") or entry.get("supplemental_wiktionary_senses") or ()
        for number, sense in enumerate(senses, 1):
            lines = [DetailLine((_c(sense.get("definition")),), "lead"),
                     _field("英语源词", lemma, "body"), _field("词性", sense.get("pos"))]
            if sense.get("matched_lemma") not in {None, lemma}:
                lines.append(_field("词典匹配词形", sense["matched_lemma"]))
            lines += [_field("例", value, "body") for value in sense.get("examples") or ()]
            lines.append(_source(sense))
            result.append(_entry("英语义项 " + str(number), *lines))
        if not senses:
            result.append(_entry("英语释义", DetailLine((_c(lemma),), "lead"),
                                 _field("词性", entry.get("pos")),
                                 DetailLine(("整个表达的独立英语释义尚未取得；不能据日语义项反推。",), "note")))
    if has_arpabet:
        result.append(_entry("英语记音说明", DetailLine((ARPABET_NOTE,), "note")))
    mandarin = english.get("mandarin_equivalents") or {}
    kinds = (("attested_transliteration_candidates", "有英语借入记载的候选"),
             ("source_labeled_loanword_candidates", "来源标作借词的候选"),
             ("semantic_translation_candidates", "普通话对应候选（译法未分类）"))
    for key, label in kinds:
        for item in mandarin.get(key) or ():
            written = item.get("simplified") or item.get("text", "")
            meaning = item.get("matched_english_gloss") or item.get("english_translation_sense")
            lines = [DetailLine(_joined([written, item.get("pinyin")], " · "), "lead"),
                     _field("英语源词", item.get("english_lemma"), "body"),
                     _field("对应英语词义", meaning, "body")]
            if item.get("traditional") != written:
                lines.append(_field("繁体", item.get("traditional")))
            other_glosses = [g for g in item.get("dictionary_glosses") or () if g != meaning]
            lines.append(_field("词典其他释义／用法", other_glosses, "body", "; "))
            evidence = item.get("borrowing_evidence") or {}
            for claim in evidence.get("claims") or ():
                lines.append(_field("借入记载", [claim.get("donor_language"), claim.get("english_source_expression"), claim.get("recipient_language")], "body", " → "))
            if evidence.get("scope_note") not in {None, _BORROWING_SCOPE}:
                lines.append(_field("证据范围", evidence["scope_note"], "note"))
            if key == "source_labeled_loanword_candidates":
                lines.append(DetailLine(("借词标签未核为英语借入。",), "meta"))
            lines.append(_source(item))
            result.append(_entry(label, *lines))
    for item in japanese.get("same_spelling_chinese_reference") or ():
        written = item.get("simplified", "")
        lines = [DetailLine(_joined([written, item.get("mandarin_pinyin")], " · "), "lead"),
                 _field("汉语词义（英文）", item.get("chinese_meaning_english"), "body", "; ")]
        if item.get("traditional") != written:
            lines.append(_field("繁体", item.get("traditional")))
        lines.append(_field("对应日语字形", item.get("matched_japanese_form")))
        if item.get("warning_zh") not in {None, _SAME_FORM_WARNING}:
            lines.append(_field("限制", item["warning_zh"], "note"))
        lines.append(_source(item))
        result.append(_entry("同形汉语词", *lines))
    return tuple(result)
