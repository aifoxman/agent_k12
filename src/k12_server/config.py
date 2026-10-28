"""Runtime configuration loaded only from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_path: str
    app_secret: str
    allowed_origins: tuple[str, ...]
    ai_api_url: str | None
    ai_api_key: str | None
    ai_model: str | None
    ai_fast_vision_model: str | None
    ai_timeout_seconds: float
    correction_timeout_seconds: float
    asr_api_url: str | None
    asr_api_key: str | None
    asr_model: str | None
    asr_local_model_path: str | None
    tts_api_key: str | None
    tts_workspace_id: str | None
    tts_provider: str
    tencent_tts_app_id: str | None
    tencent_tts_secret_id: str | None
    tencent_tts_secret_key: str | None
    tencent_tts_voice_type: int
    turn_host: str | None
    turn_shared_secret: str | None
    turn_port: int
    correction_require_parent_approval: bool = True
    tts_quota_mode: str = "application"
    tencent_tts_fallback_voice_types: tuple[int, ...] = ()

    @classmethod
    def from_env(cls) -> Settings:
        secret = os.getenv("APP_SECRET", "")
        if len(secret) < 32 or secret.startswith("replace-with-"):
            raise RuntimeError("APP_SECRET must contain at least 32 random characters")
        ai_url = os.getenv("AI_API_URL") or None
        if ai_url and not (ai_url.startswith("https://") or ai_url.startswith("http://localhost:")
                           or ai_url.startswith("http://127.0.0.1:")):
            raise RuntimeError("AI_API_URL must use HTTPS outside localhost")
        asr_url = os.getenv("ASR_API_URL") or None
        if asr_url and not (asr_url.startswith("https://") or asr_url.startswith("http://localhost:")
                            or asr_url.startswith("http://127.0.0.1:")):
            raise RuntimeError("ASR_API_URL must use HTTPS outside localhost")
        tts_provider = os.getenv("TTS_PROVIDER", "aliyun").strip().lower()
        if tts_provider not in {"tencent", "aliyun"}:
            raise RuntimeError("TTS_PROVIDER must be tencent or aliyun")
        tts_quota_mode = os.getenv("TTS_QUOTA_MODE", "application").strip().lower()
        if tts_quota_mode not in {"application", "provider"}:
            raise RuntimeError("TTS_QUOTA_MODE must be application or provider")
        fallback_voices = os.getenv("TENCENT_TTS_FALLBACK_VOICE_TYPES", "").strip()
        try:
            fallback_voice_types = tuple(dict.fromkeys(
                int(voice.strip()) for voice in fallback_voices.split(",")
            )) if fallback_voices else ()
        except ValueError as exc:
            raise RuntimeError("TENCENT_TTS_FALLBACK_VOICE_TYPES must contain positive voice IDs") from exc
        if any(voice <= 0 for voice in fallback_voice_types):
            raise RuntimeError("TENCENT_TTS_FALLBACK_VOICE_TYPES must contain positive voice IDs")
        turn_host = os.getenv("TURN_HOST") or None
        turn_secret = os.getenv("TURN_SHARED_SECRET") or None
        if turn_host and (not turn_secret or len(turn_secret) < 32):
            raise RuntimeError("TURN_SHARED_SECRET must contain at least 32 characters")
        origins = os.getenv(
            "ALLOWED_ORIGINS",
            "http://localhost:5173,http://127.0.0.1:5173,https://appassets.androidplatform.net",
        )
        correction_approval = os.getenv("CORRECTION_REQUIRE_PARENT_APPROVAL", "true").strip().lower()
        if correction_approval not in {"true", "false", "1", "0", "yes", "no", "on", "off"}:
            raise RuntimeError("CORRECTION_REQUIRE_PARENT_APPROVAL must be a boolean")
        return cls(
            database_path=os.getenv("DATABASE_PATH", "./data/homework.sqlite3"),
            app_secret=secret,
            allowed_origins=tuple(origin.strip() for origin in origins.split(",") if origin.strip()),
            ai_api_url=ai_url,
            ai_api_key=os.getenv("AI_API_KEY") or None,
            ai_model=os.getenv("AI_MODEL") or None,
            ai_fast_vision_model=os.getenv("AI_FAST_VISION_MODEL", "gpt-6-luna") or None,
            ai_timeout_seconds=float(os.getenv("AI_TIMEOUT_SECONDS", "120")),
            correction_timeout_seconds=float(os.getenv("CORRECTION_TIMEOUT_SECONDS", "240")),
            asr_api_url=asr_url,
            asr_api_key=os.getenv("ASR_API_KEY") or None,
            asr_model=os.getenv("ASR_MODEL") or None,
            asr_local_model_path=os.getenv("ASR_LOCAL_MODEL_PATH") or None,
            tts_api_key=os.getenv("TTS_API_KEY") or None,
            tts_workspace_id=os.getenv("TTS_WORKSPACE_ID") or None,
            tts_provider=tts_provider,
            tencent_tts_app_id=os.getenv("TENCENT_TTS_APP_ID") or None,
            tencent_tts_secret_id=os.getenv("TENCENT_TTS_SECRET_ID") or None,
            tencent_tts_secret_key=os.getenv("TENCENT_TTS_SECRET_KEY") or None,
            tencent_tts_voice_type=int(os.getenv("TENCENT_TTS_VOICE_TYPE", "403005")),
            turn_host=turn_host,
            turn_shared_secret=turn_secret,
            turn_port=int(os.getenv("TURN_PORT", "3478")),
            correction_require_parent_approval=correction_approval in {"true", "1", "yes", "on"},
            tts_quota_mode=tts_quota_mode,
            tencent_tts_fallback_voice_types=fallback_voice_types,
        )
