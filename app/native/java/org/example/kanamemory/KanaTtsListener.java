package org.example.kanamemory;

import android.speech.tts.UtteranceProgressListener;

/** Only bridges Android's abstract listener to a Python-implemented interface. */
public final class KanaTtsListener extends UtteranceProgressListener {
    public interface Events {
        void onStart(String utteranceId);
        void onDone(String utteranceId);
        void onError(String utteranceId, int errorCode);
        void onStop(String utteranceId, boolean interrupted);
    }

    private final Events events;

    public KanaTtsListener(Events events) { this.events = events; }
    @Override public void onStart(String id) { events.onStart(id); }
    @Override public void onDone(String id) { events.onDone(id); }
    @Override public void onError(String id) { events.onError(id, -1); }
    @Override public void onError(String id, int code) { events.onError(id, code); }
    @Override public void onStop(String id, boolean interrupted) { events.onStop(id, interrupted); }
}
