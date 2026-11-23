"""The content library page while a library file is read, checked or installed (design 08 section 16.8), and
the plain Chinese a learner reads for a file that cannot be imported. Fakes only.

The status block shows from the moment the work starts until the precheck, an error or the restart; the
copy, the checks and the install's file work run off the UI thread (here: a thread other than the test's),
the page being drawn meanwhile; every store write stays on the UI thread.
"""
import asyncio
import threading
import zipfile
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import library_activity
from kanamemory import library_file_open as files
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401

NAME = "KanaMemory-0.36.7.1-n3.kmlib"


@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.go_back = Mock()
    service = app.content_library_service()
    service._catalogue_ids = lambda: set()
    service._catalogue_fingerprints = lambda: {}
    app.seen = []  # (moment, thread is the UI thread, page text, an indicator is shown)
    app.seen_buttons = {}  # moment -> the page's buttons then
    yield app
    content_pack.clear_cache()


def _look(app, moment):
    app.seen_buttons[moment] = {view.text for view in descendants(app.body) if view.on_press}
    app.seen.append((moment, threading.get_ident() == app.ui_thread, text_of(app.body),
                     any(getattr(view, "_activity_indicator", False) for view in descendants(app.body))))


def _watch_work(app):
    """Record the page while the copy, the staging and the precheck run (the window may draw meanwhile)."""
    app.ui_thread = threading.get_ident()
    service = app.content_library_service()
    data = pack_bytes("author.n3", extension_namespaces())

    async def read(*args):
        _look(app, "copy")
        return data
    app.platform.read_library_file = read
    app.platform.import_library_pack = read
    stage = service.stage_bytes

    def staged(*args, **kwargs):
        _look(app, "stage")
        return stage(*args, **kwargs)
    service.stage_bytes = staged
    snapshot = service.snapshot

    def watched(manifest):
        view = snapshot(manifest)
        precheck, prepare = view.precheck_staged, view.prepare_install

        def checked(*args, **kwargs):
            _look(app, "precheck")
            return precheck(*args, **kwargs)

        def prepared(*args, **kwargs):
            _look(app, "install")
            return prepare(*args, **kwargs)
        view.precheck_staged, view.prepare_install = checked, prepared
        return view
    service.snapshot = watched
    return service


def _buttons(app):
    return {view.text for view in descendants(app.body) if view.on_press}


def _request(name=NAME):
    request = files.classify(files.IntentFields(action=files.ACTION_VIEW, uri="content://p/document/9",
                                                scheme="content", mime_type=files.MIME_TYPE, path="/document/9"))
    return files.named(request, name)


@pytest.mark.parametrize("path", ["open", "picker"])
def test_the_status_block_shows_each_phase_while_the_work_runs_off_the_ui_thread(library_app, path):
    app = library_app
    _watch_work(app)
    app.page = "home"
    if path == "open":
        asyncio.run(app.open_content_library_file(_request()))
    else:
        app.show_content_libraries()
        asyncio.run(app.add_content_library())
    moments = {moment: (ui, text, indicator) for moment, ui, text, indicator in app.seen}
    assert list(moments) == ["copy", "stage", "precheck"]
    copy_ui, copy_text, copy_indicator = moments["copy"]
    assert library_activity.READING in copy_text and library_activity.WAIT_NOTE in copy_text and copy_indicator
    assert (NAME in copy_text) is (path == "open")  # the name when it is known
    for moment in ("stage", "precheck"):
        ui, text, indicator = moments[moment]
        assert not ui, moment  # a worker: the page can be drawn and animate meanwhile
        assert library_activity.CHECKING in text and library_activity.READING not in text and indicator
    # Nothing on the page starts another library operation while it shows.
    assert not {"添加内容库", "移除", "立即重启", "设为重点"} & set().union(*app.seen_buttons.values())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "新增内容 1 项" in text and "正在" not in text and library_activity.WAIT_NOTE not in text
    assert not any(getattr(view, "_activity_indicator", False) for view in descendants(app.body))
    assert library_activity.of(app).working is False


@pytest.mark.parametrize("path", ["open", "picker"])
def test_an_error_takes_the_status_block_s_place(library_app, path):
    app = library_app
    app.platform.read_library_file = AsyncMock(return_value=b"not a zip")
    app.platform.import_library_pack = AsyncMock(return_value=b"not a zip")
    app.show_content_libraries()
    if path == "open":
        asyncio.run(app.open_content_library_file(_request("notes.bin")))
        assert app.alert.call_args[0][0] == "notes.bin：这不是 KanaMemory 的内容库文件。"
    else:
        asyncio.run(app.add_content_library())
        assert app.alert.call_args[0][0] == "无法读取内容库：这不是 KanaMemory 的内容库文件。"
    assert app.page == "content_libraries" and "正在" not in text_of(app.body) and "添加内容库" in _buttons(app)


def test_a_cancelled_picker_leaves_the_page_as_it_was(library_app):
    app = library_app
    app.platform.import_library_pack = AsyncMock(return_value=None)
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    app.alert.assert_not_called()
    assert "正在" not in text_of(app.body) and "添加内容库" in _buttons(app)


def test_the_install_status_shows_from_the_tap_until_the_restart(library_app, monkeypatch):
    from kanamemory import process_restart
    monkeypatch.setitem(process_restart._STATE, "attempted", False)
    app = library_app
    service = _watch_work(app)
    app.platform.can_restart = True
    app.platform.restart_app = Mock(return_value=True)
    at_close = []
    app._close_for_restart = Mock(side_effect=lambda: at_close.append(text_of(app.body)) or True)
    writes = []
    store_write = app.store.set_setting

    def set_setting(key, value):
        writes.append(threading.get_ident() == app.ui_thread)
        return store_write(key, value)
    app.store.set_setting = set_setting
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    app.seen.clear()
    click(app, "安装")
    (moment, ui, text, indicator), = app.seen
    assert moment == "install" and not ui and indicator
    assert library_activity.INSTALLING in text and library_activity.WAIT_NOTE in text and "启用" not in _buttons(app)
    assert library_activity.RESTARTING in at_close[0]  # the status says it just before the app closes
    app.platform.restart_app.assert_called_once_with()
    assert writes and all(writes)  # the registry is written on the UI thread only
    assert service.registry()["libraries"][1]["library_id"] == "author.n3"


def test_a_failed_install_drops_the_status_and_says_why_in_chinese(library_app):
    app = library_app
    _watch_work(app)
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    inspection = app._content_library_inspection
    inspection.archive.write_bytes(b"damaged meanwhile")
    click(app, "安装")
    assert app.alert.call_args[0][0] == "未能安装内容库：这不是 KanaMemory 的内容库文件。"
    assert app.page == "content_libraries" and "正在" not in text_of(app.body)
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]


def test_a_snapshot_reads_the_store_first_and_never_writes(library_app):
    app = library_app
    service = app.content_library_service()
    staged = service.stage_bytes(pack_bytes("author.n3", extension_namespaces()), "x")
    view = service.snapshot(staged.manifest)
    assert view.registry() == service.registry()
    with pytest.raises(RuntimeError):
        view.store.set_setting(rules.REGISTRY_SETTING, {})
    with pytest.raises(RuntimeError):
        view.availability()
    inspection = view.precheck_staged(staged)
    prepared = view.prepare_install(inspection)
    # The registry changed after the install was prepared: nothing is registered, the new folder goes.
    changed = dict(service.registry(), pending_delete=["gone"])
    service._write(changed)
    with pytest.raises(ValueError, match="安装期间有了变化"):
        service.commit_install(prepared)
    assert not prepared.target.exists() and not inspection.staging.exists()
    assert [e["library_id"] for e in service.registry()["libraries"]] == [rules.CORE_LIBRARY_ID]


# ------------------------------------------------------------------ plain Chinese for a wrong file


@pytest.mark.parametrize("error,sentence", [
    (rules.ContentLibraryError("Not a library pack ZIP file"), "这不是 KanaMemory 的内容库文件。"),
    (rules.ContentLibraryError("Library pack has no manifest.json"), rules.NOT_A_PACK),
    (rules.ContentLibraryError("Library manifest is not valid JSON"), rules.NOT_A_PACK),
    (rules.ContentLibraryError("Library file checksum mismatch: teaching/lesson.json"), rules.DAMAGED_PACK),
    (rules.ContentLibraryError("Library file size does not match its manifest: x"), rules.DAMAGED_PACK),
    (zipfile.BadZipFile("Bad CRC-32 for file 'x'"), rules.DAMAGED_PACK),
    (EOFError(), rules.DAMAGED_PACK),
    (ValueError(rules.PACK_TOO_LARGE), rules.PACK_TOO_LARGE),
    (rules.ContentLibraryError("Library pack exceeds its size limit"), rules.PACK_TOO_LARGE),
    (rules.ContentLibraryError("Library file exceeds its size limit: media/a.mp3"), rules.PART_TOO_LARGE),
    (rules.ContentLibraryError("Unsupported library pack format"), rules.NEWER_PACK),
    (rules.ContentLibraryError("Unsupported library requirements"), rules.NEWER_PACK),
    (rules.ContentLibraryError(rules.APP_TOO_OLD.format(needed="9.0", current="0.36.7")),
     rules.APP_TOO_OLD.format(needed="9.0", current="0.36.7")),
    (rules.ContentLibraryError("Invalid library_id"), rules.WRONG_SHAPE),
    (rules.ContentLibraryError("Teaching files and provided namespaces disagree"), rules.WRONG_SHAPE),
    (ValueError("Learner state must never enter a content library"), rules.HOLDS_LEARNER_STATE),
    (rules.ContentLibraryError("Executable code or credential material is not library content: a.py"),
     rules.FORBIDDEN_FILE),
    (RuntimeError("not an importer error"), None),
])
def test_each_importer_error_has_one_short_chinese_sentence(error, sentence):
    assert rules.learner_error(error) == sentence
    if sentence is not None:
        assert not any("a" <= ch.lower() <= "z" for ch in sentence.replace("KanaMemory", "").replace("MiB", ""))


@pytest.mark.parametrize("data,sentence", [
    (b"not a zip", rules.NOT_A_PACK),
    (pack_bytes("author.n3", extension_namespaces(), corrupt="teaching/lesson.json"), rules.DAMAGED_PACK),
    (pack_bytes("author.n3", extension_namespaces(), manifest_overrides={"format": "something-else"}),
     rules.NEWER_PACK),
    (pack_bytes("author.n3", extension_namespaces(), manifest_overrides={"library_id": "Bad Id"}), rules.WRONG_SHAPE),
])
def test_a_wrong_file_opened_with_the_app_reads_in_plain_chinese(library_app, data, sentence):
    app = library_app
    app.platform.read_library_file = AsyncMock(return_value=data)
    asyncio.run(app.open_content_library_file(_request("file.kmlib")))
    assert app.alert.call_args[0][0] == "file.kmlib：" + sentence
