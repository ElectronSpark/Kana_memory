import asyncio
import base64
from pathlib import Path
from types import SimpleNamespace
import threading

import pytest
from support.tts_setup_support import tts_environment
from kanamemory import platform_android as bridge


@pytest.fixture
def documents(tts_environment, tmp_path):
    platform, host = tts_environment
    host.documents = []
    platform._context.getCacheDir = lambda: SimpleNamespace(getCanonicalPath=lambda: str(tmp_path))
    class Document:
        def __init__(self, activity, events):
            self.events, self.cancelled, self.exported, self.imported = events, False, None, False
            self.blocks, self.output, self.finished = iter(()), bytearray(), False
            host.documents.append(self)
        def exportStream(self, name, mime): self.exported = name, mime
        def importJson(self): self.imported = True
        def readImportBlock(self): return next(self.blocks, '')
        def writeExportBlock(self, block): self.output.extend(base64.b64decode(block))
        def finishExport(self): self.finished = True
        def isCancelled(self): return self.cancelled
        def cancel(self):
            self.cancelled = True
            self.events.onComplete(None, True, None)
    original = platform._jclass
    classes = {'org.example.kanamemory.KanaBackupDocuments':Document,
        'org.example.kanamemory.KanaBackupDocuments$Events':object,
        'org.beeware.android.MainActivity':SimpleNamespace(singletonThis=object())}
    platform._jclass = lambda name: classes[name] if name in classes else original(name)
    return platform, host, tmp_path


@pytest.mark.parametrize('archive', [False, True])
def test_export_returns_only_after_real_flush_and_preserves_bytes(documents, archive):
    platform, host, _ = documents
    data = b'\xffZIP\x00' * 20000 if archive else ('{"例":"假名"}'*20000).encode()
    async def run():
        task = asyncio.create_task(platform.export_feedback(data,'x.zip') if archive else platform.export_backup(data,'x.json'))
        await asyncio.sleep(0)
        doc = host.documents[-1]
        assert not task.done() and not doc.finished
        assert doc.exported[1] == ('application/zip' if archive else 'application/json')
        doc.events.onComplete(bridge._BACKUP_EXPORT_READY, False, None)
        assert await task is True
        assert bytes(doc.output) == data and doc.finished and doc.cancelled
    asyncio.run(run())


def test_import_split_utf8_keeps_original_bytes_and_no_cache_residue(documents):
    platform, host, folder = documents
    data = b'\xef\xbb\xbf'+('仮名\n'*50000).encode()
    async def run():
        task = asyncio.create_task(platform.import_backup()); await asyncio.sleep(0)
        doc = host.documents[-1]
        doc.blocks = iter(base64.b64encode(data[i:i+65535]).decode() for i in range(0,len(data),65535))
        doc.events.onComplete(bridge._BACKUP_IMPORT_READY, False, None)
        assert await task == data
        assert doc.cancelled and not list(folder.iterdir())
    asyncio.run(run())


@pytest.mark.parametrize('outcome', ['picker_cancel','error','wrong_marker'])
def test_cancel_error_marker_and_late_callbacks_do_not_import(documents,outcome):
    platform, host, folder = documents
    async def run():
        task=asyncio.create_task(platform.import_backup()); await asyncio.sleep(0)
        doc=host.documents[-1]
        doc.events.onComplete('wrong',outcome=='picker_cancel','读取失败' if outcome=='error' else None)
        doc.events.onComplete(bridge._BACKUP_IMPORT_READY,False,None)
        if outcome=='picker_cancel': assert await task is None
        else:
            with pytest.raises(bridge.PlatformError): await task
        assert doc.cancelled and not list(folder.iterdir())
    asyncio.run(run())


@pytest.mark.parametrize('blocks,limit,error', [
    (['!!!!'],100,ValueError), ([base64.b64encode(b'\xff').decode()],100,UnicodeError),
    ([base64.b64encode(b'abcdef').decode()],5,bridge.PlatformError),
    (['A'*87385],100000,bridge.PlatformError)])
def test_reject_corrupt_oversized_transfer_and_cleanup(documents,monkeypatch,blocks,limit,error):
    platform, host, folder=documents
    # The cap reaches the reader as _backup_document's keyword default (5c55c38).
    defaults=type(platform)._backup_document.__kwdefaults__
    assert defaults['max_bytes']==bridge._BACKUP_MAX_BYTES
    monkeypatch.setitem(defaults,'max_bytes',limit)
    async def run():
        task=asyncio.create_task(platform.import_backup());await asyncio.sleep(0)
        doc=host.documents[-1];doc.blocks=iter(blocks)
        doc.events.onComplete(bridge._BACKUP_IMPORT_READY,False,None)
        with pytest.raises(error):await task
        assert doc.cancelled and not list(folder.iterdir())
    asyncio.run(run())


def test_cancel_while_worker_reading_closes_handle_waits_worker_and_cleans_temp(documents):
    platform, host, folder=documents
    started,closed,ended=threading.Event(),threading.Event(),threading.Event()
    async def run():
        task=asyncio.create_task(platform.import_backup());await asyncio.sleep(0)
        doc=host.documents[-1]
        old_cancel=doc.cancel
        def read():
            started.set()
            if not closed.wait(5): raise AssertionError('cancel did not close handle')
            ended.set();raise OSError('closed')
        def cancel(): closed.set();old_cancel()
        doc.readImportBlock,doc.cancel=read,cancel
        doc.events.onComplete(bridge._BACKUP_IMPORT_READY,False,None)
        assert await asyncio.to_thread(started.wait,5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert ended.is_set() and doc.cancelled and not list(folder.iterdir())
    asyncio.run(run())


def test_repeated_task_cancel_joins_worker_before_final_release_on_ui_thread(documents):
    platform,host,folder=documents
    started,closed,release,ended=(threading.Event() for _ in range(4))
    ui_thread=threading.get_ident()
    async def run():
        task=asyncio.create_task(platform.import_backup());await asyncio.sleep(0)
        doc=host.documents[-1];old_cancel=doc.cancel
        def read():
            started.set();assert closed.wait(5);assert release.wait(5)
            ended.set();raise OSError('closed')
        def cancel():
            assert threading.get_ident()==ui_thread
            closed.set();old_cancel()
        doc.readImportBlock,doc.cancel=read,cancel
        doc.events.onComplete(bridge._BACKUP_IMPORT_READY,False,None)
        assert await asyncio.to_thread(started.wait,5)
        task.cancel();await asyncio.sleep(0)
        task.cancel();await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):await task
        assert ended.is_set() and doc.cancelled and not list(folder.iterdir())
    asyncio.run(run())


def test_invalid_export_rejected_before_picker(documents,monkeypatch):
    platform,host,_=documents
    monkeypatch.setattr(bridge,'_BACKUP_MAX_BYTES',10)
    async def run():
        for data,error in [(b'x'*11,bridge.PlatformError),(b'\xff',UnicodeError)]:
            with pytest.raises(error):await platform.export_backup(data)
        assert host.documents==[]
    asyncio.run(run())


def test_duplicate_request_preserves_first_picker(documents):
    platform,host,_=documents
    async def run():
        task=asyncio.create_task(platform.import_backup());await asyncio.sleep(0)
        with pytest.raises(bridge.PlatformError):await platform.import_backup()
        doc=host.documents[-1];doc.events.onComplete(None,True,None)
        assert await task is None and len(host.documents)==1
    asyncio.run(run())


@pytest.mark.parametrize('operation',['import','export'])
def test_lifecycle_cancel_after_final_block_before_worker_returns_does_not_deliver_success(documents,monkeypatch,operation):
    platform,host,folder=documents
    completed,release=threading.Event(),threading.Event()
    name='_read_backup_stream' if operation=='import' else '_write_backup_stream'
    original=getattr(bridge,name)
    def paused(*args):
        value=original(*args)
        completed.set();assert release.wait(5)
        return value
    monkeypatch.setattr(bridge,name,paused)
    async def run():
        task=asyncio.create_task(platform.import_backup() if operation=='import' else platform.export_backup(b'{}'))
        await asyncio.sleep(0)
        doc=host.documents[-1]
        doc.blocks=iter([base64.b64encode(b'{}').decode()])
        marker=bridge._BACKUP_IMPORT_READY if operation=='import' else bridge._BACKUP_EXPORT_READY
        doc.events.onComplete(marker,False,None)
        assert await asyncio.to_thread(completed.wait,5)
        doc.cancel()  # ON_DESTROY, after readiness and final EOF/flush, not task.cancel.
        release.set()
        value=await task
        assert value is (None if operation=='import' else False)
        assert not list(folder.iterdir()) and platform._backup_native is None
    asyncio.run(run())
