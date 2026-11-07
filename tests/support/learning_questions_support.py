"""Shared learning questions fixtures and assertions; no collected scenarios."""



def dictionary_word():
    return {"id": "jmdict:fixture", "form": "明日", "reading": "あした",
            "readings": [{"text": "あした", "applies_to_kanji": []},
                         {"text": "あす", "applies_to_kanji": []}],
            "senses": [{"sense_id": "jmdict:fixture:sense:1", "reading_restrictions": [],
                        "kanji_restrictions": [], "japanese_meaning_english_glosses": [
                            {"language": "eng", "text": "tomorrow"}]},
                       {"sense_id": "jmdict:fixture:sense:2", "reading_restrictions": ["あす"],
                        "kanji_restrictions": [], "japanese_meaning_english_glosses": [
                            {"language": "eng", "text": "near future"}]}],
            "chinese_senses": [{"sense_id": "jmdict:fixture:sense:1", "glosses": ["明天"]}],
            "pitch_accent": []}
