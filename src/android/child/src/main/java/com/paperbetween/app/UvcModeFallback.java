package com.paperbetween.app;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Bounded fallback over published modes only; independent of Android/JNI. */
final class UvcModeFallback {
    static final int MAX_ATTEMPTS = 3;
    static final long FIRST_FRAME_TIMEOUT_MS = 4000;
    static final long OPEN_TIMEOUT_MS = 20000;
    static final int MAX_WIDTH = 3264;
    static final int MAX_HEIGHT = 2448;
    static final long MAX_PIXELS = (long) MAX_WIDTH * MAX_HEIGHT;

    static final class Mode {
        final int index, width, height, fps;
        final boolean mjpeg;
        Mode(int index, int width, int height, int fps, boolean mjpeg) {
            this.index = index;
            this.width = width;
            this.height = height;
            this.fps = fps;
            this.mjpeg = mjpeg;
        }
    }

    static boolean frameSizeUsable(int width, int height) {
        return width >= 1280 && width <= MAX_WIDTH && height >= 480 && height <= MAX_HEIGHT
            && width % 2 == 0 && height % 2 == 0 && (long) width * height <= MAX_PIXELS;
    }

    private static boolean legacySize(Mode mode) {
        return mode.width <= 1920 && mode.height <= 1440;
    }

    private static boolean usable(Mode mode) {
        // High-resolution USB capture uses MJPEG; retain low-bandwidth YUYV recovery.
        return frameSizeUsable(mode.width, mode.height) && mode.fps > 0
            && (mode.mjpeg || legacySize(mode));
    }

    static List<Integer> candidates(List<Mode> published) {
        List<Integer> result = new ArrayList<>();
        // Prefer the paper-friendly 4:3 sensor mode over a wider, potentially
        // vertically cropped 16:9 mode, at the highest bounded native resolution.
        Comparator<Mode> preference = Comparator.comparingInt((Mode mode) -> mode.mjpeg ? 0 : 1)
            .thenComparingLong(mode -> Math.abs((long) mode.width * 3 - (long) mode.height * 4)
                * 1000000 / mode.height)
            .thenComparingLong(mode -> -(long) mode.width * mode.height)
            .thenComparingInt(mode -> -mode.fps);
        Mode primary = published.stream().filter(UvcModeFallback::usable)
            .min(preference)
            .orElse(null);
        if (primary == null) return result;
        result.add(primary.index);
        if (!legacySize(primary)) {
            Mode nextHigh = published.stream().filter(UvcModeFallback::usable)
                .filter(mode -> mode.mjpeg && !legacySize(mode) && mode.index != primary.index)
                .min(preference).orElse(null);
            if (nextHigh != null) result.add(nextHigh.index);
            // Keep a smaller full-frame recovery within the original memory/USB budget.
            Mode recovery = published.stream().filter(UvcModeFallback::usable)
                .filter(UvcModeFallback::legacySize)
                .min(Comparator.comparingInt((Mode mode) -> mode.mjpeg ? 0 : 1)
                    .thenComparingLong(mode -> Math.abs((long) mode.width * 3 - (long) mode.height * 4)
                        * 1000000 / mode.height)
                    .thenComparingInt(mode -> Math.abs(mode.width - 1280)))
                .orElse(null);
            if (recovery != null) result.add(recovery.index);
            return result;
        }
        for (boolean mjpeg : new boolean[]{true, false}) {
            Mode fallback = published.stream().filter(UvcModeFallback::usable)
                .filter(mode -> mode.width == 1280 && mode.mjpeg == mjpeg)
                .min(Comparator.comparingLong(mode ->
                    Math.abs((long) mode.height * primary.width - (long) mode.width * primary.height)))
                .orElse(null);
            if (fallback != null && !result.contains(fallback.index)) result.add(fallback.index);
            // When the primary already is the 1280 MJPEG mode, retain another
            // published MJPEG option before switching to uncompressed USB video.
            if (mjpeg && result.size() == 1) {
                Mode alternative = published.stream().filter(UvcModeFallback::usable)
                    .filter(mode -> mode.mjpeg && !result.contains(mode.index))
                    .min(preference).orElse(null);
                if (alternative != null) result.add(alternative.index);
            }
        }
        return result;
    }

    /** Old native callbacks and queued encodes cannot commit to a later attempt. */
    static final class Attempts {
        private final long generation;
        private int current;
        private boolean closed;
        Attempts(long generation) { this.generation = generation; }
        synchronized int begin() {
            if (closed || current >= MAX_ATTEMPTS) return 0;
            return ++current;
        }
        synchronized boolean accepts(long candidateGeneration, int attempt) {
            return !closed && attempt > 0 && generation == candidateGeneration && current == attempt;
        }
        synchronized void close() { closed = true; }
    }
}
