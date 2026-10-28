"""One synthetic TTS-to-local-ASR loop; never room audio or human listening.

Only a run-header-verified loopback API and its synthetic state.json are allowed.
No model installation, microphone, Android configuration, or family database access.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import uuid
import wave
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.acceptance.bootstrap import verify_acceptance_api  # noqa: E402

FALLBACK_TEXT = "这是一段合成语音验收：一米等于一百厘米，二十五厘米与一米要先统一单位。"
SOURCE = {"device": "none", "input": "synthetic", "model": "none", "audio": "none"}
UNITS = r"厘米|毫米|千米|分钟|小时|千克|毫升|米|秒|克|升|元|角"
PAIR_PATTERN = re.compile(rf"([\d零〇一二两三四五六七八九十百千万点.]+)\s*({UNITS})")


def chinese_number(value: str) -> str:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        return format(Decimal(value).normalize(), "f")
    if "点" in value:
        head, fraction = value.split("点", 1)
        tail = "".join(str(digits.get(char, char)) for char in fraction)
        return chinese_number(chinese_number(head) + "." + tail)
    if value and all(char in digits for char in value):
        return str(int("".join(str(digits[char]) for char in value)))
    total, section, digit = 0, 0, 0
    for char in value:
        if char in digits:
            digit = digits[char]
        elif char.isdigit():
            digit = digit * 10 + int(char)
        elif char in {"十", "百", "千"}:
            section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            digit = 0
        elif char == "万":
            total += (section + digit) * 10000
            section, digit = 0, 0
    return str(total + section + digit)


def number_units(text: str) -> set[tuple[str, str]]:
    return {
        (chinese_number(match.group(1)), match.group(2)) for match in PAIR_PATTERN.finditer(text)
    }


def compare_units(expected: str, transcript: str) -> dict:
    original, heard = number_units(expected), number_units(transcript)
    missing = original - heard
    return {
        "expected": sorted([number + unit for number, unit in original]),
        "recognized": sorted([number + unit for number, unit in heard]),
        "missing": sorted([number + unit for number, unit in missing]),
        "preserved": bool(original) and not missing,
    }


def review_operations(result: dict) -> dict:
    """An ASR homophone is uncertain audio evidence, never a proven TTS error."""
    pattern = re.compile(
        r"([\d零〇一二两三四五六七八九十百千点.]+)\s*(加|减|乘|除)\s*([\d零〇一二两三四五六七八九十百千点.]+)"
    )

    def operations(text):
        for phrase in ("加上", "减去", "乘以", "除以"):
            text = text.replace(phrase, phrase[0])
        return {(chinese_number(a), op, chinese_number(b)) for a, op, b in pattern.findall(text)}

    expected = operations(result.get("input_text", ""))
    if not expected or not result.get("transcript"):
        return result
    missing = expected - operations(result["transcript"])
    result["checks"] = [check for check in result["checks"] if check["id"] != "math-operations"]
    result["checks"].append(
        _check(
            "math-operations",
            "数学运算词回环一致性",
            "blocked" if missing else "passed",
            "转写未保留运算词；须实听区分发音与转写错误，不能仅凭数字单位通过判定整段正确。"
            if missing
            else "",
        )
    )
    result["operation_review"] = {
        "missing": sorted([list(item) for item in missing]),
        "human_audio_listening": False,
    }
    if missing and result.get("status") == "passed":
        result["status"] = "blocked"
    return result


def pcm_wav(pcm: bytes) -> tuple[bytes, float]:
    if not pcm or len(pcm) % 2:
        raise ValueError("Speech PCM is empty or not 16-bit aligned")
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(pcm)
    return output.getvalue(), len(pcm) / 48000


def teacher_text(directory: Path) -> tuple[str, str]:
    path = directory / "model/results/units_wrong_thought.json"
    if path.is_file():
        result = json.loads(path.read_text(encoding="utf-8"))
        text = (result.get("raw_api_response") or {}).get("response", "")
        if isinstance(text, str) and 1 <= len(text) <= 180 and number_units(text):
            return text, path.relative_to(directory).as_posix()
    return FALLBACK_TEXT, "synthetic-generic-numbers-and-units"


def _check(
    identifier: str, label: str, status: str, detail: str = "", *, audio: str | None = None
) -> dict:
    check = {
        "id": identifier,
        "label": label,
        "status": status,
        "required": True,
        "requires_source": {"audio": "live"},
        "detail": detail,
    }
    if audio is not None:
        check["source"] = {"audio": audio}
    return check


def run_audio(
    directory: str | Path,
    base_url: str = "http://127.0.0.1:8016",
    *,
    client: httpx.Client | None = None,
) -> dict:
    directory = Path(directory).resolve()
    address = urlsplit(base_url)
    if (
        address.scheme not in {"http", "https"}
        or address.hostname not in {"127.0.0.1", "localhost"}
        or address.port == 8003
        or address.username
        or address.password
        or address.path not in {"", "/"}
        or address.query
        or address.fragment
    ):
        raise ValueError("Only a dedicated loopback API is allowed")
    result = {
        "schema_version": 1,
        "kind": "synthetic-tts-local-asr-loop",
        "source": dict(SOURCE),
        "human_audio_listening": False,
        "environment_recording": False,
        "started_at": datetime.now(UTC).isoformat(),
        "status": "blocked",
        "checks": [],
        "tts_attempted": False,
        "asr_attempted": False,
        "disclaimer": "合成语音→本地ASR回环，不是真人实听，也不采集环境录音。",
    }
    audio_dir = directory / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    api = client or httpx.Client(
        base_url=base_url, timeout=180, trust_env=False, follow_redirects=False
    )
    own_client = client is None
    parent, prior_voice, prior_asr = None, None, None
    started = time.monotonic()
    try:
        verify_acceptance_api(api, directory.name)
        state = json.loads((directory / "state.json").read_text(encoding="utf-8"))
        if (
            state.get("synthetic") is not True
            or state.get("run_id") != directory.name
            or state.get("base_url", "").rstrip("/") != base_url.rstrip("/")
        ):
            raise ValueError("Synthetic run state does not match the verified API")
        parent = {"Authorization": "Bearer " + state["parent_token"]}
        child = {"Authorization": "Bearer " + state["child_token"]}
        response = api.get("/v1/families/dashboard", headers=parent)
        response.raise_for_status()
        dashboard = response.json()
        if dashboard.get("family", {}).get("id") != state["family_id"]:
            raise ValueError("Synthetic family does not match the saved state")
        if not dashboard.get("tts_available"):
            result["checks"].append(
                _check("tts-ready", "真实语音合成服务", "blocked", "当前服务未配置TTS。")
            )
            return result
        local_asr_ready = (
            bool(dashboard.get("asr_available"))
            and dashboard.get("asr_provider") == "家庭服务端本地模型"
        )
        original = dashboard["family"]
        prior_voice = bool(original.get("premium_voice_enabled"))
        response = api.patch(
            "/v1/families/voice-preferences", json={"enabled": True}, headers=parent
        )
        response.raise_for_status()
        text, text_source = teacher_text(directory)
        result.update(input_text=text, text_source=text_source)
        result["tts_attempted"] = True
        response = api.post(
            "/v1/audio/speech",
            headers={**child, "Idempotency-Key": "audio-check-" + uuid.uuid4().hex},
            json={
                "model": "k12-tts",
                "voice": "teacher-warm-female",
                "input": text,
                "response_format": "pcm",
                "stream_format": "audio",
                "speed": 1,
            },
        )
        response.raise_for_status()
        pcm = response.content
        wav, duration = pcm_wav(pcm)
        (audio_dir / "speech.pcm").write_bytes(pcm)
        (audio_dir / "speech.wav").write_bytes(wav)
        result.update(
            duration_seconds=round(duration, 3), pcm="audio/speech.pcm", wav="audio/speech.wav"
        )
        result["source"]["audio"] = "live"
        tts_check = _check("tts-audio", "真实TTS返回可用PCM/WAV", "passed", audio="live")
        tts_check["evidence"] = ["audio/speech.wav"]
        result["checks"].append(tts_check)
        if not local_asr_ready:
            result["checks"].append(
                _check(
                    "local-asr",
                    "现有本地ASR",
                    "blocked",
                    "本地ASR未就绪；未启用转写授权，未安装或下载任何模型。",
                    audio="none",
                )
            )
            result["checks"].append(
                _check(
                    "number-units",
                    "关键数字和单位保留",
                    "blocked",
                    "已保存真实合成音频供点击试听；尚无ASR转写，无法自动核对数字单位。",
                    audio="none",
                )
            )
            return result
        if len(wav) > 900000:
            result["checks"].append(
                _check(
                    "asr-clip-size",
                    "ASR片段大小限制",
                    "blocked",
                    "片段超过现有900KB上限，未截断或发送。",
                )
            )
            return result
        prior_asr = bool(original.get("audio_transcription_allowed"))
        response = api.patch(
            "/v1/families/audio-transcription", json={"allowed": True}, headers=parent
        )
        response.raise_for_status()
        result["asr_attempted"] = True
        response = api.post(
            "/v1/voice/transcribe",
            headers=child,
            files={"audio": ("synthetic-teacher.wav", wav, "audio/wav")},
        )
        response.raise_for_status()
        transcript = response.json().get("text", "")
        if not isinstance(transcript, str):
            raise ValueError("ASR returned an invalid transcript")
        comparison = compare_units(text, transcript)
        (audio_dir / "transcript.json").write_text(
            json.dumps(
                {
                    "input": text,
                    "transcript": transcript,
                    "comparison": comparison,
                    "human_audio_listening": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result.update(
            transcript=transcript, comparison=comparison, transcript_file="audio/transcript.json"
        )
        result["checks"].append(
            _check("local-asr", "真实本地ASR完成合成语音转写", "passed" if transcript else "failed")
        )
        result["checks"].append(
            _check(
                "number-units",
                "关键数字和单位保留",
                "passed" if comparison["preserved"] else "failed",
                "只比较数字单位等价，不代表整段发音、理解或真人听感均正确。",
            )
        )
        result["status"] = "passed" if transcript and comparison["preserved"] else "failed"
        return result
    except (OSError, ValueError, KeyError, httpx.HTTPError) as error:
        result["checks"].append(
            _check(
                "audio-loop",
                "合成语音回环",
                "blocked",
                type(error).__name__ + "；未输出私密请求载荷。",
            )
        )
        return result
    finally:
        restored = True
        if parent:
            restorations = []
            if prior_voice is not None:
                restorations.append(("/v1/families/voice-preferences", {"enabled": prior_voice}))
            if prior_asr is not None:
                restorations.append(("/v1/families/audio-transcription", {"allowed": prior_asr}))
            for path, body in restorations:
                try:
                    api.patch(path, json=body, headers=parent).raise_for_status()
                except httpx.HTTPError:
                    restored = False
        if not restored:
            result["status"] = "blocked"
            result["checks"].append(
                _check("restore-consent", "恢复合成家庭原有语音开关", "blocked")
            )
        result.update(
            completed_at=datetime.now(UTC).isoformat(),
            elapsed_seconds=round(time.monotonic() - started, 3),
            consent_restored=restored,
        )
        review_operations(result)
        pending = directory / (".audio-report-" + uuid.uuid4().hex + ".tmp")
        pending.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.replace(directory / "audio-report.json")
        if own_client:
            api.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8016")
    parser.add_argument(
        "--review-only", action="store_true", help="Recheck saved audio evidence without API calls"
    )
    args = parser.parse_args(argv)
    directory = args.output.resolve()
    allowed = (ROOT / "output/acceptance").resolve()
    if directory == allowed or not directory.is_relative_to(allowed):
        parser.error("Output must be a run directory under output/acceptance")
    try:
        if args.review_only:
            path = directory / "audio-report.json"
            result = review_operations(json.loads(path.read_text(encoding="utf-8")))
            path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            result = run_audio(directory, args.base_url)
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "report": str(directory / "audio-report.json"),
                    "human_audio_listening": False,
                }
            )
        )
        return 0 if result["status"] == "passed" else 1
    except Exception:
        print(
            json.dumps(
                {"status": "blocked", "detail": "Audio setup failed; private payloads withheld."}
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
