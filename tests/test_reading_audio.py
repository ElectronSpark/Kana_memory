"""Chinese recording attachment matching must preserve tone and provenance."""
from copy import deepcopy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.reading_audio import MODERN, MIDDLE_CHINESE, matched_reading_audio


from support.reading_audio_support import modern, historical


def test_exact_recording_has_its_own_voice_type_and_source_metadata():
    row, audio = modern()
    item, = matched_reading_audio([{"payload": row}], MODERN)
    assert (item.character, item.reading, item.place_or_system) == ("安", "ān", "普通话")
    assert item.recording_label == "真人录音"
    assert item.notation_label == "汉语拼音"
    assert item.audio["source_ids"] == ["audio-source"]
    assert item.audio is not audio


@pytest.mark.parametrize("key,value", [
    ("reading_id", "pron-another"), ("character", "按"), ("reading", "àn"),
    ("notation_system", "IPA"), ("locality_id", "wuu-shanghai"), ("locality_label", ""),
    ("reading_kind", MIDDLE_CHINESE), ("recording_type", "human_reconstruction"),
    ("recording_type", "japanese_tts"), ("recording_type", ["human_recording"]),
    ("match_status", "same_character_only"), ("source_ids", []), ("author", ""),
    ("sha256", "not-a-checksum"), ("source_url", "file:///private/sound"),
    ("source_url", "http://[invalid"),
])
def test_near_matches_missing_provenance_and_japanese_tts_are_rejected(key, value):
    row, audio = modern()
    audio[key] = value
    assert matched_reading_audio([row], MODERN) == ()


def test_ipa_segments_require_matching_separate_tone_information():
    row, audio = modern()
    row.pop("romanization")
    row.update(ipa_segments="ø", notation_system="source_broad_IPA_and_Chao_5_level_tone")
    audio.update(reading="ø", notation_system=row["notation_system"])
    assert not matched_reading_audio([row], MODERN)
    row["tone_value"] = "53"
    assert not matched_reading_audio([row], MODERN)
    audio["tone_value"] = "55"
    assert not matched_reading_audio([row], MODERN)
    audio["tone_value"] = "53"
    item, = matched_reading_audio([row], MODERN)
    assert item.tone_label == "五度调值 53"
    row["tone_number"] = 1
    assert not matched_reading_audio([row], MODERN)
    audio["tone_number"] = "1"
    assert len(matched_reading_audio([row], MODERN)) == 1
    row["tone_category"] = "阴平"
    assert not matched_reading_audio([row], MODERN)
    audio["tone_category"] = "阳平"
    assert not matched_reading_audio([row], MODERN)
    audio["tone_category"] = "阴平"
    assert "调类 阴平" in matched_reading_audio([row], MODERN)[0].tone_label


def test_source_specific_notation_uses_its_readable_label():
    row, audio = modern()
    row["notation_system"] = audio["notation_system"] = "source_filename_transcription"
    audio["notation_label"] = "来源文件所用转写"
    assert matched_reading_audio([row], MODERN)[0].notation_label == "来源文件所用转写"


def test_phonetic_contrasts_and_diacritics_are_not_normalized_away():
    row, audio = modern()
    row["romanization"] = "tsɿ⁵²"
    for almost in ("tsɿ⁵⁵", "tɕi⁵²", "tsɿ", "tsi⁵²"):
        audio["reading"] = almost
        assert not matched_reading_audio([row], MODERN)


def test_same_audio_id_is_not_repeated_for_duplicate_citations():
    row, audio = modern()
    another = deepcopy(row)
    another["id"] = "pron-a-other-citation"
    another["reading_audio"][0]["reading_id"] = another["id"]
    assert len(matched_reading_audio([row, another, row], MODERN)) == 1
    audio["match_status"] = "unverified"
    item, = matched_reading_audio([row, another], MODERN)
    assert item.audio["reading_id"] == another["id"]


def test_middle_chinese_labels_modern_reconstruction_and_rejects_system_mismatch():
    row, audio = historical()
    item, = matched_reading_audio([row], MIDDLE_CHINESE)
    assert item.recording_label == "中古拟音合成示范"
    assert item.play_label == "播放中古拟音"
    assert item.place_or_system == "test_system"
    audio["reconstruction_system"] = "a_different_system"
    assert not matched_reading_audio([row], MIDDLE_CHINESE)
    audio["reconstruction_system"] = "test_system"
    audio["recording_type"] = "human_recording"
    assert not matched_reading_audio([row], MIDDLE_CHINESE)
    audio["recording_type"] = "human_reconstruction"
    assert matched_reading_audio([row], MIDDLE_CHINESE)[0].recording_label == "现代人声演示中古拟音"


def test_old_japanese_evidence_cannot_be_relabelled_middle_chinese():
    row, audio = historical()
    row["language"] = "Old Japanese"
    assert not matched_reading_audio([row], MIDDLE_CHINESE)
    assert not matched_reading_audio([row], MODERN)


def test_legacy_japanese_recordings_are_not_used_as_chinese_audio():
    row, audio = modern()
    row.pop("reading_audio")
    row["recordings"] = [audio]
    assert not matched_reading_audio([row], MODERN)
