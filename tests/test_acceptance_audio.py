"""Synthetic audio-loop safety and scoring, without network or microphone access."""

from __future__ import annotations

import io
import json
import wave

import httpx
import pytest

from scripts.acceptance.audio import compare_units, pcm_wav, review_operations, run_audio


def test_unit_numbers_alone_do_not_certify_misrecognized_math_operations():
    result = {
        "status": "passed",
        "checks": [],
        "input_text": "不能直接用1减25。",
        "transcript": "不能直接用1件25。",
    }
    review_operations(result)
    assert result["status"] == "blocked"
    assert result["checks"][-1]["id"] == "math-operations"
    assert result["operation_review"]["missing"] == [["1", "减", "25"]]


def test_spoken_chinese_numbers_and_operation_alias_are_equivalent():
    result = {
        "status": "passed",
        "checks": [],
        "input_text": "1减25。",
        "transcript": "一减去二十五。",
    }
    review_operations(result)
    assert result["status"] == "passed"
    assert result["checks"][-1]["status"] == "passed"


def synthetic_run(tmp_path):
    directory = tmp_path / "run-audio"
    directory.mkdir()
    state = {
        "synthetic": True,
        "run_id": directory.name,
        "base_url": "http://127.0.0.1:8016",
        "family_id": "synthetic-family",
        "parent_token": "private-parent-secret",
        "child_token": "private-child-secret",
    }
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")
    model = directory / "model/results"
    model.mkdir(parents=True)
    (model / "units_wrong_thought.json").write_text(
        json.dumps({"raw_api_response": {"response": "1米与25厘米需要先统一单位。"}}),
        encoding="utf-8",
    )
    return directory


def audio_api(
    directory, *, asr_ready=True, header=None, transcription="一米与二十五厘米需要先统一单位。"
):
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path == "/health":
            assert "authorization" not in request.headers
            return httpx.Response(
                200,
                json={"ok": True},
                headers={"X-K12-Acceptance-Run": directory.name if header is None else header},
            )
        if request.url.path == "/v1/families/dashboard":
            return httpx.Response(
                200,
                json={
                    "family": {
                        "id": "synthetic-family",
                        "premium_voice_enabled": 0,
                        "audio_transcription_allowed": 0,
                    },
                    "tts_available": True,
                    "asr_available": asr_ready,
                    "asr_provider": "家庭服务端本地模型" if asr_ready else None,
                },
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"ok": True})
        if request.url.path == "/v1/audio/speech":
            assert json.loads(request.content)["input"] == "1米与25厘米需要先统一单位。"
            return httpx.Response(200, content=b"\x01\x00" * 2400)
        if request.url.path == "/v1/voice/transcribe":
            assert b"RIFF" in request.content
            return httpx.Response(200, json={"text": transcription})
        raise AssertionError("Unexpected endpoint")

    return httpx.Client(
        base_url="http://127.0.0.1:8016", transport=httpx.MockTransport(respond)
    ), calls


def test_live_tts_local_asr_loop_restores_synthetic_consent_and_keeps_tokens_private(tmp_path):
    directory = synthetic_run(tmp_path)
    api, calls = audio_api(directory)
    with api:
        result = run_audio(directory, client=api)
    assert result["status"] == "passed"
    assert result["duration_seconds"] == 0.1
    assert result["source"]["audio"] == "live"
    assert result["source"]["input"] == "synthetic"
    assert result["human_audio_listening"] is False
    assert result["environment_recording"] is False
    assert result["consent_restored"] is True
    assert result["comparison"]["expected"] == ["1米", "25厘米"]
    assert (directory / "audio/speech.wav").is_file()
    assert (directory / "audio/transcript.json").is_file()
    patches = [json.loads(request.content) for request in calls if request.method == "PATCH"]
    assert patches == [{"enabled": True}, {"allowed": True}, {"enabled": False}, {"allowed": False}]
    report = (directory / "audio-report.json").read_text(encoding="utf-8")
    assert "private-parent-secret" not in report
    assert "private-child-secret" not in report


def test_missing_local_asr_keeps_tts_evidence_but_does_not_enable_transcription(tmp_path):
    directory = synthetic_run(tmp_path)
    api, calls = audio_api(directory, asr_ready=False)
    with api:
        result = run_audio(directory, client=api)
    assert result["status"] == "blocked"
    assert result["source"]["audio"] == "live"
    assert result["tts_attempted"] is True
    assert result["asr_attempted"] is False
    assert not any(
        request.url.path in {"/v1/families/audio-transcription", "/v1/voice/transcribe"}
        for request in calls
    )
    assert [json.loads(request.content) for request in calls if request.method == "PATCH"] == [
        {"enabled": True},
        {"enabled": False},
    ]
    assert (directory / "audio/speech.wav").is_file()
    assert result["checks"][0]["status"] == "passed"
    assert result["checks"][1]["source"]["audio"] == "none"


def test_wrong_health_header_stops_before_reading_state_or_sending_auth(tmp_path):
    directory = tmp_path / "run-with-no-state"
    api, calls = audio_api(directory, header="different-run")
    with api:
        result = run_audio(directory, client=api)
    assert result["status"] == "blocked"
    assert result["source"]["audio"] == "none"
    assert len(calls) == 1
    assert "authorization" not in calls[0].headers


def test_changed_number_fails_the_gate_instead_of_accepting_plausible_transcript(tmp_path):
    directory = synthetic_run(tmp_path)
    api, _ = audio_api(directory, transcription="一米与二百五十厘米需要先统一单位。")
    with api:
        result = run_audio(directory, client=api)
    assert result["status"] == "failed"
    assert result["comparison"]["missing"] == ["25厘米"]


def test_equivalent_chinese_numbers_and_wav_header():
    assert compare_units("1.0米、25厘米、2026秒", "一点零米、二十五厘米、二〇二六秒")["preserved"]
    assert not compare_units("没有数字", "没有数字")["preserved"]
    wav, duration = pcm_wav(b"\x00\x00" * 24000)
    assert duration == 1
    with wave.open(io.BytesIO(wav), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 24000)
        assert audio.getnframes() == 24000
    with pytest.raises(ValueError):
        pcm_wav(b"x")


def test_production_api_and_nonsynthetic_state_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        run_audio(tmp_path, "http://127.0.0.1:8003")
    directory = synthetic_run(tmp_path)
    state_path = directory / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["synthetic"] = False
    state_path.write_text(json.dumps(state), encoding="utf-8")
    api, calls = audio_api(directory)
    with api:
        result = run_audio(directory, client=api)
    assert result["status"] == "blocked"
    assert len(calls) == 1
