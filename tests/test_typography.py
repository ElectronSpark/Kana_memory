"""Font changes preserve shared Han semantics, native style and existing links."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from kanamemory import typography


def test_semantic_font_roles_do_not_infer_language_or_replace_pitch_monospace():
    assert typography.font_family('ui') == 'sans-serif'
    assert typography.font_family('study') == 'serif'
    assert typography.font_family('ui', 'monospace') == 'monospace'
    with pytest.raises(ValueError):
        typography.font_family('unreviewed')


def test_native_study_typeface_keeps_weight_and_selects_japanese_fallback(monkeypatch):
    face = SimpleNamespace(NORMAL=0, create=Mock(return_value='serif-bold'))
    monkeypatch.setitem(sys.modules, 'android.graphics', SimpleNamespace(Typeface=face))
    monkeypatch.setitem(sys.modules, 'java.util', SimpleNamespace(Locale=SimpleNamespace(JAPAN='ja-JP')))
    native = SimpleNamespace(getTypeface=lambda: SimpleNamespace(getStyle=lambda: 1),
                             setTypeface=Mock(), setTextLocale=Mock())
    typography.configure_native_text(native, 'study')
    face.create.assert_called_with('serif', 1)
    native.setTextLocale.assert_called_once_with('ja-JP')
    native.setTextLocale.reset_mock()
    typography.configure_native_text(native, 'ui')
    face.create.assert_called_with('sans-serif', 1)
    native.setTextLocale.assert_not_called()


def test_mixed_japanese_ranges_keep_links_and_use_utf16_offsets(monkeypatch):
    original_links = object()
    captures = []
    class Styled:
        def __init__(self, original):
            assert original is original_links
        def setSpan(self, span, start, end, flags):
            captures.append((span, start, end))
    monkeypatch.setattr(typography, 'current_platform', 'android')
    monkeypatch.setitem(sys.modules, 'android.text', SimpleNamespace(
        SpannableString=Styled, Spanned=SimpleNamespace(SPAN_EXCLUSIVE_EXCLUSIVE=33)))
    monkeypatch.setitem(sys.modules, 'android.text.style', SimpleNamespace(
        TypefaceSpan=lambda family: ('font', family), LocaleSpan=lambda lang: ('locale', lang)))
    monkeypatch.setitem(sys.modules, 'java.util', SimpleNamespace(Locale=SimpleNamespace(JAPAN='ja-JP')))
    native = SimpleNamespace(getText=lambda: original_links, setText=Mock())
    widget = SimpleNamespace(text='📖答案：学校', _impl=SimpleNamespace(native=native), refresh=Mock())
    typography.configure_study_ranges(widget, [(4, 6)])
    assert captures == [(('font', 'serif'), 5, 7), (('locale', 'ja-JP'), 5, 7)]
    assert widget.text == '📖答案：学校'
    widget.refresh.assert_called_once()
