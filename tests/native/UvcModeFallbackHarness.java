package com.paperbetween.app;

import java.util.Arrays;
import java.util.Collections;
import java.util.List;

/** Runs without Android/JNI; assertions exercise published mode and attempt policy. */
public final class UvcModeFallbackHarness {
    private static UvcModeFallback.Mode mode(int index, int width, int height, int fps, boolean mjpeg) {
        return new UvcModeFallback.Mode(index, width, height, fps, mjpeg);
    }

    private static void equal(Object actual, Object expected) {
        if (!actual.equals(expected)) throw new AssertionError(actual + " != " + expected);
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) {
        List<UvcModeFallback.Mode> original = Arrays.asList(
            mode(0, 1920, 1080, 30, true), mode(1, 1280, 960, 30, true),
            mode(2, 1280, 720, 30, true), mode(3, 1280, 960, 5, false),
            mode(4, 1280, 720, 30, false));
        equal(UvcModeFallback.candidates(original), Arrays.asList(1, 0, 3));

        // This camera advertises both full-frame 4:3 and widescreen modes.
        List<UvcModeFallback.Mode> fullFrame = Arrays.asList(
            mode(0, 1920, 1080, 30, true), mode(1, 1600, 1200, 30, true),
            mode(2, 1280, 720, 30, true), mode(3, 1280, 720, 10, false));
        equal(UvcModeFallback.candidates(fullFrame), Arrays.asList(1, 2, 3));
        // A widescreen-only camera remains supported; never invent a 4:3 mode.
        equal(UvcModeFallback.candidates(Arrays.asList(
            mode(0, 1920, 1080, 30, true), mode(1, 1280, 720, 30, true),
            mode(2, 1280, 720, 10, false))), Arrays.asList(0, 1, 2));
        // Prefer a decodable compressed stream to uncompressed high-bandwidth video.
        equal(UvcModeFallback.candidates(Arrays.asList(
            mode(0, 1920, 1440, 5, false), mode(1, 1600, 1200, 30, true),
            mode(2, 1280, 960, 5, false))), Arrays.asList(1, 2));

        List<UvcModeFallback.Mode> replacement = Arrays.asList(
            mode(0, 3264, 2448, 5, true), mode(1, 2048, 1536, 5, true),
            mode(2, 1600, 1200, 5, true), mode(3, 1280, 960, 5, true),
            mode(4, 800, 600, 5, true), mode(5, 1600, 1200, 5, false),
            mode(6, 1280, 960, 5, false));
        equal(UvcModeFallback.candidates(replacement), Arrays.asList(0, 1, 3));
        List<UvcModeFallback.Mode> highDefinition = Arrays.asList(
            mode(0, 3264, 2448, 15, true), mode(1, 3264, 1836, 15, true),
            mode(2, 2592, 1944, 20, true), mode(3, 2048, 1536, 20, true),
            mode(4, 1920, 1080, 30, true), mode(5, 1600, 1200, 30, true),
            mode(6, 3264, 2448, 2, false));
        equal(UvcModeFallback.candidates(highDefinition), Arrays.asList(0, 2, 5));
        check(UvcModeFallback.frameSizeUsable(3264, 2448), "native 8MP frame must reach encoder");
        check(!UvcModeFallback.frameSizeUsable(3265, 2448)
            && !UvcModeFallback.frameSizeUsable(3264, 2449)
            && !UvcModeFallback.frameSizeUsable(Integer.MAX_VALUE, Integer.MAX_VALUE)
            && !UvcModeFallback.frameSizeUsable(-3264, 2448), "invalid frames must not allocate buffers");
        check(replacement.get(6).fps == 5, "YUYV must keep the advertised 5fps");
        equal(UvcModeFallback.candidates(Collections.singletonList(mode(7, 1280, 960, 5, true))),
            Collections.singletonList(7));
        equal(UvcModeFallback.candidates(Arrays.asList(mode(0, 640, 480, 30, true),
            mode(1, 1281, 960, 5, true), mode(2, 1920, 1080, 0, true))), Collections.emptyList());

        // A missing published 1280 mode is never fabricated from a primary mode.
        equal(UvcModeFallback.candidates(Collections.singletonList(mode(9, 1600, 1200, 5, true))),
            Collections.singletonList(9));
        UvcModeFallback.Attempts attempts = new UvcModeFallback.Attempts(17);
        int first = attempts.begin();
        check(attempts.accepts(17, first), "first callback should be accepted");
        int second = attempts.begin();
        check(!attempts.accepts(17, first), "old callback/encoder/watchdog must be rejected");
        check(attempts.accepts(17, second), "new callback should be accepted");
        check(!attempts.accepts(18, second), "a different USB generation must be rejected");
        int third = attempts.begin();
        check(third == 3 && attempts.begin() == 0, "retry budget must be bounded");
        attempts.close();
        attempts.close();
        check(!attempts.accepts(17, third) && attempts.begin() == 0,
            "stop/disconnect must invalidate late work and prevent another attempt");
        UvcModeFallback.Attempts reopened = new UvcModeFallback.Attempts(18);
        check(reopened.begin() == 1 && !reopened.accepts(17, first),
            "new open must not inherit callbacks from the prior generation");
        check(UvcModeFallback.FIRST_FRAME_TIMEOUT_MS == 4000
            && UvcModeFallback.OPEN_TIMEOUT_MS == 20000
            && UvcModeFallback.MAX_ATTEMPTS * UvcModeFallback.FIRST_FRAME_TIMEOUT_MS
                < UvcModeFallback.OPEN_TIMEOUT_MS, "watchdogs must preserve the total open bound");
        System.out.println("Published-mode selection and late-attempt lifecycle checks passed");
    }
}
