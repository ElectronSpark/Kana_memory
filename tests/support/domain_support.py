"""Shared domain fixtures and assertions; no collected scenarios."""
from datetime import datetime
from datetime import timezone
from kanamemory.domain import to_script


UTC = timezone.utc


START = datetime(2026, 10, 10, 12, tzinfo=UTC)


class SmallContent:
    def __init__(self):
        pairs = [("あ", "a"), ("い", "i"), ("う", "u"), ("え", "e"), ("お", "o"),
                 ("か", "ka"), ("き", "ki"), ("く", "ku"), ("け", "ke"), ("こ", "ko"),
                 ("し", "shi"), ("じ", "ji"), ("ぢ", "ji"), ("ず", "zu"), ("づ", "zu"),
                 ("を", "o"), ("きゃ", "kya"), ("ティ", "ti")]
        self.kana = [{"id": f"kana-{i}", "hiragana": to_script(k, "hiragana"),
                      "katakana": to_script(k, "katakana"), "romaji": r,
                      "category": "foreign_combination" if k == "ティ" else "yoon" if k == "きゃ" else "basic",
                      "tier": "loanword" if k == "ティ" else "modern_core",
                      "romanization": {"display": r, "aliases": ["si"] if k == "し" else []}}
                     for i, (k, r) in enumerate(pairs)]
        self.kana += [{"id": "small", "hiragana": "っ", "katakana": "ッ", "romaji": "(small tsu)", "category": "small", "tier": "modern_core"},
                      {"id": "long", "hiragana": "ー", "katakana": "ー", "romaji": "(long)", "category": "long_vowel_mark", "tier": "orthographic_symbol"}]
        self.words = [{"id": "word-reviewed", "reading": "キャット", "gloss": "cat", "practice_approved": True},
                      {"id": "word-research", "reading": "コーヒー", "gloss": "coffee", "practice_approved": False}]

    def metadata(self):
        return {"content_version": "fixture-v7"}

    def kana_list(self):
        return self.kana

    def get_kana(self, content_id):
        return next((item for item in self.kana if item["id"] == content_id), None)

    def practice_words(self, include_research=False):
        return [w for w in self.words if include_research or w["practice_approved"]]

    def practice_word_candidates(self, include_research=False):
        return self.practice_words(include_research=include_research)

    def get_word(self, content_id):
        return next((word for word in self.words if word["id"] == content_id), None)

    def all_kana_symbols(self, script):
        # Include the isolated small kana needed by the word keyboard.
        return list(dict.fromkeys([r[script] for r in self.kana] +
                    [to_script(c, script) for c in "ゃっとーひ"] ))
