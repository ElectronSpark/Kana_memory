"""The precheck page and the messages of a library file (design 08 section 16.10), with fake controls.

A refused pack shows what it is and why, nothing of an installation; an installable pack keeps 安装 right under
its summary; the file's name shows for a picked and an opened file alike; a version refusal never reads as an
unreadable file; a file that is no pack and a damaged pack read differently (the detail only in diagnostics);
the version already installed is not offered again; after an install the page offers nothing until the app's
own restart. Synthetic packs only.
"""
import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import library_activity
from kanamemory import library_file_open as files
from kanamemory.build_info import VERSION as APP_VERSION
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401

PICKED = "KanaMemory-0.36.17.1-n3.kmlib"
OPENED = "KanaMemory-0.36.17.2-n3.kmlib"
ACTIONS = {"添加内容库", "移除", "立即重启", "设为重点", "启用", "安装", "取消"}


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
    _platform_gives(app, pack_bytes("author.n3", extension_namespaces()))
    yield app
    content_pack.clear_cache()


def _platform_gives(app, data, *, picked=PICKED):
    """Platforms as they now are: the file is written into the sink block by block, the name returned."""
    async def pick(sink=None):
        for start in range(0, len(data), 1000):
            sink.write(data[start:start + 1000])
        return picked

    async def read(request, sink=None):
        await pick(sink)
        return request.name
    app.platform.import_library_pack = pick
    app.platform.read_library_file = read


def _request(name=OPENED):
    request = files.classify(files.IntentFields(action=files.ACTION_VIEW, uri="content://p/document/9",
                                                scheme="content", mime_type=files.MIME_TYPE, path="/document/9"))
    return files.named(request, name)


def _open(app, path):
    if path == "open":
        asyncio.run(app.open_content_library_file(_request()))
        return OPENED
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    return PICKED


def _buttons(app):
    return {view.text for view in descendants(app.body) if view.on_press}


def _texts(app):
    return [str(view.text) for view in descendants(app.body) if str(view.text)]


def _stamped(tmp_path, monkeypatch, version="20261121"):
    resources = tmp_path / "resources"
    monkeypatch.setattr(content_pack, "_resources", lambda: resources)
    monkeypatch.setattr(content_pack, "bundled_teaching", lambda: (resources, resources / "teaching_content.sqlite3"))
    sha = content_pack.validate_pack(resources / "teaching_content.sqlite3")["content_sha256"]
    (resources / rules.BUNDLED_STAMP_FILE).write_text(json.dumps(rules.make_bundled_stamp(version, "date", sha)),
                                                      encoding="utf-8")


def _core_pack_bytes(tmp_path, version):
    from package_library_pack import package
    output = tmp_path / f"core-{version}.kmlib"
    package(tmp_path / "resources", output, library_version=version)
    return output.read_bytes()


def _refused_page(app, name, library, version):
    """The refused pack's page: what the file is and why; nothing of an installation, no 安装."""
    text = text_of(app.body)
    assert app.page == "content_library_precheck"
    assert f"{library} · 版本 {version}" in text and f"文件名：{name}" in text and "不能安装" in text
    for line in ("新增内容", "替换已安装", "重启应用生效", "预检结果", "与已安装库相同", "冲突", "导入时间"):
        assert line not in text, line
    assert _buttons(app) == {"返回"}
    return text


# ------------------------------------------------------------------ L1: a refused pack's page


@pytest.mark.parametrize("pack_version, reason", [("20261119.3", "比应用内置的内容（20261121）旧，不能安装。"),
                                                  ("20261121", "与应用内置的内容相同，不需要安装。")])
@pytest.mark.parametrize("path", ["picker", "open"])
def test_a_core_pack_not_newer_than_the_bundled_content_shows_only_what_it_is_and_why(
        library_app, tmp_path, monkeypatch, pack_version, reason, path):
    app = library_app
    _stamped(tmp_path, monkeypatch)
    _platform_gives(app, _core_pack_bytes(tmp_path, pack_version))
    name = _open(app, path)
    text = _refused_page(app, name, rules.CORE_LIBRARY_ID, pack_version)
    assert f"这个核心库文件的版本 {pack_version}" in text and reason in text
    asyncio.run(app.install_content_library())  # no button, and the service refuses as well
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]


def test_a_pack_that_fails_the_trial_merge_is_refused_and_offers_no_install_anyway(library_app):
    """Before, a merge error showed 默认不安装 with 仍然安装, which the service then refused."""
    app = library_app
    base = extension_namespaces()["lesson"]["_ITEMS"]["grammar:ext:masu"]
    loop = {"grammar:ext:x": dict(base, id="grammar:ext:x", depends_on=["grammar:ext:y"]),
            "grammar:ext:y": dict(base, id="grammar:ext:y", depends_on=["grammar:ext:x"])}
    _platform_gives(app, pack_bytes("author.loop", {"lesson": {"_ITEMS": loop}}))
    name = _open(app, "picker")
    text = _refused_page(app, name, "author.loop", "20260102")
    assert "与已安装的内容库合并时出错，不能安装" in text and "成环" in text


def test_an_ordinary_pack_is_not_refused(library_app):
    app = library_app
    _open(app, "picker")
    assert "不能安装" not in text_of(app.body) and "安装" in _buttons(app)


# ------------------------------------------------------------------ L3: 安装 right under the summary


@pytest.mark.parametrize("path", ["picker", "open"])
def test_the_install_button_stands_directly_under_the_summary(library_app, path):
    app = library_app
    name = _open(app, path)
    texts = _texts(app)
    position = texts.index("安装")
    assert texts[:position] == ["测试扩展库", "author.n3 · 版本 20260102", f"文件名：{name}",
                                "内容：语法 1 · 新增内容 1 项", "签名状态：未签名"]
    assert position < texts.index("预检结果") < texts.index("来源")  # the longer details follow below it
    assert texts.index("取消") < texts.index("预检结果")
    assert "安装后重启应用生效；重启前仍按当前内容出题。进度记录不会改动。" in texts[position:]


def test_a_pack_with_missing_references_keeps_its_consent_button_under_the_summary(library_app):
    app = library_app
    _platform_gives(app, pack_bytes("author.n3", extension_namespaces(), manifest_overrides={
        "references": [{"id": "grammar:nowhere", "kind": "item", "requirement": "required"}]}))
    _open(app, "picker")
    texts = _texts(app)
    consent = texts.index("仍然安装（相关项标为依赖缺失、不可加入）")
    assert consent < texts.index("预检结果") and texts[consent - 1].startswith("默认不安装：")
    assert "安装" not in _buttons(app)


# ------------------------------------------------------------------ L5(c): the file's name


@pytest.mark.parametrize("path", ["picker", "open"])
def test_the_file_s_name_is_on_the_status_line_and_the_precheck_page(library_app, path):
    app = library_app
    service = app.content_library_service()
    seen = []
    stage = service.stage_file

    def staged(*args, **kwargs):
        seen.append(text_of(app.body))
        return stage(*args, **kwargs)
    service.stage_file = staged
    name = _open(app, path)
    assert seen and name in seen[0] and library_activity.CHECKING in seen[0]  # the status line names it
    assert f"文件名：{name}" in text_of(app.body)
    click(app, "安装")
    entry = rules.registry_entry(app.store.get_setting(rules.REGISTRY_SETTING), "author.n3")
    assert entry["source"]["name"] == name  # and the library's row keeps it


def test_a_platform_that_hands_over_bytes_still_works_without_a_name(library_app):
    app = library_app
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", extension_namespaces()))
    _open(app, "picker")
    assert "文件名：导入文件" in text_of(app.body) and "安装" in _buttons(app)


# ------------------------------------------------------------------ L2: a version refusal


@pytest.mark.parametrize("path", ["picker", "open", "picker without a name"])
@pytest.mark.parametrize("kind", ["app version", "pack format"])
def test_a_version_refusal_never_says_the_file_could_not_be_read(library_app, path, kind):
    app = library_app
    if kind == "app version":
        data = pack_bytes("author.n3", extension_namespaces(), manifest_overrides={
            "requires": {"teaching_format": 1, "namespace_version": 1, "app_min_version": "99.0"}})
        sentence = rules.APP_TOO_OLD.format(needed="99.0", current=APP_VERSION)
    else:
        data = pack_bytes("author.n3", extension_namespaces(), manifest_overrides={"format": "something-else"})
        sentence = rules.NEWER_PACK
    if path == "picker without a name":  # a platform that hands over the bytes: no name is known
        app.platform.import_library_pack = AsyncMock(return_value=data)
        _open(app, "picker")
        assert app.alert.call_args[0][0] == sentence
        return
    _platform_gives(app, data)
    name = _open(app, path)
    assert app.alert.call_args[0][0] == f"{name}：{sentence}"
    assert "无法读取" not in app.alert.call_args[0][0] and app.page == "content_libraries"


def test_other_unreadable_files_keep_the_prefix_when_no_name_is_known(library_app):
    app = library_app
    app.platform.import_library_pack = AsyncMock(return_value=b"not a zip")
    _open(app, "picker")
    assert app.alert.call_args[0][0] == "无法读取内容库：" + rules.NOT_A_PACK


# ------------------------------------------------------------------ L5(b): no pack, or a damaged pack


def _no_manifest():
    import io
    import zipfile
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("NOTICE.txt", "just a zip")
    return buffer.getvalue()


def _bad_crc():
    data = bytearray(pack_bytes("author.n3", extension_namespaces()))
    at = bytes(data).index(b"Synthetic extension notice")  # a stored member's bytes
    data[at] ^= 0x01
    return bytes(data)


@pytest.mark.parametrize("data, sentence, detail", [
    (b"not a zip", "这不是内容库文件。", "Not a library pack ZIP file"),
    (_no_manifest(), "这不是内容库文件。", "Library pack has no manifest.json"),
    (pack_bytes("author.n3", extension_namespaces())[:-40], "内容库文件已损坏，请重新获取。", "Damaged library pack ZIP file"),
    (_bad_crc(), "内容库文件已损坏，请重新获取。", "Bad CRC-32"),
])
@pytest.mark.parametrize("path", ["picker", "open"])
def test_no_pack_and_a_damaged_pack_read_differently_and_the_detail_goes_to_diagnostics(
        library_app, monkeypatch, data, sentence, detail, path):
    from kanamemory import diagnostic_events
    recorded = []
    monkeypatch.setattr(diagnostic_events, "record_error", lambda *args: recorded.append(args))
    app = library_app
    _platform_gives(app, data)
    name = _open(app, path)
    message = app.alert.call_args[0][0]
    assert message == f"{name}：{sentence}" and detail not in message
    (source, operation, error), = recorded
    assert (source, operation) == ("content_library", "read") and detail in str(error)
    root = app.content_library_service().root
    assert not [p for p in root.iterdir() if p.name.startswith(".incoming-")]  # nothing left behind


# ------------------------------------------------------------------ L5(e): the version already installed


def test_the_installed_version_is_said_in_one_sentence_and_not_offered_again(library_app):
    app = library_app
    _open(app, "picker")
    click(app, "安装")
    library_activity.of(app).end()  # this fake platform cannot restart: the page went back to its actions
    _open(app, "open")
    text = _refused_page(app, OPENED, "author.n3", "20260102")
    assert "这个内容库的版本 20260102 已经安装，不需要再安装。" in text
    # Another build of the same version (other content) is not installed over it either.
    namespaces = extension_namespaces()
    namespaces["lesson"]["SOURCES"] = [{"id": "rebuilt"}]
    _platform_gives(app, pack_bytes("author.n3", namespaces))
    _open(app, "picker")
    assert "已经安装，不需要再安装" in text_of(app.body) and "安装" not in _buttons(app)


def test_a_damaged_installed_library_may_be_installed_again_with_the_same_version(library_app):
    app = library_app
    _open(app, "picker")
    click(app, "安装")
    service = app.content_library_service()
    registry = service.registry()
    rules.registry_entry(registry, "author.n3")["state"] = "damaged"
    service._write(registry)
    _open(app, "picker")
    assert "不能安装" not in text_of(app.body) and "安装" in _buttons(app)


def test_a_newer_version_is_offered(library_app):
    app = library_app
    _open(app, "picker")
    click(app, "安装")
    _platform_gives(app, pack_bytes("author.n3", extension_namespaces(), version="20260301"))
    _open(app, "picker")
    assert "（替换已安装的 20260102）" in text_of(app.body) and "安装" in _buttons(app)


# ------------------------------------------------------------------ L5(d): nothing offered until the restart


@pytest.fixture
def restarting(library_app, monkeypatch):
    from kanamemory import process_restart
    monkeypatch.setitem(process_restart._STATE, "attempted", False)
    app = library_app
    app.platform.can_restart = True
    app.platform.restart_app = Mock(return_value=True)  # the fake returns; a real restart never does
    app._close_for_restart = Mock(return_value=True)
    return app


def test_after_an_install_the_page_offers_no_action_until_the_app_s_own_restart(restarting):
    app = restarting
    _open(app, "picker")
    click(app, "安装")
    app.platform.restart_app.assert_called_once_with()
    assert library_activity.RESTARTING in text_of(app.body) and not ACTIONS & _buttons(app)
    app.show_content_libraries()  # anything that draws the page again meanwhile
    assert library_activity.RESTARTING in text_of(app.body) and not ACTIONS & _buttons(app)
    pick = app.platform.import_library_pack = AsyncMock()
    asyncio.run(app.add_content_library())
    pick.assert_not_awaited()


def test_when_the_app_does_not_restart_itself_the_page_offers_its_actions_again(restarting):
    app = restarting
    app._close_for_restart = Mock(return_value=False)  # an unsaved draft refuses the close
    _open(app, "picker")
    click(app, "安装")
    app.platform.restart_app.assert_not_called()
    assert "添加内容库" in _buttons(app) and "立即重启" in _buttons(app)
    assert not library_activity.of(app).working
