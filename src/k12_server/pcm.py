"""Streaming adapters for mono signed PCM16 little-endian speech audio."""

from __future__ import annotations

import struct


class Pcm16To24Resampler:
    """Convert 16 kHz to 24 kHz without changing playback speed or pitch.

    Linear interpolation uses an integer 3:2 phase, so output is independent of
    input chunk boundaries. The last sample is held through the final source
    sample's interval; output contains ``ceil(input_samples * 3 / 2)`` samples.
    Only a trailing byte and the preceding sample are retained between calls.
    """

    def __init__(self) -> None:
        self._pending = b""
        self._previous = 0
        self._input_samples = 0
        self._output_samples = 0
        self._finished = False

    def feed(self, chunk: bytes) -> bytes:
        """Return available audio, retaining split samples for the next chunk."""
        if self._finished:
            raise ValueError("PCM resampler is already finished")
        data = self._pending + chunk
        complete = len(data) - len(data) % 2
        self._pending = data[complete:]
        output = bytearray()
        for (sample,) in struct.iter_unpack("<h", memoryview(data)[:complete]):
            source_index = self._input_samples
            if source_index == 0:
                output.extend(struct.pack("<h", sample))
                self._output_samples = 1
            else:
                # Output position in source-sample units is output_index * 2/3.
                while self._output_samples * 2 <= source_index * 3:
                    fraction = self._output_samples * 2 - (source_index - 1) * 3
                    weighted = self._previous * (3 - fraction) + sample * fraction
                    # Division by three has no half ties; this rounds signed
                    # samples to nearest without floating point or overflow.
                    interpolated = (weighted + 1) // 3
                    output.extend(struct.pack("<h", interpolated))
                    self._output_samples += 1
            self._previous = sample
            self._input_samples += 1
        return bytes(output)

    def finish(self) -> bytes:
        """Flush the last interval, rejecting a truncated PCM16 sample.

        Call only when the upstream stream completed successfully. Repeated
        calls return no additional audio, and subsequent feeds are rejected.
        """
        if self._finished:
            return b""
        self._finished = True
        if self._pending:
            raise ValueError("PCM16 stream ended with an incomplete sample")
        target_samples = (self._input_samples * 3 + 1) // 2
        remaining = target_samples - self._output_samples
        self._output_samples = target_samples
        return struct.pack("<h", self._previous) * remaining
