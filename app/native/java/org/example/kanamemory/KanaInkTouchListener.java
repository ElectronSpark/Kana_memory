package org.example.kanamemory;

import android.view.MotionEvent;
import android.view.View;
import android.view.ViewParent;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.Arrays;

/** Preserve Android's batched pen samples across a single Python/JNI callback. */
public final class KanaInkTouchListener implements View.OnTouchListener {
    public interface Events {
        // Every sample is little-endian float x, float y, long uptimeMillis.
        void onSamples(int phase, byte[] samples);
    }

    public static final int DOWN = 0;
    public static final int MOVE = 1;
    public static final int UP = 2;
    public static final int CANCEL = 3;
    private static final int NO_POINTER = -1;

    private Events events;
    private final float density;
    private int pointerId = NO_POINTER;
    private boolean ownsGesture;

    public KanaInkTouchListener(float density, Events events) {
        if (!(density > 0) || Float.isInfinite(density) || events == null) {
            throw new IllegalArgumentException("Valid ink density and callback required");
        }
        this.density = density;
        this.events = events;
    }

    private static void intercept(View view, boolean disallow) {
        ViewParent parent = view.getParent();
        if (parent != null) parent.requestDisallowInterceptTouchEvent(disallow);
    }

    private static boolean inside(View view, float x, float y) {
        return x >= 0 && y >= 0 && x <= view.getWidth() && y <= view.getHeight();
    }

    /** Dispose without delivering stale callbacks into a replacement page. */
    public void detach(View view) {
        events = null;
        pointerId = NO_POINTER;
        if (ownsGesture) intercept(view, false);
        ownsGesture = false;
        view.setOnTouchListener(null);
    }

    private void cancelPointer() {
        if (pointerId != NO_POINTER && events != null) events.onSamples(CANCEL, new byte[0]);
        pointerId = NO_POINTER;
    }

    private void deliver(View view, MotionEvent event, int pointerIndex, int phase, boolean history) {
        int count = history ? event.getHistorySize() : 0;
        ByteBuffer packed = ByteBuffer.allocate((count + 1) * 16).order(ByteOrder.LITTLE_ENDIAN);
        int deliveredPhase = phase;
        for (int index = 0; index <= count; index++) {
            boolean current = index == count;
            float x = current ? event.getX(pointerIndex) : event.getHistoricalX(pointerIndex, index);
            float y = current ? event.getY(pointerIndex) : event.getHistoricalY(pointerIndex, index);
            long time = current ? event.getEventTime() : event.getHistoricalEventTime(index);
            if (!inside(view, x, y)) {
                // Keep the valid prefix, end the stroke, and consume the rest
                // of this gesture. Re-entering must not draw a joining line.
                deliveredPhase = CANCEL;
                pointerId = NO_POINTER;
                break;
            }
            packed.putFloat(x / density).putFloat(y / density).putLong(time);
        }
        byte[] samples = packed.position() == packed.capacity() ? packed.array()
                : Arrays.copyOf(packed.array(), packed.position());
        if (events != null) events.onSamples(deliveredPhase, samples);
    }

    @Override
    public boolean onTouch(View view, MotionEvent event) {
        if (events == null) return false;
        int action = event.getActionMasked();
        if (action == MotionEvent.ACTION_DOWN) {
            cancelPointer();
            if (ownsGesture) intercept(view, false);
            ownsGesture = false;
            int index = event.getActionIndex();
            if (!inside(view, event.getX(index), event.getY(index))) return false;
            ownsGesture = true;
            pointerId = event.getPointerId(index);
            intercept(view, true);
            deliver(view, event, index, DOWN, false);
            return true;
        }
        if (!ownsGesture) return false;
        if (action == MotionEvent.ACTION_CANCEL) {
            cancelPointer();
            ownsGesture = false;
            intercept(view, false);
            return true;
        }
        if (action == MotionEvent.ACTION_POINTER_DOWN) return true;
        int index = pointerId == NO_POINTER ? -1 : event.findPointerIndex(pointerId);
        if (pointerId != NO_POINTER && index < 0) cancelPointer();
        if (action == MotionEvent.ACTION_MOVE && index >= 0) {
            deliver(view, event, index, MOVE, true);
        } else if (action == MotionEvent.ACTION_POINTER_UP) {
            if (index >= 0 && event.getActionIndex() == index) {
                deliver(view, event, index, UP, true);
                pointerId = NO_POINTER;
            }
            // Never switch to a second finger: hold the gesture until its UP.
        } else if (action == MotionEvent.ACTION_UP) {
            if (index >= 0) deliver(view, event, index, UP, true);
            pointerId = NO_POINTER;
            ownsGesture = false;
            intercept(view, false);
        }
        return true;
    }
}
