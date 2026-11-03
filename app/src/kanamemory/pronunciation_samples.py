"""Compact pronunciation rows with evidence kept behind their disclosure.

An audio attachment still needs the full exact-reading contract. Character-input
demonstrations and system voices never borrow a dictionary transcription.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from .detail_content import DetailEntry, DetailLine, dialect_entry
from .detail_enrichment import history_entries
from .enrichment_presentation import HISTORICAL_SYSTEM_LABELS, recording_path
from .presentation import payload
from .reading_audio import MODERN, MIDDLE_CHINESE, matched_reading_audio


_GROUP_LABELS = {
    "middle_chinese": "中古汉语", "old_japanese": "上代日语",
    "historical_japanese": "历史日语", "mandarin": "普通话",
    "shanghai": "吴语 · 上海", "suzhou": "吴语 · 苏州",
    "other_wu": "其他吴语", "cantonese": "粤语",
    "southwestern": "西南官话", "other": "其他读音",
}
_NOTATIONS = {
    "Hanyu_Pinyin": "汉语拼音", "LSHK_Jyutping": "粤拼",
    "Jyutping_with_lexi_mf_tone_number": "粤拼",
    "source_broad_IPA_and_Chao_5_level_tone": "宽式 IPA · 五度调值",
    "IPA": "国际音标 IPA", **HISTORICAL_SYSTEM_LABELS,
}


@dataclass(frozen=True)
class PronunciationSample:
    key: str
    group: str
    title: str
    character: str
    reading: str
    notation: str
    audio: tuple[dict, ...] = ()
    details: tuple[DetailEntry, ...] = ()
    source_ids: tuple[str, ...] = ()
    record_ids: tuple[str, ...] = ()
    match_kind: str = "reference"
    tts_language: str = ""

    @property
    def playable(self) -> bool:
        return bool(self.audio or self.tts_language)

    @property
    def group_label(self) -> str:
        return _GROUP_LABELS[self.group]


def _data(record):
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


def _group(data, historical=False):
    if historical:
        return {"Middle Chinese": "middle_chinese", "中古汉语": "middle_chinese",
                "中古漢語": "middle_chinese", "ltc": "middle_chinese",
                "Old Japanese": "old_japanese", "Historical Japanese": "historical_japanese"}.get(data.get("language"), "other")
    locality = str(data.get("locality_id") or "")
    for prefix, group in (("cmn-", "mandarin"), ("wuu-shanghai-", "shanghai"),
                          ("synth-shanghai-", "shanghai"), ("wuu-suzhou-", "suzhou"),
                          ("wuu-", "other_wu"), ("yue-", "cantonese"),
                          ("mandarin-southwestern-", "southwestern")):
        if locality.startswith(prefix):
            return group
    return "other"


def _unique(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return tuple(result)


def _exists(resources, audio):
    try:
        return recording_path(resources, audio).is_file()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _line(label, value):
    return DetailLine((label + "：" + str(value),), "meta")


def _notation(data):
    return data.get("notation_label") or _NOTATIONS.get(data.get("notation_system"), data.get("notation_system")) or "注音方式待核"


def _reading(data, historical):
    value = data.get("reading") if historical else data.get("romanization") or data.get("ipa_segments") or data.get("transcription")
    text = str(value) if value else "原表缺记"
    if data.get("tone_value") is not None:
        text += " · 调值 " + str(data["tone_value"])
    elif value and data.get("notation_system") == "source_broad_IPA_and_Chao_5_level_tone":
        text += " · 调值原表缺记"
    if data.get("tone_number") is not None and not (value and str(value).endswith(str(data["tone_number"]))):
        text += " · 调号 " + str(data["tone_number"])
    if not value and data.get("tone_category"):
        text += " · " + str(data["tone_category"])
    return text


def _scope_key(data, historical):
    """Only collapse identical claims; source row locators remain in details."""
    keys = ("character", "hanzi", "source_hanzi", "reading", "romanization", "ipa_segments",
            "transcription", "notation_system", "tone_value", "tone_number", "tone_category",
            "language", "system", "locality_id", "period", "script_scope", "context",
            "register_or_reading_note", "claim_type", "phonological_categories", "fanqie",
            "source_definition", "applies_to_senses", "sense", "sense_id", "register",
            "meaning", "gloss", "notes")
    values = {key: data.get(key) for key in keys if data.get(key) is not None}
    # Two unknown transcriptions are not evidence of one pronunciation.
    if not any(data.get(key) for key in ("reading", "romanization", "ipa_segments", "transcription")):
        values["missing_reading_record"] = data.get("id") or repr(data)
    return historical, json.dumps(values, ensure_ascii=False, sort_keys=True)


def _details(data, historical, audio):
    if historical:
        entries = history_entries({"historical_reading_references": [data]})
        lines = [line for entry in entries for line in entry.lines
                 if line.role != "lead" and line.parts != (HISTORICAL_SYSTEM_LABELS.get(data.get("system"), data.get("system")),)]
    else:
        # The row already displays the character, locality, reading and notation.
        lines = list(dialect_entry(data).lines[3:])
    for field, label in (("tone_category", "调类"), ("source_definition", "原条目释义"),
                         ("limitation_zh", "适用限制"), ("source_locality_label", "地点说明")):
        value = data.get(field)
        if value and not any(str(value) in "".join(map(str, line.parts)) for line in lines):
            lines.append(_line(label, value))
    if data.get("source_locator"):
        locator = data["source_locator"]
        value = "；".join(f"{key}={value}" for key, value in locator.items()) if isinstance(locator, dict) else str(locator)
        lines.append(_line("出处定位", value))
    for recording in audio:
        for field, label in (("recording_type", "音频类型"), ("ipa", "音频 IPA"),
                             ("source_locality_label", "音库地点"), ("recording_scope", "音频范围"),
                             ("method_zh", "制作方法"), ("limitations_zh", "音频限制"),
                             ("author", "署名"), ("license", "许可"), ("source_url", "音频出处")):
            value = recording.get(field)
            if field == "recording_type":
                value = {"human_recording": "真人录音", "synthetic_demonstration": "合成示范",
                         "human_reconstruction": "人声拟音示范"}.get(value, value)
            if value:
                lines.append(_line(label, value))
    return (DetailEntry("资料", _unique(lines)),) if lines else ()


def _sources(data, audio=()):
    return _unique([*(data.get("source_ids") or ()),
                    *(_data(data.get("locality") or {}).get("source_ids") or ()),
                    *(source for item in audio for source in [*(item.get("source_ids") or ()), *(item.get("prompt_source_ids") or ())])])


def _merge(first, other):
    audio = tuple({item["id"]: item for item in [*first.audio, *other.audio]}.values())
    lines = _unique(line for sample in (first, other) for entry in sample.details for line in entry.lines)
    return replace(first, audio=audio, details=(DetailEntry("资料", lines),) if lines else (),
                   source_ids=_unique([*first.source_ids, *other.source_ids]),
                   record_ids=_unique([*first.record_ids, *other.record_ids]),
                   match_kind="exact_reading" if audio else first.match_kind)


def pronunciation_samples(*, dialects=(), historical=(), demonstrations=(),
                          system_characters=(), context=None, resources=None) -> tuple[PronunciationSample, ...]:
    """Ready-to-render rows, playable first, without an extra navigation layer.

    ``audio`` is original playback metadata. ``tts_language`` uses a system voice
    only when no exact bundled sample exists for that character and language.
    All origins, qualifications and source locators belong to ``details``.
    """
    resources = Path(resources) if resources is not None else Path(__file__).parent / "resources"
    context = _data(context)
    samples = {}
    for historical_kind, records in ((True, historical), (False, dialects)):
        for record in records:
            data = _data(record)
            group = _group(data, historical_kind)
            character = str(data.get("character") or data.get("hanzi") or data.get("source_hanzi") or "")
            if not character and historical_kind:
                # A historical kana claim can name the kana without claiming a
                # source Han character. This fallback is presentation only;
                # exact audio matching below still uses the untouched record.
                character = str(context.get("hiragana") or context.get("katakana") or "")
            matched = matched_reading_audio([data], MIDDLE_CHINESE if historical_kind else MODERN)
            audio = tuple(item.audio for item in matched if _exists(resources, item.audio))
            locality = _data(data.get("locality") or {})
            place = (_GROUP_LABELS[group] if historical_kind else data.get("locality_label") or locality.get("label")
                     or locality.get("label_zh") or (matched[0].place_or_system if matched else _GROUP_LABELS[group]))
            identity = _scope_key(data, historical_kind)
            sample = PronunciationSample(str(data.get("id") or identity[1]), group, str(place) + " · " + character,
                character, _reading(data, historical_kind), _notation(data), audio,
                _details(data, historical_kind, audio), _sources(data, audio),
                (str(data["id"]),) if data.get("id") else (), "exact_reading" if audio else "reference")
            samples[identity] = _merge(samples[identity], sample) if identity in samples else sample

    for record in demonstrations:
        data = _data(record)
        if not _exists(resources, data) or (data.get("signal_check") or {}).get("excluded"):
            continue
        character = data.get("character") or data.get("hanzi")
        if not isinstance(character, str) or len(character) != 1 or not data.get("id"):
            continue
        group = _group(data)
        place = str(data.get("locality_label") or _GROUP_LABELS[group]).replace("（模型示范）", "")
        lines = [_line("选音方式", "按字形自动选音，未与词典逐条注音对齐。")]
        for field, label in (("method_zh", "制作方法"), ("model_id", "模型"), ("model_variant", "版本"),
                             ("review_status", "审核状态")):
            if data.get(field):
                lines.append(_line(label, data[field]))
        lines.extend(line for entry in _details({}, False, (data,)) for line in entry.lines)
        samples[("demonstration", data["id"])] = PronunciationSample(data["id"], group, place + " · " + character,
            character, "暂无校核注音", "未标注", (data,), (DetailEntry("资料", _unique(lines)),),
            _sources(data, (data,)), (data["id"],), "automatic_character")

    exact = {(sample.character, sample.group) for sample in samples.values() if sample.match_kind == "exact_reading"}
    for character in _unique(system_characters):
        if not isinstance(character, str) or len(character) != 1:
            continue
        for language, group, place in (("cmn", "mandarin", "普通话"), ("yue", "cantonese", "香港粤语")):
            if (character, group) in exact:
                continue
            key = "system-" + language + "-" + character
            details = (DetailEntry("资料", (
                _line("选音方式", "系统按字自动选音，多音字可能与词典所列读法不同。"),
                _line("音频类型", "系统合成语音"),
                _line("使用条件", "需已安装对应的系统语音包。"),
            )),)
            samples[("system", language, character)] = PronunciationSample(key, group, place + " · " + character,
                character, "暂无校核注音", "未标注", details=details, match_kind="automatic_character", tts_language=language)
    order = {key: number for number, key in enumerate(_GROUP_LABELS)}
    return tuple(sorted(samples.values(), key=lambda sample: (not sample.playable, order[sample.group])))
