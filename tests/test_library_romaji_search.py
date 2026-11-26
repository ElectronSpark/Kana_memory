"""Library searches accept romaji (library_search): the learner never needs a Japanese input method.

Made-up content. The example library (ExampleStore.examples_page) finds an example through its own
reading and through its linked lessons' readings; kana, kanji and Chinese queries find what they
found before; the dictionary search's terms (word_search) are unchanged. The two target pickers (例句 and AI
例句) match romaji in the same way. A lesson's title counts as a reading for its runs of two or more kana:
a grammar lesson without a reading field is found by the romaji of its title's kana.
"""
from types import SimpleNamespace

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.ai_ui import AIUIMixin
from kanamemory.example_store import ExampleStore, empty_pack, example_id_for
from kanamemory.example_ui import ExampleUIMixin
from kanamemory.library_search import (LessonQuery, readings_of, romaji_matches, romaji_needle, romaji_of,
                                       title_kana_runs)
from kanamemory.word_search import word_search_terms
from support.example_store_support import reference
from support.fake_ui import View
from support.learning_store_fixtures import item
from support.learning_store_fixtures import stores  # noqa: F401


def _targets():
    return [item("word:sensei", title="先生", family="word", reading="せんせい",
                 abilities=["understanding", "reading", "use", "listening"]),
            item("word:coffee", title="珈琲", family="word", reading="コーヒー",
                 abilities=["understanding", "reading", "use", "listening"]),
            item("grammar:topic", title="话题は")]


def _pack():
    linked = {"id": example_id_for("先生が来た。"), "revision": "1", "text": "先生が来た。",
              "translation": "老师来了。", "source_ids": []}
    own = {"id": example_id_for("彼女は学生です。"), "revision": "1", "text": "彼女は学生です。",
           "translation": "她是学生。", "reading": "かのじょはがくせいです。", "source_ids": []}
    plain = {"id": example_id_for("CDを買った。"), "revision": "1", "text": "CDを買った。",
             "translation": "买了CD。", "source_ids": []}
    return {**empty_pack(), "examples": [linked, own, plain],
            "references": [reference(linked, "word:sensei", "先生"), reference(own, "grammar:topic", "は"),
                           reference(plain, "grammar:topic", "を")]}


def _texts(page):
    return sorted(row["text"] for row in page["examples"])


def test_the_query_is_compared_with_romaji_only_when_it_has_a_latin_letter():
    assert romaji_needle(" Sensei ") == "sensei" and romaji_needle("ＳＥＮＳＥＩ") == "sensei"
    assert romaji_needle("せんせい") == romaji_needle("先生") == romaji_needle("老师") == ""
    assert romaji_matches("sensei", ["せんせい"]) and romaji_matches("sen", ["せんせい"])
    assert romaji_matches("koohii", ["コーヒー"]) and romaji_matches("kohi", ["コーヒー"])
    assert not romaji_matches("", ["せんせい"]) and not romaji_matches("kanojo", ["せんせい", ""])
    assert readings_of({"reading": "いく", "accepted_readings": ["いく"], "alternative_readings": ["ゆく"]}) == ["いく", "ゆく"]
    assert readings_of(None) == []


def test_aliases_are_computed_once_per_reading():
    romaji_of.cache_clear()
    for _ in range(5):
        romaji_matches("zz", ["かのじょ", "せんせい"])
    info = romaji_of.cache_info()
    assert info.misses == 2 and info.hits == 8


def test_the_example_library_finds_romaji_through_a_linked_lesson_and_the_own_reading(stores):
    store, learning = stores
    service = ExampleStore(store, learning, _targets(), _pack())
    assert _texts(service.examples_page("sensei")) == ["先生が来た。"]          # the linked lesson's reading
    assert _texts(service.examples_page("kanojo")) == ["彼女は学生です。"]      # the example's own reading
    assert _texts(service.examples_page("KANOJO")) == ["彼女は学生です。"]


def test_kana_kanji_chinese_and_latin_meaning_queries_find_what_they_found_before(stores):
    store, learning = stores
    service = ExampleStore(store, learning, _targets(), _pack())
    assert _texts(service.examples_page("かのじょ")) == ["彼女は学生です。"]
    assert _texts(service.examples_page("先生")) == ["先生が来た。"]
    assert _texts(service.examples_page("老师")) == ["先生が来た。"]
    assert _texts(service.examples_page("CD")) == ["CDを買った。"]
    assert service.examples_page("不存在")["total"] == 0
    assert service.examples_page("")["total"] == 3


def test_the_dictionary_search_terms_are_unchanged():
    word = {"form": "先生", "reading": "せんせい", "kanji": [{"text": "先生"}], "readings": [{"text": "せんせい"}],
            "gloss": "teacher", "chinese_gloss": "老师"}
    assert word_search_terms(word) == sorted({"先生": 0, "せんせい": 0, "sensei": 2, "teacher": 3, "老师": 3}.items())


PICKER_ROWS = [{"id": "word:sensei", "family": "word", "title": "先生", "reading": "せんせい", "meaning": "老师"},
               {"id": "word:coffee", "family": "word", "title": "珈琲", "reading": "コーヒー", "meaning": "咖啡"},
               {"id": "grammar:topic", "family": "grammar", "title": "话题は", "meaning": "提示话题"}]


@pytest.fixture
def picker(monkeypatch):
    """A page stub with the fake controls: shown() lists the titles of the last picker page drawn."""
    monkeypatch.setattr(component_ui.toga, "TextInput", lambda **kw: View(**kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler))
    pages = []
    page = SimpleNamespace(
        store=SimpleNamespace(library_unavailable_ids=lambda: set()),
        learning_items_available=lambda **_: [dict(row) for row in PICKER_ROWS],
        example_targets_available=lambda **_: [dict(row) for row in PICKER_ROWS],
        screen=lambda name, title, *children: pages.append(children))
    titles = {row["title"]: row["title"] for row in PICKER_ROWS}

    def shown(render, query):
        render(page, query=query)
        return [titles.get(node.text.split(" · ")[0]) for node in pages[-1]
                if node.text.split(" · ")[0] in titles]
    return shown


EXAMPLE_PICKER = ExampleUIMixin._render_example_target_picker
AI_PICKER = AIUIMixin._render_ai_target_picker


@pytest.mark.parametrize("render", [EXAMPLE_PICKER, AI_PICKER], ids=["example", "ai"])
def test_each_target_picker_finds_a_lesson_by_the_romaji_of_its_reading(picker, render):
    assert picker(render, "sensei") == ["先生"] and picker(render, "SEN") == ["先生"]
    assert picker(render, "koohii") == ["珈琲"]
    assert picker(render, "kanojo") == []


@pytest.mark.parametrize("render", [EXAMPLE_PICKER, AI_PICKER], ids=["example", "ai"])
def test_each_target_picker_finds_kana_kanji_and_chinese_as_before(picker, render):
    assert picker(render, "せんせい") == ["先生"] and picker(render, "先生") == ["先生"]
    assert picker(render, "老师") == ["先生"] and picker(render, "话题") == ["话题は"]
    assert picker(render, "") == ["先生", "珈琲", "话题は"]


# A grammar lesson whose form is only in its title (no reading field), one with a single kana between other
# characters, and one whose title has no kana.
PAST = {"id": "grammar:t:past", "family": "grammar", "title": "い形容词：かった／くなかった", "meaning": "形容词过去"}
TOPIC = {"id": "grammar:t:topic", "family": "grammar", "title": "话题は", "meaning": "提示话题"}
NO_KANA = {"id": "grammar:t:plain", "family": "grammar", "title": "名词句", "meaning": "判断句"}
SPOKEN = {"id": "grammar:t:spoken", "family": "grammar", "title": "～のです和～んです", "reading": "のです",
          "meaning": "说明"}
TITLE_LESSONS = [PAST, TOPIC, NO_KANA, SPOKEN]


def _found(query, lessons=TITLE_LESSONS):
    search = LessonQuery(query)
    return [lesson["id"] for lesson in lessons if search.matches(lesson)]


def test_a_lesson_without_a_reading_is_found_by_the_romaji_of_its_title_s_kana():
    assert title_kana_runs(PAST["title"]) == ("かった", "くなかった")
    assert readings_of(PAST) == ["かった", "くなかった"]
    assert _found("katta") == [PAST["id"]] and _found("kunakatta") == [PAST["id"]]
    assert _found("KATTA") == [PAST["id"]] and _found("nakat") == [PAST["id"]]
    # A title's runs join the reading fields, each once: のです is the reading and a run of the title.
    assert readings_of(SPOKEN) == ["のです", "んです"] and _found("ndesu") == [SPOKEN["id"]]


def test_a_single_kana_in_a_title_and_a_title_without_kana_add_nothing():
    assert title_kana_runs(TOPIC["title"]) == () and readings_of(TOPIC) == []
    assert _found("wa") == [] and _found("ha") == []
    assert title_kana_runs(NO_KANA["title"]) == () and readings_of(NO_KANA) == []
    assert readings_of({"title": "先生", "reading": "せんせい"}) == ["せんせい"]
    assert title_kana_runs("") == () and readings_of({"title": None}) == []


def test_kana_kanji_and_chinese_queries_match_the_title_as_before():
    # No Latin letter: only the substring of form, reading, meaning and topic (no romaji, no kana runs).
    assert _found("かった") == [PAST["id"]] and _found("った") == [PAST["id"]]
    assert _found("形容词") == [PAST["id"]] and _found("话题") == [TOPIC["id"]] and _found("判断") == [NO_KANA["id"]]
    assert _found("は") == [TOPIC["id"]] and _found("xyz") == []


def test_the_kana_runs_are_cut_once_per_title():
    title_kana_runs.cache_clear()
    for _ in range(4):
        LessonQuery("zz").matches(PAST)
    info = title_kana_runs.cache_info()
    assert info.misses == 1 and info.hits == 3


@pytest.mark.parametrize("render", [EXAMPLE_PICKER, AI_PICKER], ids=["example", "ai"])
def test_each_target_picker_finds_a_lesson_by_the_romaji_of_its_title_s_kana(monkeypatch, render):
    monkeypatch.setattr(component_ui.toga, "TextInput", lambda **kw: View(**kw))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler))
    pages = []
    page = SimpleNamespace(
        store=SimpleNamespace(library_unavailable_ids=lambda: set()),
        learning_items_available=lambda **_: [dict(row) for row in TITLE_LESSONS],
        example_targets_available=lambda **_: [dict(row) for row in TITLE_LESSONS],
        screen=lambda name, title, *children: pages.append(children))
    titles = {row["title"] for row in TITLE_LESSONS}

    def shown(query):
        render(page, query=query)
        return [node.text.split(" · ")[0] for node in pages[-1] if node.text.split(" · ")[0] in titles]
    assert shown("katta") == [PAST["title"]] and shown("kunakatta") == [PAST["title"]]
    assert shown("wa") == [] and shown("かった") == [PAST["title"]]


def test_the_example_library_finds_romaji_through_a_linked_lesson_s_title(stores):
    store, learning = stores
    sentence = {"id": example_id_for("昨日は寒かった。"), "revision": "1", "text": "昨日は寒かった。",
                "translation": "昨天很冷。", "source_ids": []}
    pack = {**empty_pack(), "examples": [sentence], "references": [reference(sentence, "grammar:t:past", "かった")]}
    lesson = item("grammar:t:past", title=PAST["title"])
    service = ExampleStore(store, learning, [lesson], pack)
    assert _texts(service.examples_page("katta")) == ["昨日は寒かった。"]
    assert _texts(service.examples_page("kunakatta")) == ["昨日は寒かった。"]    # the title's other run
    assert service.examples_page("wa")["total"] == 0
