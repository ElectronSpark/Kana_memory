"""Shared reading audio fixtures and assertions; no collected scenarios."""
from kanamemory.reading_audio import MIDDLE_CHINESE
from kanamemory.reading_audio import MODERN


def modern():
    row = {"id": "pron-a", "character": "安", "romanization": "ān", "notation_system": "Hanyu_Pinyin",
           "locality_id": "cmn-standard", "source_ids": ["text-source"]}
    audio = {"id": "audio-a", "reading_id": row["id"], "reading_kind": MODERN, "character": "安",
             "reading": "ān", "notation_system": "Hanyu_Pinyin", "locality_id": "cmn-standard",
             "locality_label": "普通话", "author": "Test speaker", "license": "CC-BY-SA-4.0",
             "source_ids": ["audio-source"], "source_url": "https://example.org/audio-a",
             "local_path": "media/audio-a.ogg", "sha256": "a" * 64, "recording_type": "human_recording",
             "match_status": "exact_reading"}
    row["reading_audio"] = [audio]
    return row, audio


def historical():
    row, audio = modern()
    row.pop("romanization")
    row.pop("locality_id")
    row.update(id="historical-a", reading="ʔɑn", notation_system="test_IPA_reconstruction", system="test_system",
               language="Middle Chinese")
    audio.update(id="historical-audio-a", reading_id=row["id"], reading="ʔɑn", notation_system=row["notation_system"], reading_kind=MIDDLE_CHINESE,
                 reconstruction_system="test_system", recording_type="synthetic_demonstration")
    return row, audio
