package org.example.kanamemory;

import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ColorFilter;
import android.graphics.Paint;
import android.graphics.PixelFormat;
import android.graphics.drawable.Drawable;
import android.os.Build;
import android.view.Gravity;
import android.widget.CheckBox;

/** Standard checkboxes; a horizontal mark represents a partially selected group. */
public final class KanaTreeCheckBox extends CheckBox {
    private final Drawable normalIndicator;
    private final Drawable mixedIndicator;
    private boolean mixed;

    public KanaTreeCheckBox(Context context) {
        super(context);
        normalIndicator = getButtonDrawable();
        mixedIndicator = new MixedIndicator(context.getResources().getDisplayMetrics().density);
        setButtonTintList(new ColorStateList(new int[][] {
            new int[] {-android.R.attr.state_enabled},
            new int[] {android.R.attr.state_checked}, new int[] {}
        }, new int[] {Color.rgb(135, 144, 141), Color.rgb(24, 92, 80), Color.rgb(91, 106, 101)}));
        setGravity(Gravity.CENTER);
        setPadding(0, 0, 0, 0);
        setMinWidth(0);
        setMinHeight(0);
    }

    public void setMixed(boolean value) {
        if (mixed != value) {
            mixed = value;
            setButtonDrawable(value ? mixedIndicator : normalIndicator);
        }
        if (Build.VERSION.SDK_INT >= 30) {
            setStateDescription(value ? "部分已选" : null);
        }
    }

    private static final class MixedIndicator extends Drawable {
        private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float density;

        MixedIndicator(float density) {
            this.density = density;
        }

        @Override public void draw(Canvas canvas) {
            float cx = getBounds().exactCenterX(), cy = getBounds().exactCenterY();
            float radius = 9 * density;
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.rgb(24, 92, 80));
            canvas.drawRoundRect(cx - radius, cy - radius, cx + radius, cy + radius,
                                 2 * density, 2 * density, paint);
            paint.setColor(Color.WHITE);
            canvas.drawRect(cx - 5 * density, cy - density, cx + 5 * density, cy + density, paint);
        }

        @Override public int getIntrinsicWidth() { return Math.round(32 * density); }
        @Override public int getIntrinsicHeight() { return Math.round(32 * density); }
        @Override public void setAlpha(int alpha) { paint.setAlpha(alpha); }
        // A uniform CompoundButton tint would also cover the white minus mark.
        @Override public void setColorFilter(ColorFilter filter) { }
        @Override public int getOpacity() { return PixelFormat.TRANSLUCENT; }
    }
}
