"""Compile the actual OCR bridge against cached Android/ML Kit APIs; run pure policy checks."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


def test_native_ocr_binding_limits_and_cancellation(tmp_path):
    javac, java = shutil.which("javac"), shutil.which("java")
    if not javac or not java:
        pytest.skip("Native OCR checks require an available JDK")
    root = Path(__file__).resolve().parents[1]
    sdk = Path(os.environ.get("ANDROID_HOME", root / ".tools/sdk"))
    platforms = sorted((sdk / "platforms").glob("android-*/android.jar"))
    cache = Path(os.environ.get("GRADLE_USER_HOME", Path.home() / ".gradle")) / "caches"
    jars = sorted(
        p
        for p in cache.glob("*/transforms/*/transformed/*-api.jar")
        if re.search(
            r"mlkit|vision|text-recognition|play-services-(tasks|base|basement)|^common-|^image-",
            p.name,
        )
    )
    jars.extend(
        sorted(cache.glob("modules-2/files-2.1/androidx.lifecycle/lifecycle-common/*/*/*.jar"))[-1:]
    )
    if not platforms or not any("mlkit-text-recognition-common" in p.name for p in jars):
        pytest.skip("Native OCR checks require the project's cached Android/ML Kit build APIs")
    classpath = os.pathsep.join(str(p) for p in [platforms[-1], *jars])
    source = root / "src/android/child/src/main/java/com/paperbetween/app/PaperVision.java"
    harness = root / "tests/native/PaperVisionAnchorsHarness.java"
    compiled = subprocess.run(
        [
            javac,
            "-J-Duser.language=en",
            "-J-Duser.country=US",
            "-encoding",
            "UTF-8",
            "-cp",
            classpath,
            "-d",
            str(tmp_path),
            str(source),
            str(harness),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        [java, "-cp", str(tmp_path), "com.paperbetween.app.PaperVisionAnchorsHarness"],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert (
        "native OCR hash, confidence, bounds, budget and late-callback checks passed"
        in result.stdout
    )
