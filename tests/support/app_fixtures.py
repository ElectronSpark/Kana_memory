"""Practice controller fixtures; consumers explicitly import required fixtures.

No autouse registration. Controls remain synthetic; Android is verified separately.
"""
from kanamemory import ui_components as component_ui, lesson_diagram_ui
from types import MethodType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from kanamemory.navigation import BackStack
from support.fake_ui import View, ScrollView

CHOICE = "看假名选读音"

HANDWRITING = "听音手写假名"

WORD = "听词选假名"

PARTIAL = "部分假名（较容易）"

FULL = "完整假名表（较难）"

DEFAULT_SCOPES = ("清音与拨音", "浊音", "半浊音", "拗音")

DEFAULT_CATEGORIES = ("basic", "dakuten", "handakuten", "yoon")

@pytest.fixture
def setup_app(monkeypatch):
    def fake_diagram(diagram):
        node = View(text=diagram['label'])
        node._lesson_vector = True
        node._diagram_spec = diagram
        return node
    monkeypatch.setattr(lesson_diagram_ui, "lesson_canvas", fake_diagram)
    # Real labels expose a mutable style when their semantic text role changes.
    monkeypatch.setattr(component_ui, "label", lambda text, **kw:
        View(text=text, **{"style": SimpleNamespace(), **kw}))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: View(text=text, on_press=handler, **kw))
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, **kw))
    monkeypatch.setattr(app_module.toga, "Selection", lambda **kw: View(**kw))
    monkeypatch.setattr(app_module.toga, "TextInput", lambda **kw: View(**kw))
    monkeypatch.setattr(app_module.toga, "Switch", lambda text, **kw: View(text=text, **kw))
    monkeypatch.setattr(app_module.toga, "ScrollContainer", ScrollView)
    monkeypatch.setattr(app_module, "kana_scope_inventory", lambda *args, scopes, **kw:
        {title: {"total": 0, "learned": 0, "queued": 0, "not_included": 0} for title in scopes})
    app = SimpleNamespace(
        page="home", _page_token=8, _starting_practice=False, loop=Mock(),
        # These legacy configuration cases exercise fixed-round practice;
        # interval-review defaults are covered in test_joint_practice_ui.
        _practice_preferences={"kind": CHOICE, "script": "平假名", "word_table": PARTIAL,
                               "research": False, "schedule": "多轮强化"},
        model_state="not_downloaded", model_message="", _model_request=0, _model_checking=False,
        _model_downloading=False, research_enabled=False, script="hiragana", use_full_keyboard=False,
        platform=SimpleNamespace(tts_ready=False, model_status=AsyncMock(return_value="ready"), stop=Mock()),
        heading=lambda text: View(text=text), note=lambda text: View(text=text),
        entry_card=lambda *children, **kw: View(*children, **kw),
        go_back=Mock(), show_settings=Mock(), show_tutor_center=Mock(), show_retention_check_center=Mock(), alert=Mock(), repo=Mock(), store=Mock(),
        back_stack=BackStack(), _assisted_attempts=set(), _answered_attempts=set(),
        show_question=Mock(), _checkpoint=Mock(), refresh_home_actions=Mock(), refresh_shared_material_notice=Mock(),
        practice_plan_summary=Mock(return_value={"total": 10, "due": 0, "new": 10}),
        # The production app always offers study-path next steps on completion/wait pages.
        study_path_service=lambda: SimpleNamespace(overview=lambda: {}),
        _path_next_controls=Mock(return_value=[]),
    )
    app.store.daily_new_limit = 10
    # App-run plans are an explicit startup opt-in (practice_round.enable);
    # a bare Mock would answer that flag with a truthy Mock.
    app.store._practice_run_enabled = False
    app.store.start_session.return_value = "new-session"
    app.repo.approved_word_count.return_value = 0
    app.repo.lexical_review = None
    for name in ("refresh_practice_setup", "_practice_config", "_practice_kinds", "_practice_rounds", "_practice_schedule_mode", "_practice_word_collection", "_practice_new_only", "start_practice", "_start_practice", "model_label",
                 "model_controls", "refresh_model_ui", "check_model", "download_model"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))

    def screen(name, title, *children, **kwargs):
        app.page, app.title = name, title
        app._page_token += 1
        app.body = View(*children)
        return app.body

    app.screen = screen
    KanaMemory.show_practice_setup.__wrapped__(app)
    return app

def set_mode(app, kind, *, script=None, research=None, table=None, scopes=None, count=None, batch=None):
    app.kind_picker.value = kind
    if script is not None:
        app.script_picker.value = script
    if research is not None:
        app.research_switch.value = research
    if table is not None:
        app.word_table_picker.value = table
    if scopes is not None:
        for title, control in app.scope_switches.items():
            control.value = title in scopes
    if count is not None:
        app.count_picker.value = f"{count} 项"
    if batch is not None:
        app.batch_picker.value = batch
    app.refresh_practice_setup()

@pytest.fixture
def question_app(setup_app, monkeypatch):
    app = setup_app
    monkeypatch.setattr(app_module.toga, "ScrollContainer", ScrollView)
    app.main_window = SimpleNamespace(size=SimpleNamespace(width=440))
    app.session_id, app.question_index, app.session_right = "session", 0, 0
    # Some focused feedback tests begin at an already displayed question.
    app._question_started = app_module.time.monotonic()
    app._answer_submitted_at, app._answer_duration_ms = None, None
    app.content_label = lambda text, **kw: View(text=text, **kw)
    app.mixed_label = lambda *parts, **kw: View(text="".join(str(getattr(part, "text", part)) for part in parts), **kw)
    app.show_ink_page = Mock()
    app._preview_pending = None
    app.preview_practice_answer = Mock()
    for name in ("_attempt_key", "show_word_question", "change_romaji_answer", "append_word_key", "delete_word_key", "reveal_answer", "choose_practice_answer"):
        setattr(app, name, MethodType(getattr(KanaMemory, name), app))
    app.check_answer = MethodType(KanaMemory.check_answer.__wrapped__, app)
    app.show_question = MethodType(KanaMemory.show_question.__wrapped__, app)
    app.save_rating = Mock()
    app.play_question_audio = Mock()
    return app


def pause_typing(app, control):
    """Edit a debounced text field, then let the typing pause elapse."""
    app.loop.call_later.reset_mock()
    control.on_change(control)
    delay, callback = app.loop.call_later.call_args.args[:2]
    assert delay == KanaMemory.TEXT_INPUT_DELAY
    callback()


def kana_route_handoff(app):
    """Main-lane kana practice hands off to the study-path route, which resumes
    the saved kana queue. Stub only that handoff for kana-queue fakes; course
    controllers without a route keep their standalone behaviour."""
    app.continue_study_path = Mock(side_effect=lambda *args, **kwargs: app.show_question())
    return app.continue_study_path

def render_question(app, kind):
    from kanamemory.domain import Question
    app.questions = [Question("kana-test" if not kind.startswith("word_") else "word-test", kind,
        "hiragana", "", "shi" if kind == "kana_romaji" else "し",
        options=["し", "き", "い"], metadata={"kana_symbol":"し", "romaji":"shi", "meaning_hint":"poem", "content_version":"test"})]
    app.show_question()
