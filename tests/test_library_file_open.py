"""Content library files (.kmlib) opened from outside the app (design 08 section 16.8), with fakes only.

The decisions (library_file_open), the lines that read an Android Intent (platform_android), the app's
routing (cold and warm delivery, once only, refusal while busy, a file that is not a pack, never an
install), the in-app picker's types, the tools' default extension and the build configuration's filters.
"""
import asyncio
import base64
import importlib.util
from pathlib import Path
import sys
import tomllib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import xml.etree.ElementTree as ET

import pytest

from kanamemory import content_library_rules as rules
from kanamemory import content_pack
from kanamemory import library_file_open as files
from kanamemory import library_activity
from kanamemory import platform_android as bridge
from support.app_fixtures import setup_app, question_app  # noqa: F401
from support.content_library_support import build_core_resources, extension_namespaces, pack_bytes
from support.content_library_support import no_ambient_bundled_stamp  # noqa: F401  (autouse: no ambient stamp)
from support.fake_ui import descendants, text_of
from support.learning_fixtures import learning_app, click  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ANDROID = "{http://schemas.android.com/apk/res/android}"
PACK_NAME = "KanaMemory-0.36.6-library-author.n3-20260102.kmlib"


def _fields(**values):
    base = dict(action=files.ACTION_VIEW, uri="content://downloads/document/7", scheme="content",
                mime_type=files.MIME_TYPE, path="/document/7", flags=0, handled=False)
    base.update(values)
    return files.IntentFields(**base)


# ------------------------------------------------------------------ decisions


@pytest.mark.parametrize("scheme", ["content", "file"])
@pytest.mark.parametrize("mime_type,path,match", [
    (files.MIME_TYPE, "/document/7", files.OWN_TYPE),
    ("application/octet-stream", "/document/7", files.OCTET),
    ("application/octet-stream", "/Download/" + PACK_NAME, files.PATH_SUFFIX),
    (None, "/storage/emulated/0/Download/" + PACK_NAME, files.PATH_SUFFIX),
    ("application/x-something", "/Download/OLD.KMLIB", files.PATH_SUFFIX),
])
def test_a_view_intent_of_each_filter_kind_is_a_request(scheme, mime_type, path, match):
    request = files.classify(_fields(scheme=scheme, uri=scheme + "://x" + path, mime_type=mime_type, path=path))
    assert request.match == match and request.scheme == scheme and request.uri == scheme + "://x" + path
    assert request.name == ("7" if path == "/document/7" else path.rsplit("/", 1)[1])


@pytest.mark.parametrize("change", [
    dict(action="android.intent.action.MAIN"), dict(action=None), dict(uri=None),
    dict(scheme="https", uri="https://example.org/a.kmlib", path="/a.kmlib"),
    dict(mime_type="application/zip", path="/Download/pack.zip"), dict(mime_type="text/plain"),
    dict(mime_type=None, path="/document/7"),
    dict(handled=True), dict(flags=files.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY | 0x10000000)])
def test_other_intents_relaunches_from_history_and_handled_ones_are_not_requests(change):
    assert files.classify(_fields(**change)) is None


def test_the_display_name_is_the_provider_s_else_the_path_s_last_part():
    request = files.classify(_fields(path="/document/primary%3ADownload%2F" + PACK_NAME))
    assert request.name == PACK_NAME
    assert files.named(request, "  课程.kmlib\n").name == "课程.kmlib"
    assert files.named(request, "") is request and files.named(None, "x") is None
    assert files.display_name(None, "/") == files.NO_NAME and len(files.display_name("x" * 500, None)) == files.MAX_NAME
    assert files.with_name("a.kmlib", "无法读取内容库：坏") == "a.kmlib：无法读取内容库：坏"
    assert files.with_name(None, "无法读取内容库：坏") == "无法读取内容库：坏"


def test_requests_wait_for_the_started_app_then_each_is_handled_once():
    requests, seen = files.Requests(), []
    first, second = (files.classify(_fields(uri=f"content://d/{n}")) for n in (1, 2))
    assert requests.deliver(None) is None
    assert requests.deliver(first, False) == "queued" and requests.deliver(second, False) == "queued"
    assert seen == []  # cold: nothing before the app is fully started; the newest waits
    requests.start(seen.append)
    assert seen == [second] and requests.pending is None
    assert requests.deliver(first, True) == "handled" and seen == [second, first]  # warm
    requests.start(seen.append)
    assert seen == [second, first]  # starting again hands nothing over twice
    requests.stop()
    assert requests.deliver(first, True) == "queued"


def test_busy_is_any_guard_that_stops_leaving_the_page():
    assert files.busy() is False
    for guard in ("choosing", "working", "confirming", "navigation_hidden", "unsaved"):
        assert files.busy(**{guard: True}) is True
    assert files.busy(blocked_reason="备份或恢复进行中") is True


# ------------------------------------------------------------------ the lines that read an Intent


class FakeUri:
    def __init__(self, text):
        self.text = text
        self.scheme, rest = text.split(":", 1)
        self.path = "/" + rest.lstrip("/").split("/", 1)[1] if "/" in rest.lstrip("/") else rest

    def toString(self): return self.text
    def getScheme(self): return self.scheme
    def getPath(self): return self.path


class FakeIntent:
    def __init__(self, action=files.ACTION_VIEW, uri="content://com.provider/document/9", mime_type=files.MIME_TYPE,
                 flags=0):
        self.action, self.data = action, FakeUri(uri) if uri else None
        self.mime_type, self.flags, self.extras = mime_type, flags, {}

    def getAction(self): return self.action
    def getData(self): return self.data
    def getType(self): return self.mime_type
    def getFlags(self): return self.flags
    def getBooleanExtra(self, key, default): return self.extras.get(key, default)
    def putExtra(self, key, value): self.extras[key] = value


class FakeResolver:
    def __init__(self, name=PACK_NAME, mime_type=None):
        self.name, self.mime_type, self.queries, self.closed = name, mime_type, [], 0

    def getType(self, uri): return self.mime_type

    def query(self, uri, *args):
        self.queries.append((uri.toString(), args))
        resolver = self

        class Cursor:
            def moveToFirst(self): return resolver.name is not None
            def getColumnIndex(self, column): return 0 if column == "_display_name" else -1
            def getString(self, index): return resolver.name
            def close(self): resolver.closed += 1
        return Cursor()


def test_reading_an_intent_marks_it_so_a_second_delivery_does_nothing():
    intent, resolver = FakeIntent(), FakeResolver()
    request = bridge.library_file_request(intent, resolver)
    assert request.match == files.OWN_TYPE and request.name == PACK_NAME and request.uri == intent.data.text
    assert intent.extras == {files.HANDLED_EXTRA: True} and resolver.closed == 1
    assert resolver.queries == [(intent.data.text, (None, None, None, None))]  # no projection array crosses JNI
    assert bridge.library_file_request(intent, resolver) is None  # the same Intent again (or a re-created window)


def test_the_type_comes_from_the_provider_when_the_intent_has_none_and_file_uris_are_named_by_their_path():
    intent = FakeIntent(mime_type=None)
    assert bridge.library_file_request(intent, FakeResolver(mime_type="application/octet-stream")).match == files.OCTET
    intent = FakeIntent(uri="file:///storage/emulated/0/Download/" + PACK_NAME, mime_type=None)
    resolver = FakeResolver()
    request = bridge.library_file_request(intent, resolver)
    assert request.scheme == "file" and request.match == files.PATH_SUFFIX and request.name == PACK_NAME
    assert resolver.queries == []  # nothing is read from a file path, not even its name


def test_an_ambiguous_put_extra_names_the_boolean_overload(monkeypatch):
    marker = object()
    monkeypatch.setitem(sys.modules, "java", SimpleNamespace(jboolean=lambda value: marker if value is True else None))
    intent = FakeIntent()
    plain = intent.putExtra

    def put(key, value):
        if value is True:
            raise TypeError("ambiguous overload")
        plain(key, value)
    intent.putExtra = put
    assert bridge.library_file_request(intent, FakeResolver()) is not None
    assert intent.extras == {files.HANDLED_EXTRA: marker}


def test_launcher_restart_and_history_intents_are_ignored_and_left_unmarked():
    restart = FakeIntent(action="android.intent.action.MAIN", uri=None, mime_type=None)  # KanaRestart's launch
    history = FakeIntent(flags=files.FLAG_ACTIVITY_LAUNCHED_FROM_HISTORY)
    for intent in (restart, history, None):
        assert bridge.library_file_request(intent, FakeResolver()) is None
    assert restart.extras == {} and history.extras == {}


@pytest.fixture
def android(monkeypatch, tmp_path):
    """An AndroidPlatform over fake Java classes: the activity, its listeners and the document bridge."""
    host = SimpleNamespace(listeners=[], documents=[], resolver=FakeResolver())
    activity = SimpleNamespace(intent=FakeIntent())
    activity.getIntent = lambda: activity.intent
    activity.setIntent = lambda intent: setattr(activity, "intent", intent)
    activity.addOnNewIntentListener = host.listeners.append
    activity.removeOnNewIntentListener = host.listeners.remove

    class Document:
        def __init__(self, owner, events):
            self.events, self.cancelled, self.calls, self.blocks = events, False, [], iter(())
            host.documents.append(self)
        def importJson(self): self.calls.append(("importJson",))
        def importDocument(self, types): self.calls.append(("importDocument", types))
        def importUri(self, uri): self.calls.append(("importUri", uri))
        def readImportBlock(self): return next(self.blocks, "")
        def isCancelled(self): return self.cancelled
        def cancel(self): self.cancelled = True

    classes = {"org.beeware.android.MainActivity": SimpleNamespace(singletonThis=activity),
               "androidx.core.util.Consumer": object, "android.content.Intent": FakeIntent,
               "org.example.kanamemory.KanaBackupDocuments": Document,
               "org.example.kanamemory.KanaBackupDocuments$Events": object}
    monkeypatch.setitem(sys.modules, "java", SimpleNamespace(jclass=classes.__getitem__, cast=lambda cls, obj: obj,
                                                             dynamic_proxy=lambda interface: object))
    context = SimpleNamespace(getContentResolver=lambda: host.resolver,
                              getCacheDir=lambda: SimpleNamespace(getCanonicalPath=lambda: str(tmp_path)))
    context.getApplicationContext = lambda: context
    platform = bridge.AndroidPlatform(context)
    platform.stop = Mock()
    return platform, activity, host


def test_cold_start_reads_the_launching_intent_and_warm_deliveries_come_through_on_new_intent(android):
    platform, activity, host = android
    received = []
    platform.watch_library_file_requests(lambda request, warm: received.append((request.uri, warm)))
    assert received == [(activity.intent.data.text, False)] and len(host.listeners) == 1
    warm = FakeIntent(uri="content://com.provider/document/10", mime_type="application/octet-stream")
    host.listeners[0].accept(FakeIntent(action="android.intent.action.MAIN", uri=None, mime_type=None))
    host.listeners[0].accept(warm)
    host.listeners[0].accept(warm)  # the same intent delivered twice
    assert received[1:] == [("content://com.provider/document/10", True)]
    assert activity.intent is warm and warm.extras == {files.HANDLED_EXTRA: True}  # the newest delivery, marked
    # A window re-created in this process reads the activity's intent: the last one, already handled.
    platform.watch_library_file_requests(lambda request, warm: received.append((request.uri, warm)))
    assert len(received) == 2 and len(host.listeners) == 1
    platform.close()
    assert host.listeners == []


def test_the_picker_offers_kmlib_and_zip_types_and_an_opened_file_reads_through_its_uri(android):
    platform, _, host = android
    assert files.OCTET_STREAM in files.PICKER_TYPES and "application/zip" in files.PICKER_TYPES
    assert files.MIME_TYPE in files.PICKER_TYPES and "application/json" not in files.PICKER_TYPES

    async def run(call):
        task = asyncio.create_task(call)
        await asyncio.sleep(0)
        document = host.documents[-1]
        document.blocks = iter([base64.b64encode(b"PK-pack").decode()])
        document.events.onComplete(bridge._BACKUP_IMPORT_READY, False, None)
        return await task, document.calls

    data, calls = asyncio.run(run(platform.import_library_pack()))
    assert data == b"PK-pack" and calls == [("importDocument", "\n".join(files.PICKER_TYPES))]
    request = files.classify(_fields(uri="content://com.provider/document/9"))
    data, calls = asyncio.run(run(platform.read_library_file(request)))
    assert data == b"PK-pack" and calls == [("importUri", "content://com.provider/document/9")]
    data, calls = asyncio.run(run(platform.import_backup()))
    assert calls == [("importJson",)]  # backups keep their own picker


def test_a_file_uri_the_app_may_not_read_says_so(android):
    platform, _, host = android
    request = files.classify(_fields(scheme="file", uri="file:///sdcard/Download/a.kmlib", path="/sdcard/Download/a.kmlib"))

    async def run():
        task = asyncio.create_task(platform.read_library_file(request))
        await asyncio.sleep(0)
        host.documents[-1].events.onComplete(None, False, "无法读取文件：open failed: EACCES (Permission denied)")
        return await task

    with pytest.raises(bridge.PlatformError, match="不允许本应用按文件路径读取"):
        asyncio.run(run())


def test_the_java_bridge_reads_a_uri_through_the_resolver_and_keeps_the_backup_picker_types():
    source = (ROOT / "app/native/java/org/example/kanamemory/KanaBackupDocuments.java").read_text("utf-8")
    assert "public void importUri(String uriText)" in source and "public void importDocument(String mimeTypes)" in source
    assert 'importDocument("application/json\\napplication/octet-stream\\napplication/zip\\ntext/plain")' in source
    assert "resolver.openInputStream(uri)" in source and source.count("openInputStream") == 1
    assert '"content".equals(scheme) && !"file".equals(scheme)' in source


# ------------------------------------------------------------------ the app


@pytest.fixture
def library_app(learning_app, tmp_path, monkeypatch):
    app = learning_app
    resources = build_core_resources(tmp_path / "resources")
    monkeypatch.setenv("KANAMEMORY_TEACHING_PACK", str(resources / "teaching_content.sqlite3"))
    monkeypatch.setenv("KANAMEMORY_LIBRARY_ROOT", str(tmp_path / "libraries"))
    content_pack.clear_cache()
    app.platform.read_library_file = AsyncMock(return_value=pack_bytes("author.n3", extension_namespaces()))
    app.platform.import_library_pack = AsyncMock(return_value=pack_bytes("author.n3", extension_namespaces()))
    app.go_back = Mock()
    service = app.content_library_service()
    service._catalogue_ids = lambda: set()
    service._catalogue_fingerprints = lambda: {}
    yield app
    content_pack.clear_cache()


def _request(name=PACK_NAME):
    return files.named(files.classify(_fields(uri="content://com.provider/document/9")), name)


def _buttons(app):
    return {view.text: view for view in descendants(app.body) if view.on_press}


def test_an_opened_file_shows_the_precheck_of_add_and_never_installs(library_app):
    app = library_app
    app.page = "home"
    asyncio.run(app.open_content_library_file(_request()))
    assert app.page == "content_library_precheck"
    text = text_of(app.body)
    assert "测试扩展库" in text and "新增内容 1 项" in text and f"文件名：{PACK_NAME}" in text and "安装" in _buttons(app)
    app.platform.read_library_file.assert_awaited_once_with(_request())
    assert app.store.get_setting(rules.REGISTRY_SETTING) in (None, rules.default_registry())  # nothing installed
    picked = text.replace(f"文件名：{PACK_NAME}", "")
    asyncio.run(app.add_content_library())  # the same file picked with 添加内容库: the same page
    assert text_of(app.body).replace("文件名：导入文件", "") == picked
    click(app, "安装")
    assert app.store.get_setting(rules.REGISTRY_SETTING)["libraries"][1]["library_id"] == "author.n3"


def test_a_file_that_is_not_a_pack_is_reported_with_its_name(library_app):
    app = library_app
    app.platform.read_library_file = AsyncMock(return_value=b"not a zip")
    asyncio.run(app.open_content_library_file(_request("notes.bin")))
    assert app.page == "content_libraries"
    assert app.alert.call_args[0][0] == "notes.bin：这不是 KanaMemory 的内容库文件。"
    assert "正在" not in text_of(app.body)  # the status block went before the message
    app.platform.read_library_file = AsyncMock(side_effect=bridge.PlatformError(files.FILE_URI_UNREADABLE))
    asyncio.run(app.open_content_library_file(_request("a.kmlib")))
    assert app.alert.call_args[0][0] == "a.kmlib：无法读取内容库：" + files.FILE_URI_UNREADABLE


@pytest.mark.parametrize("guard", ["backup", "practice", "working", "choosing", "confirming", "navigation", "unsaved"])
def test_while_the_app_cannot_leave_what_it_is_doing_one_notice_and_the_request_is_dropped(library_app, guard):
    app = library_app
    app.page = "settings_hub"
    if guard == "backup":
        app._backup_busy = True
    elif guard == "practice":
        app._practice_context = True
    elif guard == "working":
        library_activity.of(app).begin(library_activity.INSTALLING)
    elif guard == "choosing":
        app._content_library_request = True
    elif guard == "confirming":
        app._leave_confirmation_active = True
    elif guard == "navigation":
        app.navigation, app.root = object(), SimpleNamespace(children=[])
    else:
        app.flush_drafts = Mock(return_value=False)
    asyncio.run(app.open_content_library_file(_request()))
    app.alert.assert_called_once_with(files.BUSY_NOTICE)
    app.platform.read_library_file.assert_not_awaited()
    assert app.page == "settings_hub"


def test_cold_and_warm_requests_are_handled_once_after_start_up(library_app):
    app = library_app
    later = []
    app.loop = SimpleNamespace(call_soon=lambda callback, *args: later.append((callback, args)),
                               create_task=lambda coroutine: asyncio.run(coroutine))
    delivered = {}

    def watch(receive):
        delivered["receive"] = receive
        receive(_request(), False)  # the launching intent, read during start-up

    app.platform.watch_library_file_requests = watch
    assert app.watch_library_file_requests() is True
    app.platform.read_library_file.assert_not_awaited()  # never before the app is fully started
    callback, args = later.pop()
    callback(*args)  # the loop runs: the app is started
    assert app.page == "content_library_precheck" and app.platform.read_library_file.await_count == 1
    click(app, "取消")
    delivered["receive"](_request("second.kmlib"), True)  # warm: onNewIntent
    assert app.page == "content_library_precheck" and app.platform.read_library_file.await_count == 2
    assert "文件名：second.kmlib" in text_of(app.body)


def test_without_the_platform_hook_nothing_is_watched(library_app):
    app = library_app
    app.loop = Mock()
    assert app.watch_library_file_requests() is False
    app.platform.watch_library_file_requests = Mock(side_effect=bridge.PlatformError("x"))
    assert app.watch_library_file_requests() is False
    app.loop.call_soon.assert_not_called()


def test_the_empty_libraries_page_names_the_file_to_pick(library_app):
    app = library_app
    app.show_content_libraries()
    assert "导入内容库文件（.kmlib，旧的 .zip 也可以）后" in text_of(app.body)


# ------------------------------------------------------------------ tools


def _tool(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_packaging_tool_names_a_pack_by_app_version_edition_and_short_id(tmp_path):
    tool = _tool("package_library_pack")
    from kanamemory.build_info import VERSION
    resources = build_core_resources(tmp_path / "resources")
    out = tmp_path / "out"
    out.mkdir()
    (out / f"KanaMemory-{VERSION}.3-other.kmlib").write_bytes(b"")  # another library's editions do not count
    (out / f"KanaMemory-0.1.9-core.kmlib").write_bytes(b"")  # nor another app version's
    first = Path(tool.package(resources, out, library_version="20261120", app_min_version="0.36.0")["path"])
    assert first.name == f"KanaMemory-{VERSION}.1-core.kmlib" and first.parent == out.resolve()
    manifest = rules.validate_library_pack(first).manifest
    assert manifest["library_id"] == rules.CORE_LIBRARY_ID and manifest["library_version"] == "20261120"
    assert manifest["requires"]["app_min_version"] == "0.36.0"  # the name changes nothing inside the pack
    second = Path(tool.package(resources, out, library_version="20261121")["path"])
    assert second.name == f"KanaMemory-{VERSION}.2-core.kmlib"  # counted: one more than the highest there
    given = Path(tool.package(resources, out, library_version="20261122", edition=7)["path"])
    assert given.name == f"KanaMemory-{VERSION}.7-core.kmlib"
    assert Path(tool.package(resources, out, library_version="20261123")["path"]).name == f"KanaMemory-{VERSION}.8-core.kmlib"
    with pytest.raises(ValueError, match="new output file"):
        tool.package(resources, out, library_version="20261120", edition=7)
    short = Path(tool.package(resources, out, library_version="20261120", short="kana")["path"])
    assert short.name == f"KanaMemory-{VERSION}.1-kana.kmlib"
    explicit = tool.package(resources, tmp_path / "old-name.zip", library_version="20261120")
    assert Path(explicit["path"]).name == "old-name.zip"
    assert tool.short_id("kanamemory.core") == "core" and tool.short_id("mylibrary") == "mylibrary"
    assert tool.short_id("author.n3") == "n3"
    for edition, label in ((0, "core"), ("2", "core"), (1, "a/b")):
        with pytest.raises(ValueError):
            tool.pack_file_name(VERSION, edition, label)


def test_the_signing_tool_writes_kmlib_into_a_directory_and_keeps_an_explicit_zip_name(tmp_path):
    tool = _tool("sign_library_pack")
    source = tmp_path / "author.n3.zip"
    source.write_bytes(pack_bytes("author.n3", extension_namespaces()))
    seed = bytes(range(32))
    out = tmp_path / "signed"
    out.mkdir()
    assert tool.sign_pack(source, out, seed, public_key_id="author:a")["signature_status"] == "untrusted_key"
    assert [p.name for p in out.iterdir()] == ["author.n3.kmlib"]
    assert tool.verify_pack(out / "author.n3.kmlib")["library_id"] == "author.n3"  # read by content, whatever the name
    tool.sign_pack(out / "author.n3.kmlib", tmp_path / "signed.zip", seed, public_key_id="author:a")
    assert (tmp_path / "signed.zip").is_file()


# ------------------------------------------------------------------ build configuration


def library_filters(fragment):
    """The intent filters of an activity extra-content fragment, as (action, categories, data) tuples."""
    root = ET.fromstring(f'<activity xmlns:android="{ANDROID[1:-1]}">{fragment}</activity>')
    result = []
    for node in root.findall("intent-filter"):
        actions = tuple(a.get(ANDROID + "name") for a in node.findall("action"))
        categories = frozenset(c.get(ANDROID + "name") for c in node.findall("category"))
        data = tuple(sorted((key.replace(ANDROID, ""), value) for d in node.findall("data") for key, value in d.items()))
        result.append((actions, categories, data))
    return result


def activity_extra_content():
    config = tomllib.loads((ROOT / "app/pyproject.toml").read_text("utf8"))
    return config["tool"]["briefcase"]["app"]["kanamemory"]["android"]["android_manifest_activity_extra_content"]


def test_the_build_configuration_associates_kmlib_files_with_the_main_activity():
    filters = library_filters(activity_extra_content())
    assert len(filters) == 4
    for actions, categories, data in filters:
        assert actions == ("android.intent.action.VIEW",)
        assert categories == {"android.intent.category.DEFAULT", "android.intent.category.BROWSABLE"}
        assert {value for key, value in data if key == "scheme"} == {"content", "file"}
        assert "application/zip" not in {value for key, value in data if key == "mimeType"}
    types = [{value for key, value in data if key == "mimeType"} for _, _, data in filters]
    assert types == [{files.MIME_TYPE}, {files.OCTET_STREAM}, {"*/*"}, set()]
    for _, _, data in filters[2:]:
        assert ("host", "*") in data and ("pathSuffix", files.EXTENSION) in data
        patterns = [value for key, value in data if key == "pathPattern"]
        # Android's pathPattern does not backtrack: one pattern per number of dots in the path.
        assert sorted(pattern.count("\\\\.") for pattern in patterns) == list(range(1, 11))
        assert all(pattern.startswith(".*") and pattern.endswith("\\\\.kmlib") for pattern in patterns)
    for _, _, data in filters[:2]:
        assert not any(key in {"host", "pathSuffix", "pathPattern"} for key, _ in data)
    assert "application/zip" not in activity_extra_content()


def test_the_manifest_patch_carries_the_same_filters_word_for_word():
    runner = (ROOT / "environment/run_app.ps1").read_text("utf8")
    start = runner.index("$libraryFilters = @'\n") + len("$libraryFilters = @'\n")
    patch = runner[start:runner.index("\n'@", start)]
    assert patch.strip() == activity_extra_content().strip()
    assert library_filters(patch) == library_filters(activity_extra_content())
