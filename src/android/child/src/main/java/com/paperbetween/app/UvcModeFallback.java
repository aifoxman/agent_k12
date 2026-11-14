package com.paperbetween.app;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

/** Bounded fallback over published modes only; independent of Android/JNI. */
final class UvcModeFallback {
    static final int MAX_ATTEMPTS = 3;
    static final long FIRST_FRAME_TIMEOUT_MS = 4000;
    static final long OPEN_TIMEOUT_MS = 20000;

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

    private static boolean usable(Mode mode) {
        return mode.width >= 1280 && mode.width <= 1920 && mode.height >= 480
            && mode.height <= 1440 && mode.width % 2 == 0 && mode.height % 2 == 0
            && mode.fps > 0;
    }

    static List<Integer> candidates(List<Mode> published) {
        List<Integer> result = new ArrayList<>();
        // Prefer the paper-friendly 4:3 sensor mode over a wider, potentially
        // vertically cropped 16:9 mode. Keep native decode/encode bounds intact.
        Comparator<Mode> preference = Comparator.comparingInt((Mode mode) -> mode.mjpeg ? 0 : 1)
            .thenComparingLong(mode -> Math.abs((long) mode.width * 3 - (long) mode.height * 4)
                * 1000000 / mode.height)
            .thenComparingLong(mode -> (long) Math.abs(mode.width - 1920) * 10000
                + Math.abs(mode.height - 1440));
        Mode primary = published.stream().filter(UvcModeFallback::usable)
            .min(preference)
            .orElse(null);
        if (primary == null) return result;
        result.add(primary.index);
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
