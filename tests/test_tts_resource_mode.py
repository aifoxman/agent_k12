"""Cloud resource mode preserves consent and accounting without the app trial cap."""

from __future__ import annotations

import json
import struct
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_tts import make_client, paired_family, speech_request

from k12_server.config import Settings
from k12_server.tts import billable_characters


@pytest.fixture
def resource_family(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_QUOTA_MODE", "provider")
    client, module = make_client(monkeypatch, tmp_path, provider="tencent")
    parent, child = paired_family(client)
    return client, module, parent, child


def test_quota_mode_defaults_to_existing_application_allowance(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.delenv("TTS_QUOTA_MODE", raising=False)
    monkeypatch.delenv("TENCENT_TTS_VOICE_TYPE", raising=False)
    monkeypatch.delenv("TENCENT_TTS_FALLBACK_VOICE_TYPES", raising=False)
    settings = Settings.from_env()
    assert settings.tts_quota_mode == "application"
    assert settings.tencent_tts_voice_type == 403005
    assert settings.tencent_tts_fallback_voice_types == ()


@pytest.mark.parametrize("value, expected", [
    ("application", "application"), (" PROVIDER ", "provider"),
])
def test_quota_mode_is_explicit(monkeypatch, value, expected):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("TTS_QUOTA_MODE", value)
    assert Settings.from_env().tts_quota_mode == expected


@pytest.mark.parametrize("value", ["", "false", "unlimited", "cloud", "provder"])
def test_invalid_quota_mode_cannot_silently_remove_local_limit(monkeypatch, value):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("TTS_QUOTA_MODE", value)
    with pytest.raises(RuntimeError, match="TTS_QUOTA_MODE"):
        Settings.from_env()


def test_fallback_voice_configuration_keeps_order_and_deduplicates(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("TENCENT_TTS_FALLBACK_VOICE_TYPES", "501002, 101001,501002")
    assert Settings.from_env().tencent_tts_fallback_voice_types == (501002, 101001)


@pytest.mark.parametrize("value", ["0", "-1", "501002,,101001", "voice", "1.5", "101001,"])
def test_invalid_fallback_voice_configuration_is_rejected(monkeypatch, value):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    monkeypatch.setenv("TENCENT_TTS_FALLBACK_VOICE_TYPES", value)
    with pytest.raises(RuntimeError, match="TENCENT_TTS_FALLBACK_VOICE_TYPES"):
        Settings.from_env()


def test_provider_mode_reports_policy_without_inventing_cloud_balance(resource_family):
    client, _module, parent, child = resource_family
    result = client.patch("/v1/families/voice-preferences", json={"enabled": True},
                          headers=parent)
    assert result.json() == {
        "enabled": True, "used_characters": 0, "trial_characters": None,
        "quota_mode": "provider",
    }
    for principal in (parent, child):
        dashboard = client.get("/v1/families/dashboard", headers=principal).json()
        assert dashboard["family"]["premium_voice_quota_mode"] == "provider"
        assert dashboard["family"]["premium_voice_trial_chars"] is None
        assert dashboard["family"]["premium_voice_used_chars"] == 0
        assert dashboard["tts_provider"] == "腾讯云"


def test_provider_mode_keeps_parent_opt_in_and_paired_child_guard(resource_family, monkeypatch):
    client, module, parent, child = resource_family
    calls = []

    def audio(*args):
        calls.append(args)
        yield b"\x01\x00"

    monkeypatch.setattr(module, "stream_tts", audio)
    assert speech_request(client, child).json()["error"]["code"] == "voice_not_enabled"
    assert client.patch("/v1/families/voice-preferences", json={"enabled": True},
                        headers=child).status_code == 403
    assert speech_request(client, parent).json()["error"]["code"] == "forbidden"
    assert not calls
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    assert speech_request(client, child).status_code == 200
    client.patch("/v1/families/voice-preferences", json={"enabled": False}, headers=parent)
    assert speech_request(client, child).json()["error"]["code"] == "voice_not_enabled"
    assert len(calls) == 1


def test_provider_mode_preserves_usage_reservations_and_idempotency(resource_family, monkeypatch):
    client, module, parent, child = resource_family
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE families SET premium_voice_used_chars=20007")
        family_id = db.execute("SELECT id FROM families").fetchone()[0]
        db.execute("INSERT INTO speech_requests VALUES(?,?,?,?,?,?)",
                   ("pending", family_id, "other-request", 5, "reserved", module.current()))
    calls = []

    def audio(settings, text, audio_format, speed):
        with module.connect(settings.database_path) as db:
            assert db.execute("SELECT COUNT(*) FROM speech_requests WHERE status='reserved'").fetchone()[0] == 2
            assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == 20007
        calls.append((settings.tts_provider, text, audio_format, speed))
        yield b"\x01\x00"
        yield b"\x02\x00"

    monkeypatch.setattr(module, "stream_tts", audio)
    headers = {**child, "Idempotency-Key": "beyond-application-trial"}
    response = speech_request(client, headers)
    assert response.status_code == 200 and response.content == b"\x01\x00\x02\x00"
    assert speech_request(client, headers).json()["error"]["code"] == "duplicate_request"
    assert calls == [("tencent", "先看这一行。", "pcm", 1)]
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == 20007 + billable_characters("先看这一行。")
        assert dict(db.execute("SELECT id,status FROM speech_requests")) == {
            "pending": "reserved",
            db.execute("SELECT id FROM speech_requests WHERE status='charged'").fetchone()[0]: "charged",
        }
    monkeypatch.setattr(module, "settings", replace(module.settings, tts_quota_mode="application"))
    assert speech_request(client, child).json()["error"]["code"] == "insufficient_quota"
    dashboard = client.get("/v1/families/dashboard", headers=child).json()["family"]
    assert dashboard["premium_voice_quota_mode"] == "application"
    assert dashboard["premium_voice_trial_chars"] == 20000
    assert dashboard["premium_voice_used_chars"] == 20007 + billable_characters("先看这一行。")


def test_provider_failure_releases_only_current_reservation_without_refunding_history(resource_family, monkeypatch):
    client, module, parent, child = resource_family
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE families SET premium_voice_used_chars=20007")

    def unavailable(*_args):
        raise module.SpeechServiceError("unavailable")
        yield b""  # pragma: no cover

    monkeypatch.setattr(module, "stream_tts", unavailable)
    assert speech_request(client, child).json()["error"]["code"] == "provider_error"
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM speech_requests").fetchone()[0] == 0
        assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == 20007


class FakeTencentSocket:
    def __init__(self, frames):
        self.frames = iter(frames)
        self.sent = []
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def recv(self, **_kwargs):
        return next(self.frames)

    def send(self, message):
        self.sent.append(json.loads(message))


@pytest.mark.parametrize("audio_format", ["pcm", "mp3"])
def test_general_voice_uses_native_16k_and_preserves_public_output_contract(monkeypatch, audio_format):
    import k12_server.tts as tts

    native_audio = struct.pack("<hhh", 0, 3000, 6000)
    socket = FakeTencentSocket([
        json.dumps({"code": 0, "ready": 1}),
        native_audio[:1], native_audio[1:4], native_audio[4:],
        json.dumps({"code": 0, "final": 1}),
    ])
    urls = []

    def connect(url, **_kwargs):
        urls.append(url)
        return socket

    monkeypatch.setattr(tts, "connect", connect)
    result = b"".join(tts.tencent_audio("123", "secret-id", "secret-key", 101001,
                                      "先看这一步。", audio_format, 1))
    assert socket.closed
    params = parse_qs(urlsplit(urls[0]).query)
    assert params["VoiceType"] == ["101001"]
    assert params["SampleRate"] == ["16000"]
    assert params["Speed"] == ["0.0"]
    assert params["Codec"] == [audio_format]
    if audio_format == "pcm":
        assert struct.unpack("<hhhhh", result) == (0, 2000, 4000, 6000, 6000)
    else:
        assert result == native_audio


def test_general_voice_cancel_closes_socket_without_flushing_remaining_audio(monkeypatch):
    import k12_server.tts as tts

    socket = FakeTencentSocket([
        json.dumps({"code": 0, "ready": 1}), struct.pack("<hh", 0, 3000),
        struct.pack("<h", 6000), json.dumps({"code": 0, "final": 1}),
    ])
    monkeypatch.setattr(tts, "connect", lambda *_args, **_kwargs: socket)
    audio = tts.tencent_audio("123", "secret-id", "secret-key", 101001,
                              "先看这一步。", "pcm", 1)
    assert next(audio)
    audio.close()
    assert socket.closed
    assert next(socket.frames) == struct.pack("<h", 6000)


def test_general_voice_rejects_truncated_pcm_instead_of_dropping_bytes(monkeypatch):
    import k12_server.tts as tts

    socket = FakeTencentSocket([
        json.dumps({"code": 0, "ready": 1}), b"\x01",
        json.dumps({"code": 0, "final": 1}),
    ])
    monkeypatch.setattr(tts, "connect", lambda *_args, **_kwargs: socket)
    with pytest.raises(tts.SpeechServiceError):
        list(tts.tencent_audio("123", "secret-id", "secret-key", 101001,
                              "先看这一步。", "pcm", 1))
    assert socket.closed


@pytest.mark.parametrize("audio_started", [False, True])
def test_provider_rejection_has_safe_structured_pre_audio_state(monkeypatch, audio_started):
    import k12_server.tts as tts

    frames = [json.dumps({"code": 0, "ready": 1})]
    if audio_started:
        frames.append(b"\x01\x00")
    frames.append(json.dumps({"code": 10003, "message": "must not expose provider details"}))
    socket = FakeTencentSocket(frames)
    monkeypatch.setattr(tts, "connect", lambda *_args, **_kwargs: socket)
    with pytest.raises(tts.TencentSpeechServiceError) as failure:
        list(tts.tencent_audio("123", "secret-id", "secret-key", 502001,
                              "先看这一步。", "pcm", 1))
    assert failure.value.code == 10003
    assert failure.value.audio_started is audio_started
    assert "secret" not in str(failure.value)
    assert "provider details" not in str(failure.value)
    assert socket.closed


def _resource_settings(monkeypatch):
    monkeypatch.setenv("APP_SECRET", "test-secret-abcdefghijklmnopqrstuvwxyz-123456")
    return replace(Settings.from_env(), tts_provider="tencent", tencent_tts_voice_type=502001,
                   tencent_tts_fallback_voice_types=(502001, 501002, 101001, 501002))


def test_resource_fallback_uses_ordered_unique_voices_only_before_audio(monkeypatch):
    import k12_server.tts as tts

    settings = _resource_settings(monkeypatch)
    calls, closed = [], []

    def audio(_app_id, _secret_id, _secret_key, voice, text, audio_format, speed):
        calls.append((voice, text, audio_format, speed))
        try:
            if voice != 101001:
                raise tts.TencentSpeechServiceError(3022, "test-session", False)
            yield b"\x01\x00"
            yield b"\x02\x00"
        finally:
            closed.append(voice)

    monkeypatch.setattr(tts, "tencent_audio", audio)
    assert b"".join(tts.stream_tts(settings, "先看这一步。", "pcm", 1)) == b"\x01\x00\x02\x00"
    assert calls == [(voice, "先看这一步。", "pcm", 1) for voice in (502001, 501002, 101001)]
    assert closed == [502001, 501002, 101001]


@pytest.mark.parametrize("code", [10003, 10001, 400, 3023, "3022", None])
def test_unconfirmed_errors_never_select_a_different_resource_pool(monkeypatch, code):
    import k12_server.tts as tts

    settings = _resource_settings(monkeypatch)
    calls = []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        raise tts.TencentSpeechServiceError(code, "test-session", False)
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "tencent_audio", audio)
    with pytest.raises(tts.TencentSpeechServiceError) as failure:
        list(tts.stream_tts(settings, "先看这一步。", "pcm", 1))
    assert failure.value.code == code
    assert calls == [502001]


@pytest.mark.parametrize("error", [OSError("network failure"), TimeoutError("timeout")])
def test_network_and_timeout_errors_do_not_retry_another_voice(monkeypatch, error):
    import k12_server.tts as tts

    settings = _resource_settings(monkeypatch)
    calls = []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        raise error
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "tencent_audio", audio)
    with pytest.raises(type(error)):
        list(tts.stream_tts(settings, "先看这一步。", "pcm", 1))
    assert calls == [502001]


@pytest.mark.parametrize("yielded_audio, provider_started", [(True, False), (True, True), (False, True)])
def test_partial_audio_never_switches_voice_or_replays_the_sentence(monkeypatch, yielded_audio, provider_started):
    import k12_server.tts as tts

    settings = _resource_settings(monkeypatch)
    calls, played = [], []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        if yielded_audio:
            yield b"\x01\x00"
        raise tts.TencentSpeechServiceError(3022, "test-session", provider_started)

    monkeypatch.setattr(tts, "tencent_audio", audio)
    with pytest.raises(tts.TencentSpeechServiceError):
        for chunk in tts.stream_tts(settings, "先看这一步。", "pcm", 1):
            played.append(chunk)
    assert calls == [502001]
    assert played == ([b"\x01\x00"] if yielded_audio else [])


def test_fallback_stream_cancel_closes_active_generator_without_selecting_another_voice(monkeypatch):
    import k12_server.tts as tts

    settings = _resource_settings(monkeypatch)
    calls, closed = [], []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        try:
            yield b"\x01\x00"
            yield b"\x02\x00"
        finally:
            closed.append(voice)

    monkeypatch.setattr(tts, "tencent_audio", audio)
    stream = tts.stream_tts(settings, "先看这一步。", "pcm", 1)
    assert next(stream) == b"\x01\x00"
    stream.close()
    assert calls == closed == [502001]


def test_all_resource_pools_exhausted_returns_quota_without_charging_or_resetting(resource_family, monkeypatch):
    import k12_server.tts as tts

    client, module, parent, child = resource_family
    monkeypatch.setattr(module, "settings", replace(module.settings,
        tencent_tts_voice_type=502001, tencent_tts_fallback_voice_types=(501002, 101001)))
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    with module.transaction(module.settings.database_path) as db:
        db.execute("UPDATE families SET premium_voice_used_chars=20007")
    calls = []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        raise tts.TencentSpeechServiceError(3022, "test-session", False)
        yield b""  # pragma: no cover

    monkeypatch.setattr(tts, "tencent_audio", audio)
    response = speech_request(client, {**child, "Idempotency-Key": "exhausted-resources"})
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "insufficient_quota"
    assert calls == [502001, 501002, 101001]
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM speech_requests").fetchone()[0] == 0
        assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == 20007


def test_successful_fallback_is_one_speech_request_and_one_usage_charge(resource_family, monkeypatch):
    import k12_server.tts as tts

    client, module, parent, child = resource_family
    monkeypatch.setattr(module, "settings", replace(module.settings,
        tencent_tts_voice_type=502001, tencent_tts_fallback_voice_types=(501002, 101001)))
    client.patch("/v1/families/voice-preferences", json={"enabled": True}, headers=parent)
    calls = []

    def audio(_app_id, _secret_id, _secret_key, voice, *_args):
        calls.append(voice)
        if voice == 502001:
            raise tts.TencentSpeechServiceError(3022, "test-session", False)
        yield b"\x01\x00"

    monkeypatch.setattr(tts, "tencent_audio", audio)
    response = speech_request(client, child)
    assert response.status_code == 200 and response.content == b"\x01\x00"
    assert calls == [502001, 501002]
    with module.connect(module.settings.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM speech_requests WHERE status='charged'").fetchone()[0] == 1
        assert db.execute("SELECT premium_voice_used_chars FROM families").fetchone()[0] == billable_characters("先看这一行。")
