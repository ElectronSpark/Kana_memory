"""The two-line design's first part on the pages: the add page's lines and quick choices, the home's line buttons
and the mark on B10's note (fake UI and the add-content service over a fake path service; no course content).

The add page: each line's next section, the mark 建议先加, the readiness line
现在能出：…, the missing lessons and 一并加入 (a tick), the three quick choices
(加入勾选 by route order, 只勾选词汇, 只勾选语法), and the page without a readiness
table or after a failed plan. The service: the lines of a plan (the selection
counts as known), adding by line, 一并加入 and the readiness job. The home:
下一节词汇：… / 下一节语法：… where 加入下一小节 · … stood, never twice the
section the card offers. B10's note: the mark and the reason.
"""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from kanamemory import ui_components as component_ui
from kanamemory.admission import display
from kanamemory.admission.assembly import StoreAdmission
from kanamemory.admission.display import AddDelta, AddPageModel, LineRow, LinesModel, PlanModel, Prerequisites
from kanamemory.admission.ui import add_page
from kanamemory import admission_workload
from kanamemory.arrangement.display import HomeRowsModel, SectionNote
from kanamemory.arrangement.model import RouteFacts
from kanamemory.arrangement.ui.section_note import section_note_rows
from kanamemory.home.display import compose
from kanamemory.home.model import HomeActionKind
from kanamemory.home.ui import home_page
from kanamemory.route_mix.readiness import Readiness
from kanamemory.study_lines import GRAMMAR, WORD, route_lines
from kanamemory.study_routes import CORE_ROUTE_ID
from support.fake_ui import View, descendants


@pytest.fixture
def fake_ui(monkeypatch):
    def label(text, **style):
        return View(text=text, style=style)

    def button(text, handler, **style):
        return View(text=text, on_press=handler, style=style)

    def text_input(placeholder="", **kw):
        field = View(**kw)
        field.placeholder = placeholder
        return field

    monkeypatch.setattr(component_ui, "label", label)
    monkeypatch.setattr(component_ui, "button", button)
    monkeypatch.setattr(component_ui, "col", lambda *children, **kw: View(*children, style=kw))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: View(*children, style=kw))
    monkeypatch.setattr(component_ui.toga, "TextInput", text_input)
    monkeypatch.setattr(component_ui, "current_platform", "windows-fake")
    return View


def texts(widget):
    return [str(node.text) for node in descendants(widget) if node.text]


def press(widget, caption):
    control = next(node for node in descendants(widget) if node.text == caption and node.on_press)
    control.on_press(control)


# ------------------------------------------------------------------ the add-content service

# Stage a (unit u: s0 [e0, e0b] words, s1 [e1] grammar), stage b (unit u3: s3 [e3] a word).
TREE = [("a", "u", "s0", "认识假名", [("e0", "手紙", "word"), ("e0b", "新しい", "word")]),
        ("a", "u", "s1", "表达身份", [("e1", "名前", "grammar")]),
        ("b", "u3", "s3", "稍后再见", [("e3", "また", "word")])]


class LinePaths:
    """A path service over TREE for the add-content service: reads, plans, plan_next by line and readiness."""

    def __init__(self, readiness=None):
        self.states = {eid: "new" for *_, entries in TREE for eid, _, _ in entries}
        self.readiness = readiness
        self.log, self.jobs = [], []

    def enroll(self, *ids):
        self.states.update(dict.fromkeys(ids, "added"))

    def overview(self, route_id=None):
        stages = []
        for stage_id, unit_id, section_id, title, entries in TREE:
            if not stages or stages[-1]["id"] != stage_id:
                stages.append({"id": stage_id, "title": stage_id, "units": []})
            units = stages[-1]["units"]
            if not units or units[-1]["id"] != unit_id:
                units.append({"id": unit_id, "title": unit_id, "sections": []})
            rows = [{"id": eid, "title": name, "kind": "learning", "family": family, "content_id": "word:" + eid,
                     "state": self.states[eid]} for eid, name, family in entries]
            units[-1]["sections"].append({"id": section_id, "title": title, "unit_id": unit_id, "entries": rows,
                                          "new_count": sum(row["state"] == "new" for row in rows)})
        sections = [s for stage in stages for unit in stage["units"] for s in unit["sections"]]
        return {"stages": stages, "next_section": next((s for s in sections if s["new_count"]), None)}

    def preview_entries(self, ids, route_id=None):
        selected = [eid for eid in ids if self.states[eid] == "new"]
        return {"selected_entry_ids": selected, "prerequisite_entry_ids": [], "entry_ids": selected,
                "selected_count": len(selected), "prerequisite_count": 0, "total_count": len(selected),
                "section_count": 1, "summary": f"已选 {len(selected)} 项"}

    def plan_next(self, count, route_id=None, line=None):
        from kanamemory.study_lines import next_sections
        self.log.append(("plan_next", count, line))
        return next_sections(self.overview(), count, line)

    @contextmanager
    def read_operation(self, *, now=None):
        read = SimpleNamespace(service=self, check_active=lambda: None, overview=self.overview,
                               preview_entries=self.preview_entries, plan_next=self.plan_next)
        yield read

    def line_readiness(self, content_ids):
        self.log.append(("line_readiness", tuple(content_ids)))
        return self.readiness

    def line_readiness_job(self, content_ids):
        self.jobs.append(tuple(content_ids))
        return lambda: True


GRAMMAR_NEEDS_E3 = Readiness({"word:e1": {"application": [("t1", ["word:e3"])], "understanding": [("t2", [])]},
                              "word:e0": {}, "word:e0b": {}})


def service_of(paths, *, breadth=False, monkeypatch):
    snapshot = {"new_item_count": 0, "new_direction_count": 0, "new_reference_count": 0,
                "pending_intro_count": 0, "ready_unit_count": 0, "waiting_unit_count": 0}
    monkeypatch.setattr(admission_workload, "admission_snapshot", lambda ctx, plan, **kw: dict(snapshot))
    service = StoreAdmission.for_store(object(), object(), paths=lambda: paths).service
    service._breadth = lambda: breadth
    return service


def test_each_plan_draws_both_lines_with_the_selection_known(monkeypatch):
    paths = LinePaths(GRAMMAR_NEEDS_E3)
    service = service_of(paths, breadth=True, monkeypatch=monkeypatch)
    session, model = service.open()
    lines = model.plan.lines
    assert [(row.line, row.section_id) for row in lines.rows] == [(WORD, "s0"), (GRAMMAR, "s1")]
    word, grammar = lines.rows
    # s0 is preselected (as today): its words count as known; the grammar lesson misses e3 (s3).
    # Breadth, nothing unlearned: route order says s0 first (a word section), marked.
    assert word.text == "下一节词汇：认识假名" + display.SUGGESTED and word.suggested and word.readiness == ""
    assert grammar.text == "下一节语法：表达身份" and not grammar.suggested
    assert grammar.readiness == "现在能出：理解 1／应用 0"
    assert grammar.missing == "还缺：また（稍后再见）" and grammar.missing_ids == ("e3",)
    assert lines.suggestion == "" and lines.reason == ""
    # 一并加入 ticks e3: the grammar section is ready, nothing is missing any more.
    delta = service.select_entries(session, grammar.missing_ids)
    assert "e3" in session.selection.selected_entry_ids
    ready = delta.plan.lines.rows[1]
    assert ready.readiness == "现在能出：理解 1／应用 1" and ready.missing == ""


def test_the_suggestion_is_marked_and_a_grammar_section_waits_for_its_word_section(monkeypatch):
    paths = LinePaths(GRAMMAR_NEEDS_E3)
    paths.enroll("e0", "e0b")   # the word line has added but unlearned content: (a) suggests grammar
    service = service_of(paths, breadth=True, monkeypatch=monkeypatch)
    _, model = service.open()
    lines = model.plan.lines
    assert [row.section_id for row in lines.rows] == ["s3", "s1"]
    # (c) the grammar section misses e3: the suggestion is e3's word section, which is the word line's next one.
    assert lines.rows[0].text == "下一节词汇：稍后再见" + display.SUGGESTED and lines.rows[0].suggested
    assert lines.reason == "「表达身份」的题还要用到 1 项没加入的内容，先加「稍后再见」。"
    # 深度优先: the route's next section (s1) is the suggestion, as today, without a reason.
    _, thorough = service_of(paths, breadth=False, monkeypatch=monkeypatch).open()
    assert thorough.plan.lines.rows[1].suggested and thorough.plan.lines.reason == ""


def test_adding_by_line_ticks_the_next_sections_of_that_line(monkeypatch):
    paths = LinePaths()
    service = service_of(paths, monkeypatch=monkeypatch)
    session, _ = service.open()
    service.clear(session)
    service.select_next(session, "2", WORD)
    assert session.selection.selected_entry_ids == ["e0", "e0b", "e3"]
    service.clear(session)
    service.select_next(session, "1", GRAMMAR)
    assert session.selection.selected_entry_ids == ["e1"]
    service.select_next(session, "1")   # by route order: plan_next without a line, as today
    assert ("plan_next", 1, None) in paths.log and ("plan_next", 1, GRAMMAR) in paths.log
    assert session.selection.selected_entry_ids == ["e0", "e0b", "e1"]
    assert service.select_next(session, "x", WORD).rejection == display.COUNT_INVALID


def test_without_a_readiness_table_the_lines_have_no_readiness_and_the_job_reads_it(monkeypatch):
    paths = LinePaths(None)
    service = service_of(paths, monkeypatch=monkeypatch)
    session, model = service.open()
    assert [row.readiness for row in model.plan.lines.rows] == ["", ""]
    assert [row.missing for row in model.plan.lines.rows] == ["", ""]
    job = service.readiness_job(session)
    assert job() is True and paths.jobs == [("word:e0", "word:e0b", "word:e1")]


# ------------------------------------------------------------------ the add page (fake UI)

def plan(lines=None, **changes):
    feedback = {"kind": "study_path_selection", "version": 1, "valid": True, "selected_entry_ids": ["e0"]}
    values = dict(workload_text="", workload={}, lines=lines)
    values.update(changes)
    return PlanModel("已选 1 项 · 涉及 1 小节", True, True, feedback, **values)


LINES = LinesModel((LineRow(WORD, "s3", "下一节词汇：动物" + display.SUGGESTED, True, "现在能出：理解 4"),
                    LineRow(GRAMMAR, "s1", "下一节语法：は", False, "现在能出：理解 6／应用 1",
                            "还缺：猫（动物）", ("e3",))),
                   reason="「は」的题还要用到 1 项没加入的内容，先加「动物」。")


class Ops:
    def __init__(self):
        self.calls = []
        self.delta = AddDelta((), plan(LINES))

    def rows(self, expanded):
        return ()

    def toggle(self, key):
        return self.delta

    def clear(self, *, replan=True):
        return self.delta

    def select_next(self, count_text, line=None):
        self.calls.append(("select_next", count_text, line))
        return self.delta

    def select_entries(self, entry_ids):
        self.calls.append(("select_entries", tuple(entry_ids)))
        return AddDelta((), plan(None))

    def preview_sections(self, count_text):
        from kanamemory.admission.display import ShortcutPreview
        return ShortcutPreview({"kind": "study_path_add_preview"}, heading="将勾选 1 节")

    def prerequisites(self, *, open):
        return Prerequisites(0, False)

    def selection_feedback(self):
        return {"kind": "study_path_selection"}

    def refresh(self, **kw):
        return None


def build(lines, alive=True):
    ops = Ops()
    model = AddPageModel((), frozenset(), plan(lines), False, prerequisites=Prerequisites(0, False))
    page = add_page.AddPage(model, ops, live=lambda: alive, on_submit=lambda shown: None,
                            on_save_route=lambda title: None, on_detail=lambda detail: None,
                            on_switch_route=lambda rid: None, alert=lambda message: None)
    return page, ops


def test_the_add_page_draws_the_lines_under_the_main_button(fake_ui):
    page, ops = build(LINES)
    widgets = page.widgets()
    assert widgets.index(page.lines) == widgets.index(page.action) + 1
    assert texts(page.lines) == ["下一节词汇：动物 · 建议先加", "现在能出：理解 4", "下一节语法：は",
                                 "现在能出：理解 6／应用 1", "还缺：猫（动物）", "一并加入",
                                 "「は」的题还要用到 1 项没加入的内容，先加「动物」。"]
    marked = page.lines.children[0]
    assert marked.style["bold"] is True and marked.style["color"] == component_ui.GREEN
    assert page.lines.children[2].style["bold"] is False
    press(page.lines, "一并加入")
    assert ops.calls == [("select_entries", ("e3",))]
    # The answer's plan has no lines (a page without them): the block is empty.
    assert texts(page.lines) == []


def test_the_shortcut_has_three_quick_choices_by_route_and_by_line(fake_ui):
    page, ops = build(LINES)
    page.toggle_shortcut()
    assert [text for text in texts(page.shortcut) if text in {display.ADD_PREVIEWED, *display.ADD_LINE.values()}] == [
        "加入勾选", "只勾选词汇", "只勾选语法"]
    page.section_count.value = "2"
    press(page.shortcut, "加入勾选")
    press(page.shortcut, "只勾选词汇")
    press(page.shortcut, "只勾选语法")
    assert ops.calls == [("select_next", "2", None), ("select_next", "2", WORD), ("select_next", "2", GRAMMAR)]


def test_a_dead_page_ignores_the_line_controls(fake_ui):
    page, ops = build(LINES, alive=False)
    page.toggle_shortcut()
    press(page.shortcut, "只勾选语法")
    press(page.lines, "一并加入")
    assert ops.calls == []


def test_a_page_without_lines_or_after_a_failed_plan_shows_none(fake_ui):
    page, _ = build(None)
    assert texts(page.lines) == []
    page, _ = build(LINES)
    page._draw_plan(PlanModel.failed("出错了", ["e0"], can_save_route=True))
    assert texts(page.lines) == []


def test_lines_model_of_route_lines_names_the_suggestion_apart_when_it_is_no_lines_next_section():
    route = {"stages": [{"id": "a", "units": [{"id": "u", "sections": [
        {"id": "g1", "title": "は", "new_count": 1, "entries": [
            {"id": "p", "content_id": "p", "kind": "learning", "family": "grammar", "state": "new"}]},
        {"id": "w1", "title": "动物", "new_count": 1, "entries": [
            {"id": "a", "content_id": "a", "kind": "learning", "family": "word", "state": "new"}]},
        {"id": "w2", "title": "更多", "new_count": 1, "entries": [
            {"id": "b", "content_id": "b", "kind": "learning", "family": "word", "state": "new", "title": "猫"}]}]}]}]}
    readiness = Readiness({"p": {"application": [("t1", ["b"])]}, "a": {}})
    model = LinesModel.of(route_lines(route, readiness=readiness, breadth=True))
    assert [row.text for row in model.rows] == ["下一节词汇：动物", "下一节语法：は"]
    assert model.suggestion == "建议先加：更多"
    assert model.reason == "「は」的题还要用到 1 项没加入的内容，先加「更多」。"


# ------------------------------------------------------------------ the home

def home(**changes):
    values = dict(rows=HomeRowsModel(), focus=None, route=RouteFacts(), no_new_content=False, route_id=CORE_ROUTE_ID,
                  route_title="主线", core=True, has_work=True, ready=0, waiting=0, instruction=0, blocked_content=0,
                  blocked_material=0, blocked_input=0, blocked_kana=0, next_section=("w1", "动物"), others=(),
                  orphans=0, libraries_page=False, word_count=0, completed=0, total=9, added=0, partial=0,
                  next_change_at=None, line_sections=(("词汇", "w1", "动物"), ("语法", "g1", "は")))
    values.update(changes)
    return compose(**values)


def test_the_line_buttons_take_the_place_of_the_next_section_button():
    model = home()
    assert model.add_next is None
    assert [button.caption for button in model.line_next] == ["下一节词汇：动物", "下一节语法：は"]
    assert [(b.action.kind, b.action.section_id, b.action.route_id) for b in model.line_next] == [
        (HomeActionKind.ADD_SECTIONS, "w1", CORE_ROUTE_ID), (HomeActionKind.ADD_SECTIONS, "g1", CORE_ROUTE_ID)]
    # A route next section on neither line keeps 加入下一小节 · …; nothing by line: as before.
    kana = home(next_section=("k", "あ行"), line_sections=())
    assert kana.add_next.caption == "加入下一小节 · あ行" and kana.line_next == ()


def test_a_line_never_repeats_the_section_the_card_offers():
    noted = home(rows=HomeRowsModel(note=SectionNote("g1", "は")))
    assert [button.caption for button in noted.line_next] == ["下一节词汇：动物"]
    idle = home(has_work=False)
    assert idle.primary.action.then.section_id == "w1"
    assert [button.caption for button in idle.line_next] == ["下一节语法：は"]


def test_the_home_page_draws_the_line_buttons_after_the_next_section_button(fake_ui):
    model = home()
    card, column, stats, pressed = View(), View(), View(), []
    home_page.draw_home(model, action_slot=card, secondary_slot=column, stats_label=stats,
                        on_action=lambda action, widget: pressed.append(action))
    assert texts(column)[:2] == ["下一节词汇：动物", "下一节语法：は"]
    press(column, "下一节语法：は")
    assert pressed == [model.line_next[1].action]
    assert column.children[1].style.get("multiline") is True


def test_b10s_note_carries_the_mark_and_the_reason(fake_ui):
    note = SectionNote("w2", "动物", mark="建议先加 · 词汇", reason="「は」的题还要用到 1 项没加入的内容，先加「动物」。")
    added = []
    rows = section_note_rows(note, on_add_next=added.append)
    assert [row.text for row in rows] == ["已加入的内容都学过了", "建议先加 · 词汇",
                                          "「は」的题还要用到 1 项没加入的内容，先加「动物」。", "加入下一小节 · 动物"]
    rows[-1].on_press(rows[-1])
    assert added == ["w2"]
    assert [row.text for row in section_note_rows(SectionNote("w2", "动物"), on_add_next=added.append)] == [
        "已加入的内容都学过了", "加入下一小节 · 动物"]


def test_route_facts_carry_the_suggestion_for_the_note():
    from kanamemory.study_lines import Suggestion
    facts = RouteFacts.from_overview({"next_section": {"id": "g1", "title": "は"}},
                                     Suggestion(WORD, "w2", "动物", "因为"), "建议先加 · 词汇")
    assert (facts.next_section_id, facts.suggested_section_id, facts.suggested_mark, facts.suggested_reason) == (
        "g1", "w2", "建议先加 · 词汇", "因为")
    assert RouteFacts.from_overview({}).suggested_section_id is None


# ------------------------------------------------------------------ the second part: 建议先加 follows the share (design 12 LS8)

def test_the_add_page_suggestion_follows_the_line_behind_its_share(monkeypatch):
    paths = LinePaths()
    service = service_of(paths, breadth=True, monkeypatch=monkeypatch)
    asked = []
    service._behind = lambda: asked.append(1) or GRAMMAR
    session, model = service.open()
    # Nothing unlearned on either line: rule (b) names the line behind its share, not the route order.
    assert session.behind == GRAMMAR and asked == [1]
    assert [row.suggested for row in model.plan.lines.rows] == [False, True]
    service._behind = lambda: WORD
    refreshed = service.refresh(session, expanded=())
    assert session.behind == WORD and [row.suggested for row in refreshed.plan.lines.rows] == [True, False]
    # 深度优先: the share is not asked for and the route's next section stays the suggestion.
    thorough = service_of(paths, breadth=False, monkeypatch=monkeypatch)
    thorough._behind = lambda: asked.append(2) or GRAMMAR
    session, model = thorough.open()
    assert session.behind is None and 2 not in asked
    assert [row.suggested for row in model.plan.lines.rows] == [True, False]


def test_an_admission_without_an_arrangement_passes_no_line(monkeypatch):
    service = service_of(LinePaths(), breadth=True, monkeypatch=monkeypatch)
    session, model = service.open()
    assert session.behind is None and [row.suggested for row in model.plan.lines.rows] == [True, False]


def test_the_admission_asks_the_arrangement_s_dispatcher_with_breadth_known():
    from kanamemory.admission import assembly
    calls = []
    dispatcher = SimpleNamespace(line_behind=lambda now, breadth=None: calls.append((now.tzinfo is not None, breadth))
                                 or GRAMMAR)
    assert assembly._behind(lambda: SimpleNamespace(dispatcher=dispatcher)) == GRAMMAR
    assert calls == [(True, True)]
