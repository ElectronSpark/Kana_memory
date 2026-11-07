"""Shared feedback context fixtures and assertions; no collected scenarios."""
from types import SimpleNamespace


class Widget:
    def __init__(self, *children, text="", **attrs):
        self.children = list(children)
        self.text = text
        self.enabled = True
        self.style = SimpleNamespace(font_size=18, background_color="#fff")
        for key, value in attrs.items():
            setattr(self, key, value)


class ReadStore:
    practice_lane = "main"

    def __init__(self):
        self.settings = {"prompt_display_v1": {"furigana": True}, "api_key": "must-not-read"}
        self.reads = []

    def get_setting(self, key, default=None):
        self.reads.append(key)
        return self.settings.get(key, default)


class LearningStore:
    def __init__(self):
        self.targets = []

    def get_item(self, target):
        self.targets.append(target)
        if target != "grammar:desu":
            return None
        return {"content_id": target, "stage": "review", "units": [
            {"unit_id": target + ":meaning", "due_at_utc": "2030-02-02T08:00:00+00:00", "review_count": 3}]}

    def get_question(self, qid):
        return {"question_id": qid, "status": "pending", "draft": {"answer": "saved"}, "not_before_utc": "2030-02-02T08:00:00+00:00"}


def fake_app(**attrs):
    app = SimpleNamespace(page="home", title_label=Widget(text="今日学习"),
        root=Widget(Widget(text="上方"), Widget(text="屏幕下方")),
        store=ReadStore(), learning_store=LearningStore(),
        _view_key=("show_home", None), _practice_preferences={},
        platform=SimpleNamespace(feedback_environment=lambda: {"platform": "test", "density": 3}),
        _feedback_events=[{"event": "screen", "page": "home"}],
        main_window=SimpleNamespace(size=SimpleNamespace(width=360, height=800)))
    for key, value in attrs.items():
        setattr(app, key, value)
    return app
