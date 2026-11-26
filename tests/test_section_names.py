"""Sections that share a title are told apart where a page names a section to add (design 02, two lines).

study_lines.section_name: the title alone when no other section of the route has it; otherwise the title with
its first lesson, 「<title>（<lesson>）」 or 「<title>（<lesson> 等 N 课）」; a section without lessons keeps its
title. The add page's line rows, its suggestion and reason, and the multi-section preview name sections so;
the home's line buttons carry 建议先加 on the suggested one while both are drawn (home.display.compose). Pure
rules, the add-content service over a fake path service and the home model; no course content.
"""
from contextlib import contextmanager
from types import SimpleNamespace

from kanamemory import admission_workload
from kanamemory.admission.assembly import StoreAdmission
from kanamemory.arrangement.display import HomeRowsModel, SectionNote
from kanamemory.arrangement.model import RouteFacts
from kanamemory.home.display import compose
from kanamemory.route_mix.readiness import Readiness
from kanamemory.study_lines import SectionNames, route_lines, section_name
from kanamemory.study_routes import CORE_ROUTE_ID


def section(section_id, title, *lessons, kana=()):
    entries = [{"id": f"{section_id}:{index}", "kind": "learning", "family": "word", "title": lesson}
               for index, lesson in enumerate(lessons)]
    entries += [{"id": f"{section_id}:k{index}", "kind": "kana", "title": symbol} for index, symbol in enumerate(kana)]
    return {"id": section_id, "title": title, "entries": entries}


def test_a_title_no_other_section_has_is_the_name():
    assert section_name(section("s", "动物", "猫", "犬"), {"身份判断与否定"}) == "动物"


def test_a_shared_title_names_the_first_lesson_and_the_number_of_lessons():
    shared = {"身份判断与否定"}
    assert section_name(section("a", "身份判断与否定", "名词句：です"), shared) == "身份判断与否定（名词句：です）"
    assert section_name(section("b", "身份判断与否定", "名词句：でした", "名词句：ではありませんでした"),
                        shared) == "身份判断与否定（名词句：でした 等 2 课）"
    # Kana entries are no lessons: they are neither the first lesson nor counted.
    assert section_name(section("c", "身份判断与否定", "名词句：だ", kana=("あ",)), shared) == "身份判断与否定（名词句：だ）"


def test_a_shared_title_without_lessons_stays_the_title():
    assert section_name(section("k", "平假名", kana=("あ", "い")), {"平假名"}) == "平假名"
    assert section_name(section("e", "平假名"), {"平假名"}) == "平假名"


def test_section_names_finds_the_shared_titles_of_the_route_once():
    route = {"stages": [{"units": [{"sections": [section("a", "同名", "一"), section("b", "独有", "二"),
                                                 section("c", "同名", "三", "四")]}]}]}
    names = SectionNames(route)
    assert [names(s) for s in route["stages"][0]["units"][0]["sections"]] == ["同名（一）", "独有", "同名（三 等 2 课）"]
    assert names.of("c") == "同名（三 等 2 课）" and names.of("missing", "原题") == "原题"


# ------------------------------------------------------------------ the add page

# s0 词汇 [猫] (word), s1 身份判断与否定 [です] (grammar), s2 词汇 [犬, 鳥], s3 身份判断与否定 [ではありません],
# s4 词汇 [熊].
TREE = [("s0", "词汇", [("e0", "猫", "word")]), ("s1", "身份判断与否定", [("e1", "名词句：です", "grammar")]),
        ("s2", "词汇", [("e2", "犬", "word"), ("e2b", "鳥", "word")]),
        ("s3", "身份判断与否定", [("e3", "名词句：ではありません", "grammar")]), ("s4", "词汇", [("e4", "熊", "word")])]


class Paths:
    """A path service over TREE for the add-content service."""

    def __init__(self, readiness=None):
        self.states = {eid: "new" for _, _, entries in TREE for eid, _, _ in entries}
        self.readiness = readiness

    def overview(self, route_id=None):
        sections = []
        for section_id, title, entries in TREE:
            rows = [{"id": eid, "title": name, "kind": "learning", "family": family, "content_id": "word:" + eid,
                     "state": self.states[eid]} for eid, name, family in entries]
            sections.append({"id": section_id, "title": title, "entries": rows,
                             "new_count": sum(row["state"] == "new" for row in rows)})
        return {"stages": [{"id": "a", "units": [{"id": "u", "sections": sections}]}],
                "next_section": next((s for s in sections if s["new_count"]), None)}

    def preview_entries(self, ids, route_id=None):
        selected = [eid for eid in ids if self.states[eid] == "new"]
        return {"selected_entry_ids": selected, "prerequisite_entry_ids": [], "entry_ids": selected,
                "selected_count": len(selected), "prerequisite_count": 0, "total_count": len(selected),
                "section_count": 1, "summary": f"已选 {len(selected)} 项"}

    def plan_next(self, count, route_id=None, line=None):
        from kanamemory.study_lines import next_sections
        return next_sections(self.overview(), count, line)

    @contextmanager
    def read_operation(self, *, now=None):
        yield SimpleNamespace(service=self, check_active=lambda: None, overview=self.overview,
                              preview_entries=self.preview_entries, plan_next=self.plan_next)

    def line_readiness(self, content_ids):
        return self.readiness

    def line_readiness_job(self, content_ids):
        return lambda: True


def service_of(paths, monkeypatch, *, breadth=True):
    snapshot = {"new_item_count": 0, "new_direction_count": 0, "new_reference_count": 0,
                "pending_intro_count": 0, "ready_unit_count": 0, "waiting_unit_count": 0}
    monkeypatch.setattr(admission_workload, "admission_snapshot", lambda ctx, plan, **kw: dict(snapshot))
    service = StoreAdmission.for_store(object(), object(), paths=lambda: paths).service
    service._breadth = lambda: breadth
    return service


def test_the_preview_names_the_sections_apart(monkeypatch):
    paths = Paths()
    service = service_of(paths, monkeypatch)
    session, _ = service.open()
    preview = service.preview_sections(session, "3")
    assert preview.titles == ("词汇（猫）", "身份判断与否定（名词句：です）", "词汇（犬 等 2 课）")
    assert [line.text for line in preview.lines] == [
        "只勾选词汇：3 节 · 词汇（猫）、词汇（犬 等 2 课）、词汇（熊）",
        "只勾选语法：2 节 · 身份判断与否定（名词句：です）、身份判断与否定（名词句：ではありません）"]
    # After the first two sections are added, the next ones are named by their own lessons.
    paths.states.update(e0="added", e1="added")
    after = service.preview_sections(session, "1")
    assert after.titles == ("词汇（犬 等 2 课）",)


def test_the_line_rows_the_suggestion_and_the_reason_name_the_sections_apart(monkeypatch):
    paths = Paths(Readiness({"word:e1": {"application": [("t1", ["word:e4"])]}, "word:e0": {}}))
    service = service_of(paths, monkeypatch)
    _, model = service.open()
    lines = model.plan.lines
    assert [row.text for row in lines.rows] == ["下一节词汇：词汇（猫） · 建议先加", "下一节语法：身份判断与否定（名词句：です）"]
    paths.states["e0"] = "added"   # the word line has unlearned content: grammar first, which misses e4 (s4)
    _, model = service.open()
    lines = model.plan.lines
    # s4 is no line's next section: the suggestion names it on its own row.
    assert [row.text for row in lines.rows] == ["下一节词汇：词汇（犬 等 2 课）", "下一节语法：身份判断与否定（名词句：です）"]
    assert lines.suggestion == "建议先加：词汇（熊）"
    assert lines.reason == "「身份判断与否定（名词句：です）」的题还要用到 1 项没加入的内容，先加「词汇（熊）」。"
    # The missing lesson keeps its section's plain title beside its own.
    assert lines.rows[1].missing == "还缺：熊（词汇）"


def test_route_lines_name_their_sections_and_route_facts_take_the_names():
    route = Paths().overview()
    names = SectionNames(route)
    lines = route_lines(route, breadth=True, names=names)
    assert [(item.section_id, item.title) for item in lines.lines] == [("s0", "词汇（猫）"),
                                                                        ("s1", "身份判断与否定（名词句：です）")]
    assert lines.suggestion.title == "词汇（猫）"
    facts = RouteFacts.from_overview(route, lines.suggestion, "建议先加 · 词汇", name=names)
    assert (facts.next_section_title, facts.suggested_section_title) == ("词汇（猫）", "词汇（猫）")
    assert RouteFacts.from_overview(route).next_section_title == "词汇"


# ------------------------------------------------------------------ the home's mark

def home(**changes):
    values = dict(rows=HomeRowsModel(), focus=None, route=RouteFacts(), no_new_content=False, route_id=CORE_ROUTE_ID,
                  route_title="主线", core=True, has_work=True, ready=0, waiting=0, instruction=0, blocked_content=0,
                  blocked_material=0, blocked_input=0, blocked_kana=0, next_section=("w1", "动物"), others=(),
                  orphans=0, libraries_page=False, word_count=0, completed=0, total=9, added=0, partial=0,
                  next_change_at=None, line_sections=(("词汇", "w1", "动物"), ("语法", "g1", "は")))
    values.update(changes)
    return compose(**values)


def test_the_suggested_line_button_is_marked_while_both_are_drawn():
    def captions(section_id, **changes):
        model = home(suggested_section=(section_id, "建议先加"), **changes)
        return [button.caption for button in model.line_next]
    assert captions("g1") == ["加入下一节词汇 · 动物", "加入下一节语法 · は · 建议先加"]
    assert captions("w1") == ["加入下一节词汇 · 动物 · 建议先加", "加入下一节语法 · は"]
    # A suggestion that is neither button's section (a word section a grammar section needs): no mark.
    assert captions("w9") == ["加入下一节词汇 · 动物", "加入下一节语法 · は"]
    # One of them is the card's own offer (B10's note with its mark): today's drawing, no mark.
    assert captions("g1", rows=HomeRowsModel(note=SectionNote("g1", "は", mark="建议先加 · 语法"))) == [
        "加入下一节词汇 · 动物"]
    assert captions("g1", has_work=False) == ["加入下一节语法 · は"]
