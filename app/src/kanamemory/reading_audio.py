"""Consume explicitly aligned Chinese audio without inferring pronunciation.

Matching a character alone is insufficient: recording identity, pronunciation,
tone, locality or reconstruction system must agree with the cited reading row.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from urllib.parse import urlsplit

from .presentation import payload


MODERN = "modern_dialect"
MIDDLE_CHINESE = "middle_chinese_reconstruction"
_TYPES = {
    MODERN: {"human_recording": "真人录音", "synthetic_demonstration": "现代方言合成示范"},
    MIDDLE_CHINESE: {"human_reconstruction": "现代人声演示中古拟音", "synthetic_demonstration": "中古拟音合成示范"},
}
_NOTATIONS = {
    "Hanyu_Pinyin": "汉语拼音",
    "LSHK_Jyutping": "粤拼",
    "Jyutping_with_lexi_mf_tone_number": "粤拼",
    "source_broad_IPA_and_Chao_5_level_tone": "宽式 IPA 与五度调值",
    "IPA": "国际音标 IPA",
}


def _data(record):
    return {**(payload(record) or {}), **{k: v for k, v in (record or {}).items() if k != "payload"}}


def _text(value):
    return isinstance(value, str) and bool(value.strip())


@dataclass(frozen=True)
class ReadingAudio:
    audio: dict
    character: str
    reading: str
    kind: str
    place_or_system: str
    recording_label: str
    notation_label: str
    tone_label: str

    @property
    def play_label(self):
        if self.kind == MIDDLE_CHINESE:
            return "播放中古拟音"
        return "播放读音" if self.audio["recording_type"] == "human_recording" else "播放方言合成示范"


def matched_reading_audio(records, kind) -> tuple[ReadingAudio, ...]:
    """Return exact, fully described attachments only, once per audio ID."""
    if kind not in _TYPES:
        return ()
    result, seen = [], set()
    for record in records:
        data = _data(record)
        if kind == MIDDLE_CHINESE and data.get("language") not in {None, "Middle Chinese", "中古汉语", "中古漢語", "ltc"}:
            continue
        if kind == MODERN:
            reading = data.get("romanization") or data.get("ipa_segments") or data.get("transcription")
        else:
            reading = data.get("reading")
        character = data.get("character") or data.get("hanzi") or data.get("source_hanzi")
        for audio in data.get("reading_audio") or ():
            if not isinstance(audio, dict):
                continue
            required = ("id", "reading_id", "reading_kind", "recording_type", "character", "reading", "notation_system", "author",
                        "license", "source_url", "local_path", "sha256")
            if any(not _text(audio.get(key)) for key in required):
                continue
            if any(audio.get(key) is not None and not isinstance(audio[key], str)
                   for key in ("ipa", "notation_label", "recording_scope", "source_locality_label", "reconstruction_system_label")):
                continue
            if (audio["id"] in seen or audio.get("match_status") != "exact_reading"
                    or audio.get("reading_kind") != kind or audio.get("recording_type") not in _TYPES[kind]
                    or audio["reading_id"] != data.get("id") or audio["character"] != character
                    or audio["reading"] != reading or audio["notation_system"] != data.get("notation_system")):
                continue
            sources = audio.get("source_ids")
            if not isinstance(sources, list) or not sources or not all(_text(s) for s in sources):
                continue
            if not re.fullmatch(r"[0-9a-fA-F]{64}", audio["sha256"]):
                continue
            try:
                url = urlsplit(audio["source_url"])
            except ValueError:
                continue
            if url.scheme not in {"http", "https"} or not url.netloc:
                continue
            # Do not promote a bare segmental transcription to a tonal match.
            tone_keys = [key for key in ("tone_value", "tone_number", "tone_category") if data.get(key) is not None]
            if any(str(audio.get(key)) != str(data[key]) for key in tone_keys):
                continue
            if kind == MODERN:
                if (not _text(audio.get("locality_id")) or audio["locality_id"] != data.get("locality_id")
                        or not _text(audio.get("locality_label"))):
                    continue
                if not data.get("romanization") and data.get("ipa_segments") and not any(key in tone_keys for key in ("tone_value", "tone_number")):
                    continue
                place = audio["locality_label"]
            else:
                system = data.get("system") or data.get("notation_system")
                if not _text(audio.get("reconstruction_system")) or audio["reconstruction_system"] != system:
                    continue
                place = audio.get("reconstruction_system_label") or system
            tone_names = {"tone_value": "五度调值 ", "tone_number": "调号 ", "tone_category": "调类 "}
            tone = "；".join(tone_names[key] + str(data[key]) for key in tone_keys)
            notation_label = audio.get("notation_label") or (data.get("notation_label") if _text(data.get("notation_label")) else None)
            notation_label = notation_label or _NOTATIONS.get(audio["notation_system"], audio["notation_system"])
            result.append(ReadingAudio(dict(audio), character, reading, kind, place,
                                       _TYPES[kind][audio["recording_type"]],
                                       notation_label, tone))
            seen.add(audio["id"])
    return tuple(result)
