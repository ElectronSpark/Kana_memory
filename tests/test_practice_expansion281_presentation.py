from kanamemory.question_support import learning_text_role


def test_japanese_contexts_use_study_font_without_reclassifying_old_chinese_prompts():
    for kind, family in [("vocab_orthography", "contextual_orthography"), ("vocab_recall", "word_form_recall")]:
        assert learning_text_role({"kind": kind, "practice": {"task_family": family}}) == "study"
        assert learning_text_role({"kind": kind}) == "ui"
        assert learning_text_role({"kind": kind, "practice": {"task_family": family}}, "answer") == "study"


def test_invalid_metadata_cannot_break_the_question_typography():
    assert learning_text_role(None) == "ui"
    for value in (None, [], {"task_family": {}}, {"task_family": []}):
        assert learning_text_role({"kind": "vocab_recall", "practice": value}) == "ui"
