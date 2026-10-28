"""Optional local speech transcription; raw clips stay in memory."""

from __future__ import annotations

import io
import os
from functools import lru_cache
from importlib.util import find_spec
from pathlib import Path


def ready(model_path: str | None) -> bool:
    return bool(
        model_path
        and (Path(model_path) / "model.bin").is_file()
        and (Path(model_path) / "config.json").is_file()
        and (Path(model_path) / "vocabulary.txt").is_file()
        and find_spec("faster_whisper")
    )


@lru_cache(maxsize=1)
def _model(model_path: str):
    from faster_whisper import WhisperModel

    return WhisperModel(
        model_path,
        device="cpu",
        compute_type="int8",
        cpu_threads=min(6, os.cpu_count() or 4),
        local_files_only=True,
    )


def transcribe(model_path: str, audio: bytes) -> str:
    segments, _ = _model(model_path).transcribe(
        io.BytesIO(audio),
        language="zh",
        beam_size=3,
        vad_filter=True,
        vad_parameters={"min_speech_duration_ms": 200, "min_silence_duration_ms": 500},
        condition_on_previous_text=False,
    )
    return "".join(segment.text for segment in segments).strip()
