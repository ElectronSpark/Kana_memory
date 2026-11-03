"""Display collected linguistic evidence with its scope and uncertainty intact."""
from .kana_links import ContentText
from .presentation import payload
def C(value):
    return ContentText(str(value or ""))


def example_review_label(example):
    data = payload(example) or example
    sense = example.get("matched_sense_id", "").rsplit(":", 1)[-1]
    if data.get("content_origin") == "ai_generated":
        return "AI 编写例句，待人工校订。" + ("编写时选用义项 " + sense + "。" if sense else "")
    if sense:
        return "对应义项 " + sense
    if data.get("curation_status") == "long_exact_surface_word_candidate_not_editor_approved":
        return "按完整写法检索的候选；句中读法、义项待核。"
    return "词条用例；尚未与本词典义项逐一对齐。"

HISTORICAL_SYSTEM_LABELS = {
    "Baxter_Middle_Chinese_transcription_not_IPA":"Baxter 中古汉语转写（非 IPA）",
    "Frellesvig_Old_Japanese_orthographic_syllable_transcription_not_IPA":"Frellesvig 上代日语音节转写（非 IPA）",
    "Frellesvig_Old_Japanese_phonemic_analysis_not_IPA":"Frellesvig 上代日语音系分析（非 IPA）",
    "Nipponica_broad_phonemic_notation":"《日本大百科全书》宽式音系记号",
    "Unicode_historical_syllable_label_not_IPA":"Unicode 历史音节说明（非 IPA）",
    "Guangyun_fanqie_and_traditional_phonological_categories_not_IPA":"《广韵》反切及传统音韵类别（非拟音）",
}


def block(title, *lines):
    parts = []
    for i, line in enumerate(lines):
        if i:
            parts.append("\n")
        parts.extend(line if isinstance(line, tuple) else (line,))
    return title, parts


def historical_blocks(data):
    direct = data.get("historical_reading_references", [])
    components = data.get("component_historical_reading_references", [])
    for record in direct or components:
        title = "构件字的历史参照" if not direct else "历史读音参照"
        limitation = record.get("limitation_zh") or ""
        if limitation.startswith("供历史比较，不是该假名形成"):
            limitation = ""  # The page-level explanation states this shared scope.
        yield block(title, (C(record.get('source_hanzi') or data.get('hiragana') or data.get('katakana')), " · ", C(record.get('reading', ''))),
                    C(record.get("period", "")), HISTORICAL_SYSTEM_LABELS.get(record.get("system"), record.get("system") or ""),
                    C(record.get("reading_convention_zh", "")),
                    ("仅适用于片假名。" if record.get("script_scope") == "katakana" else "仅适用于平假名。" if record.get("script_scope") == "hiragana" else ""),
                    C(limitation))


def word_enrichment_blocks(data):
    japanese = data.get("japanese_enrichment", {})
    pitch = data.get("pitch_accent", [])
    if pitch:
        yield block("日语音调", *[(C(p['reading']), " [", C(p.get('raw', ', '.join(map(str, p['positions'])))), "] · 来源字形 ", C(p.get('source_form', ''))) for p in pitch])
        yield "音调标记", ["东京式辞书形：0 为无下降；n 表示第 n 拍后下降。复合、活用和句中语调可能变化；合成音不用于校定音调。"]
    else:
        yield "日语音调", ["所查开放资料中尚无可精确匹配的标注。"]
    pronunciations = []
    for p in japanese.get("pronunciations", []):
        if p.get("ipa"):
            line = (C(p.get('form', '')), " · ", C(' / '.join(p.get('applies_to_readings', []))), " · ", C(p['ipa']))
            if line not in pronunciations:
                pronunciations.append(line)
    if pronunciations:
        yield block("日语记音参照", *pronunciations)
    evidence = japanese.get("etymology_evidence", [])
    if evidence:
        yield "语源记载", ["以下保留来源的语源说明，适用字形和读法各自列出；尚未完成逐项教学审核。"]
        for e in evidence:
            yield block("来源说明", (C(e.get('form', '')), " · ", C(' / '.join(e.get('applies_to_readings', [])))), C(e.get("text", "")))


def external_word_blocks(data):
    japanese = data.get("japanese_enrichment", {})
    english = data.get("english_enrichment", {})
    if english.get("english_entries"):
        yield "英语原词", ["以下是英语源词本身的释义和发音，尚未与日语各义项逐一对齐。"]
    for entry in english.get("english_entries", []):
        lines = [(C(entry["lemma"]), " · ", C(", ".join(entry.get("pos", []))))]
        lines += [(C(p.get('variety', '')), " ", C(p['notation']), ": ", C(p['value'])) for p in entry.get("pronunciations", [])]
        if not entry.get("pronunciations"):
            lines.append("整个英语表达的读音尚未取得。")
            for component in entry.get("component_pronunciations", []):
                for p in component.get("pronunciations", []):
                    lines.append(("分词参照：", C(component["word"]), " · ", C(p["notation"]), " ", C(p["value"])))
            if entry.get("component_pronunciations"):
                lines.append("分词读音不代表整个表达的重音与连续语流。")
        if any(p.get("notation") == "ARPABET" for p in entry.get("pronunciations", [])):
            lines.append("ARPABET 的元音后缀 0/1/2 分别为非重读/主重音/次重音。")
        senses = entry.get("senses") or entry.get("supplemental_wiktionary_senses", [])
        yield block("英语词条", *lines)
        for i, sense in enumerate(senses, 1):
            yield block("英语义项 " + str(i), (C(sense.get('pos', '')), " · ", C(sense.get('definition', ''))),
                        *[("例：", C(str(e))) for e in sense.get("examples", [])[:2]])
        if not senses:
            yield "英语释义", ["整个表达的独立英语释义尚未取得；不能据日语义项反推。"]
    mandarin = english.get("mandarin_equivalents", {})
    if mandarin:
        yield "英语源词的普通话对应", ["按英语词义匹配的候选，未逐项核对日语义项。保留地域与旧式词，未核当代普通话常用程度。借入记载、借词标签和普通译名分别显示。"]
        for key, title in (("attested_transliteration_candidates", "另有英语借入记载的候选"),
                           ("source_labeled_loanword_candidates", "来源标作借词的候选"),
                           ("semantic_translation_candidates", "普通话对应候选（译法未分类）")):
            records = mandarin.get(key, [])
            if records:
                yield block(title, *[(C(r.get('simplified') or r.get('text', '')), " ", C(r.get('pinyin') or ''), " ← ", C(r.get('english_lemma', '')), " · ", C(r.get('matched_english_gloss') or r.get('english_translation_sense', ''))) for r in records])
                if key == "attested_transliteration_candidates":
                    yield "证据范围", ["英语借入记载支持词源关系，尚不能据此确定是纯音译或对应哪个日语义项。"]
    references = japanese.get("same_spelling_chinese_reference", [])
    if references:
        yield "同形汉语词参照", ["以下资料属于汉语词条；同形关系不证明日语词源或词义相同。"]
        yield block("普通话词音与释义", *[(C(r['simplified']), " · ", C(r['mandarin_pinyin']), " · ", C('; '.join(r['chinese_meaning_english']))) for r in references])


def recording_path(resource_folder, audio):
    """Packaged audio cannot address files outside the resources/media folder."""
    from pathlib import Path
    relative = Path(audio["local_path"])
    if relative.is_absolute() or ".." in relative.parts or not relative.parts or relative.parts[0] != "media":
        raise ValueError("无效的录音路径。")
    destination = (resource_folder / relative).resolve()
    if not destination.is_relative_to((resource_folder / "media").resolve()):
        raise ValueError("无效的录音路径。")
    return destination
