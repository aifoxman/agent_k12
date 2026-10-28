"""Premium speech contract, consent, quota, and pronunciation."""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from k12_server.tts import billable_characters, provider_audio, speech_markup, spoken_text


def make_client(monkeypatch, tmp_path, provider="aliyun"):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "speech.sqlite3"))
    monkeypatch.setenv("TTS_API_KEY", "test-tts-key")
    monkeypatch.setenv("TTS_WORKSPACE_ID", "test-workspace")
    monkeypatch.setenv("TTS_PROVIDER", provider)
    if provider == "tencent":
        monkeypatch.setenv("TENCENT_TTS_APP_ID", "123456789")
        monkeypatch.setenv("TENCENT_TTS_SECRET_ID", "test-secret-id")
        monkeypatch.setenv("TENCENT_TTS_SECRET_KEY", "test-secret-key")
    module = importlib.import_module("k12_server.app")
    module = importlib.reload(module)
    return TestClient(module.app), module


def paired_family(client):
    registered = client.post("/v1/auth/register", json={
        "email": "voice@example.com", "password": "long-test-password",
        "child_name": "小雨", "age_band": "primary",
    })
    parent = {"Authorization": "Bearer " + registered.json()["token"]}
    claim = client.post("/v1/devices/claim", json={}).json()
    assert client.post("/v1/families/pair", json={"code": claim["pairing_code"]},
                       headers=parent).status_code == 200
    status = client.post("/v1/devices/status", json={
        "device_id": claim["device_id"], "device_secret": claim["device_secret"],
    }).json()
    child = {"Authorization": "Bearer " + status["token"]}
    return parent, child


def speech_request(client, child, **changes):
    body = {"model": "k12-tts", "input": "先看这一行。", "voice": "teacher-warm-female",
            "response_format": "pcm", "speed": 1, "stream_format": "audio"}
    body.update(changes)
    return client.post("/v1/audio/speech", json=body, headers=child)


def test_pronunciation_uses_whole_word_and_escapes_markup():
    text = spoken_text("**重新** 看 2×3=6 & 角度")
    markup = speech_markup(text)
    assert "<phoneme alphabet=\"py\" ph=\"chong2 xin1\">重新</phoneme>" in markup
    assert "<phoneme alphabet=\"py\" ph=\"jiao3 du4\">角度</phoneme>" in markup
    assert "2乘以3等于6" in markup
    assert "&amp;" in markup
    assert billable_characters("一行 A") == 6


def test_speech_requires_parent_opt_in_and_returns_openai_shaped_audio(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    parent, child = paired_family(client)
    assert speech_request(client, child).json()["error"]["code"] == "voice_not_enabled"
    assert client.patch("/v1/families/voice-preferences", json={"enabled": True},
                        headers=child).status_code == 403
    assert client.patch("/v1/families/voice-preferences", json={"enabled": True},
                        headers=parent).status_code == 200
    calls = []

    def fake_audio(settings, text, audio_format, speed):
        calls.append((settings.tts_api_key, settings.tts_workspace_id,
                      text, audio_format, speed))
        yield b"\x01\x00"
        yield b"\x02\x00"

    monkeypatch.setattr(module, "stream_tts", fake_audio)
    headers = {**child, "Idempotency-Key": "first-utterance"}
    result = speech_request(client, headers)
    assert result.status_code == 200
    assert result.headers["content-type"].startswith("audio/pcm")
    assert result.content == b"\x01\x00\x02\x00"
    assert calls == [("test-tts-key", "test-workspace", "先看这一行。", "pcm", 1)]
    assert speech_request(client, headers).json()["error"]["code"] == "duplicate_request"
    dashboard = client.get("/v1/families/dashboard", headers=parent).json()
    assert dashboard["family"]["premium_voice_used_chars"] == billable_characters("先看这一行。")
    assert dashboard["family"]["premium_voice_trial_chars"] == 20_000


def test_speech_failure_releases_trial_reservation(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    parent, child = paired_family(client)
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)

    def failed_audio(*_args):
        raise module.SpeechServiceError("unavailable")
        yield b""  # pragma: no cover

    monkeypatch.setattr(module, "stream_tts", failed_audio)
    response = speech_request(client, child)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_error"
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM speech_requests").fetchone()[0] == 0
        assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == 0


def test_speech_quota_and_unsupported_stream_mode(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path)
    parent, child = paired_family(client)
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    assert speech_request(client, child, stream_format="sse").json()["error"]["param"] == "stream_format"
    with module.connect(module.settings.database_path) as db:
        db.execute("UPDATE families SET premium_voice_used_chars=19999")
        db.commit()
    assert speech_request(client, child).json()["error"]["code"] == "insufficient_quota"


def test_provider_stream_decodes_audio_frames_and_checks_completion(monkeypatch):
    import k12_server.tts as tts

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def raise_for_status(self):
            pass

        def iter_lines(self):
            frames = [
                {"output": {"audio": {"data": base64.b64encode(b"\x01\x00").decode()}}},
                {"output": {"finish_reason": "stop", "audio": {"data": ""}}},
            ]
            return iter("data: " + json.dumps(frame) for frame in frames)

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            pass

        def stream(self, method, url, *, headers, json):
            assert method == "POST"
            assert url.startswith("https://test-workspace.cn-beijing.maas.aliyuncs.com/")
            assert headers["X-DashScope-SSE"] == "enable"
            assert json["input"]["format"] == "pcm"
            assert json["input"]["enable_ssml"] is True
            return FakeResponse()

    monkeypatch.setattr(tts.httpx, "Client", FakeClient)
    assert list(provider_audio("secret", "test-workspace", "重新看", "pcm", 1)) == [b"\x01\x00"]


@pytest.mark.parametrize(("rate", "provider_speed"), [(1, 0), (.85, -.75)])
def test_tencent_provider_signs_session_and_streams_pcm(monkeypatch, rate, provider_speed):
    import k12_server.tts as tts

    class FakeSocket:
        def __init__(self, frames=None):
            self.frames = iter(frames if frames is not None else [
                json.dumps({"code": 0, "ready": 0}),
                json.dumps({"code": 0, "ready": 1}),
                b"\x01\x00",
                b"\x02\x00",
                json.dumps({"code": 0, "final": 1}),
            ])
            self.sent = []
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.closed = True

        def recv(self, *, timeout):
            assert timeout in {8, 25}
            return next(self.frames)

        def send(self, data):
            self.sent.append(json.loads(data))

    socket = FakeSocket()
    urls = []

    def fake_connect(url, **_options):
        urls.append(url)
        return socket

    monkeypatch.setattr(tts, "connect", fake_connect)
    assert list(tts.tencent_audio("123456789", "test-secret-id", "test-secret-key",
                                  403005, "先看 2×3=6", "pcm", rate)) == [b"\x01\x00", b"\x02\x00"]
    assert socket.closed
    parsed = urlsplit(urls[0])
    assert (parsed.scheme, parsed.netloc, parsed.path) == (
        "wss", "tts.cloud.tencent.com", "/stream_wsv2")
    signed_query, signature = parsed.query.rsplit("&Signature=", 1)
    expected = base64.b64encode(hmac.new(b"test-secret-key",
        f"GETtts.cloud.tencent.com/stream_wsv2?{signed_query}".encode(),
        hashlib.sha1).digest()).decode()
    assert parse_qs(parsed.query)["Signature"] == [expected]
    params = parse_qs(parsed.query)
    assert params["VoiceType"] == ["403005"]
    assert params["SampleRate"] == ["24000"]
    assert params["Codec"] == ["pcm"]
    assert float(params["Speed"][0]) == provider_speed
    assert [message["action"] for message in socket.sent] == [
        "ACTION_SYNTHESIS", "ACTION_COMPLETE"]
    assert socket.sent[0]["data"] == "先看 2×3=6。"
    assert socket.sent[0]["session_id"] == params["SessionId"][0]

    socket = FakeSocket()
    canceled = tts.tencent_audio("123456789", "test-secret-id", "test-secret-key",
                                 403005, "先看这一步", "pcm", 1)
    assert next(canceled) == b"\x01\x00"
    canceled.close()
    assert socket.closed

    socket = FakeSocket([json.dumps({"code": 10003, "message": "auth failed"})])
    with pytest.raises(tts.SpeechServiceError, match="10003"):
        list(tts.tencent_audio("123456789", "test-secret-id", "test-secret-key",
                               403005, "先看这一步", "pcm", 1))
    assert socket.closed
    socket = FakeSocket([
        json.dumps({"code": 0, "ready": 1}),
        json.dumps({"code": 0, "final": 1}),
    ])
    with pytest.raises(tts.SpeechServiceError, match="no audio"):
        list(tts.tencent_audio("123456789", "test-secret-id", "test-secret-key",
                               403005, "先看这一步", "pcm", 1))
    assert socket.closed
    assert tts._tencent_text("<speak>银行</speak>") == "银行。"


def test_tencent_is_selected_without_aliyun_credentials(monkeypatch, tmp_path):
    client, module = make_client(monkeypatch, tmp_path, provider="tencent")
    monkeypatch.delenv("TTS_API_KEY")
    monkeypatch.delenv("TTS_WORKSPACE_ID")
    parent, child = paired_family(client)
    assert client.get("/v1/families/dashboard", headers=parent).json()["tts_provider"] == "腾讯云"
    assert client.patch("/v1/families/voice-preferences", json={"enabled": True},
                        headers=parent).status_code == 200
    calls = []

    def fake_audio(settings, text, audio_format, speed):
        calls.append((settings.tts_provider, settings.tencent_tts_voice_type,
                      text, audio_format, speed))
        yield b"\x01\x00"

    monkeypatch.setattr(module, "stream_tts", fake_audio)
    response = speech_request(client, child)
    assert response.status_code == 200
    assert response.content == b"\x01\x00"
    assert calls == [("tencent", 403005, "先看这一行。", "pcm", 1)]
