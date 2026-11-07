"""Native SAF results resolve once; cancelled files never become imports."""
import asyncio
import base64
from types import SimpleNamespace

import pytest

from support.tts_setup_support import tts_environment
from kanamemory.platform_android import PlatformError


@pytest.fixture
def documents(tts_environment, tmp_path):
    platform, host = tts_environment
    host.documents = []
    platform._context.getCacheDir = lambda: SimpleNamespace(getCanonicalPath=lambda: str(tmp_path))

    class Document:
        def __init__(self, activity, events):
            self.events, self.cancelled, self.exported, self.imported = events, False, None, False
            self.payload, self.output, self.finished = b'', bytearray(), False
            host.documents.append(self)

        def exportStream(self, filename, mime): self.exported = (filename, mime)
        def writeExportBlock(self, encoded): self.output.extend(base64.b64decode(encoded))
        def finishExport(self): self.finished = True
        def isCancelled(self): return self.cancelled
        def readImportBlock(self):
            value, self.payload = self.payload, b''
            return base64.b64encode(value).decode()
        def importJson(self): self.imported = True
        def cancel(self):
            self.cancelled = True
            self.events.onComplete(None, True, None)

    original = platform._jclass
    classes = {"org.example.kanamemory.KanaBackupDocuments": Document,
               "org.example.kanamemory.KanaBackupDocuments$Events": object,
               "org.beeware.android.MainActivity": SimpleNamespace(singletonThis=object())}
    platform._jclass = lambda name: classes[name] if name in classes else original(name)
    return platform, host


def test_export_waits_for_real_document_write_completion(documents):
    platform, host = documents
    async def run():
        task = asyncio.create_task(platform.export_backup('{"name":"假名"}'.encode(), "progress.json"))
        await asyncio.sleep(0)
        document = host.documents[-1]
        assert document.exported == ("progress.json", "application/json") and not task.done()
        assert not document.finished
        document.events.onComplete('kana-backup-export-stream-v1', False, None)
        assert await task is True
        assert bytes(document.output) == '{"name":"假名"}'.encode() and document.finished
        assert document.cancelled and platform._backup_native is None
    asyncio.run(run())


@pytest.mark.parametrize("cancelled", [False, True])
def test_import_returns_utf8_or_none_for_cancel(documents, cancelled):
    platform, host = documents
    async def run():
        task = asyncio.create_task(platform.import_backup())
        await asyncio.sleep(0)
        assert host.documents[-1].imported
        host.documents[-1].payload = '{"name":"假名"}'.encode()
        host.documents[-1].events.onComplete('kana-backup-import-stream-v1', cancelled, None)
        assert await task == (None if cancelled else '{"name":"假名"}'.encode())
    asyncio.run(run())


def test_errors_and_duplicate_requests_do_not_resolve_as_success(documents):
    platform, host = documents
    async def run():
        task = asyncio.create_task(platform.import_backup())
        await asyncio.sleep(0)
        with pytest.raises(PlatformError, match="当前"):
            await platform.export_backup(b"{}")
        host.documents[-1].events.onComplete(None, False, "文件读取失败")
        with pytest.raises(PlatformError, match="读取失败"):
            await task
        assert platform._backup_native is None
    asyncio.run(run())


def test_cancellation_releases_native_request(documents):
    platform, host = documents
    async def run():
        task = asyncio.create_task(platform.import_backup())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert host.documents[-1].cancelled and platform._backup_native is None
    asyncio.run(run())


def test_voice_inventory_cached_until_explicit_refresh_but_actual_voice_verified(tts_environment, monkeypatch):
    platform, host = tts_environment
    from support.tts_setup_support import Voice
    from unittest.mock import Mock
    from kanamemory import platform_android
    host.voices = [Voice("ja-original"), Voice("en-extra", language="en")]
    async def run():
        await platform.prepare_tts()
        original = platform._tts.getVoices
        platform._tts.getVoices = Mock(wraps=original)
        describe = Mock(wraps=platform_android.describe_voice)
        monkeypatch.setattr(platform_android, "describe_voice", describe)
        platform._select_tts_voice("ja")
        platform._select_tts_voice("ja")
        assert platform._tts.getVoices.call_count == 2
        assert len(describe.call_args_list) == 4  # Requested installed and actual voices.
        assert all(call.args[0].name == "ja-original" for call in describe.call_args_list)
        # Cached inventory never bypasses checking the actual selected voice.
        host.selected_override = Voice("ja-original", network=True)
        with pytest.raises(PlatformError, match="离线"):
            platform._select_tts_voice("ja")
        host.selected_override = None
        host.voices = [Voice("ja-reinstalled")]
        await platform.prepare_tts()
        assert platform.tts_voice == "ja-reinstalled"
    asyncio.run(run())
