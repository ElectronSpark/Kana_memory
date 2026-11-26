"""WP-K2: cognate-class selection rules (design 11 section 4, S2-S4) on synthetic data only."""
import pytest

from kanamemory import learning_questions as lq
from kanamemory.intro_batch_ui import intro_quiz_question
from kanamemory.learning_practice_policy import practice_candidates
from support.question_dependency_support import WORD_ID, word

MEANING = [{"prompt": "駅です。", "answer": "车站", "practice": {"background_ids": []}},
           {"prompt": "駅", "answer": "车站"},
           {"prompt": "駅", "answer": "车站"},
           {"prompt": "駅に行く", "answer": "车站"},
           {"prompt": "駅です", "answer": "车站", "practice": {"background_ids": ["other"]}}]
RECALL = [{"prompt": "车站", "task": "写出本课所学的单词基本形，可用本课汉字写法或假名。"},
          {"prompt": "（　）へ", "task": "本课词：去车站。"},
          {"prompt": "駅でした", "task": "标记部分的基本形是？"}]
USAGE = [{"prompt": "（　）へ行きます。", "answer": "駅"}]
TEMPLATES = {"vocab_meaning": MEANING, "vocab_recall": RECALL, "vocab_usage": USAGE}
STATION = {"id": "c1", "family": "word", "title": "駅", "reading": "えき", "meaning": "车站"}


def listed(monkeypatch, cid="c1", title="駅", reading="えき", **entry):
    monkeypatch.setattr(lq, "_SOUND_MIX", {"cognate_words": {cid: {"title": title, "reading": reading, **entry}}})


def kept(retired, kind, templates=TEMPLATES):
    return {index for index in range(len(templates[kind])) if (kind, index) not in retired}


def test_s_keeps_one_bare_meaning_task_and_recall_whose_text_does_not_show_the_form(monkeypatch):
    listed(monkeypatch, **{"class": "S"})
    retired = lq.retired_practice_variants(STATION, TEMPLATES)
    assert lq.cognate_class(STATION) == "S"
    assert kept(retired, "vocab_meaning") == {1}
    # 车站 does not show 駅 (its Chinese form is 驿): every recall task stays.
    assert kept(retired, "vocab_recall") == {0, 1, 2}
    assert kept(retired, "vocab_usage") == {0}


def test_s_without_a_bare_task_keeps_one_task_that_needs_no_other_word(monkeypatch):
    listed(monkeypatch, **{"class": "S"})
    templates = {"vocab_meaning": [MEANING[4], MEANING[3], MEANING[0]], "vocab_recall": RECALL}
    retired = lq.retired_practice_variants(STATION, templates)
    # Not the background task (index 0); the reviewed self-contained one ranks first.
    assert kept(retired, "vocab_meaning", templates) == {2}
    assert kept(retired, "vocab_recall", templates) == {0, 1, 2}


def s_recall_kept(monkeypatch, title, recall, *, group="S", cid="c9"):
    """The kept recall variants of a made-up listed word."""
    listed(monkeypatch, cid=cid, title=title, reading="よみ", **({} if group is None else {"class": group}))
    item = {"id": cid, "family": "word", "title": title, "reading": "よみ", "meaning": "义"}
    templates = {"vocab_recall": recall}
    return kept(lq.retired_practice_variants(item, templates), "vocab_recall", templates)


FREE = "写出本课所学的单词基本形，可用本课汉字写法或假名。"


@pytest.mark.parametrize("title,shown", [
    ("学校", {"prompt": "学校", "task": FREE}),                        # the same characters
    ("豪華", {"prompt": "豪华；奢华", "task": FREE}),                   # the simplified form
    ("電話", {"prompt": "（　）をかけます。", "task": "本课词：给朋友打电话。"}),  # inside a longer word, in the hint
    ("会場", {"prompt": "x", "task": FREE, "choices": ["会场", "车站"]}),  # an option shows it
    ("学校", {"prompt": "学校で勉強します。", "task": FREE}),            # the Japanese prompt shows it as written
])
def test_s_recall_is_withheld_where_the_shown_text_shows_the_written_form(monkeypatch, title, shown):
    assert s_recall_kept(monkeypatch, title, [shown]) == set()


@pytest.mark.parametrize("title,shown", [
    ("道具", {"prompt": "工具；用具", "task": FREE}),                    # another gloss
    ("場所", {"prompt": "パーティーの（　）は大学の図書館です。", "task": "本课词：派对的地点是大学图书馆。"}),
    ("戦う", {"prompt": "战斗；作战", "task": FREE}),                    # a title with kana
    ("紹介", {"prompt": "介绍", "task": FREE}),                          # the same characters, another order
])
def test_s_recall_stays_where_the_shown_text_does_not_show_the_form(monkeypatch, title, shown):
    assert s_recall_kept(monkeypatch, title, [shown]) == {0}


@pytest.mark.parametrize("title", ["働く", "弁当"])
def test_s_recall_of_a_form_without_chinese_spelling_stays_withheld(monkeypatch, title):
    # 働 is a Japanese-made character; 弁's Chinese counterpart depends on the word (fail safe).
    assert s_recall_kept(monkeypatch, title, [{"prompt": "工作", "task": FREE}]) == set()


def test_base_form_recall_stays_even_when_the_text_shows_the_form(monkeypatch):
    recall = [{"prompt": "学校でした", "task": "标记部分的基本形是？"}, {"prompt": "学校", "task": FREE}]
    assert s_recall_kept(monkeypatch, "学校", recall) == {0}


def test_p_and_classless_words_keep_their_recall_rules(monkeypatch):
    other_gloss = [{"prompt": "工具；用具", "task": FREE}, {"prompt": "道具でした", "task": "标记部分的基本形是？"}]
    # P: the previous rule (every recall task but base-form recall), whatever the text shows.
    assert s_recall_kept(monkeypatch, "道具", other_gloss, group="P") == {1}
    # A word that is not listed has no class: nothing is withheld.
    listed(monkeypatch, cid="other")
    item = {"id": "c9", "family": "word", "title": "学校", "reading": "よみ"}
    recall = [{"prompt": "学校", "task": FREE}]
    assert lq.cognate_class(item) is None
    assert lq.retired_practice_variants(item, {"vocab_recall": recall}) == frozenset()


def test_shown_text_takes_the_prompt_task_and_options():
    raw = {"prompt": "p", "task": "本课词：t", "choices": ["a", "b"], "answer": "学校", "feedback": "学校"}
    assert lq.shown_text(raw) == "p\n本课词：t\na\nb"
    assert lq.shown_text(None) == ""


@pytest.mark.parametrize("entry", [{"class": "P"}, {}])
def test_p_and_an_unclassified_listed_word_keep_the_previous_rule(monkeypatch, entry):
    listed(monkeypatch, **entry)
    retired = lq.retired_practice_variants(STATION, TEMPLATES)
    assert lq.cognate_class(STATION) == "P"
    # Two meaning tasks that need no other known word; the bare one is among them.
    assert kept(retired, "vocab_meaning") == {0, 1}
    assert kept(retired, "vocab_recall") == {2}


KANA_FORM = {"prompt": "えきでまちます。", "answer": "车站",
             "practice": {"task_family": lq.KANA_FORM_MEANING, "background_ids": ["w:matsu"]}}


@pytest.mark.parametrize("group,expected", [("S", {5}), ("P", {0, 5})])
def test_kana_form_meaning_tasks_stay_for_cognate_classes(monkeypatch, group, expected):
    # Design 11 S2: the word written in kana tests reading -> meaning, so the class rule
    # never retires it; with one present, an S word loses its kanji-prompt bare task
    # (a copy task) and a P word keeps a single sentence task beside it.
    listed(monkeypatch, **{"class": group})
    templates = {**TEMPLATES, "vocab_meaning": [*MEANING, KANA_FORM]}
    retired = lq.retired_practice_variants(STATION, templates)
    assert kept(retired, "vocab_meaning", templates) == expected


@pytest.mark.parametrize("group", ["D", "E"])
def test_d_and_e_retire_nothing(monkeypatch, group):
    listed(monkeypatch, **{"class": group})
    assert lq.retired_practice_variants(STATION, TEMPLATES) == frozenset()


@pytest.mark.parametrize("change", [{"title": "驛"}, {"reading": "うまや"}, {"id": "other"}, {"ai_generated": True}])
def test_an_edited_or_unlisted_word_has_no_class_and_keeps_its_bank(monkeypatch, change):
    listed(monkeypatch, **{"class": "S"})
    target = {**STATION, **change}
    assert lq.retired_practice_variants(target, TEMPLATES) == frozenset()
    if not change.get("ai_generated"):
        assert lq.cognate_class(target) is None


def test_an_unknown_class_is_an_invalid_pack(monkeypatch):
    listed(monkeypatch, **{"class": "X"})
    with pytest.raises(ValueError):
        lq.retired_practice_variants(STATION, TEMPLATES)


def test_selection_follows_the_class_and_retired_tasks_still_rebuild(monkeypatch):
    item = word()
    listed(monkeypatch, cid=WORD_ID, title=item["title"], reading=item["reading"], **{"class": "S"})

    def meaning_variants():
        return {c["variant"] for c in practice_candidates(item, ["vocab_meaning"]) if c["kind"] == "vocab_meaning"}
    # The word alone stays; its sentence task leaves future selection only.
    assert meaning_variants() == {0}
    rebuilt = lq.build_learning_question(item, "vocab_meaning", variant=1)
    assert rebuilt["prompt"] == item["question_templates"]["vocab_meaning"][1]["prompt"]
    # P keeps the first-ranked and the first bare task; here (no practice blocks) they coincide.
    listed(monkeypatch, cid=WORD_ID, title=item["title"], reading=item["reading"], **{"class": "P"})
    assert meaning_variants() == {0}
    listed(monkeypatch, cid=WORD_ID, title=item["title"], reading=item["reading"], **{"class": "D"})
    assert meaning_variants() == {0, 1}


def test_the_batch_quiz_of_an_s_word_is_its_bare_task(monkeypatch):
    item = {**word(), "enrolled_abilities": ["understanding", "use"]}
    kinds = ["vocab_meaning", "vocab_usage"]
    # The quiz prefers the second variant (the sentence task) ...
    assert intro_quiz_question(item, kinds, known_background_ids=set())["variant"] == 1
    listed(monkeypatch, cid=WORD_ID, title=item["title"], reading=item["reading"], **{"class": "S"})
    # ... but an S word's sentence task is retired: the bare word is asked instead.
    question = intro_quiz_question(item, kinds, known_background_ids=set())
    assert (question["kind"], question["variant"], question["prompt"]) == ("vocab_meaning", 0, item["title"])
