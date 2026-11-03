package org.example.kanamemory;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.widget.Toast;
import java.nio.charset.StandardCharsets;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;

/** Only text/plain. No URI, attachment, HTML, permission grant or model call. */
public final class KanaTextShareActivity extends Activity {
    private static final Object LOCK = new Object();
    private String receivedId;
    private static SharedPreferences prefs(Context context) {
        return context.getApplicationContext().getSharedPreferences("kanamemory_text_share_v1", Context.MODE_PRIVATE);
    }
    private static JSONArray pending(Context context) throws Exception {
        String text = prefs(context).getString("pending", "[]");
        if (text == null || text.length() > 300000) throw new IllegalStateException("Invalid share inbox");
        JSONArray rows = new JSONArray(text);
        if (rows.length() > 16) throw new IllegalStateException("Invalid share inbox");
        return rows;
    }
    private static void save(Context context, JSONArray rows) {
        if (!prefs(context).edit().putString("pending", rows.toString()).commit())
            throw new IllegalStateException("Could not persist share");
    }
    private static String receive(Context context, String text, String identity) throws Exception {
        if (text.trim().isEmpty() || text.length() > 8000 || text.codePointCount(0, text.length()) > 4000
                || text.getBytes(StandardCharsets.UTF_8).length > 16000)
            throw new IllegalArgumentException("Invalid share size");
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (c < 32 && c != '\n' && c != '\r' && c != '\t') throw new IllegalArgumentException("Invalid character");
            if (Character.isHighSurrogate(c)) {
                if (++i >= text.length() || !Character.isLowSurrogate(text.charAt(i)))
                    throw new IllegalArgumentException("Invalid character");
            } else if (Character.isLowSurrogate(c)) throw new IllegalArgumentException("Invalid character");
        }
        synchronized (LOCK) {
            JSONArray rows = pending(context);
            for (int i = 0; i < rows.length(); i++) {
                JSONObject row = rows.getJSONObject(i);
                if (row.getString("text").equals(text)) return row.getString("id");
            }
            if (rows.length() >= 16) throw new IllegalStateException("Share inbox full");
            JSONObject row = new JSONObject();
            row.put("id", identity); row.put("text", text); row.put("received_ms", System.currentTimeMillis());
            rows.put(row); save(context, rows); return identity;
        }
    }
    /** Python acknowledges only after its database commit. */
    public static String peekPending(Context context) throws Exception {
        synchronized (LOCK) { return pending(context).toString(); }
    }
    public static void acknowledge(Context context, String identity) throws Exception {
        if (identity == null || !identity.matches("[a-f0-9]{32}")) throw new IllegalArgumentException("Invalid share identity");
        synchronized (LOCK) {
            JSONArray old = pending(context), kept = new JSONArray();
            for (int i = 0; i < old.length(); i++) {
                JSONObject row = old.getJSONObject(i);
                if (!identity.equals(row.getString("id"))) kept.put(row);
            }
            if (kept.length() != old.length()) save(context, kept);
        }
    }
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        try {
            receivedId = state == null ? null : state.getString("received_id");
            if (receivedId == null) {
                Intent incoming = getIntent();
                if (incoming == null || !Intent.ACTION_SEND.equals(incoming.getAction()) || !"text/plain".equals(incoming.getType()))
                    throw new IllegalArgumentException("Only plain text is supported");
                CharSequence raw = incoming.getCharSequenceExtra(Intent.EXTRA_TEXT);
                if (raw == null || raw.length() > 8000) throw new IllegalArgumentException("Invalid share size");
                receivedId = receive(this, raw.toString(), UUID.randomUUID().toString().replace("-", ""));
            }
            Toast.makeText(this, "已保存到 AI 中心的分享文本，尚未发送给模型。", Toast.LENGTH_LONG).show();
            Intent launch = new Intent(Intent.ACTION_MAIN).setClassName(getPackageName(), "org.beeware.android.MainActivity");
            launch.addCategory(Intent.CATEGORY_LAUNCHER);
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(launch);
        } catch (Exception error) {
            // Do not reveal incoming text, file names or raw exception details.
            Toast.makeText(this, "未能接收分享。请确认是 4000 字以内的纯文本，并处理已有收件。", Toast.LENGTH_LONG).show();
        } finally { finish(); }
    }
    @Override public void onSaveInstanceState(Bundle state) {
        if (receivedId != null) state.putString("received_id", receivedId);
        super.onSaveInstanceState(state);
    }
}
