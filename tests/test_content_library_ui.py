"""Settings → 内容库 page with fake controls: list, add precheck, toggle, remove, home line."""
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory.learning_ui import LearningUIMixin
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
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
    app.install_content_library()
    assert "包错误" in app.alert.call_args[0][0]
    assert [e["library_id"] for e in app.store.get_setting(rules.REGISTRY_SETTING)["libraries"]] == [rules.CORE_LIBRARY_ID]
