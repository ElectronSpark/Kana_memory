"""Discoverable reading groups without merging places, sources, or claims.

Groups are navigation labels only. Every source row remains its own detail card,
including locality, notation, dates, qualifications and inline audio attachment.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from .detail_content import DetailEntry, dialect_entry
from .detail_enrichment import history_entries
from .enrichment_presentation import recording_path
from .presentation import payload


_LABELS = {
    "middle_chinese": "中古汉语",
    "old_japanese": "上代日语",
    "historical_japanese": "其他历史日语",
    "mandarin": "普通话",
    "shanghai": "吴语 · 上海",
    "suzhou": "吴语 · 苏州",
    "other_wu": "吴语 · 其他地点",
    "cantonese": "粤语",
    "southwestern": "西南官话",
    "other": "其他字音资料",
}
_PRIMARY = frozenset({"middle_chinese", "old_japanese", "mandarin", "shanghai",
                      "suzhou", "cantonese", "southwestern"})


@dataclass(frozen=True)
class PronunciationSection:
    key: str
    label: str
    entries: tuple[DetailEntry, ...]
    reading_count: int
    audio_count: int

    @property
    def title(self) -> str:
        sound = f"{self.audio_count} 段音频" if self.audio_count else "暂无音频"
        return f"{self.label} · {self.reading_count} 条 · {sound}"


def _data(record):
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


def _modern_group(record):
    locality = _data(record).get("locality_id") or ""
    for prefix, group in (("cmn-", "mandarin"), ("wuu-shanghai-", "shanghai"),
                          ("wuu-suzhou-", "suzhou"), ("wuu-", "other_wu"),
                          ("yue-", "cantonese"), ("mandarin-southwestern-", "southwestern")):
        if locality.startswith(prefix):
            return group
    return "other"


def _history_group(record):
    return {"Middle Chinese": "middle_chinese", "中古汉语": "middle_chinese",
            "中古漢語": "middle_chinese", "ltc": "middle_chinese",
            "Old Japanese": "old_japanese", "Historical Japanese": "historical_japanese"}.get(
                _data(record).get("language"), "other")


def _with_playable_audio(entry, resources):
    playable = []
    for item in entry.audio:
        try:
            if recording_path(resources, item.audio).is_file():
                playable.append(item)
        except (OSError, ValueError, TypeError):
            continue
    return replace(entry, audio=tuple(playable))


def pronunciation_sections(*, dialects=(), historical=(), component_history=False,
                           context=None, resources=None, include_empty=True) -> tuple[PronunciationSection, ...]:
    """Build group headings and cards for a kana or source-character page.

    ``reading_count`` counts source rows, excluding shared notation notes.
    ``audio_count`` counts unique exact-match audio IDs whose local files exist;
    system TTS and unreviewed character-input demonstrations are not counted.
    Empty primary groups make existing gaps visible without inventing readings.
    """
    resources = Path(resources) if resources is not None else Path(__file__).parent / "resources"
    context = _data(context)
    modern, history = {}, {}
    for reading in dialects:
        modern.setdefault(_modern_group(reading), []).append(reading)
    for reading in historical:
        history.setdefault(_history_group(reading), []).append(reading)
    result = []
    for key, label in _LABELS.items():
        rows = modern.get(key, [])
        old_rows = history.get(key, [])
        if not rows and not old_rows and not (include_empty and key in _PRIMARY):
            continue
        entries = [dialect_entry(reading) for reading in rows]
        if old_rows:
            history_key = "component_historical_reading_references" if component_history else "historical_reading_references"
            entries.extend(history_entries({history_key: old_rows, "hiragana": context.get("hiragana"),
                                            "katakana": context.get("katakana")}))
        entries = tuple(_with_playable_audio(entry, resources) for entry in entries)
        audio_ids = {audio.audio["id"] for entry in entries for audio in entry.audio}
        result.append(PronunciationSection(key, label, entries, len(rows) + len(old_rows), len(audio_ids)))
    return tuple(result)
