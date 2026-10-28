package com.paperbetween.app;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;
import android.util.Base64;
import android.webkit.JavascriptInterface;
import android.webkit.WebView;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.chinese.ChineseTextRecognizerOptions;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/** Preflight returns geometry only; explicit teaching OCR may return temporary text anchors. */
public final class PaperVision implements AutoCloseable {
    private final WebView web;
    private final TextRecognizer recognizer;
    private final AtomicBoolean busy = new AtomicBoolean(false);
    private final AtomicBoolean closed = new AtomicBoolean(false);
    private final AnchorRequests anchorRequests = new AnchorRequests();

    public PaperVision(WebView web) {
        this.web = web;
        this.recognizer = TextRecognition.getClient(
            new ChineseTextRecognizerOptions.Builder().build());
    }

    @JavascriptInterface public void recognize(String encodedJpeg, String requestId) {
        if (closed.get()) return;
        if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{1,64}")) return;
        if (encodedJpeg == null || encodedJpeg.length() > 2_800_000) {
            reply(requestId, null, 0, "image_too_large");
            return;
        }
        if (!busy.compareAndSet(false, true)) {
            reply(requestId, null, 0, "busy");
            return;
        }
        long started = android.os.SystemClock.elapsedRealtime();
        Bitmap bitmap;
        try {
            byte[] bytes = Base64.decode(encodedJpeg, Base64.DEFAULT);
            if (bytes.length > 2_000_000 || bytes.length < 4 ||
                (bytes[0] & 255) != 255 || (bytes[1] & 255) != 216)
                throw new IllegalArgumentException("JPEG required");
            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            if (bitmap == null) throw new IllegalArgumentException("Invalid JPEG");
        } catch (IllegalArgumentException error) {
            busy.set(false);
            reply(requestId, null, elapsed(started), "invalid_image");
            return;
        }
        try {
            recognizer.process(InputImage.fromBitmap(bitmap, 0))
                .addOnSuccessListener(result -> {
                    JSONArray boxes = new JSONArray();
                    for (Text.TextBlock block : result.getTextBlocks()) {
                        for (Text.Line line : block.getLines()) {
                            Rect rect = line.getBoundingBox();
                            if (rect == null || boxes.length() >= 120) continue;
                            try {
                                JSONArray box = new JSONArray();
                                box.put((double) rect.left / bitmap.getWidth());
                                box.put((double) rect.top / bitmap.getHeight());
                                box.put((double) rect.width() / bitmap.getWidth());
                                box.put((double) rect.height() / bitmap.getHeight());
                                boxes.put(box);
                            } catch (JSONException ignored) {
                                // Coordinates from a decoded bitmap should always be finite.
                            }
                        }
                    }
                    reply(requestId, boxes, elapsed(started), null);
                })
                .addOnFailureListener(error -> reply(requestId, null, elapsed(started), "ocr_failed"))
                .addOnCompleteListener(task -> {
                    bitmap.recycle();
                    busy.set(false);
                });
        } catch (RuntimeException error) {
            bitmap.recycle();
            busy.set(false);
            reply(requestId, null, elapsed(started), "ocr_failed");
        }
    }

    /** Called only for the exact frozen JPEG used by an explicit teaching request. */
    @JavascriptInterface public void recognizeAnchors(String encodedJpeg, String requestId) {
        if (requestId == null || !requestId.matches("[A-Za-z0-9_-]{1,64}")) return;
        AnchorRequests.Token token = anchorRequests.begin(requestId);
        if (token == null) return;
        if (encodedJpeg == null || encodedJpeg.length() > 2_800_000) {
            replyAnchors(token, "", 0, 0, null, 0, "image_too_large");
            return;
        }
        if (!busy.compareAndSet(false, true)) {
            replyAnchors(token, "", 0, 0, null, 0, "busy");
            return;
        }
        long started = android.os.SystemClock.elapsedRealtime();
        Bitmap bitmap;
        String imageHash;
        try {
            byte[] bytes = Base64.decode(encodedJpeg, Base64.DEFAULT);
            if (bytes.length > 2_000_000 || bytes.length < 4 ||
                (bytes[0] & 255) != 255 || (bytes[1] & 255) != 216)
                throw new IllegalArgumentException("JPEG required");
            BitmapFactory.Options dimensions = new BitmapFactory.Options();
            dimensions.inJustDecodeBounds = true;
            BitmapFactory.decodeByteArray(bytes, 0, bytes.length, dimensions);
            if (!AnchorPolicy.acceptsDimensions(dimensions.outWidth, dimensions.outHeight))
                throw new IllegalArgumentException("Invalid image dimensions");
            imageHash = AnchorPolicy.sha256(bytes);
            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
            if (bitmap == null) throw new IllegalArgumentException("Invalid JPEG");
        } catch (IllegalArgumentException error) {
            busy.set(false);
            replyAnchors(token, "", 0, 0, null, elapsed(started), "invalid_image");
            return;
        }
        if (!anchorRequests.current(token)) {
            bitmap.recycle();
            busy.set(false);
            return;
        }
        try {
            recognizer.process(InputImage.fromBitmap(bitmap, 0))
                .addOnSuccessListener(result -> {
                    if (!anchorRequests.current(token)) return;
                    JSONArray anchors = teachingAnchors(result, bitmap.getWidth(), bitmap.getHeight());
                    replyAnchors(token, imageHash, bitmap.getWidth(), bitmap.getHeight(),
                        anchors, elapsed(started), null);
                })
                .addOnFailureListener(error -> replyAnchors(token, imageHash,
                    bitmap.getWidth(), bitmap.getHeight(), null, elapsed(started), "ocr_failed"))
                .addOnCompleteListener(task -> {
                    bitmap.recycle();
                    busy.set(false);
                });
        } catch (RuntimeException error) {
            bitmap.recycle();
            busy.set(false);
            replyAnchors(token, imageHash, 0, 0, null, elapsed(started), "ocr_failed");
        }
    }

    @JavascriptInterface public void cancelAnchors(String requestId) {
        anchorRequests.cancel(requestId);
    }

    private JSONArray teachingAnchors(Text result, int width, int height) {
        JSONArray anchors = new JSONArray();
        AnchorPolicy.Budget budget = new AnchorPolicy.Budget();
        int lineIndex = 0;
        for (Text.TextBlock block : result.getTextBlocks()) {
            for (Text.Line line : block.getLines()) {
                int before = anchors.length();
                int elementIndex = 0;
                for (Text.Element element : line.getElements()) {
                    appendAnchor(anchors, budget, "local_e_" + lineIndex + "_" + elementIndex++,
                        "element", element.getText(), element.getBoundingBox(),
                        element.getConfidence(), width, height);
                }
                // A line is a fallback, never a guessed sub-word rectangle.
                if (anchors.length() == before) {
                    appendAnchor(anchors, budget, "local_l_" + lineIndex, "line", line.getText(),
                        line.getBoundingBox(), line.getConfidence(), width, height);
                }
                lineIndex++;
                if (anchors.length() >= AnchorPolicy.MAX_ITEMS) return anchors;
            }
        }
        return anchors;
    }

    private void appendAnchor(JSONArray anchors, AnchorPolicy.Budget budget, String id,
                              String level, String text, Rect rect, float confidence,
                              int width, int height) {
        if (rect == null || !AnchorPolicy.acceptsText(text)) return;
        double[] coordinates = AnchorPolicy.box(rect.left, rect.top, rect.right, rect.bottom,
            width, height);
        if (coordinates == null) return;
        try {
            JSONArray box = new JSONArray();
            for (double coordinate : coordinates) box.put(coordinate);
            JSONObject anchor = new JSONObject();
            anchor.put("id", id);
            anchor.put("level", level);
            anchor.put("text", text.trim());
            anchor.put("box", box);
            Double reported = AnchorPolicy.confidence(confidence);
            anchor.put("confidence", reported == null ? JSONObject.NULL : reported);
            if (budget.accept(anchor.toString().getBytes(StandardCharsets.UTF_8).length))
                anchors.put(anchor);
        } catch (JSONException ignored) {
            // Invalid values are omitted; no fabricated confidence or text is substituted.
        }
    }

    private void replyAnchors(AnchorRequests.Token token, String hash, int width, int height,
                              JSONArray anchors, long duration, String error) {
        if (!anchorRequests.current(token)) return;
        try {
            JSONObject detail = new JSONObject();
            detail.put("id", token.id);
            detail.put("image_sha256", hash);
            detail.put("width", width);
            detail.put("height", height);
            detail.put("durationMs", duration);
            detail.put("anchors", anchors == null ? new JSONArray() : anchors);
            if (error != null) detail.put("error", error);
            String script = "window.dispatchEvent(new CustomEvent('paper-vision-anchors',{detail:"
                + detail.toString() + "}))";
            if (!web.post(() -> anchorRequests.dispatch(token, () -> {
                // Cancel can happen after OCR finishes but before the WebView queue runs.
                try { web.evaluateJavascript(script, null); }
                catch (RuntimeException ignored) { /* The optional view may already be destroyed. */ }
            }))) anchorRequests.finish(token);
        } catch (JSONException ignored) {
            anchorRequests.finish(token);
        }
    }

    private long elapsed(long started) {
        return android.os.SystemClock.elapsedRealtime() - started;
    }

    private void reply(String requestId, JSONArray boxes, long duration, String error) {
        try {
            JSONObject detail = new JSONObject();
            detail.put("id", requestId);
            detail.put("boxes", boxes == null ? new JSONArray() : boxes);
            detail.put("durationMs", duration);
            if (error != null) detail.put("error", error);
            String script = "window.dispatchEvent(new CustomEvent('paper-vision',{detail:"
                + detail.toString() + "}))";
            web.post(() -> {
                if (!closed.get()) web.evaluateJavascript(script, null);
            });
        } catch (JSONException ignored) {
            // Numeric coordinates and a validated request ID should always serialize.
        }
    }

    @Override public void close() {
        if (closed.compareAndSet(false, true)) {
            anchorRequests.close();
            recognizer.close();
        }
    }

    /** Package-visible pure policy for native tests without an Android runtime. */
    static final class AnchorPolicy {
        static final int MAX_ITEMS = 120;
        static final int MAX_PAYLOAD_BYTES = 48 * 1024;

        static boolean acceptsDimensions(int width, int height) {
            return width > 0 && height > 0 && width <= 4096 && height <= 4096
                && (long) width * height <= 8_000_000;
        }

        static boolean acceptsText(String text) {
            if (text == null) return false;
            String trimmed = text.trim();
            return !trimmed.isEmpty() && trimmed.codePointCount(0, trimmed.length()) <= 80;
        }

        static Double confidence(float reported) {
            return Float.isFinite(reported) && reported >= 0 && reported <= 1
                ? (double) reported : null;
        }

        static double[] box(int left, int top, int right, int bottom, int width, int height) {
            if (!acceptsDimensions(width, height)) return null;
            int l = Math.max(0, left), t = Math.max(0, top);
            int r = Math.min(width, right), b = Math.min(height, bottom);
            if (r <= l || b <= t) return null;
            return new double[] {(double) l / width, (double) t / height,
                (double) (r - l) / width, (double) (b - t) / height};
        }

        static String sha256(byte[] bytes) {
            try {
                byte[] digest = MessageDigest.getInstance("SHA-256").digest(bytes);
                StringBuilder hash = new StringBuilder(64);
                for (byte value : digest) {
                    int unsigned = value & 255;
                    hash.append(Character.forDigit(unsigned >>> 4, 16));
                    hash.append(Character.forDigit(unsigned & 15, 16));
                }
                return hash.toString();
            } catch (NoSuchAlgorithmException impossible) {
                throw new IllegalStateException("SHA-256 unavailable", impossible);
            }
        }

        static final class Budget {
            int count;
            int bytes;

            boolean accept(int itemBytes) {
                // Reserve the envelope and separators; budget the actual UTF-8 JSON bytes.
                if (itemBytes < 0 || count >= MAX_ITEMS ||
                    itemBytes > MAX_PAYLOAD_BYTES - 1024 - bytes) return false;
                count++;
                bytes += itemBytes + 1;
                return true;
            }
        }
    }

    static final class AnchorRequests {
        static final class Token {
            final String id;
            Token(String id) { this.id = id; }
        }

        private final Map<String, Token> pending = new LinkedHashMap<>();
        private boolean closed;

        synchronized Token begin(String id) {
            if (closed) return null;
            if (pending.size() >= 8) pending.remove(pending.keySet().iterator().next());
            Token token = new Token(id);
            pending.put(id, token);
            return token;
        }

        synchronized boolean current(Token token) {
            return !closed && token != null && pending.get(token.id) == token;
        }

        synchronized void cancel(String id) { pending.remove(id); }

        synchronized void finish(Token token) {
            if (current(token)) pending.remove(token.id);
        }

        synchronized boolean dispatch(Token token, Runnable reply) {
            if (!current(token)) return false;
            try { reply.run(); }
            finally { finish(token); }
            return true;
        }

        synchronized void close() {
            closed = true;
            pending.clear();
        }
    }
}
