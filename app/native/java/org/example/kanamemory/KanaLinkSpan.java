package org.example.kanamemory;

import android.text.style.ClickableSpan;
import android.view.View;

/** A content-only text link. No Activity, TextView, or Python widget is retained. */
public final class KanaLinkSpan extends ClickableSpan {
    public interface Handler {
        void openKana(String kanaId);
    }

    private final String kanaId;
    private final Handler handler;

    public KanaLinkSpan(String kanaId, Handler handler) {
        this.kanaId = kanaId;
        this.handler = handler;
    }

    @Override
    public void onClick(View widget) {
        handler.openKana(kanaId);
    }
    // ClickableSpan's default draw state uses the theme link color and underline.
    // Standard TextView exposes these spans to accessibility services on API 26+.
}
