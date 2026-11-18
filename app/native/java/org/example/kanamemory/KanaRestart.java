package org.example.kanamemory;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.util.Log;

/**
 * Restart the app's process: content libraries take effect only in a new process.
 *
 * Starts the package's launcher activity as a new, cleared task and ends this process. The
 * activity manager records the start before the process exits and creates a new process for it.
 * Called on the UI thread of a visible activity, so the start is a foreground start. Needs no
 * manifest change and no permission. Returns false (and the process lives on) when there is no
 * launcher activity or the start is refused; it never returns after a successful start.
 */
public final class KanaRestart {
    private KanaRestart() {
    }

    public static boolean restart(Activity activity) {
        if (activity == null) {
            return false;
        }
        try {
            Intent launch = activity.getPackageManager().getLaunchIntentForPackage(activity.getPackageName());
            ComponentName component = launch == null ? null : launch.getComponent();
            if (component == null) {
                return false;
            }
            Intent restart = Intent.makeRestartActivityTask(component);
            activity.startActivity(restart);
        } catch (RuntimeException error) {
            Log.e("KanaMemoryRestart", "Restart refused", error);
            return false;
        }
        Runtime.getRuntime().exit(0);
        return true;
    }
}
