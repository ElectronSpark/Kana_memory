"""Real Python link adapter with a small Android protocol double.

No Toga window, JVM, or emulator is started. The simulated native span calls the
actual dynamic-proxy method created by AndroidPlatform; Java compilation and
TextView/TalkBack behavior remain Android integration checks.
"""
import gc
from pathlib import Path
import sys
from types import SimpleNamespace
import weakref

import pytest

ROOT = Path(__file__).resolve().parents[1]
from kanamemory.platform_android import AndroidPlatform, _kana_span_offsets
from kanamemory.platform_desktop import DesktopPlatform


class NativeText:
    def __init__(self, text):
        self.text = text
        self.spans = []

    def __str__(self):
        return self.text

    def setSpan(self, span, start, end, flags):
        self.spans.append((span, start, end, flags))


class NativeSpan:
    """Only models KanaLinkSpan.onClick -> Handler.openKana at the JVM boundary."""
    def __init__(self, kana_id, handler):
        self.kana_id = kana_id
        self.handler = handler

    def onClick(self, view):
        self.handler.openKana(self.kana_id)


class NativeLabel:
    def __init__(self, text):
        self.text = text
        self.values = {
            "MovementMethod": None, "LinksClickable": False,
            "ImportantForAccessibility": 0, "Focusable": False,
            "FocusableInTouchMode": False, "Clickable": False,
            "LongClickable": False,
        }

    def setText(self, text):
        self.text = text

    def __getattr__(self, name):
        prefix = next((p for p in ("get", "set", "is") if name.startswith(p)), None)
        key = name[len(prefix):] if prefix else ""
        if key not in self.values:
            raise AttributeError(name)
        if prefix == "set":
            return lambda value: self.values.__setitem__(key, value)
        return lambda: self.values[key]


class Label:
    def __init__(self, text):
        self._impl = SimpleNamespace(native=NativeLabel(text))

    @property
    def text(self):
        return str(self._impl.native.text)


@pytest.fixture
def platform():
    classes = {
        "android.text.SpannableString": NativeText,
        "org.example.kanamemory.KanaLinkSpan": NativeSpan,
        "org.example.kanamemory.KanaLinkSpan$Handler": object,
        "android.text.Spanned": SimpleNamespace(SPAN_EXCLUSIVE_EXCLUSIVE=33),
        "android.text.method.LinkMovementMethod": SimpleNamespace(
            getInstance=lambda: "native-link-movement"
        ),
        "android.view.View": SimpleNamespace(IMPORTANT_FOR_ACCESSIBILITY_YES=1),
    }
    service = AndroidPlatform.__new__(AndroidPlatform)
    service._closed = False
    service._jclass = classes.__getitem__
    service._dynamic_proxy = lambda interface: object
    return service


@pytest.mark.parametrize("text,spans,expected", [
    ("きゃく", [{"start": 0, "end": 2, "kana_id": "kana-304d-3083"}],
     [(0, 2, "kana-304d-3083")]),
    ("😀きゃ𠮷カ", [
        {"start": 4, "end": 5, "kana_id": "kana-304b"},
        {"start": 1, "end": 3, "kana_id": "kana-304d-3083"},
    ], [(2, 4, "kana-304d-3083"), (6, 7, "kana-304b")]),
    ("𛀁か\u3099", [
        {"start": 0, "end": 1, "kana_id": "historical-documented"},
        {"start": 1, "end": 3, "kana_id": "kana-304c"},
    ], [(0, 2, "historical-documented"), (2, 4, "kana-304c")]),
])
def test_offsets_preserve_bmp_non_bmp_and_combining_sequences(text, spans, expected):
    assert _kana_span_offsets(text, spans) == expected


@pytest.mark.parametrize("spans,error", [
    ([{"start": -1, "end": 1, "kana_id": "a"}], ValueError),
    ([{"start": 0, "end": 4, "kana_id": "a"}], ValueError),
    ([{"start": 1, "end": 1, "kana_id": "a"}], ValueError),
    ([{"start": True, "end": 1, "kana_id": "a"}], TypeError),
    ([{"start": 0, "end": 1.0, "kana_id": "a"}], TypeError),
    ([{"start": 0, "end": 1, "kana_id": ""}], ValueError),
    ([{"start": 0, "end": 2, "kana_id": "a"},
      {"start": 1, "end": 3, "kana_id": "b"}], ValueError),
])
def test_invalid_ranges_fail_before_modifying_widget(platform, spans, error):
    label = Label("きゃく")
    before = label._impl.native.values.copy()
    with pytest.raises(error):
        platform.link_kana_label(label, spans, lambda kana_id: None)
    assert label.text == "きゃく"
    assert label._impl.native.values == before
    assert not hasattr(label, "_kana_link_state")


def test_native_span_dispatches_catalog_id_and_old_proxy_becomes_inert(platform):
    label = Label("😀きゃ𠮷カ")
    native = label._impl.native
    spans = [{"start": 1, "end": 3, "kana_id": "kana-304d-3083"},
             {"start": 4, "end": 5, "kana_id": "kana-304b"}]
    opened = []
    # Temporary callbacks stay alive for as long as their label does.
    assert platform.link_kana_label(label, spans, lambda kid: opened.append(kid)) is True
    assert [(start, end) for _, start, end, _ in native.text.spans] == [(2, 4), (6, 7)]
    old_span = native.text.spans[0][0]
    old_span.onClick(native)
    native.text.spans[1][0].onClick(native)
    assert opened == ["kana-304d-3083", "kana-304b"]

    platform.link_kana_label(label, spans, lambda kid: opened.append("new:" + kid))
    old_span.onClick(native)
    assert opened == ["kana-304d-3083", "kana-304b"]
    native.text.spans[0][0].onClick(native)
    assert opened[-1] == "new:kana-304d-3083"
    assert label.text == "😀きゃ𠮷カ"


def test_empty_spans_clear_links_and_restore_original_interaction_settings(platform):
    label = Label("あ")
    native = label._impl.native
    before = native.values.copy()
    opened = []
    platform.link_kana_label(label, [{"start": 0, "end": 1, "kana_id": "kana-3042"}], opened.append)
    old_span = native.text.spans[0][0]
    assert native.values["MovementMethod"] == "native-link-movement"
    assert native.values["ImportantForAccessibility"] == 1
    assert platform.link_kana_label(label, [], opened.append) is True
    old_span.onClick(native)
    assert opened == []
    assert native.text == "あ"
    assert native.values == before
    assert label._kana_link_state is None


def test_native_proxy_cannot_retain_widget_or_bound_callback_owner(platform):
    class Owner:
        def __init__(self):
            self.label = Label("あ")

        def open(self, kana_id):
            raise AssertionError("A collected owner's callback must not run")

    owner = Owner()
    platform.link_kana_label(owner.label,
                            [{"start": 0, "end": 1, "kana_id": "kana-3042"}], owner.open)
    # Keep the native graph alive as Android could, while releasing the Python
    # owner/widget cycle. The proxy must only keep a weak route back to Python UI.
    native = owner.label._impl.native
    handler = owner.label._kana_link_state["events"]
    owner_ref, widget_ref = weakref.ref(owner), weakref.ref(owner.label)
    del owner
    gc.collect()
    assert owner_ref() is None
    assert widget_ref() is None
    handler.openKana("kana-3042")
    native.text.spans[0][0].onClick(native)


def test_callback_stops_when_platform_is_closed(platform):
    label = Label("あ")
    opened = []
    platform.link_kana_label(label, [{"start": 0, "end": 1, "kana_id": "kana-3042"}], opened.append)
    native = label._impl.native
    platform._closed = True
    native.text.spans[0][0].onClick(native)
    assert opened == []


def test_desktop_requests_fallback_without_modifying_content():
    label = Label("きゃ")
    before = label.__dict__.copy()
    assert DesktopPlatform().link_kana_label(
        label, [{"start": 0, "end": 2, "kana_id": "kana-304d-3083"}], lambda kid: None
    ) is False
    assert label.__dict__ == before
    assert label.text == "きゃ"
