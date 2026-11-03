package org.example.kanamemory;

import android.util.Log;
import android.view.View;

import androidx.activity.OnBackPressedCallback;
import androidx.activity.OnBackPressedDispatcher;
import androidx.appcompat.app.AppCompatActivity;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.lifecycle.Lifecycle;
import androidx.lifecycle.LifecycleEventObserver;
import androidx.lifecycle.LifecycleOwner;

/** One lifecycle-bound adapter; Python owns the application's page history. */
public final class KanaBackHandler extends OnBackPressedCallback implements LifecycleEventObserver {
    public interface Handler {
        boolean onBack();
    }

    private AppCompatActivity activity;
    private OnBackPressedDispatcher dispatcher;
    private Handler handler;
    private boolean removed;
    private boolean pageBackEnabled;

    public KanaBackHandler(AppCompatActivity activity, Handler handler) {
        // Python enables this after registration, including at the root page:
        // the app deliberately consumes Back there without leaving the app.
        super(false);
        this.activity = activity;
        this.dispatcher = activity.getOnBackPressedDispatcher();
        this.handler = handler;
        dispatcher.addCallback(activity, this);
        activity.getLifecycle().addObserver(this);
    }

    @Override
    public void handleOnBackPressed() {
        AppCompatActivity currentActivity = activity;
        OnBackPressedDispatcher currentDispatcher = dispatcher;
        Handler currentHandler = handler;
        if (removed || currentActivity == null || currentDispatcher == null || currentHandler == null) {
            return;
        }
        // Normally Android routes Back to the focused IME/dialog window first.
        // Also guard the Activity path: hiding an IME must not pop a Python page.
        View decor = currentActivity.getWindow().getDecorView();
        WindowInsetsCompat insets = ViewCompat.getRootWindowInsets(decor);
        if (insets != null && insets.isVisible(WindowInsetsCompat.Type.ime())) {
            WindowCompat.getInsetsController(currentActivity.getWindow(), decor)
                    .hide(WindowInsetsCompat.Type.ime());
            return;
        }

        final boolean handled;
        try {
            handled = currentHandler.onBack();
        } catch (RuntimeException error) {
            // A page-navigation bug should not unexpectedly close the app.
            Log.e("KanaMemoryBack", "Page back handler failed", error);
            return;
        }
        if (handled) {
            return;
        }

        // Explicit opt-out for a different host policy. KanaMemory's root
        // returns true; this fallback is not used to exit its home page.
        setEnabled(false);
        try {
            currentDispatcher.onBackPressed();
        } finally {
            if (!removed && !currentActivity.isDestroyed() && !currentActivity.isFinishing()) {
                setEnabled(pageBackEnabled);
            }
        }
    }

    public void setPageBackEnabled(boolean enabled) {
        pageBackEnabled = enabled;
        setEnabled(!removed && enabled);
    }

    @Override
    public void onStateChanged(LifecycleOwner source, Lifecycle.Event event) {
        if (event == Lifecycle.Event.ON_DESTROY) {
            unregister();
        }
    }

    public void unregister() {
        if (removed) {
            return;
        }
        removed = true;
        pageBackEnabled = false;
        setEnabled(false);
        remove();
        if (activity != null) {
            activity.getLifecycle().removeObserver(this);
        }
        // Toga does not call Python on_exit when Android destroys the Activity.
        // Release the Python proxy even when Python still retains this bridge.
        handler = null;
        dispatcher = null;
        activity = null;
    }
}
