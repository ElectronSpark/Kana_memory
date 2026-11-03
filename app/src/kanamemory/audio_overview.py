"""Expose every existing pronunciation version as a separate playback choice.

This is a widget-free projection. It does not merge equal readings, invent an
extra voice, or attach a dictionary transcription to automatic character audio.
The complete source objects remain available for the later information panel.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable

from .enrichment_presentation import recording_path
from .kana_audio import KanaAudioPlan, kana_audio_plan
from .presentation import payload
from .pronunciation_samples import PronunciationSample
from .romanization import kana_learning_romaji


@dataclass(frozen=True)
class AudioChoice:
    key: str
    title: str
    full_title: str
    reading: str
    notation: str
    kind: str
    plan: KanaAudioPlan | None = None
    kana: dict | None = None
    recording: dict | None = None
    fallback_recordings: tuple[dict, ...] = ()
    sample: PronunciationSample | None = None


def recording_available(audio: dict) -> bool:
    try:
        return recording_path(Path(__file__).parent / "resources", audio).is_file()
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _data(record: dict) -> dict:
    data = payload(record)
    return {**(data if isinstance(data, dict) else {}),
            **{key: value for key, value in record.items() if key != "payload"}}


def _sample_title(sample: PronunciationSample) -> str:
    short_group = {
        "middle_chinese": "中古", "old_japanese": "上代", "historical_japanese": "古日",
        "mandarin": "普通话", "shanghai": "上海", "suzhou": "苏州",
        "other_wu": "吴语", "cantonese": "粤语", "southwestern": "西南",
        "other": "读音",
    }.get(sample.group, "读音")
    if sample.group == "southwestern":
        # Keep the explicitly broad 乐山 attribution broad, even if its title
        # also mentions a file's 犍为 label. Full qualifications remain intact.
        short_group = next((place for place in ("乐山", "成都", "犍为", "武昌")
                            if place in sample.title), short_group)
    elif sample.group == "cantonese":
        short_group = next((place for place in ("香港", "广州") if place in sample.title), short_group)
    return short_group + sample.character


def _distinct(choices: list[AudioChoice]) -> tuple[AudioChoice, ...]:
    """Number ambiguous short labels without merging sources or changing IDs."""
    counts = Counter(choice.title for choice in choices)
    occurrences: Counter[str] = Counter()
    used_keys: set[str] = set()
    result = []
    for choice in choices:
        occurrences[choice.title] += 1
        title = choice.title + str(occurrences[choice.title]) if counts[choice.title] > 1 else choice.title
        key, occurrence = choice.key, 1
        while key in used_keys:
            occurrence += 1
            key = choice.key + "#" + str(occurrence)
        used_keys.add(key)
        result.append(replace(choice, key=key, title=title))
    return tuple(result)


def kana_audio_choices(item: dict, family: Iterable[dict], samples: Iterable[PronunciationSample], *,
                       available: Callable[[dict], bool] = recording_available) -> tuple[AudioChoice, ...]:
    """Return main kana voices, each original recording, then each sample voice.

    ``japanese`` choices use their plan when TTS is ready and may fall back to
    ``fallback_recordings``. ``recording`` and ``sample_audio`` play exactly
    ``recording``. ``sample_tts`` uses ``sample.character``/``tts_language``.
    No callback, widget, progress record, or content object is modified here.
    The selected item supplies its complete data when family holds summaries.
    """
    choices: list[AudioChoice] = []
    members = list(family)
    if not any(member.get("id") == item.get("id") for member in members):
        members.append(item)
    for member in members:
        source = item if member.get("id") == item.get("id") else member
        data = _data(source)
        clips = tuple(audio for audio in (data.get("recordings") or ()) if available(audio))
        plan = kana_audio_plan(data)
        form = data.get("hiragana") or data.get("katakana") or ""
        forms = "／".join(dict.fromkeys(value for value in (data.get("hiragana"), data.get("katakana")) if value))
        identity = str(data.get("id") or forms)
        reading = kana_learning_romaji(data)
        notation = "罗马音（字形标记）" if reading else ""
        if plan.playable or clips:
            title = "日语" + form
            full_title = "日语 · " + forms + (" · " + plan.label if plan.label else "")
            choices.append(AudioChoice("japanese:" + identity, title, full_title, reading, notation,
                "japanese", plan=plan, kana=source, fallback_recordings=clips))
        for index, audio in enumerate(clips, 1):
            identity_audio = str(audio.get("id") or audio.get("local_path") or index)
            full_title = "日语原录音 · " + forms
            if audio.get("author"):
                full_title += " · " + audio["author"]
            choices.append(AudioChoice("recording:" + identity + ":" + identity_audio,
                "录音" + form, full_title, reading, notation, "recording", kana=source, recording=audio))
    for sample in samples:
        for index, audio in enumerate(sample.audio, 1):
            if not available(audio):
                continue
            identity = str(audio.get("id") or audio.get("local_path") or index)
            choices.append(AudioChoice("sample-audio:" + sample.key + ":" + identity,
                _sample_title(sample), sample.title, sample.reading, sample.notation,
                "sample_audio", recording=audio, sample=sample))
        if sample.tts_language:
            choices.append(AudioChoice("sample-tts:" + sample.key + ":" + sample.tts_language,
                _sample_title(sample), sample.title, sample.reading, sample.notation,
                "sample_tts", sample=sample))
    return _distinct(choices)
