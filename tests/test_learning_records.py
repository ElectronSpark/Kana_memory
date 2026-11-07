"""Learning records manage abilities while retaining exact queues and dates."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from support.learning_store_fixtures import START, item, question, ready, stores
from kanamemory.learning_store import LearningStore, FEEDBACK_COOLDOWN_SECONDS
from kanamemory import learning_records


class View:
    def __init__(self, *children, text="", on_press=None, **style):
        self.children, self.text, self.on_press = list(children), text, on_press
        self.enabled = True
    def add(self, *children):
        self.children.extend(children)
    def clear(self):
        self.children.clear()


def all_nodes(view):
    yield view
    for child in view.children:
        yield from all_nodes(child)


def fake_ui(monkeypatch):
    monkeypatch.setattr(learning_records, "_ui", lambda: SimpleNamespace(
        col=lambda *children, **style: View(*children), row=lambda *children, **style: View(*children),
        label=lambda text, **style: View(text=text),
        button=lambda text, handler, **style: View(text=text, on_press=handler), GREEN="green", MUTED="grey"))


def test_pause_resume_changes_no_schedule_history_or_pending_draft(stores):
    _, learning = stores
    learning.enroll(item(), now=START)
    learning.append_questions(question(), now=START)
    when = ready(learning)
    learning.submit("q1", "正在读书", True, now=when)
    result = learning.finalize("q1", 3, now=when)
    learning.append_questions(question(qid="q2"), now=when)
    learning.save_draft("q2", {"answer": "草稿", "assisted": True})
    before, snapshot, history = learning.get_unit(item()["id"], "understanding"), learning.get_question("q2"), learning.history()
    other = learning.get_unit(item()["id"], "application")
    learning.set_suspended(item()["id"], "understanding", True)
    paused = learning.get_unit(item()["id"], "understanding")
    assert paused == {**before, "suspended": 1}
    assert learning.current(now=datetime.fromisoformat(result["due_at_utc"]))["type"] == "disabled"
    learning.set_suspended(item()["id"], "understanding", False)
    assert learning.get_unit(item()["id"], "understanding") == before
    assert learning.get_unit(item()["id"], "application") == other
    assert learning.get_question("q2") == snapshot
    assert learning.history() == history
    assert learning.current(now=datetime.fromisoformat(result["due_at_utc"]))["question"]["id"] == "q2"


def test_pause_survives_backup_and_same_store_restore(stores):
    store, learning = stores
    learning.enroll(item(), now=START)
    learning.append_questions(question(), now=START)
    learning.save_draft("q1", {"answer": "abc"})
    learning.set_suspended(item()["id"], "understanding", True)
    data = store.export_backup()
    learning.set_suspended(item()["id"], "understanding", False)
    store.import_backup(data)
    assert learning.get_unit(item()["id"], "understanding")["suspended"] == 1
    assert learning.get_question("q1")["draft"] == {"answer": "abc"}


def test_records_count_content_not_number_of_abilities_or_questions(stores):
    _, learning = stores
    learning.enroll([item(title="进行"), item("grammar:other", title="结果")], now=START)
    learning.append_questions([question(), question(qid="q2", ability="application")], now=START)
    when = ready(learning)
    for qid in ("q1", "q2"):
        learning.submit(qid, "正在读书", True, now=when)
        learning.finalize(qid, 3, now=when)
        # The next ability of this same lesson must wait after its answer
        # was exposed; this fixture tests record counts, not bypassing that guard.
        when += timedelta(seconds=FEEDBACK_COOLDOWN_SECONDS + 1)
    learning.append_questions([question(qid="next-meaning"),
        question(qid="next-application", ability="application", kind="grammar_cloze")], now=when)
    snapshot = learning_records.learning_records_snapshot(learning, now=when+timedelta(days=1))
    assert snapshot["total"] == 2
    assert snapshot["reviewed"] == 1
    assert snapshot["due"] == 1
    assert all(unit["latest_rating"]["rating"] == 3 for unit in snapshot["items"][0]["units"])
    assert all(unit["latest_rating"] is None for unit in snapshot["items"][1]["units"])


def test_records_controls_pause_only_their_ability_and_stale_page_does_nothing(stores, monkeypatch):
    store, learning = stores
    fake_ui(monkeypatch)
    learning.enroll(item(title="〜ている"), now=START)
    learning.append_questions(question(), now=START)
    app = SimpleNamespace(store=store, learning_store=learning, page="learning_records")
    panel = learning_records.build_learning_records(app)
    buttons = [node for node in all_nodes(panel) if node.text == "暂停"]
    assert len(buttons) == 2
    buttons[0].on_press(None)
    assert learning.get_unit(item()["id"], "understanding")["suspended"] == 1
    assert learning.get_unit(item()["id"], "application")["suspended"] == 0
    restored = next(node for node in all_nodes(panel) if node.text == "恢复")
    app.page = "home"
    restored.on_press(None)
    assert learning.get_unit(item()["id"], "understanding")["suspended"] == 1
    app.page = "learning_records"
    new_panel = learning_records.build_learning_records(app)
    restored.on_press(None)  # retired panel callback is also inert
    assert learning.get_unit(item()["id"], "understanding")["suspended"] == 1
    next(node for node in all_nodes(new_panel) if node.text == "恢复").on_press(None)
    assert learning.get_unit(item()["id"], "understanding")["suspended"] == 0


def test_records_pages_bound_native_widget_count_and_recover_after_reset(stores, monkeypatch):
    store, learning = stores
    fake_ui(monkeypatch)
    learning.enroll([item(f"grammar:{n}", title=f"内容 {n}") for n in range(13)], now=START)
    app = SimpleNamespace(store=store, learning_store=learning, page="learning_records")
    panel = learning_records.build_learning_records(app)
    assert len([node for node in all_nodes(panel) if node.text.startswith("内容 ")]) == 12
    next(node for node in all_nodes(panel) if node.text == "下一页").on_press(None)
    assert len([node for node in all_nodes(panel) if node.text.startswith("内容 ")]) == 1
    assert app._learning_records_offset == 12
    store.clear_learning_data()
    panel = learning_records.build_learning_records(app)
    assert app._learning_records_offset == 0
    assert any("还没有加入" in node.text for node in all_nodes(panel))


@pytest.mark.parametrize("value", [0, 1, "true", None])
def test_suspension_rejects_non_boolean(stores, value):
    _, learning = stores
    learning.enroll(item(), now=START)
    with pytest.raises(ValueError):
        learning.set_suspended(item()["id"], "understanding", value)


def test_unknown_ability_cannot_be_created_by_pause(stores):
    _, learning = stores
    learning.enroll(item(), now=START)
    with pytest.raises(KeyError):
        learning.set_suspended(item()["id"], "handwriting", True)
    assert len(learning.get_item(item()["id"])["units"]) == 2


def test_records_use_probe_queue_time_and_match_home_after_pause_and_disable(stores):
    from support.learning_probe_support import overdue_probe
    store, learning = stores
    deadline, original = overdue_probe(learning)
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_text"]})
    learning.append_questions(question(qid="probe-record", kind="grammar_text", input_mode="text"), now=deadline)
    before = learning_records.learning_records_snapshot(learning, now=deadline)
    assert before["due"] == learning.counts(["grammar_text"], now=deadline)["ready"] == 0
    unit = next(x for x in before["items"][0]["units"] if x["ability"] == "understanding")
    assert unit["practice_due_at_utc"] == (deadline + timedelta(seconds=60)).isoformat()
    assert unit["due_at_utc"] == original["due_at_utc"]
    now = deadline + timedelta(seconds=60)
    snapshot = learning_records.learning_records_snapshot(learning, now=now)
    assert snapshot["due"] == learning.counts(["grammar_text"], now=now)["ready"] == 1
    assert datetime.fromisoformat(original["due_at_utc"]) > now
    learning.set_suspended(item()["id"], "understanding", True)
    assert learning_records.learning_records_snapshot(learning, now=now)["due"] == learning.counts(["grammar_text"], now=now)["ready"] == 0
    learning.set_suspended(item()["id"], "understanding", False)
    store.set_setting("learning_preferences_v1", {"enabled": []})
    snapshot = learning_records.learning_records_snapshot(learning, now=now)
    assert snapshot["due"] == learning.counts([], now=now)["ready"] == 0
    assert not snapshot["items"][0]["units"][0]["question_enabled"]
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_text"]})
    assert learning_records.learning_records_snapshot(learning, now=now)["due"] == 1
    assert learning.get_unit(item()["id"], "understanding") == original


def test_records_exclude_teaching_and_show_unscored_feedback_cooldown(stores):
    _, learning = stores
    learning.enroll(item(), now=START)
    learning.append_questions(question(), now=START)
    assert learning_records.learning_records_snapshot(learning, now=START)["due"] == 0
    when = ready(learning)
    wait = learning_records.learning_records_snapshot(learning, now=START)
    assert wait["due"] == 0
    assert wait["items"][0]["units"][0]["practice_due_at_utc"] == (START+timedelta(seconds=60)).isoformat()
    assert learning_records.learning_records_snapshot(learning, now=when)["due"] == 1
    learning.submit("q1", "unverified", None, outcome="unresolved", now=when)
    learning.finalize("q1", now=when)
    learning.append_questions(question(qid="after-feedback"), now=when)
    assert learning_records.learning_records_snapshot(learning, now=when)["due"] == 0
    assert learning_records.learning_records_snapshot(learning, now=when+timedelta(seconds=60))["due"] == 1


def test_record_widgets_display_effective_probe_time_not_later_fsrs_time(stores, monkeypatch):
    from support.learning_probe_support import overdue_probe
    store, learning = stores
    fake_ui(monkeypatch)
    deadline, original = overdue_probe(learning)
    store.set_setting("learning_preferences_v1", {"enabled": ["grammar_text"]})
    learning.append_questions(question(qid="visible-probe", kind="grammar_text", input_mode="text"), now=deadline)
    app = SimpleNamespace(store=store, learning_store=learning, page="learning_records")
    panel = learning_records.build_learning_records(app)
    labels = [node.text for node in all_nodes(panel)]
    expected = (deadline+timedelta(seconds=60)).strftime("%m月%d日 %H:%M:%S")
    assert "待练：" + expected in labels
    assert not any(label.startswith(("待练：", "复习时间：")) and datetime.fromisoformat(original["due_at_utc"]).strftime("%m月%d日 %H:%M:%S") in label for label in labels)
