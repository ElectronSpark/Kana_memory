package org.example.kanamemory;

import android.content.Context;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.AbsListView;
import android.widget.ArrayAdapter;
import android.widget.CheckedTextView;
import android.widget.TextView;

/** A normal Spinner adapter whose selected and popup labels may wrap. */
public final class KanaSelectionAdapter extends ArrayAdapter<String> {
    private int contentWidth;
    private final int minimumHeight;

    public KanaSelectionAdapter(Context context) {
        super(context, android.R.layout.simple_spinner_item);
        setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        minimumHeight = (int) Math.ceil(48 * context.getResources().getDisplayMetrics().density);
    }

    /** Width is native pixels, excluding the Spinner's left/right padding. */
    public void setContentWidth(int width) {
        width = Math.max(1, width);
        if (contentWidth != width) {
            contentWidth = width;
            // Spinner can recycle its selected child without calling getView again.
            // Invalidate that child when rotation or a parent layout changes width.
            notifyDataSetChanged();
        }
    }

    private View wrap(View view) {
        TextView text = (TextView) view;
        text.setSingleLine(false);
        text.setHorizontallyScrolling(false);
        text.setMaxLines(Integer.MAX_VALUE);
        text.setEllipsize(null);
        text.setMinHeight(minimumHeight);
        text.setMaxWidth(contentWidth > 0 ? contentWidth : Integer.MAX_VALUE);
        ViewGroup.LayoutParams params = text.getLayoutParams();
        if (params != null) {
            params.width = ViewGroup.LayoutParams.MATCH_PARENT;
            params.height = ViewGroup.LayoutParams.WRAP_CONTENT;
            text.setLayoutParams(params);
        }
        return text;
    }

    @Override
    public View getView(int position, View convertView, ViewGroup parent) {
        return wrap(super.getView(position, convertView, parent));
    }

    @Override
    public View getDropDownView(int position, View convertView, ViewGroup parent) {
        DropDownLabel text = convertView instanceof DropDownLabel
            ? (DropDownLabel) convertView : new DropDownLabel(getContext());
        text.setText(getItem(position));
        text.availableWidth = contentWidth;
        text.setLayoutParams(new AbsListView.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        return wrap(text);
    }

    /** Popup lists probe row heights before assigning their final bounds. */
    private static final class DropDownLabel extends CheckedTextView {
        int availableWidth;

        DropDownLabel(Context context) {
            // Preserve the platform spinner popup's text appearance/check state,
            // but don't inherit the inflated resource's fixed row layout height.
            super(context, null, android.R.attr.spinnerDropDownItemStyle);
            setId(android.R.id.text1);
            setGravity(Gravity.CENTER_VERTICAL);
        }

        @Override
        public void setHorizontallyScrolling(boolean horizontallyScrolling) {
            // DropDownListView.obtainView calls this with true after the adapter
            // returns a TextView, overriding normal wrapping configuration.
            // Popup choices intentionally retain wrapping even under that call.
            super.setHorizontallyScrolling(false);
        }

        @Override
        protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
            int offeredWidth = View.MeasureSpec.getSize(widthMeasureSpec);
            int width = availableWidth > 0 ? availableWidth : offeredWidth;
            if (View.MeasureSpec.getMode(widthMeasureSpec) != View.MeasureSpec.UNSPECIFIED
                    && offeredWidth > 0) {
                width = Math.min(width, offeredWidth);
            }
            // An UNSPECIFIED/AT_MOST probe can otherwise report a one-line height
            // for a row which is later drawn in the narrower popup. Always measure
            // the same width that the row will occupy, with a content-sized height.
            super.onMeasure(View.MeasureSpec.makeMeasureSpec(Math.max(1, width),
                View.MeasureSpec.EXACTLY), View.MeasureSpec.UNSPECIFIED);
        }
    }
}
