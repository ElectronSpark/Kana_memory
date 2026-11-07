package org.example.kanamemory;

import android.app.Activity;
import android.view.View;

import androidx.core.view.ViewCompat;
import androidx.core.view.WindowInsetsCompat;

/**
 * Keeps the app content above the soft keyboard.
 *
 * Target SDK 35+ forces edge-to-edge, so adjustResize no longer shrinks the
 * window, and the Briefcase MainActivity only reserves the system bars. Its
 * listener stays on the content view; this one pads that view's parent by the
 * part of the keyboard the system bar margin does not already cover.
 */
public final class KanaKeyboardInsets {
    private KanaKeyboardInsets() {
    }

    public static void install(Activity activity) {
        View content = activity.findViewById(android.R.id.content);
        View parent = (View) content.getParent();
        ViewCompat.setOnApplyWindowInsetsListener(parent, (view, insets) -> {
            int keyboard = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom;
            int bars = insets.getInsets(WindowInsetsCompat.Type.systemBars()
                    | WindowInsetsCompat.Type.displayCutout()).bottom;
            int padding = Math.max(0, keyboard - bars);
            if (view.getPaddingBottom() != padding) {
                view.setPadding(view.getPaddingLeft(), view.getPaddingTop(), view.getPaddingRight(), padding);
            }
            // Not consumed: MainActivity's listener still needs these insets.
            return insets;
        });
        ViewCompat.requestApplyInsets(parent);
    }
}
