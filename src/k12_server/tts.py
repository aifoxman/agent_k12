"""Provider-neutral speech output for the OpenAI-shaped speech endpoint."""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import re
import time
import uuid
from collections.abc import Iterator
from urllib.parse import quote, urlencode

import httpx
from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .config import Settings
from .pcm import Pcm16To24Resampler

TRIAL_CHARACTERS = 20_000
MODEL_ALIAS = "k12-tts"
VOICE_ALIAS = "teacher-warm-female"
PROVIDER_MODEL = "cosyvoice-v3-flash"
PROVIDER_VOICE = "longyingling_v3"
_TENCENT_HOST = "tts.cloud.tencent.com"
_TENCENT_PATH = "/stream_wsv2"
_LOG = logging.getLogger(__name__)

_PRONUNCIATIONS = {
    "重新": "chong2 xin1",
    "重量": "zhong4 liang4",
    "银行": "yin2 hang2",
    "一行": "yi4 hang2",
    "行吗": "xing2 ma5",
    "家长": "jia1 zhang3",
    "长方形": "chang2 fang1 xing2",
    "角度": "jiao3 du4",
    "角色": "jue2 se4",
    "得数": "de2 shu4",
}
_WORDS = re.compile("|".join(re.escape(word) for word in sorted(_PRONUNCIATIONS, key=len, reverse=True)))


class SpeechServiceError(Exception):
    """Provider failed before or during speech synthesis."""


class TencentSpeechServiceError(SpeechServiceError):
    """Structured provider rejection without signed URLs or provider message text."""

    def __init__(self, code: int | str | None, session_id: str, audio_started: bool):
        super().__init__(f"Tencent TTS error {code} session={session_id}")
        self.code = code
        self.audio_started = audio_started
        # Confirmed WSv2 resource-pack exhaustion. Authentication, bad parameters,
        # concurrency and transient failures must never select a different voice.
        self.resource_exhausted = code == 3022


def spoken_text(text: str) -> str:
    """Remove display-only syntax without changing the visible caption."""
    text = re.sub(r"[`*_#]+", "", text.strip())
    text = re.sub(r"第[（(](\d{1,2})[）)]", r"第\1", text)
    text = text.replace("×", "乘以").replace("÷", "除以")
    text = re.sub(r"(?<=\d)\s*\+\s*(?=\d)", "加", text)
    text = re.sub(r"(?<=\d)\s*=\s*(?=\d)", "等于", text)
    return re.sub(r"\s+", " ", text)


def billable_characters(text: str) -> int:
    return sum(2 if _is_han(character) else 1 for character in text)


def _is_han(character: str) -> bool:
    code = ord(character)
    return (0x3400 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
            or 0x20000 <= code <= 0x2FA1F)


def speech_markup(text: str) -> str:
    """Mark only known whole words, preserving context for all other words."""
    parts = []
    position = 0
    for match in _WORDS.finditer(text):
        parts.append(html.escape(text[position:match.start()]))
        word = match.group()
        parts.append(f'<phoneme alphabet="py" ph="{_PRONUNCIATIONS[word]}">{word}</phoneme>')
        position = match.end()
    parts.append(html.escape(text[position:]))
    return "<speak>" + "".join(parts) + "</speak>"


def configured_tts(settings: Settings) -> bool:
    """Whether the selected provider has enough server-side configuration to run."""
    if settings.tts_provider == "tencent":
        return bool(settings.tencent_tts_app_id and settings.tencent_tts_app_id.isdecimal()
                    and settings.tencent_tts_secret_id and settings.tencent_tts_secret_key
                    and settings.tencent_tts_voice_type > 0)
    return bool(settings.tts_api_key and settings.tts_workspace_id)


def speech_quota_limit(settings: Settings) -> int | None:
    """The local allowance, never an estimate of the provider's remaining balance."""
    return None if settings.tts_quota_mode == "provider" else TRIAL_CHARACTERS


def stream_tts(settings: Settings, text: str, audio_format: str,
               speed: float) -> Iterator[bytes]:
    """Dispatch a normalized utterance without exposing provider details to callers."""
    if settings.tts_provider == "tencent":
        return _tencent_resource_audio(settings, text, audio_format, speed)
    return provider_audio(settings.tts_api_key or "", settings.tts_workspace_id or "",
                          text, audio_format, speed)


def _tencent_resource_audio(settings: Settings, text: str, audio_format: str,
                            speed: float) -> Iterator[bytes]:
    """Select the next configured pool only after an explicit pre-audio exhaustion."""
    voices = tuple(dict.fromkeys((settings.tencent_tts_voice_type,
                                  *settings.tencent_tts_fallback_voice_types)))
    for index, voice_type in enumerate(voices):
        audio = tencent_audio(settings.tencent_tts_app_id or "",
                              settings.tencent_tts_secret_id or "",
                              settings.tencent_tts_secret_key or "",
                              voice_type, text, audio_format, speed)
        audio_started = False
        try:
            for chunk in audio:
                audio_started = audio_started or bool(chunk)
                yield chunk
            return
        except TencentSpeechServiceError as exc:
            if (not exc.resource_exhausted or exc.audio_started or audio_started
                    or index + 1 == len(voices)):
                raise
            _LOG.info("Tencent TTS resource exhausted voice=%d next_voice=%d code=%s",
                      voice_type, voices[index + 1], exc.code)
        finally:
            audio.close()


def _tencent_speed(speed: float) -> float:
    """Map playback multipliers to WSv2 Speed; normal 1.0 must be provider 0.

    Official scale: https://cloud.tencent.com/document/api/1073/108595
    PCM is already synthesized at this rate and plays at its original 24 kHz.
    """
    points = ((0.6, -2.0), (0.8, -1.0), (1.0, 0.0), (1.2, 1.0),
              (1.5, 2.0), (2.5, 6.0))
    if speed <= points[0][0]:
        return points[0][1]
    for (low_rate, low_value), (high_rate, high_value) in zip(points, points[1:], strict=False):
        if speed <= high_rate:
            return round(low_value + (speed - low_rate) * (high_value - low_value)
                         / (high_rate - low_rate), 2)
    return points[-1][1]


def _tencent_text(text: str) -> str:
    # The streaming-text API rejects SSML. Keep display text intact upstream.
    text = re.sub(r"</?[A-Za-z][^>]*>", "", text).strip()
    return text if text.endswith(("。", "！", "？", "；", "!", "?", ";")) else text + "。"


def _tencent_url(app_id: str, secret_id: str, secret_key: str,
                 voice_type: int, audio_format: str, speed: float,
                 session_id: str, timestamp: int, sample_rate: int = 24000) -> str:
    params = {
        "Action": "TextToStreamAudioWSv2", "AppId": app_id,
        "Codec": audio_format, "Expired": timestamp + 60,
        "SampleRate": sample_rate, "SecretId": secret_id,
        "SessionId": session_id, "Speed": _tencent_speed(speed),
        "Timestamp": timestamp, "VoiceType": voice_type,
    }
    query = urlencode(sorted(params.items()))
    signature = base64.b64encode(hmac.new(secret_key.encode("utf-8"),
        f"GET{_TENCENT_HOST}{_TENCENT_PATH}?{query}".encode(),
        hashlib.sha1).digest()).decode("ascii")
    return f"wss://{_TENCENT_HOST}{_TENCENT_PATH}?{query}&Signature={quote(signature, safe='')}"


def tencent_audio(app_id: str, secret_id: str, secret_key: str, voice_type: int,
                  text: str, audio_format: str, speed: float) -> Iterator[bytes]:
    """Stream native WSv2 audio, keeping the public PCM contract at 24 kHz."""
    session_id = str(uuid.uuid4())
    # This configured general voice is native 16 kHz; the existing large-model
    # voices remain 24 kHz. MP3 carries its own rate, while raw PCM needs conversion.
    sample_rate = 16000 if voice_type == 101001 else 24000
    resampler = Pcm16To24Resampler() if sample_rate == 16000 and audio_format == "pcm" else None
    url = _tencent_url(app_id, secret_id, secret_key, voice_type, audio_format,
                       speed, session_id, int(time.time()), sample_rate)
    started = time.monotonic()
    first_audio = False
    completed = False
    sent = False
    try:
        with connect(url, open_timeout=8, close_timeout=2, max_size=2 * 1024 * 1024) as ws:
            while not completed:
                frame = ws.recv(timeout=25 if sent else 8)
                if isinstance(frame, bytes):
                    if frame:
                        if not first_audio:
                            first_audio = True
                            _LOG.info("Tencent TTS first audio session=%s voice=%d elapsed_ms=%d",
                                      session_id, voice_type,
                                      int((time.monotonic() - started) * 1000))
                        output = resampler.feed(frame) if resampler else frame
                        if output:
                            yield output
                    continue
                event = json.loads(frame)
                if event.get("code") != 0:
                    _LOG.warning("Tencent TTS failed session=%s voice=%d code=%s",
                                 session_id, voice_type, event.get("code"))
                    raise TencentSpeechServiceError(event.get("code"), session_id, first_audio)
                if event.get("ready") == 1 and not sent:
                    ws.send(json.dumps({"session_id": session_id,
                                        "message_id": str(uuid.uuid4()),
                                        "action": "ACTION_SYNTHESIS",
                                        "data": _tencent_text(text)}, ensure_ascii=False))
                    ws.send(json.dumps({"session_id": session_id,
                                        "message_id": str(uuid.uuid4()),
                                        "action": "ACTION_COMPLETE", "data": ""}))
                    sent = True
                if event.get("final") == 1:
                    completed = True
        if not completed:
            raise SpeechServiceError("Tencent TTS ended an incomplete stream")
        if not first_audio:
            raise SpeechServiceError("Tencent TTS returned no audio")
        if resampler:
            final_audio = resampler.finish()
            if final_audio:
                yield final_audio
        _LOG.info("Tencent TTS complete session=%s voice=%d elapsed_ms=%d",
                  session_id, voice_type, int((time.monotonic() - started) * 1000))
    except (OSError, TimeoutError, ValueError, WebSocketException) as exc:
        raise SpeechServiceError("Tencent TTS is unavailable") from exc


def provider_audio(
    api_key: str, workspace_id: str, text: str, audio_format: str, speed: float
) -> Iterator[bytes]:
    """Decode DashScope SSE into raw audio chunks for the public speech endpoint."""
    if not re.fullmatch(r"[A-Za-z0-9-]+", workspace_id):
        raise SpeechServiceError("Invalid TTS workspace ID")
    url = (f"https://{workspace_id}.cn-beijing.maas.aliyuncs.com/api/v1/services/"
           "audio/tts/SpeechSynthesizer")
    payload = {
        "model": PROVIDER_MODEL,
        "input": {
            "text": speech_markup(text),
            "voice": PROVIDER_VOICE,
            "format": audio_format,
            "sample_rate": 24000,
            "rate": speed,
            "enable_ssml": True,
            "language_hints": ["zh"],
        },
    }
    complete = False
    try:
        with httpx.Client(timeout=httpx.Timeout(40, connect=6)) as client:
            with client.stream("POST", url,
                               headers={"Authorization": f"Bearer {api_key}",
                                        "X-DashScope-SSE": "enable"},
                               json=payload) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = json.loads(line[5:].strip())
                    if data.get("code") or data.get("error"):
                        raise SpeechServiceError("Speech provider rejected the request")
                    output = data.get("output") or {}
                    audio = (output.get("audio") or {}).get("data")
                    if audio:
                        yield base64.b64decode(audio, validate=True)
                    if output.get("finish_reason") == "stop":
                        complete = True
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise SpeechServiceError("Speech provider is unavailable") from exc
    if not complete:
        raise SpeechServiceError("Speech provider ended an incomplete stream")
