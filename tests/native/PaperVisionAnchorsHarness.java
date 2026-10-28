package com.paperbetween.app;

import java.nio.charset.StandardCharsets;

/** Tests policy only; never creates a WebView, camera, or ML Kit recognizer. */
public final class PaperVisionAnchorsHarness {
    private static void require(boolean value, String message) {
        if (!value) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        PaperVision.AnchorRequests requests = new PaperVision.AnchorRequests();
        PaperVision.AnchorRequests.Token first = requests.begin("frame_one");
        PaperVision.AnchorRequests.Token second = requests.begin("frame_two");
        int[] dispatched = {0};
        requests.cancel("frame_one");
        require(!requests.dispatch(first, () -> dispatched[0]++), "cancelled OCR replied late");
        require(requests.current(second), "cancelling one request affected another");
        require(requests.dispatch(second, () -> dispatched[0]++), "current OCR did not reply");
        require(dispatched[0] == 1, "unexpected callback count");
        require(!requests.dispatch(second, () -> dispatched[0]++), "request replied twice");

        PaperVision.AnchorRequests.Token old = requests.begin("same_id");
        PaperVision.AnchorRequests.Token replacement = requests.begin("same_id");
        requests.finish(old);
        require(!requests.current(old) && requests.current(replacement), "old cleanup killed new ID");
        requests.close();
        requests.close();
        require(!requests.dispatch(replacement, () -> dispatched[0]++), "closed OCR replied late");
        require(requests.begin("after_close") == null, "closed registry accepted work");

        PaperVision.AnchorRequests bounded = new PaperVision.AnchorRequests();
        PaperVision.AnchorRequests.Token earliest = bounded.begin("bounded_0");
        PaperVision.AnchorRequests.Token latest = null;
        for (int index = 1; index <= 8; index++) latest = bounded.begin("bounded_" + index);
        require(!bounded.current(earliest) && bounded.current(latest), "pending calls unbounded");

        require(PaperVision.AnchorPolicy.sha256("abc".getBytes(StandardCharsets.UTF_8)).equals(
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"),
            "hash is not SHA-256 over original bytes");
        byte[] jpeg = {(byte) 255, (byte) 216, 0, 1, (byte) 255, (byte) 217};
        String original = PaperVision.AnchorPolicy.sha256(jpeg);
        jpeg[3] = 2;
        require(!original.equals(PaperVision.AnchorPolicy.sha256(jpeg)), "different JPEG reused hash");

        require(PaperVision.AnchorPolicy.acceptsDimensions(1200, 1600), "portrait rejected");
        require(!PaperVision.AnchorPolicy.acceptsDimensions(0, 1600), "empty image accepted");
        require(!PaperVision.AnchorPolicy.acceptsDimensions(4096, 4096), "oversized bitmap accepted");
        double[] box = PaperVision.AnchorPolicy.box(120, 320, 360, 640, 1200, 1600);
        require(Math.abs(box[0] - .1) < 1e-9 && Math.abs(box[1] - .2) < 1e-9 &&
            Math.abs(box[2] - .2) < 1e-9 && Math.abs(box[3] - .2) < 1e-9,
            "box did not use decoded portrait dimensions");
        require(PaperVision.AnchorPolicy.box(400, 200, 400, 300, 1200, 1600) == null,
            "empty box accepted");
        double[] clipped = PaperVision.AnchorPolicy.box(-20, -30, 120, 160, 1200, 1600);
        require(clipped[0] == 0 && clipped[1] == 0 && clipped[2] == .1 && clipped[3] == .1,
            "box escaped the current JPEG");

        require(PaperVision.AnchorPolicy.confidence(.73f).equals((double) .73f),
            "reported confidence was rounded or raised");
        require(PaperVision.AnchorPolicy.confidence(0f).equals(0d), "zero confidence was fabricated");
        for (float invalid : new float[] {Float.NaN, Float.POSITIVE_INFINITY, -1, 2})
            require(PaperVision.AnchorPolicy.confidence(invalid) == null, "invalid confidence accepted");
        require(!PaperVision.AnchorPolicy.acceptsText("  "), "blank text accepted");
        require(PaperVision.AnchorPolicy.acceptsText("字".repeat(80)), "short line rejected");
        require(!PaperVision.AnchorPolicy.acceptsText("字".repeat(81)), "text silently truncated");
        require(PaperVision.AnchorPolicy.acceptsText("😀".repeat(80)), "UTF-16 split a code point");

        PaperVision.AnchorPolicy.Budget count = new PaperVision.AnchorPolicy.Budget();
        for (int index = 0; index < 120; index++) require(count.accept(4), "item budget too short");
        require(!count.accept(4), "item limit exceeded");
        PaperVision.AnchorPolicy.Budget bytes = new PaperVision.AnchorPolicy.Budget();
        int utf8 = "字".repeat(80).getBytes(StandardCharsets.UTF_8).length;
        require(utf8 == 240, "payload test did not count UTF-8 bytes");
        while (bytes.accept(utf8 + 200)) { /* Fill actual serialized-byte budget. */ }
        require(bytes.bytes <= PaperVision.AnchorPolicy.MAX_PAYLOAD_BYTES - 1024,
            "payload envelope exceeded its limit");
        require(!bytes.accept(PaperVision.AnchorPolicy.MAX_PAYLOAD_BYTES), "huge payload accepted");
        System.out.println("native OCR hash, confidence, bounds, budget and late-callback checks passed");
    }
}
