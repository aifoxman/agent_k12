"""Download a Chinese-capable Whisper model for local family-server transcription."""

from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import snapshot_download


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--size", choices=("base", "small"), default="base")
    parser.add_argument("--output", type=Path, default=Path("data/asr-model"))
    args = parser.parse_args()
    destination = args.output.resolve()
    snapshot_download(
        repo_id=f"Systran/faster-whisper-{args.size}",
        local_dir=destination,
        allow_patterns=("*.bin", "*.json", "vocabulary.txt"),
    )
    if not all((destination / file).is_file() for file in
               ("model.bin", "config.json", "vocabulary.txt")):
        raise RuntimeError("Downloaded model is incomplete")
    print(f"Local ASR model ready: {destination}")


if __name__ == "__main__":
    main()
