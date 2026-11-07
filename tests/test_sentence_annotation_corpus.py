"""Public corpus collection must never introduce hidden/transformed text."""
from sentence_annotation_corpus import public_prompt_parts


def test_collector_uses_only_public_prompt_and_skips_audio_and_answers():
    question = {"kind": "grammar_meaning", "prompt": "私は学生です。",
                "answer": "秘密の答え", "audio_text": "隠れた音声", "feedback": "非公開の解説"}
    assert public_prompt_parts(question) == ("私は学生です。",)
    for field in ("audio_only", "requires_audio", "has_hidden_transcript"):
        assert public_prompt_parts({**question, field: True}) == ()
    assert public_prompt_parts({**question, "prompt_visible": False}) == ()
    assert public_prompt_parts({**question, "kind": "listening_comprehension"}) == ()


def test_collector_separates_chinese_instructions_and_preserves_exact_japanese():
    assert public_prompt_parts({"kind": "grammar_cloze",
        "prompt": "变化已发生，状态仍持续：窓が（　）。"}) == ("窓が（　）。",)
    assert public_prompt_parts({"kind": "grammar_reorder", "prompt": "我是学生。"}) == ()
    assert public_prompt_parts({"kind": "grammar_meaning", "prompt": "　私は\n学生です。 "}) == ("　私は\n学生です。 ",)


def test_reading_prompt_can_be_precomputed_but_collecting_is_not_display_permission():
    assert public_prompt_parts({"kind": "vocab_reading", "prompt": "手紙", "answer": "てがみ"}) == ("手紙",)
    assert public_prompt_parts({"kind": "pronunciation_rule", "prompt": "橋",
        "text_roles": {"prompt": "ui", "answer": "study"}}) == ()
