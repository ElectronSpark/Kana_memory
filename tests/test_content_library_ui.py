"""Settings → 内容库 page with fake controls: list, add precheck, toggle, remove, home line."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import content_library_ui as library_ui
from kanamemory import library_activity
from kanamemory.learning_ui import LearningUIMixin
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401
from support.study_path_runtime_support import path_app, home  # noqa: F401
from support.study_home_support import home_page


@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", extension_namespaces()))
    app.go_back = Mock()
    service = app.content_library_service()
    service._catalogue_ids = lambda: set()
    service._catalogue_fingerprints = lambda: {}
    yield app
    content_pack.clear_cache()


def _buttons(app):
    return {view.text: view for view in descendants(app.body) if view.on_press}


def test_empty_state_lists_core_and_offers_add(library_app):
    app = library_app
    LearningUIMixin.show_settings_hub.__wrapped__(app)
    click(app, "内容库")
    assert app.page == "content_libraries"
    text = text_of(app.body)
    assert "还没有内容库" in text and "添加内容库" in text and "内置核心库" in text and rules.CORE_LIBRARY_ID in text
    assert "核心库始终启用，不可移除" in text and "移除" not in _buttons(app)
    assert "停用后立即不再派题，重启后从目录与路线中移除" in text and "都在重启后生效" not in text  # review B-06


def test_add_flow_shows_precheck_then_installs_pending_restart(library_app):
    app = library_app
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "测试扩展库" in text and "新增内容 1 项" in text and "同 ID 但内容不同（冲突） 0 项" in text
    assert "未满足的必需引用 0 项" in text and "重启应用生效" in text
    click(app, "安装")
    assert app.page == "content_libraries"
    text = text_of(app.body)
    assert "测试扩展库" in text and "author.n3 · 版本 20260102" in text and "重启后生效" in text and "语法 1" in text
    assert "重启应用后生效" in app.alert.call_args[0][0]
    registry = app.store.get_setting(rules.REGISTRY_SETTING)
    assert registry["libraries"][1]["state"] == "pending_restart" and registry["libraries"][1]["enabled"]
    assert "author.n3" in app.content_library_switches
    switch = app.content_library_switches["author.n3"]
    switch.value = False
    switch.on_change(switch)
    assert app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]["enabled"] is False
    assert app.page == "content_libraries"
    assert "停用后立即不再派题" in app.alert.call_args[0][0] and "· 所属内容库已停用" in text_of(app.body)
    app.main_window.dialog = AsyncMock(return_value=True)
    asyncio.run(app.remove_content_library("author.n3"))
    app.main_window.dialog.assert_awaited_once()
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]
    assert "还没有内容库" in text_of(app.body)


def test_precheck_cancel_discards_staging_and_blocking_pack_needs_explicit_accept(library_app):
    app = library_app
    data = pack_bytes("author.n3", extension_namespaces(), manifest_overrides={
        "references": [{"id": "grammar:nowhere", "kind": "item", "requirement": "required"}]})
    app.platform.import_library_pack = AsyncMock(return_value=data)
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "未满足的必需引用 1 项" in text and "缺少：grammar:nowhere" in text and "默认不安装" in text
    assert "安装" not in _buttons(app)
    staging = app._content_library_inspection.staging
    click(app, "取消")
    assert app.page == "content_libraries" and not staging.exists() and app._content_library_inspection is None
    asyncio.run(app.add_content_library())
    click(app, "仍然安装（相关项标为依赖缺失、不可加入）")
    assert app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]["library_id"] == "author.n3"


def test_changes_are_refused_during_backup_or_practice_round(library_app):
    app = library_app
    app.show_content_libraries()
    app._backup_busy = True
    asyncio.run(app.add_content_library())
    app.platform.import_library_pack.assert_not_awaited()
    assert "备份" in app.alert.call_args[0][0] and app.page == "content_libraries"
    app._backup_busy = False
    app._practice_context = True
    asyncio.run(app.add_content_library())
    assert "练习" in app.alert.call_args[0][0]
    app._practice_context = False
    asyncio.run(app.add_content_library())
    click(app, "安装")
    switch = app.content_library_switches["author.n3"]
    app._backup_busy = True
    switch.value = False
    switch.on_change(switch)
    assert switch.value is True and "备份" in app.alert.call_args[0][0]
    assert app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]["enabled"] is True


def test_bad_file_is_reported_and_leaves_the_list_unchanged(library_app):
    app = library_app
    app.platform.import_library_pack = AsyncMock(return_value=b"not a zip")
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_libraries" and "无法读取内容库" in app.alert.call_args[0][0]
    assert app.store.get_setting(rules.REGISTRY_SETTING) == rules.default_registry()


def test_home_shows_disabled_library_line_only_with_orphans(path_app, tmp_path, monkeypatch):
    app = path_app
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    from kanamemory.content_availability import Catalogue
    from support.library_availability_support import course
    app.content_library_service().registry()  # every App start writes the registry
    # The home line reads the store's availability projection (08 section 8.2):
    # one learned item the effective catalogue no longer serves is an orphan.
    app.store.content_catalogue = Catalogue(ids=frozenset({"grammar:demo:desu", "grammar:gone:x"}))
    app.learning_store.enroll(course("grammar:gone:x"))
    app.store.content_catalogue = Catalogue(ids=frozenset({"grammar:demo:desu"}))
    # The line opens the content libraries: the press lands on the app's show_content_libraries.
    monkeypatch.setattr(app, "show_content_libraries", Mock())
    home(app)
    texts = [view.text for view in descendants(home_page(app).secondary_slot) if view.on_press]
    assert "1 项所属内容库已停用" in texts
    line = home_page(app).press("1 项所属内容库已停用")
    app.show_content_libraries.assert_called_once_with(line)
    app.store.content_catalogue = Catalogue(ids=frozenset({"grammar:demo:desu", "grammar:gone:x"}))
    home(app)
    assert not any("内容库已停用" in text for text in [view.text for view in descendants(home_page(app).secondary_slot) if view.on_press])


def test_upgrade_precheck_lists_fingerprint_retired_and_alias_differences(library_app):
    from support.content_library_support import CORE_ITEMS
    app = library_app
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    click(app, "安装")
    service = app.content_library_service()
    service._learned_ids = lambda: {"grammar:ext:masu", "grammar:ext:old"}
    namespaces = extension_namespaces()
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"] = dict(namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"],
                                                              revision=2, meaning="动词敬体（修订）")
    namespaces["lesson"]["_ITEMS"]["grammar:ext:new"] = dict(CORE_ITEMS["grammar:demo:desu"], id="grammar:ext:new")
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", namespaces, version="20260201",
        manifest_overrides={"retires": [{"id": "grammar:ext:old", "replaced_by": "grammar:ext:new", "reason": "merged"}],
                            "aliases": {"grammar:ext:alt": "grammar:ext:new"}}))
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "替换已安装的 20260102" in text and "升级差异（相对已安装版本 20260102）" in text
    assert "指纹变化 1 项（其中已学 1 项" in text and "内容有变化的已学项：grammar:ext:masu" in text
    assert "新版本新增 1 项" in text and "被 retires 的项 1 项（其中已学 1 项）" in text
    assert "不再提供的已学项：grammar:ext:old，由 grammar:ext:new 取代" in text
    assert "别名：grammar:ext:alt → grammar:ext:new" in text and "安装" in _buttons(app)
    click(app, "取消")
    assert app._content_library_differences is None


def test_update_of_a_disabled_library_shows_the_differences_and_keeps_it_disabled(library_app):
    # native8: the update compared against nothing and switched the library back on without a word.
    app = library_app
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    click(app, "安装")
    service = app.content_library_service()
    service.set_enabled("author.n3", False)
    namespaces = extension_namespaces()
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"] = dict(namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"],
                                                              revision=2, meaning="动词敬体（修订）")
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", namespaces, version="20260201"))
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "升级差异（相对已安装版本 20260102）" in text and "指纹变化 1 项" in text and "新版本新增 0 项" in text
    assert "更新后仍保持停用" in text
    # The summary counts what the update adds, not the whole library again, and names no conflict with itself.
    assert "新增内容 0 项" in text and "同 ID 但内容不同（冲突） 0 项" in text and "未在 overrides 中声明" not in text
    click(app, "安装")
    entry = app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]
    assert (entry["library_version"], entry["enabled"], entry["state"]) == ("20260201", False, "pending_restart")
    assert "仍保持停用" in app.alert.call_args[0][0] and "重启应用后生效" not in app.alert.call_args[0][0]
    # An enabled library stays enabled, and nothing is said about the switch.
    service.set_enabled("author.n3", True)
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"]["revision"] = 3
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"]["meaning"] = "动词敬体（再修订）"
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", namespaces, version="20260301"))
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert "更新后仍保持停用" not in text_of(app.body)
    click(app, "安装")
    entry = app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]
    assert (entry["library_version"], entry["enabled"]) == ("20260301", True)
    assert "重启应用后生效" in app.alert.call_args[0][0]


def test_same_revision_different_content_is_a_package_error(library_app):
    from kanamemory.material_review import item_fingerprint
    from support.content_library_support import CORE_ITEMS
    app = library_app
    service = app.content_library_service()
    service._catalogue_ids = lambda: set(CORE_ITEMS)
    service._catalogue_fingerprints = lambda: {cid: item_fingerprint(item) for cid, item in CORE_ITEMS.items()}
    app.content_library_installed_revisions = lambda ids: {cid: "1" for cid in ids}
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes(
        "author.n3", extension_namespaces(conflict=True),
        manifest_overrides={"revisions": {"grammar:demo:desu": "1"}, "overrides": [{"id": "grammar:demo:desu", "reason": "x"}]}))
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    text = text_of(app.body)
    assert "同 ID 冲突" in text and "冲突：grammar:demo:desu（本库声明替代" in text
    assert "包错误：1 项" in text and "包错误：grammar:demo:desu" in text
    assert "安装" not in _buttons(app) and "仍然安装（相关项标为依赖缺失、不可加入）" not in _buttons(app)
    asyncio.run(app.install_content_library())
    assert "包错误" in app.alert.call_args[0][0]
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]


def _install(app):
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    click(app, "安装")
    assert app.page == "content_libraries"


def test_without_a_platform_restart_the_note_says_how_and_offers_no_button(library_app):
    app = library_app
    app.platform.can_restart = False
    _install(app)
    assert "立即重启" not in _buttons(app) and "只切到后台再回来不算重启" in text_of(app.body)
    assert "只切到后台再回来不算重启" in app.alert.call_args[0][0]


def test_restart_now_closes_like_an_exit_then_restarts_the_process(library_app, monkeypatch):
    from kanamemory import process_restart
    monkeypatch.setitem(process_restart._STATE, "attempted", False)
    app = library_app
    app.platform.can_restart = True
    app.platform.restart_app = Mock(return_value=True)  # the fake returns; a real restart never does
    app._close_for_restart = Mock(return_value=False)  # e.g. an unsaved draft: the automatic restart is refused
    _install(app)
    app.platform.restart_app.assert_not_called()
    assert library_ui.NO_AUTO_RESTART["refused"] in text_of(app.body)
    assert "立即重启" in _buttons(app) and "只切到后台再回来不算重启" not in text_of(app.body)
    asyncio.run(app.restart_for_library_change())  # 立即重启, still refused
    app.platform.restart_app.assert_not_called()
    app._close_for_restart = Mock(return_value=True)
    asyncio.run(app.restart_for_library_change())
    app._close_for_restart.assert_called_once_with()
    app.platform.restart_app.assert_called_once_with()
    assert process_restart.attempted()


def test_a_refused_restart_says_how_and_ends_the_closed_app(library_app, monkeypatch):
    from kanamemory import process_restart
    monkeypatch.setitem(process_restart._STATE, "attempted", False)
    app = library_app
    app.platform.can_restart = True
    app.platform.restart_app = Mock(return_value=False)
    app._close_for_restart = Mock(return_value=True)
    app.main_window = Mock(dialog=AsyncMock(return_value=None))
    app.exit = Mock()
    from kanamemory import ui_components
    info = Mock(name="InfoDialog")
    monkeypatch.setattr(ui_components.toga, "InfoDialog", info)
    _install(app)  # the automatic restart right after the install
    app.platform.restart_app.assert_called_once_with()
    assert "只切到后台再回来不算重启" in info.call_args[0][1]
    app.main_window.dialog.assert_awaited_once_with(info.return_value)
    app.exit.assert_called_once_with()


# --- the automatic restart after a change: at once, through 立即重启's path -------------------------------

@pytest.fixture
def restart_app(library_app, monkeypatch):
    """A platform that can restart and a close that saves; what the page showed when the app closed."""
    from kanamemory import process_restart
    monkeypatch.setitem(process_restart._STATE, "attempted", False)
    app = library_app
    app.platform.can_restart = True
    app.platform.restart_app = Mock(return_value=True)  # the fake returns; a real restart never does
    app.closing_pages = []

    def close():
        app.closing_pages.append(text_of(app.body))
        return True
    app._close_for_restart = Mock(side_effect=close)
    return app


def _toggle(app, enabled):
    switch = app.content_library_switches["author.n3"]
    switch.value = enabled
    switch.on_change(switch)


@pytest.mark.parametrize("change", ["install", "disable", "enable", "remove"])
def test_each_change_that_needs_a_restart_restarts_at_once(restart_app, change, monkeypatch):
    app = restart_app
    if change != "install":
        app.platform.can_restart = False
        _install(app)
        app.platform.can_restart = True
        app.alert.reset_mock()
        if change == "remove":  # this process loaded the installed library: removing it needs a restart
            loaded = app.content_library_service().registry()
            monkeypatch.setattr(content_pack, "active_registry", lambda: loaded)
        if change == "enable":
            app.platform.can_restart = False
            _toggle(app, False)
            app.platform.can_restart = True
            app.alert.reset_mock()
    if change == "install":
        _install(app)
    elif change == "remove":
        app.main_window.dialog = AsyncMock(return_value=True)
        asyncio.run(app.remove_content_library("author.n3"))
    else:
        _toggle(app, change == "enable")
    app._close_for_restart.assert_called_once_with()  # the App's normal close first, as 立即重启
    app.platform.restart_app.assert_called_once_with()
    assert library_activity.RESTARTING in app.closing_pages[0]  # the notice just before
    app.alert.assert_not_called()


@pytest.mark.parametrize("why", ["refused", "blocked"])
def test_a_refused_or_blocked_automatic_restart_keeps_today_s_note_and_says_why(restart_app, why):
    app = restart_app
    if why == "refused":
        app._close_for_restart = Mock(return_value=False)  # e.g. an unsaved draft: the close refuses
    else:
        app.platform.can_restart = False
        _install(app)
        app.platform.can_restart = True
        app.alert.reset_mock()
        app._content_library_blocked = Mock(return_value="练习正在进行")
    if why == "refused":
        _install(app)
    else:
        app.show_content_libraries()
        assert app._begin_library_restart() is True
        asyncio.run(app._automatic_library_restart())
    app.platform.restart_app.assert_not_called()
    text = text_of(app.body)
    assert library_ui.PENDING_NOTE + library_ui.NO_AUTO_RESTART[why] in text
    assert library_ui.RESTART_NOW in _buttons(app) and library_activity.RESTARTING not in text
    app.alert.assert_not_called()


def test_without_a_platform_restart_the_page_says_it_did_not_restart_by_itself(library_app):
    app = library_app
    app.platform.can_restart = False
    _install(app)
    text = text_of(app.body)
    assert library_ui.NO_AUTO_RESTART["unsupported"] in text and "只切到后台再回来不算重启" in text
    assert "立即重启" not in _buttons(app) and library_activity.RESTARTING not in text
    assert "只切到后台再回来不算重启" in app.alert.call_args[0][0]  # today's alert as well


def test_an_install_that_stays_disabled_does_not_restart(restart_app):
    app = restart_app
    _install(app)
    app.platform.restart_app.reset_mock()
    app.content_library_service().set_enabled("author.n3", False)
    namespaces = extension_namespaces()
    namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"] = dict(namespaces["lesson"]["_ITEMS"]["grammar:ext:masu"],
                                                              revision=2, meaning="动词敬体（修订）")
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", namespaces, version="20260201"))
    _install(app)
    app.platform.restart_app.assert_not_called()
    assert "仍保持停用" in app.alert.call_args[0][0]


def test_a_removal_with_nothing_pending_does_not_restart(restart_app):
    app = restart_app
    app.platform.can_restart = False
    _install(app)  # installed, never loaded by this process
    app.platform.can_restart = True
    app.main_window.dialog = AsyncMock(return_value=True)
    asyncio.run(app.remove_content_library("author.n3"))
    app.platform.restart_app.assert_not_called()
    assert "已移除" in app.alert.call_args[0][0]


def test_no_countdown_is_left():
    import importlib.util
    assert importlib.util.find_spec("kanamemory.library_restart") is None
    for name in ("AUTO_RESTART_NOTE", "RESTART_LATER", "postpone_library_restart"):
        assert not hasattr(library_ui, name) and not hasattr(library_ui.ContentLibraryUIMixin, name)
    for name in ("_arm_library_restart", "_cancel_library_restart", "_tick_library_restart"):
        assert not hasattr(library_ui.ContentLibraryUIMixin, name)


# --- design 08 section 5.7 and the removal of an imported core pack ---------------------------------------------

def _stamped(app, tmp_path, monkeypatch, version="20261121"):
    resources = tmp_path / "resources"
    monkeypatch.setattr(content_pack, "_resources", lambda: resources)
    monkeypatch.setattr(content_pack, "bundled_teaching", lambda: (resources, resources / "teaching_content.sqlite3"))
    sha = content_pack.validate_pack(resources / "teaching_content.sqlite3")["content_sha256"]
    (resources / rules.BUNDLED_STAMP_FILE).write_text(
        __import__("json").dumps(rules.make_bundled_stamp(version, "date", sha)), encoding="utf-8")


def _core_pack_bytes(tmp_path, version):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "package_library_pack", Path(__file__).resolve().parents[1] / "tools/package_library_pack.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = tmp_path / f"core-{version}.kmlib"
    module.package(tmp_path / "resources", output, library_version=version)
    return output.read_bytes()


def test_the_page_shows_the_bundled_version_and_which_pack_it_replaced(library_app, tmp_path, monkeypatch):
    app = library_app
    _stamped(app, tmp_path, monkeypatch)
    service = app.content_library_service()
    service._write_bundled_notice({"replaced_version": "20261119.3", "bundled_version": "20261121"})
    app.show_content_libraries()
    text = text_of(app.body)
    assert "版本 20261121" in text and "内置内容版本 20261121" in text
    assert library_ui.BUNDLED_REPLACED_NOTE.format(replaced_version="20261119.3", bundled_version="20261121") in text
    assert library_ui.PENDING_NOTE not in text


@pytest.mark.parametrize("version, sentence", [("20261119.3", "旧，不能安装"), ("20261121", "与应用内置的内容相同")])
def test_a_core_pack_not_newer_than_the_bundled_content_cannot_be_installed(library_app, tmp_path, monkeypatch,
                                                                            version, sentence):
    app = library_app
    _stamped(app, tmp_path, monkeypatch)
    app.platform.import_library_pack = AsyncMock(return_value=_core_pack_bytes(tmp_path, version))
    app.show_content_libraries()
    asyncio.run(app.add_content_library())
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert sentence in text and version in text
    assert "安装" not in _buttons(app) and not any(name.startswith("仍然安装") for name in _buttons(app))


def test_after_a_core_pack_was_removed_and_the_app_started_again_no_change_is_pending(restart_app, tmp_path,
                                                                                     monkeypatch):
    """Owner report: the note stayed after the removal and a restart (the bundled entry stayed pending)."""
    app = restart_app
    app.platform.import_library_pack = AsyncMock(return_value=_core_pack_bytes(tmp_path, "20261122"))
    _install(app)  # restarts at once
    app.platform.restart_app.reset_mock()
    app._close_for_restart.reset_mock()
    monkeypatch.delenv("KANAMEMORY_TEACHING_PACK")
    content_pack.clear_cache()  # the new process
    service = app.content_library_service()
    service.startup_sync()
    app.main_window.dialog = AsyncMock(return_value=True)
    app.show_content_libraries()
    asyncio.run(app.remove_content_library(rules.CORE_LIBRARY_ID))
    app._close_for_restart.assert_called_once_with()  # a removal restarts by the install's own path
    app.platform.restart_app.assert_called_once_with()
    content_pack.clear_cache()  # the new process
    service.startup_sync()
    LearningUIMixin.show_settings_hub.__wrapped__(app)
    app.show_content_libraries()
    assert library_ui.PENDING_NOTE not in text_of(app.body) and library_ui.RESTART_NOW not in _buttons(app)
