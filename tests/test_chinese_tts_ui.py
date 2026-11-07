"""Chinese device synthesis stays separate from pronunciation-aligned recordings."""
from kanamemory import ui_components as component_ui
import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import app as app_module
from kanamemory.app import KanaMemory
from support.reading_audio_ui_support import Node
from support.reading_audio_ui_support import nodes


def test_character_buttons_use_explicit_language_and_explain_auto_reading(monkeypatch):
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Node(text=text))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: Node(text=text, handler=handler))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: Node(*children))
    monkeypatch.setattr(component_ui, "speaker_button", lambda handler, description="播放读音": Node(text="🔊", handler=handler, description=description))
    app = SimpleNamespace(heading=lambda text: Node(text=text), note=lambda text: Node(text=text),
        entry_card=lambda *children, **kw: Node(*children), speak_chinese=Mock())
    cards = KanaMemory.chinese_tts_cards(app, ["安", "奴", "安"])
    items = list(nodes(cards))
    assert any("多音字" in n.text and "自动选音" in n.text for n in items)
    buttons = [n for n in items if n.handler]
    assert [n.text for n in buttons] == ["🔊"] * 4
    assert [n.options["description"] for n in buttons] == ["普通话系统示范：安", "香港粤语系统示范：安", "普通话系统示范：奴", "香港粤语系统示范：奴"]
    assert [n.text for n in items].count("普通话 · 自动选音") == 2
    assert [n.text for n in items].count("香港粤语 · 自动选音") == 2
    for node in buttons:
        node.handler(None)
    assert [c.args for c in app.speak_chinese.call_args_list] == [("安", "cmn"), ("安", "yue"), ("奴", "cmn"), ("奴", "yue")]
    assert KanaMemory.chinese_tts_cards(app, []) == []


@pytest.mark.parametrize("state", ["success", "error", "stale_page", "superseded"])
@pytest.mark.parametrize("language", ["cmn", "yue"])
def test_chinese_playback_uses_only_requested_language_and_respects_navigation(state, language):
    async def run():
        scheduled = []
        def schedule(coroutine):
            scheduled.append(asyncio.create_task(coroutine))
            return scheduled[-1]
        app = SimpleNamespace(_page_token=7, _audio_serial=0, loop=SimpleNamespace(create_task=schedule),
            platform=SimpleNamespace(play_chinese=AsyncMock(), play=AsyncMock(), play_recording=AsyncMock()),
            store=Mock(), alert=Mock())
        if state == "error":
            app.platform.play_chinese.side_effect = RuntimeError("请先下载香港粤语音包")
        KanaMemory.speak_chinese(app, "安", language)
        if state == "stale_page":
            app._page_token += 1
        elif state == "superseded":
            app._audio_serial += 1
        await asyncio.gather(*scheduled)
        if state in {"stale_page", "superseded"}:
            app.platform.play_chinese.assert_not_called()
        else:
            app.platform.play_chinese.assert_awaited_once_with("安", language)
        if state == "error":
            app.alert.assert_called_once_with("请先下载香港粤语音包")
        else:
            app.alert.assert_not_called()
        app.platform.play.assert_not_called()
        app.platform.play_recording.assert_not_called()
        assert app.store.mock_calls == []
    asyncio.run(run())


def test_wu_generated_file_is_separate_and_never_claims_dictionary_alignment(monkeypatch, tmp_path):
    path = tmp_path / "synthesis.wav"
    path.write_bytes(b"test file fixture")
    audio = dict(character="安", locality_label="上海话（模型示范）", local_path="media/shanghai-an.wav",
                 model_id="test-model", model_variant="test-variant", author="test author", license="test license",
                 source_url="https://example.org/model", source_ids=["model-source"], prompt_source_ids=["prompt-source"],
                 limitations_zh="单字自动读音可能有误。", method_zh="使用有来源的口音参考样本生成。", auto_reading=True)
    monkeypatch.setattr(app_module, "recording_path", Mock(return_value=path))
    monkeypatch.setattr(component_ui, "label", lambda text, **kw: Node(text=text))
    monkeypatch.setattr(component_ui, "button", lambda text, handler, **kw: Node(text=text, handler=handler))
    monkeypatch.setattr(component_ui, "row", lambda *children, **kw: Node(*children))
    monkeypatch.setattr(component_ui, "speaker_button", lambda handler, description="播放读音": Node(text="🔊", handler=handler, description=description))
    app = SimpleNamespace(repo=SimpleNamespace(character_synthesis_for_hanzi=Mock(return_value=[audio])),
                          heading=lambda text: Node(text=text), note=lambda text: Node(text=text),
                          entry_card=lambda *children, **kw: Node(*children), play_recording=Mock(), open_url=Mock())
    cards, sources = KanaMemory.character_synthesis_cards(app, ["安", "安"])
    app.repo.character_synthesis_for_hanzi.assert_called_once_with("安")
    flat = list(nodes(cards))
    assert sources == ["model-source", "prompt-source"]
    assert any("不表示与各条词典注音一致" in n.text for n in flat)
    assert "合成示范 · 待审听" in [n.text for n in flat]
    speaker, = [n for n in flat if n.text == "🔊"]
    pronunciation_row, = [n for n in flat if speaker in n.children]
    assert [n.text for n in pronunciation_row.children] == ["安", "🔊"]
    speaker.handler(None)
    app.play_recording.assert_called_once_with(audio)
    next(n for n in flat if n.text == "合成音出处").handler(None)
    assert app.open_url.call_args.kwargs["title"] == "合成音出处"
    path.unlink()
    assert KanaMemory.character_synthesis_cards(app, ["安"]) == ([], [])
