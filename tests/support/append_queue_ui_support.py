"""Shared append queue ui fixtures and assertions; no collected scenarios."""
from kanamemory.domain import Question
from kanamemory.domain import to_script


def question(ident="old", kind="kana_romaji", script="hiragana"):
    answer = "a" if kind in {"kana_romaji", "kana_choice"} else to_script("あ", script)
    return Question(ident, kind, script, "", answer, options=["a", "i"] if kind == "kana_choice" else [to_script(symbol, script) for symbol in ("あ", "い")],
        metadata={"kana_symbol": "あ", "romaji": "a", "content_version": "test",
            "research_content": False, "word_table": "partial",
            "practice_config": {"schedule_mode": "spaced", "word_table": "partial", "include_research": False}})
