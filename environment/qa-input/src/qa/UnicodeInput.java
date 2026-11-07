package qa;

import android.os.Bundle;
import android.util.Base64;
import android.view.accessibility.AccessibilityNodeInfo;
import com.android.uiautomator.core.UiObject;
import com.android.uiautomator.core.UiSelector;
import com.android.uiautomator.testrunner.UiAutomatorTestCase;
import java.nio.charset.StandardCharsets;

/** QA-only Unicode input through the existing uiautomator shell runner.
 * No installed APK, IME, clipboard, application hooks, or database access.
 */
public final class UnicodeInput extends UiAutomatorTestCase {
    // Override with -e package org.example.demo.kanamemory for the public demo build.
    private static final String DEFAULT_PACKAGE = "org.example.kanamemory";

    private String appPackage() {
        return getParams().getString("package", DEFAULT_PACKAGE);
    }

    private static final class AccessibleEdit extends UiObject {
        AccessibleEdit(UiSelector selector) {
            super(selector);
        }

        AccessibilityNodeInfo node() {
            return findAccessibilityNodeInfo(5000);
        }
    }

    public void testSetText() throws Exception {
        final Bundle params = getParams();
        final String encoded = params.getString("text");
        assertNotNull("Pass UTF-8 text as base64 with -e text", encoded);
        final String text = new String(Base64.decode(encoded, Base64.DEFAULT), StandardCharsets.UTF_8);
        assertTrue("Input exceeds the QA limit", text.length() <= 10000);

        UiSelector selector = new UiSelector().packageName(appPackage())
                .className("android.widget.EditText").enabled(true);
        final String index = params.getString("index");
        if (index == null) {
            // The operator must focus the intended field first. Never silently
            // choose another field if the screen changed after a screenshot.
            selector = selector.focused(true);
        } else {
            final int number = Integer.parseInt(index);
            assertTrue("index must be nonnegative", number >= 0);
            selector = selector.instance(number);
        }
        final AccessibleEdit edit = new AccessibleEdit(selector);
        AccessibilityNodeInfo node = edit.node();
        assertNotNull("No matching enabled KanaMemory EditText; focus it first or pass index", node);
        try {
            assertFalse("QA helper refuses password fields", node.isPassword());
            final Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text);
            assertTrue("ACTION_SET_TEXT was rejected", node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args));
        } finally {
            node.recycle();
        }

        // Verify the actual native value after its text-change callback, not
        // merely that an accessibility action was accepted.
        boolean matched = false;
        final long deadline = System.currentTimeMillis() + 5000;
        while (System.currentTimeMillis() < deadline) {
            node = edit.node();
            if (node != null) {
                try {
                    matched = text.contentEquals(node.getText() == null ? "" : node.getText());
                } finally {
                    node.recycle();
                }
            }
            if (matched) break;
            Thread.sleep(100);
        }
        assertTrue("The native field did not retain the requested Unicode value", matched);
        Bundle report = new Bundle();
        report.putString("unicode_input", "verified");
        report.putString("characters", Integer.toString(text.codePointCount(0, text.length())));
        getAutomationSupport().sendStatus(0, report);
    }

    /** Fixed public fake credentials only; never accepts a secret parameter. */
    public void testSyntheticCredential() throws Exception {
        assertNull("Arbitrary input forbidden in credential QA", getParams().getString("text"));
        String token = "sk-unit-test-";
        for (int i = 0; i < 25; i++) token += "A1_-b2";
        String mode = getParams().getString("case", "bare");
        String value;
        if ("bare".equals(mode)) value = token;
        else if ("assignment".equals(mode)) value = "OPENAI_API_KEY=" + token;
        else if ("quoted".equals(mode)) value = "OPENAI_API_KEY=\"" + token + "\"";
        else if ("wrapped".equals(mode)) value = token.substring(0, 50) + "\n" + token.substring(50);
        else if ("invalid".equals(mode)) value = "PUBLIC_INVALID_TEST_INPUT";
        else if ("empty".equals(mode)) value = "";
        else throw new IllegalArgumentException("Unknown fixed public fixture");
        AccessibleEdit edit = new AccessibleEdit(new UiSelector().packageName(appPackage())
                .className("android.widget.EditText").enabled(true).instance(1));
        AccessibilityNodeInfo node = edit.node();
        assertNotNull("AI service password field missing", node);
        try {
            assertTrue("Only the AI service password input may be tested", node.isPassword());
            Bundle args = new Bundle();
            args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, value);
            assertTrue("Synthetic input rejected", node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args));
        } finally { node.recycle(); }
        Bundle report = new Bundle();
        report.putString("synthetic_credential_input", "set");
        report.putString("fixture", mode);
        getAutomationSupport().sendStatus(0, report);
    }
}
