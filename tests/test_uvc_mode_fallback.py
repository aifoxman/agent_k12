"""Check native mode/attempt policy without opening USB or loading Android JNI."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_published_uvc_modes_and_attempt_lifecycle(tmp_path):
    javac = shutil.which("javac")
    java = shutil.which("java")
    if not javac or not java:
        pytest.skip("Native policy check requires an available JDK")
    root = Path(__file__).resolve().parents[1]
    source = root / "src/android/child/src/main/java/com/paperbetween/app/UvcModeFallback.java"
    harness = root / "tests/native/UvcModeFallbackHarness.java"
    subprocess.run(
        [javac, "-encoding", "UTF-8", "-d", str(tmp_path), str(source), str(harness)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    result = subprocess.run(
        [java, "-cp", str(tmp_path), "com.paperbetween.app.UvcModeFallbackHarness"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert "late-attempt lifecycle checks passed" in result.stdout
