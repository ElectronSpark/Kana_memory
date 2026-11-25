package org.example.kanamemory;

import android.content.ContentResolver;
import androidx.activity.result.ActivityResultLauncher;
import androidx.activity.result.contract.ActivityResultContracts;
import androidx.appcompat.app.AppCompatActivity;
import androidx.lifecycle.Lifecycle;
import androidx.lifecycle.LifecycleEventObserver;
import androidx.lifecycle.LifecycleOwner;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.UUID;

/** One explicit Storage Access Framework operation. No storage permissions. */
public final class KanaBackupDocuments implements LifecycleEventObserver {
    public interface Events {
        void onComplete(String json, boolean cancelled, String error);
    }
    private static final int MAX_BYTES = 64 * 1024 * 1024;
    // The most an import may carry: a content library pack (content_library_rules.MAX_PACK_BYTES).
    // Each import's own limit (64 MiB for backups and feedback packs) is applied where the blocks are received.
    private static final long MAX_IMPORT_BYTES = 512L * 1024 * 1024;
    private static final int BLOCK_BYTES = 64 * 1024;
    private static final String IMPORT_READY = "kana-backup-import-stream-v1";
    private static final String EXPORT_READY = "kana-backup-export-stream-v1";
    private volatile AppCompatActivity activity;
    private Events events;
    private ActivityResultLauncher<String> creator;
    private ActivityResultLauncher<String[]> opener;
    private boolean finished;
    private boolean started;
    private final Object streamLock = new Object();
    private volatile boolean cancelled;
    private InputStream importInput;
    private OutputStream exportOutput;
    private long streamBytes;
    private boolean streamReading, streamWriting, importEnded;
    // The provider's display name of the document being read (OpenableColumns.DISPLAY_NAME), "" when none.
    private volatile String displayName = "";

    public KanaBackupDocuments(AppCompatActivity activity, Events events) {
        this.activity = activity;
        this.events = events;
        activity.getLifecycle().addObserver(this);
    }

    public void exportJson(String json, String filename) {
        exportBytes(json.getBytes(StandardCharsets.UTF_8), filename, "application/json");
    }

    public void exportZipBase64(String encoded, String filename) {
        if (encoded.length() > ((MAX_BYTES + 2L) / 3L) * 4L)
            throw new IllegalArgumentException("反馈包超过 64 MiB，请分批导出。");
        exportBytes(android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP), filename, "application/zip");
    }

    private void exportBytes(final byte[] bytes, String filename, String mimeType) {
        if (started || finished) throw new IllegalStateException("Document request already used");
        started = true;
        if (bytes.length > MAX_BYTES) {
            complete(null, false, "备份超过 64 MiB 限制。");
            return;
        }
        final ContentResolver resolver = activity.getApplicationContext().getContentResolver();
        creator = activity.getActivityResultRegistry().register("kana-export-" + UUID.randomUUID(),
                new ActivityResultContracts.CreateDocument(mimeType), uri -> {
            if (cancelled) return;
            if (uri == null) { complete(null, true, null); return; }
            new Thread(() -> {
                if (cancelled) return;
                String error = null;
                try (OutputStream output = resolver.openOutputStream(uri, "wt")) {
                    if (output == null) throw new java.io.IOException("无法打开目标文件。");
                    output.write(bytes);
                    output.flush();
                } catch (Exception exception) {
                    error = "无法保存备份：" + exception.getMessage();
                }
                finishFromWorker(null, error);
            }, "KanaBackupExport").start();
        });
        try { creator.launch(filename); }
        catch (RuntimeException error) { complete(null, false, "无法打开保存文件窗口：" + error.getMessage()); }
    }

    public void importJson() {
        importDocument("application/json\napplication/octet-stream\napplication/zip\ntext/plain");
    }

    /** The picker for one import, offering the given MIME types (one per line). */
    public void importDocument(String mimeTypes) {
        if (started || finished) throw new IllegalStateException("Document request already used");
        started = true;
        final String[] types = mimeTypes == null ? new String[0] : mimeTypes.trim().split("\n");
        if (types.length == 0 || types[0].isEmpty()) throw new IllegalArgumentException("No document type");
        final ContentResolver resolver = activity.getApplicationContext().getContentResolver();
        opener = activity.getActivityResultRegistry().register("kana-import-" + UUID.randomUUID(),
                new ActivityResultContracts.OpenDocument(), uri -> {
            if (cancelled) return;
            if (uri == null) { complete(null, true, null); return; }
            new Thread(() -> openImport(resolver, uri, "无法读取备份："), "KanaBackupImport").start();
        });
        try { opener.launch(types); }
        catch (RuntimeException error) { complete(null, false, "无法打开选择文件窗口：" + error.getMessage()); }
    }

    /**
     * A document the app was asked to open (ACTION_VIEW, content or file URI): the same bounded
     * stream as a picked document, read through the content resolver with the permission the
     * sender granted. A file URI the app may not read fails here; nothing bypasses the resolver.
     */
    public void importUri(String uriText) {
        if (started || finished) throw new IllegalStateException("Document request already used");
        started = true;
        final android.net.Uri uri = uriText == null ? null : android.net.Uri.parse(uriText);
        final String scheme = uri == null ? null : uri.getScheme();
        if (!"content".equals(scheme) && !"file".equals(scheme)) {
            complete(null, false, "无法读取这个文件。");
            return;
        }
        final ContentResolver resolver = activity.getApplicationContext().getContentResolver();
        new Thread(() -> openImport(resolver, uri, "无法读取文件："), "KanaDocumentOpen").start();
    }

    private void openImport(ContentResolver resolver, android.net.Uri uri, String failure) {
        if (cancelled) return;
        String error = null;
        InputStream input = null;
        try {
            input = resolver.openInputStream(uri);
            if (input == null) throw new java.io.IOException("无法读取选中的文件。");
            synchronized (streamLock) {
                if (cancelled) throw new IOException("文件读取已取消。");
                importInput = input;
            }
        } catch (Exception exception) {
            closeQuietly(input);
            error = failure + exception.getMessage();
        }
        if (error == null) displayName = queryDisplayName(resolver, uri);
        finishFromWorker(error == null ? IMPORT_READY : null, error);
    }

    /** The provider's display name of a document, "" when it gives none; never fails the read. */
    private static String queryDisplayName(ContentResolver resolver, android.net.Uri uri) {
        if (!"content".equals(uri.getScheme())) return "";
        try (android.database.Cursor cursor = resolver.query(uri,
                new String[] { android.provider.OpenableColumns.DISPLAY_NAME }, null, null, null)) {
            if (cursor != null && cursor.moveToFirst()) {
                String name = cursor.getString(0);
                return name == null ? "" : name;
            }
        } catch (Exception ignored) { }
        return "";
    }

    /** The name of the document being read (the picker's choice or an opened file), "" when unknown. */
    public String displayName() { return displayName; }

    /** Pull exactly one bounded block. No external file path crosses the bridge. */
    public String readImportBlock() throws IOException {
        final InputStream input;
        synchronized (streamLock) {
            if (cancelled) throw new IOException("文件读取已取消。");
            if (importEnded) return "";
            if (importInput == null || streamReading) throw new IOException("文件读取状态无效。");
            input = importInput;
            streamReading = true;
        }
        try {
            byte[] block = new byte[BLOCK_BYTES];
            int count;
            do { count = input.read(block); } while (count == 0 && !cancelled);
            synchronized (streamLock) {
                if (cancelled || input != importInput) throw new IOException("文件读取已取消。");
                if (count == -1) {
                    importEnded = true;
                    importInput = null;
                } else {
                    if (streamBytes + count > MAX_IMPORT_BYTES) throw new IOException("文件超过 512 MiB 限制。");
                    streamBytes += count;
                }
            }
            if (count == -1) { closeQuietly(input); return ""; }
            return android.util.Base64.encodeToString(count == block.length ? block : Arrays.copyOf(block, count),
                    android.util.Base64.NO_WRAP);
        } finally {
            synchronized (streamLock) { streamReading = false; }
        }
    }

    /** Python writes bounded blocks after the SAF picker, then calls finishExport. */
    public void exportStream(String filename, String mimeType) {
        if (started || finished) throw new IllegalStateException("Document request already used");
        if (!"application/json".equals(mimeType) && !"application/zip".equals(mimeType))
            throw new IllegalArgumentException("Unsupported document type");
        started = true;
        final ContentResolver resolver = activity.getApplicationContext().getContentResolver();
        creator = activity.getActivityResultRegistry().register("kana-export-" + UUID.randomUUID(),
                new ActivityResultContracts.CreateDocument(mimeType), uri -> {
            if (cancelled) return;
            if (uri == null) { complete(null, true, null); return; }
            new Thread(() -> {
                if (cancelled) return;
                String error = null;
                OutputStream output = null;
                try {
                    output = resolver.openOutputStream(uri, "wt");
                    if (output == null) throw new IOException("无法打开目标文件。");
                    synchronized (streamLock) {
                        if (cancelled) throw new IOException("文件保存已取消。");
                        exportOutput = output;
                    }
                } catch (Exception exception) {
                    closeQuietly(output);
                    error = "无法保存备份：" + exception.getMessage();
                }
                finishFromWorker(error == null ? EXPORT_READY : null, error);
            }, "KanaBackupExport").start();
        });
        try { creator.launch(filename); }
        catch (RuntimeException error) { complete(null, false, "无法打开保存文件窗口：" + error.getMessage()); }
    }

    public void writeExportBlock(String encoded) throws IOException {
        if (encoded == null || encoded.length() > ((BLOCK_BYTES + 2L) / 3L) * 4L)
            throw new IOException("文件传输块超过限制。");
        final byte[] block = android.util.Base64.decode(encoded, android.util.Base64.NO_WRAP);
        if (block.length == 0 || block.length > BLOCK_BYTES) throw new IOException("文件传输块无效。");
        final OutputStream output;
        synchronized (streamLock) {
            if (cancelled || exportOutput == null || streamWriting) throw new IOException("文件保存状态无效。");
            if (streamBytes + block.length > MAX_BYTES) throw new IOException("文件超过 64 MiB 限制。");
            output = exportOutput;
            streamWriting = true;
        }
        try {
            output.write(block);
            synchronized (streamLock) {
                if (cancelled || output != exportOutput) throw new IOException("文件保存已取消。");
                streamBytes += block.length;
            }
        } finally {
            synchronized (streamLock) { streamWriting = false; }
        }
    }

    public void finishExport() throws IOException {
        final OutputStream output;
        synchronized (streamLock) {
            if (cancelled || exportOutput == null || streamWriting) throw new IOException("文件保存状态无效。");
            output = exportOutput;
            streamWriting = true;
        }
        try {
            output.flush();
            output.close();
            synchronized (streamLock) {
                if (cancelled || output != exportOutput) throw new IOException("文件保存已取消。");
                exportOutput = null;
            }
        } finally {
            synchronized (streamLock) { streamWriting = false; }
        }
    }

    private static void closeQuietly(java.io.Closeable stream) {
        if (stream != null) try { stream.close(); } catch (IOException ignored) { }
    }

    private void finishFromWorker(final String json, final String error) {
        AppCompatActivity current = activity;
        if (current != null) current.runOnUiThread(() -> complete(json, false, error));
    }

    private void complete(String json, boolean cancelled, String error) {
        if (finished) return;
        finished = true;
        Events callback = events;
        events = null;
        if (creator != null) creator.unregister();
        if (opener != null) opener.unregister();
        creator = null;
        opener = null;
        // A readiness callback still owns an open provider stream. Keep the
        // lifecycle observer until Python releases the request in its finally.
        if (callback != null) callback.onComplete(json, cancelled, error);
    }

    public void cancel() {
        cancelled = true;
        final InputStream input;
        final OutputStream output;
        synchronized (streamLock) {
            input = importInput; importInput = null;
            output = exportOutput; exportOutput = null;
        }
        // Do not hold streamLock while read/write or close may block.
        closeQuietly(input);
        closeQuietly(output);
        complete(null, true, null);
        AppCompatActivity current = activity;
        if (current != null) current.getLifecycle().removeObserver(this);
        activity = null;
    }

    public boolean isCancelled() { return cancelled; }

    @Override public void onStateChanged(LifecycleOwner owner, Lifecycle.Event event) {
        if (event == Lifecycle.Event.ON_DESTROY) cancel();
    }
}
