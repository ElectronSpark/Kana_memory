"""Brief J6 R5-R8: an ability that cannot be probed; the probe page; the guide's own pick; stopped answer audio.

Made-up lessons, temporary stores and the synthetic test clock only.
"""
import asyncio
from types import SimpleNamespace

from support.learning_fixtures import learning_app, click
from support.app_fixtures import question_app, setup_app  # noqa: F401 (fixtures used by learning_app)
from support.fake_ui import text_of
from kanamemory import answer_autoplay, learning_probe, probe_rules
from kanamemory.feedback_context import capture_feedback_context
from kanamemory.learning_questions import PREFERENCE_VERSION, SOUND_PREFERENCE_VERSION
from kanamemory.learning_ui import LEARNING_PREFS
from kanamemory.platform_android import PlatformError, PlaybackStopped

CID = "word:synthetic:form"
GID = "grammar:synthetic:guide"
KINDS = ["vocab_meaning", "vocab_reading", "vocab_usage", "grammar_meaning", "grammar_cloze"]
SENSE = "本课义项：词一。写出本课所学的假名读法。"


def choice(prompt, answer, other, **extra):
    return {"prompt": prompt, "answer": answer, "choices": [answer, other], "feedback": "词一的说明",
            "input_mode": "choice", "safe": True, **extra}


def typed(prompt, answer, **extra):
    return {"prompt": prompt, "answer": answer, "input_mode": "text", "feedback": "词一的说明", "strict": True,
            **extra}


def word(abilities, templates):
    return {"id": CID, "content_id": CID, "family": "word", "title": "語一", "reading": "ごいち", "meaning": "词一",
            "revision": "1", "abilities": list(abilities), "source_ids": ["test"],
            "instruction": {"summary": "词一", "rule": "名词。", "steps": [], "example": "語一です。",
                            "translation": "是词一。", "prerequisite": "", "contrast": ""},
            "question_templates": templates}


def grammar():
    return {"id": GID, "content_id": GID, "family": "grammar", "title": "～です", "revision": "1",
            "abilities": ["understanding"], "source_ids": ["test"],
            "instruction": {"summary": "判断", "rule": "名词＋です。", "steps": [], "example": "本です。",
                            "translation": "是书。", "prerequisite": "", "contrast": ""},
            "question_templates": {"grammar_meaning": [choice("本です。", "是书", "不是书"),
                                                       choice("猫です。", "是猫", "不是猫")]}}


def start(app, item):
    app.store.set_setting(LEARNING_PREFS, {"enabled": KINDS, SOUND_PREFERENCE_VERSION: PREFERENCE_VERSION})
    app.learning_store.enroll([item])


def open_scope(app, ability):
    learning = app.learning_store
    scope = learning.ability_instruction_scope(CID, ability)
    item = learning.instruction_item(CID, scope)
    return learning.open_new_scope(CID, instruction_scope=scope, expected_introduction_id=item["introduction_id"],
                                   enabled_kinds=KINDS)


def probe_understanding(app):
    opened = open_scope(app, "understanding")
    assert opened["route"] == "probe"
    return opened


USE_CHOICE = {"vocab_meaning": [choice("語一", "词一", "别的词")],
              "vocab_usage": [choice("（　）です。", "語一", "語二", task="填入表示“词一”的本课单词。")]}
USE_TYPED = {"vocab_meaning": [choice("語一", "词一", "别的词")],
             "vocab_usage": [typed("（　）です。", "ごいち", task="用本课表示“词一”的单词补全，用假名作答。")]}


# R5

def test_no_template_qualifies_by_form_opens_an_ordinary_first_check(learning_app):
    app = learning_app
    start(app, word(["understanding", "use"], USE_CHOICE))
    probe_understanding(app)
    view = learning_probe.LessonView(app.learning_store, CID)
    assert view.probing   # never taught, no probe failed
    assert open_scope(app, "use")["route"] == "check"
    item = app.learning_store.instruction_item(CID, app.learning_store.ability_instruction_scope(CID, "use"))
    assert item["stage"] == "bridge"
    assert item["checkpoint"]["initial_familiarity"]["route"] == "full"


def test_a_form_qualifying_template_with_unmet_requirements_waits(learning_app, monkeypatch):
    app = learning_app
    start(app, word(["understanding", "use"], USE_TYPED))
    probe_understanding(app)
    monkeypatch.setattr(learning_probe, "_requirements_met", lambda item, question, known: False)
    assert open_scope(app, "use") == {"route": "wait"}
    stored = app.learning_store.instruction_item(CID, app.learning_store.ability_instruction_scope(CID, "use"))
    assert stored["stage"] == "intro" and not stored["checkpoint"].get("initial_familiarity")
    monkeypatch.undo()
    assert open_scope(app, "use")["route"] == "probe"


def test_probe_by_form_reads_the_rules_without_the_dependency_gate(learning_app):
    app = learning_app
    for templates, expected in ((USE_CHOICE, False), (USE_TYPED, True)):
        source = word(["understanding", "use"], templates)
        assert learning_probe.probe_by_form(app.learning_store, source, "use", enabled_kinds=KINDS,
                                            system_japanese=False) is expected
    source = word(["understanding", "use"], USE_TYPED)
    assert not learning_probe.probe_by_form(app.learning_store, source, "use", enabled_kinds=["vocab_meaning"],
                                            system_japanese=False)


def test_a_lesson_with_nothing_left_to_probe_or_check_is_complete(learning_app):
    app = learning_app
    start(app, word(["understanding"], {"vocab_meaning": [choice("語一", "词一", "别的词")]}))
    probe_understanding(app)
    app.continue_learning()
    click(app, "词一")
    click(app, "很轻松")
    assert app.learning_store.get_item(CID)["stage"] == "done"


# R6

def test_a_reading_probe_shows_the_cue_alone(learning_app):
    app = learning_app
    start(app, word(["reading"], {"vocab_reading": [typed("語一", "ごいち", task=SENSE)]}))
    app.continue_learning()
    assert app.page == "learning_question" and app._learning_question["kind"] == "vocab_reading"
    shown = text_of(app.body)
    assert "読" in shown and "词一" not in shown and "ごいち" not in shown
    from kanamemory.question_presentation import question_presentation
    question = app._learning_question
    assert question_presentation(question)["instruction"] == "読み · 词一"   # an ordinary reading question
    assert question_presentation(probe_rules.bare_view(question))["instruction"] == "読み"


def test_bare_view_drops_the_gloss_and_marks_the_probe():
    view = probe_rules.bare_view({"prompt": "語一", "gloss": "词一", "support": {}})
    assert view["gloss"] == "" and view[probe_rules.PROBE_VIEW] is True


def test_a_probe_page_draws_no_gloss_of_its_own_target(learning_app, monkeypatch):
    app = learning_app
    question = {"target_id": CID, "prompt": "語一です。"}
    hints = [{"surface": "語一", "meaning": "词一", "target_id": CID, "start": 0, "end": 2}]
    from kanamemory import question_support
    monkeypatch.setattr(question_support, "learning_question_display", lambda q: {**q, "background_glosses": hints})
    ordinary = app._learning_context_support(question, frozenset())
    assert any("词一" in text_of(widget) for widget in ordinary)
    probe = app._learning_context_support(question, frozenset(), probe=True)
    assert all("词一" not in text_of(widget) for widget in probe)


# R7

def failed_guide(app):
    """A grammar lesson whose probe failed: its guide page, the choice question waiting for an answer."""
    start(app, grammar())
    app.continue_learning()
    assert app.page == "learning_question"
    click(app, "还不会")
    app._test_clock[0] = app._test_clock[0].replace(hour=app._test_clock[0].hour + 1)
    app.continue_learning()
    assert app.page == "learning_intro"
    click(app, "跟着做一题")
    assert app.page == "learning_guide"
    guide = app._learning_guide
    wrong = next(option for option in guide["choices"] if option != guide["answer"])
    return guide, wrong


def test_the_guide_result_shows_the_learners_own_pick(learning_app):
    app = learning_app
    guide, wrong = failed_guide(app)
    click(app, wrong)
    shown = text_of(app.body)
    assert "我的回答：" in shown and wrong in shown


def test_the_export_carries_the_checkpoint_of_the_guides_scope(learning_app):
    app = learning_app
    guide, wrong = failed_guide(app)
    click(app, wrong)
    practice = capture_feedback_context(app)["practice"]
    scope = practice["guide_scope"]
    assert scope["content_id"] == GID and scope["checkpoint"]["guide_answer"] == wrong


def test_the_export_reads_a_separated_scope(learning_app):
    app = learning_app
    start(app, word(["understanding", "use"], USE_TYPED))
    probe_understanding(app)
    scope = app.learning_store.ability_instruction_scope(CID, "use")
    assert scope == "use"
    app.page = "learning_guide"
    app._learning_guide = {"id": "guide", "kind": "vocab_usage"}
    app._learning_item = app.learning_store.instruction_item(CID, scope)
    practice = capture_feedback_context(app)["practice"]
    assert practice["guide_scope"]["instruction_scope"] == "use"
    assert practice["guide_scope"]["checkpoint"] == app._learning_item["checkpoint"]


# R8

def autoplay_with(error, monkeypatch):
    recorded = []
    monkeypatch.setattr(answer_autoplay, "record_error", lambda *args: recorded.append(args))

    async def play(text):
        raise error

    app = SimpleNamespace(page="learning_feedback", _page_token=1, platform=SimpleNamespace(play=play),
                          loop=SimpleNamespace(create_task=lambda coroutine: asyncio.run(coroutine)))
    assert answer_autoplay.autoplay_answer(app, "答え", ("key", 1))
    return recorded


def test_a_playback_the_app_stopped_is_not_recorded(monkeypatch):
    assert autoplay_with(PlaybackStopped("朗读已停止，请重播。"), monkeypatch) == []


def test_a_real_playback_failure_is_recorded(monkeypatch):
    recorded = autoplay_with(PlatformError("系统语音播放失败（代码 -3）"), monkeypatch)
    assert [entry[:2] for entry in recorded] == [("answer_autoplay", "play")]


def test_stop_events_by_cause():
    from kanamemory.platform_android import AndroidPlatform
    loop = asyncio.new_event_loop()
    try:
        for focus_lost, expected in ((False, PlaybackStopped), (True, PlatformError)):
            platform = AndroidPlatform.__new__(AndroidPlatform)
            future = loop.create_future()
            platform._utterances = {"u1": future}
            platform._utterance_languages = {"u1": "ja"}
            platform._current_utterance = "u1"
            platform._jclass = lambda name: SimpleNamespace(i=lambda *args: None)
            platform._focus_lost = focus_lost
            platform._audio_manager = platform._focus_request = platform._focus_listener = None
            platform._on_tts_event("stop", "u1", None)
            exc = future.exception()
            assert type(exc) is expected and getattr(exc, "app_stopped", False) is (not focus_lost)
    finally:
        loop.close()


def test_probe_by_form_reads_the_effective_answer_policy(learning_app, monkeypatch):
    """J6M meeting 1: a non-strict typed template that an overlay row makes strict qualifies by form."""
    from copy import deepcopy
    from kanamemory import content_caches
    from kanamemory.answer_policy import source as policy_source
    from kanamemory.learning_questions import template_fingerprint
    raw = typed("（　）です。", "ごいち", task="用本课表示“词一”的单词补全，用假名作答。", strict=False)
    lesson = word(["understanding", "use"], {"vocab_meaning": [choice("語一", "词一", "别的词")], "vocab_usage": [raw]})
    rows = {}
    monkeypatch.setattr("kanamemory.learning_content.get_learning_item", lambda cid: deepcopy(lesson) if cid == CID else None)
    monkeypatch.setattr("kanamemory.learning_content.learning_item_view", lambda cid: deepcopy(lesson) if cid == CID else None)
    monkeypatch.setattr("kanamemory.learning_questions.question_template",
                        lambda item, question, **_: (item, deepcopy(raw)) if question.get("kind") == "vocab_usage" else None)
    monkeypatch.setattr(policy_source, "_OVERLAY", {"schema_version": 1, "items": {CID: {"vocab_usage": rows}}})
    policy_source._LOCATED.clear()
    try:
        assert not learning_probe.probe_by_form(app_store(learning_app), lesson, "use", enabled_kinds=KINDS,
                                                system_japanese=False)
        rows[template_fingerprint(raw)] = {"strict": True}
        policy_source._LOCATED.clear()
        content_caches.clear_all()
        assert learning_probe.probe_by_form(app_store(learning_app), lesson, "use", enabled_kinds=KINDS,
                                            system_japanese=False)
    finally:
        policy_source._LOCATED.clear()
        content_caches.clear_all()


def app_store(app):
    return app.learning_store


def test_a_rebuilt_guide_page_shows_the_stored_pick_and_verdict_without_judging(learning_app, monkeypatch):
    """J6M meeting 6: the stored guide verdict is never judged again; the page shows the stored pick."""
    app = learning_app
    guide, wrong = failed_guide(app)
    click(app, wrong)
    from kanamemory import learning_ui

    def no_judging(*args, **kwargs):
        raise AssertionError("the stored guide verdict was judged again")
    monkeypatch.setattr(learning_ui, "grade_learning_answer", no_judging)
    item = app.learning_store.instruction_item(GID, app._learning_item.get("instruction_scope"))
    app.show_learning_instruction(item)
    assert app.page == "learning_guide"
    shown = text_of(app.body)
    assert "我的回答：" in shown and wrong in shown
