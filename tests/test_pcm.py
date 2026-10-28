"""PCM adaptation preserves speech timing and split-frame sample boundaries."""

from __future__ import annotations

import math
import random
import struct

import pytest

from k12_server.pcm import Pcm16To24Resampler


def pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def samples(audio):
    return [value for (value,) in struct.iter_unpack("<h", audio)]


def convert(chunks):
    resampler = Pcm16To24Resampler()
    return b"".join([*(resampler.feed(chunk) for chunk in chunks), resampler.finish()])


def test_interpolates_ramp_and_flushes_final_sample_interval():
    assert samples(convert([pcm([0, 300, 600, 900])])) == [0, 200, 400, 600, 800, 900]
    assert samples(convert([pcm([-32768, 0, 32767])])) == [
        -32768, -10923, 10922, 32767, 32767,
    ]


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 5, 31, 160, 16000, 16001])
def test_output_duration_is_within_half_a_target_sample(count):
    audio = convert([pcm([1200] * count)])
    assert len(audio) // 2 == math.ceil(count * 1.5)
    assert abs(len(audio) / 2 / 24000 - count / 16000) <= 0.5 / 24000 + 1e-15
    assert set(samples(audio)) <= {1200}


def test_arbitrary_byte_splits_and_empty_chunks_do_not_change_audio():
    rng = random.Random(231)
    source = pcm([rng.randint(-32768, 32767) for _ in range(16001)])
    chunks = []
    position = 0
    while position < len(source):
        size = rng.randrange(1, 87)
        chunks.extend([b"", source[position:position + size]])
        position += size
    assert convert(chunks) == convert([source])
    assert convert(bytes([byte]) for byte in source) == convert([source])


def test_all_signed_sample_values_remain_in_range_and_preserve_endpoints():
    audio = convert([pcm(list(range(-32768, 32768)))])
    output = samples(audio)
    assert len(output) == 65536 * 3 // 2
    assert output[0] == -32768
    assert output[-1] == 32767
    assert all(-32768 <= value <= 32767 for value in output)
    assert all(left <= right for left, right in zip(output, output[1:], strict=False))


def test_speech_frequency_and_one_second_duration_are_preserved():
    source = [round(20000 * math.sin(2 * math.pi * 1000 * index / 16000))
              for index in range(16000)]
    output = samples(convert([pcm(source)]))
    assert len(output) == 24000
    crossings = sum(left <= 0 < right for left, right in zip(output, output[1:], strict=False))
    assert crossings == 1000
    assert max(abs(actual - 20000 * math.sin(2 * math.pi * 1000 * index / 24000))
               for index, actual in enumerate(output[:-1])) < 400


def test_split_sample_is_held_until_its_second_byte_arrives():
    resampler = Pcm16To24Resampler()
    assert resampler.feed(b"\xff") == b""
    assert resampler.feed(b"\x7f") == pcm([32767])
    assert resampler.finish() == pcm([32767])


def test_truncated_final_sample_is_rejected_instead_of_discarded():
    resampler = Pcm16To24Resampler()
    assert resampler.feed(pcm([300]) + b"\x00") == pcm([300])
    with pytest.raises(ValueError, match="incomplete sample"):
        resampler.finish()


def test_finish_is_idempotent_and_feed_after_finish_is_rejected():
    resampler = Pcm16To24Resampler()
    assert resampler.feed(pcm([300])) == pcm([300])
    assert resampler.finish() == pcm([300])
    assert resampler.finish() == b""
    with pytest.raises(ValueError, match="already finished"):
        resampler.feed(b"")


def test_no_final_audio_is_invented_for_empty_stream():
    resampler = Pcm16To24Resampler()
    assert resampler.feed(b"") == b""
    assert resampler.finish() == b""
