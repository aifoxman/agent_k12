"""Family homework pilot API and authenticated realtime signaling."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import logging
import math
import re
import secrets
import sqlite3
import time
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, date, datetime
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Annotated, Literal
from urllib.parse import urlparse

import httpx
from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    WebSocket,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from opencc import OpenCC
from pydantic import BaseModel, Field
from starlette.datastructures import Headers

from . import local_asr
from .ai import (
    analyze,
    assess_hint_attempt,
    assess_idle_checkin,
    assess_submission,
    detect_help_candidate,
    draft_child_feedback,
    local_teaching_text_anchors,
    locate_question,
    normalized_box,
    normalized_teaching_turn,
    normalized_text_anchors,
    quoted_target_conflict,
    read_teaching_anchors,
    recheck_single,
    resolved_teaching_level,
    scan_pages,
    set_usage_family,
    spoken_anchor_text,
    stream_model,
    teach_from_evidence,
    teaching_call_context,
    teaching_geometry_needed,
    teaching_goal,
    teaching_region_needed,
    teaching_scope_guidance,
    teaching_stage,
    tight_teaching_anchor_image,
    verify_teaching_anchors,
    withholds_final_answer,
)
from .config import Settings
from .db import RETENTION_SECONDS, connect, initialize, purge_expired, transaction
from .grading import (
    correction_regions,
    focus_question_image,
    merge_correction_regions,
)
from .page_prep import (
    ALLOWED_MODELS,
    PagePreparation,
    assess_spoken_answer,
    preferences,
    solve_question,
)
from .reports import daily_report_details, session_review
from .security import hash_password, make_token, read_token, verify_password
from .session_context import format_session_context
from .teaching_ink import (
    MAX_TEACHING_BEATS,
    fallback_teaching_beats,
    grounded_teaching_solution,
    reveals_final_answer,
    stream_teaching_beats,
    unresolved_teaching_beat,
)
from .timer import daily_summary, snapshot, suggestion, task_counts
from .tts import (
    MODEL_ALIAS,
    VOICE_ALIAS,
    SpeechServiceError,
    TencentSpeechServiceError,
    billable_characters,
    configured_tts,
    speech_quota_limit,
    spoken_text,
    stream_tts,
)

logger = logging.getLogger(__name__)
to_simplified = OpenCC("t2s")
settings = Settings.from_env()
initialize(settings.database_path)
_preparation_services: dict[str, PagePreparation] = {}
_preparation_lock = Lock()


def page_preparation() -> PagePreparation:
    with _preparation_lock:
        service = _preparation_services.get(settings.database_path)
        if service is None:
            service = PagePreparation(settings)
            _preparation_services[settings.database_path] = service
        return service


def grading_settings(family_id: str) -> Settings:
    """Use the family's grading model for full grading and question rechecks."""
    model = preferences(settings.database_path, family_id)["grading_model"]
    return replace(settings, ai_model=model)


def speech_provider() -> tuple[str | None, str | None]:
    if settings.asr_api_url and settings.asr_model:
        return settings.asr_api_url, urlparse(settings.asr_api_url).hostname
    if local_asr.ready(settings.asr_local_model_path):
        return f"local:{Path(settings.asr_local_model_path).resolve()}", "家庭服务端本地模型"
    return None, None


@asynccontextmanager
async def lifespan(_application: FastAPI):
    async def cleaner():
        while True:
            await asyncio.to_thread(purge_expired, settings.database_path)
            await asyncio.sleep(3600)

    async def submission_worker():
        with transaction(settings.database_path) as db:
            db.execute("UPDATE task_submissions SET ai_status='pending' WHERE ai_status='processing'")
            db.execute("UPDATE submission_pages SET grade_status='pending' WHERE grade_status='processing'")
        while True:
            pending = many("SELECT id FROM task_submissions WHERE ai_status='pending' "
                           "AND expires_at>? ORDER BY submitted_at LIMIT 3", (current(),))
            for item in pending:
                await asyncio.to_thread(run_submission_review, item["id"])
            grading = many("SELECT id FROM submission_pages WHERE grade_status='pending' "
                           "AND expires_at>? ORDER BY created_at LIMIT 2", (current(),))
            for item in grading:
                await asyncio.to_thread(run_submission_page_grade, item["id"])
            await asyncio.sleep(30)

    job = asyncio.create_task(cleaner())
    review_job = asyncio.create_task(submission_worker())
    try:
        yield
    finally:
        job.cancel()
        review_job.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await job
        with contextlib.suppress(asyncio.CancelledError):
            await review_job
        for service in _preparation_services.values():
            service.close()


app = FastAPI(title="家庭作业陪伴 API", version="0.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=list(settings.allowed_origins),
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


def uid() -> str:
    return uuid.uuid4().hex


def current() -> int:
    return int(time.time())


def day_bounds(offset_minutes: int) -> tuple[int, int]:
    offset = offset_minutes * 60
    start = ((current() + offset) // 86400) * 86400 - offset
    return start, start + 86400


def ai_usage_summary(family_id: str, offset_minutes: int) -> dict:
    now = current()
    today, _ = day_bounds(offset_minutes)
    offset = offset_minutes * 60
    local_month = datetime.fromtimestamp(now + offset, UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    month_start = int(local_month.timestamp()) - offset

    def aggregate(since: int | None) -> dict:
        condition = " AND created_at>=?" if since is not None else ""
        args = (family_id, since) if since is not None else (family_id,)
        result = one(
            "SELECT COUNT(*) AS calls, COALESCE(SUM(total_tokens),0) AS total_tokens, "
            "COALESCE(SUM(prompt_tokens),0) AS prompt_tokens, "
            "COALESCE(SUM(completion_tokens),0) AS completion_tokens, "
            "COALESCE(SUM(CASE WHEN reported=0 THEN 1 ELSE 0 END),0) AS unreported_calls "
            "FROM ai_usage WHERE family_id=?" + condition, args
        )
        if since is None:
            archived = one("SELECT calls,total_tokens,prompt_tokens,completion_tokens,"
                           "unreported_calls FROM ai_usage_totals WHERE family_id=?", (family_id,))
            if archived:
                return {key: result[key] + archived[key] for key in result}
        return result

    return {"today": aggregate(today), "month": aggregate(month_start),
            "all_time": aggregate(None)}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def limit_key(kind: str, value: str) -> str:
    return kind + ":" + digest(value)


def check_limit(key: str) -> None:
    row = one("SELECT failures,window_start FROM rate_limits WHERE key=?", (key,))
    if row and row["window_start"] > current() - 600 and row["failures"] >= 5:
        raise HTTPException(429, "Too many attempts; try again later")


def record_failure(key: str) -> None:
    now = current()
    with transaction(settings.database_path) as db:
        db.execute("INSERT INTO rate_limits VALUES(?,1,?) ON CONFLICT(key) DO UPDATE SET failures=CASE WHEN window_start<? THEN 1 ELSE failures+1 END,window_start=CASE WHEN window_start<? THEN ? ELSE window_start END",
                   (key, now, now - 600, now - 600, now))


def clear_failures(key: str) -> None:
    with transaction(settings.database_path) as db:
        db.execute("DELETE FROM rate_limits WHERE key=?", (key,))


def one(query: str, args: tuple = ()) -> dict | None:
    with connect(settings.database_path) as db:
        row = db.execute(query, args).fetchone()
        return dict(row) if row else None


def many(query: str, args: tuple = ()) -> list[dict]:
    with connect(settings.database_path) as db:
        return [dict(row) for row in db.execute(query, args).fetchall()]


def learning_context(session_id: str, task_id: str | None, purpose: str = "hint") -> str:
    """Read only this session's bounded learning record, scoped to the selected task."""
    scope = "session_id=? AND expires_at>?"
    args: tuple = (session_id, current())
    if task_id:
        scope += " AND (task_id=? OR task_id IS NULL)"
        args += (task_id,)

    def latest(table: str, columns: str, limit: int) -> list[dict]:
        return many(f"SELECT {columns},created_at FROM {table} WHERE {scope} "
                    f"ORDER BY created_at DESC,rowid DESC LIMIT {limit}", args)

    task_note = ""
    if task_id:
        task = one("SELECT title,subject,status,rejection_reason,child_feedback FROM tasks WHERE id=?", (task_id,))
        if task:
            task_note = f"当前选中任务：{task['title'][:80]}"
            if task["subject"]:
                task_note += f"（{task['subject'][:30]}）"
            feedback = task["child_feedback"] or task["rejection_reason"]
            if task["status"] == "needs_revision" and feedback:
                task_note += f"；家长要求补做：{feedback[:150]}"
    correction_rows = []
    if purpose != "correction":
        columns = "id,subject_label,page_label,summary,checks_json"
        correction_rows = latest("corrections", columns, 2)
        reviewed = many(
            f"SELECT {columns},created_at FROM corrections WHERE {scope} AND checks_json LIKE ? "
            "ORDER BY created_at DESC,rowid DESC LIMIT 2",
            (*args, '%"parent_review"%'),
        )
        correction_rows = list({row["id"]: row for row in [*correction_rows, *reviewed]}.values())
    timeline = format_session_context(
        latest("hints", "question,response", 5),
        latest("reflections", "text", 4),
        latest("observations", "label,progress", 3),
        correction_rows,
        purpose=purpose,
        max_chars=1500 - len(task_note) - bool(task_note),
    )
    return "\n".join(part for part in (task_note, timeline) if part)


def principal(authorization: Annotated[str | None, Header()] = None) -> dict:
    token = (authorization or "").removeprefix("Bearer ")
    claims = read_token(settings.app_secret, token)
    if not claims:
        raise HTTPException(401, "Authentication required")
    return claims


def family_for(claims: dict) -> dict:
    if claims["role"] == "parent":
        family = one("SELECT * FROM families WHERE parent_id = ?", (claims["sub"],))
    else:
        family = one("SELECT f.* FROM families f JOIN devices d ON d.family_id=f.id WHERE d.id=?",
                     (claims["sub"],))
    if not family:
        raise HTTPException(403, "No paired family")
    set_usage_family(family["id"])
    return family


def require_role(claims: dict, role: str) -> None:
    if claims["role"] != role:
        raise HTTPException(403, f"{role} account required")


def family_session(session_id: str, family_id: str) -> dict:
    session = one("SELECT * FROM sessions WHERE id=? AND family_id=?", (session_id, family_id))
    if not session:
        raise HTTPException(404, "Session not found")
    return session


def correction_authorized(session, now: int | None = None) -> bool:
    """Apply the server's test policy without creating or extending parent grants."""
    return session["status"] == "active" and (
        not settings.correction_require_parent_approval
        or (session["correction_authorized_until"] or 0) > (current() if now is None else now)
    )


def correction_access(session, claims: dict | None = None) -> dict:
    bypass = not settings.correction_require_parent_approval
    owns_session = not claims or claims["role"] != "child" or session["device_id"] == claims["sub"]
    return {
        "allowed": owns_session and correction_authorized(session),
        "mode": "test_bypass" if bypass else "parent_authorization",
        "authorized_until": None if bypass else session["correction_authorized_until"],
    }


def family_task(task_id: str, family_id: str) -> dict:
    task = one("SELECT * FROM tasks WHERE id=? AND family_id=?", (task_id, family_id))
    if not task:
        raise HTTPException(404, "Task not found")
    return task


class Registration(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=12, max_length=128)
    child_name: str = Field(min_length=1, max_length=80)
    age_band: Literal["kindergarten", "primary", "junior"]
    grade: str | None = Field(default=None, max_length=30)
    textbook: str | None = Field(default=None, max_length=80)


class Login(BaseModel):
    email: str
    password: str


class DeviceClaim(BaseModel):
    device_id: str | None = None
    device_secret: str | None = None


class Pair(BaseModel):
    code: str = Field(pattern=r"^\d{8}$")


class TaskImport(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    subject: str | None = Field(default=None, max_length=80)
    due_date: date | None = None
    timezone_offset_minutes: int = Field(default=480, ge=-720, le=840)


class TaskPublishItem(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    subject: str | None = Field(default=None, max_length=80)
    due_at: int | None = None


class TaskBatchPublish(BaseModel):
    items: list[TaskPublishItem] = Field(min_length=1, max_length=50)
    due_at: int
    request_id: str = Field(min_length=8, max_length=100)
    timezone_offset_minutes: int = Field(default=480, ge=-720, le=840)


class TaskReview(BaseModel):
    submission_id: str
    version: int = Field(ge=1)
    decision: Literal["accepted", "rejected"]
    reason: str = Field(default="", max_length=500)
    child_feedback: str = Field(default="", max_length=300)


class FeedbackDraftRequest(BaseModel):
    submission_id: str
    reason: str = Field(min_length=1, max_length=500)


class BatchReviewItem(BaseModel):
    task_id: str
    submission_id: str
    version: int = Field(ge=1)


class BatchReviews(BaseModel):
    items: list[BatchReviewItem] = Field(min_length=1, max_length=50)


class TaskReminderUpdate(BaseModel):
    event_key: str = Field(min_length=1, max_length=100)
    action: Literal["heard", "snooze"]


class TaskDetailsUpdate(BaseModel):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=500)
    subject: str | None = Field(default=None, max_length=80)
    due_at: int
    timezone_offset_minutes: int = Field(default=480, ge=-720, le=840)


class TaskCancel(BaseModel):
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=300)


class TaskUpdate(BaseModel):
    status: Literal["todo", "doing", "confirmed"]
    version: int = Field(ge=1)


class ObservationUpdate(BaseModel):
    review_status: Literal["accepted", "rejected"]


class CorrectionUpdate(BaseModel):
    review_status: Literal["accepted", "rejected"]


class ParentCheckUpdate(BaseModel):
    status: Literal["looks_correct", "check_again", "needs_discussion", "unclear"]
    answer: str = Field(max_length=200)
    note: str = Field(default="", max_length=500)
    box: list[float] | None = None
    revision: int = Field(ge=0)


class CorrectionAccess(BaseModel):
    allowed: bool


class SessionState(BaseModel):
    status: Literal["active", "paused"]
    setup_ticket: str | None = None


class SessionStart(BaseModel):
    setup_ticket: str
    timer_goal_seconds: int | None = Field(default=None, ge=300, le=10800)


class ParentHelpPreferences(BaseModel):
    mode: Literal["off", "gentle", "standard"]


class FamilyAiPreferences(BaseModel):
    solver_model: Literal["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"]
    grading_model: Literal["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"] | None = None
    locator_model: Literal["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"]
    tutor_model: Literal["gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna"]
    precompute_enabled: bool


class AudioTranscriptionPermission(BaseModel):
    allowed: bool


class PremiumVoicePreference(BaseModel):
    enabled: bool


class SpeechRequest(BaseModel):
    model: str
    input: str
    voice: str
    response_format: str = "mp3"
    speed: float = 1.0
    stream_format: str = "audio"
    instructions: str | None = None


class SessionHelpPreferences(BaseModel):
    enabled: bool


class TimerGoal(BaseModel):
    goal_seconds: int | None = Field(default=None, ge=300, le=10800)


class TimerEvent(BaseModel):
    id: str = Field(min_length=1, max_length=100)
    seq: int = Field(ge=1)
    kind: Literal["focus", "assisted", "grading_wait"]
    subject: str | None = Field(default=None, max_length=60)
    duration_ms: int = Field(ge=1, le=10000)
    ended_at: int


class TimerEventBatch(BaseModel):
    events: list[TimerEvent] = Field(min_length=1, max_length=120)


class Reflection(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    task_id: str | None = None


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/v1/auth/register")
def register(body: Registration) -> dict:
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(422, "Valid email required")
    parent_id, family_id, now = uid(), uid(), current()
    try:
        with transaction(settings.database_path) as db:
            db.execute("INSERT INTO parents VALUES(?,?,?,?)",
                       (parent_id, email, hash_password(body.password), now))
            db.execute("INSERT INTO families(id,parent_id,child_name,age_band,grade,textbook,created_at) VALUES(?,?,?,?,?,?,?)", (family_id, parent_id,
                       body.child_name.strip(), body.age_band, body.grade, body.textbook, now))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Email already registered") from exc
    return {"token": make_token(settings.app_secret, "parent", parent_id), "family_id": family_id}


@app.post("/v1/auth/login")
def login(body: Login) -> dict:
    email = body.email.strip().lower()
    key = limit_key("login", email)
    check_limit(key)
    parent = one("SELECT * FROM parents WHERE email=?", (email,))
    if not parent or not verify_password(body.password, parent["password_hash"]):
        record_failure(key)
        raise HTTPException(401, "Invalid credentials")
    clear_failures(key)
    return {"token": make_token(settings.app_secret, "parent", parent["id"])}


@app.post("/v1/devices/claim")
def claim_device(body: DeviceClaim) -> dict:
    now = current()
    device_id = body.device_id or uid()
    device_secret = body.device_secret or secrets.token_urlsafe(32)
    existing = one("SELECT * FROM devices WHERE id=?", (device_id,))
    if existing and not hmac.compare_digest(existing["secret_hash"], digest(device_secret)):
        raise HTTPException(403, "Invalid device secret")
    if existing and existing["family_id"]:
        return {"device_id": device_id, "device_secret": device_secret, "paired": True,
                "token": make_token(settings.app_secret, "child", device_id)}
    code = f"{secrets.randbelow(100_000_000):08d}"
    with transaction(settings.database_path) as db:
        if existing:
            db.execute("UPDATE devices SET pairing_code_hash=?, pairing_expires_at=?, pairing_attempts=0 WHERE id=?",
                       (digest(code), now + 600, device_id))
        else:
            db.execute("INSERT INTO devices VALUES(?,?,?,?,?,?,?)", (device_id, digest(device_secret),
                       digest(code), now + 600, 0, None, now))
    return {"device_id": device_id, "device_secret": device_secret, "paired": False,
            "pairing_code": code, "expires_at": now + 600}


@app.post("/v1/devices/transfer-claim")
def claim_device_transfer(body: DeviceClaim, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    if not body.device_id or claims["sub"] != body.device_id or not body.device_secret:
        raise HTTPException(403, "Device confirmation required")
    code, now = f"{secrets.randbelow(100_000_000):08d}", current()
    with transaction(settings.database_path) as db:
        device = db.execute("SELECT secret_hash,family_id FROM devices WHERE id=?",
                            (body.device_id,)).fetchone()
        if (not device or not device["family_id"] or
                not hmac.compare_digest(device["secret_hash"], digest(body.device_secret))):
            raise HTTPException(403, "Device confirmation required")
        db.execute("UPDATE devices SET pairing_code_hash=?,pairing_expires_at=?,"
                   "pairing_attempts=0 WHERE id=?",
                   (digest(code), now + 600, body.device_id))
    return {"pairing_code": code, "expires_at": now + 600,
            "current_family_id": device["family_id"]}


@app.post("/v1/families/pair")
def pair_device(body: Pair, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    key = limit_key("pair", claims["sub"])
    check_limit(key)
    family = family_for(claims)
    now = current()
    try:
        with transaction(settings.database_path) as db:
            device = db.execute("SELECT * FROM devices WHERE pairing_code_hash=? AND pairing_expires_at>?",
                                (digest(body.code), now)).fetchone()
            if not device:
                raise HTTPException(404, "Code invalid or expired")
            if device["pairing_attempts"] >= 5:
                raise HTTPException(403, "Pairing unavailable")
            old_family_id = device["family_id"]
            if old_family_id and old_family_id != family["id"]:
                db.execute("UPDATE live_sessions SET status='ended',ended_at=? WHERE session_id IN "
                           "(SELECT id FROM sessions WHERE device_id=? AND status IN ('active','paused')) "
                           "AND status='active'", (now, device["id"]))
                db.execute("UPDATE session_pauses SET ended_at=? WHERE session_id IN "
                           "(SELECT id FROM sessions WHERE device_id=? AND status IN ('active','paused')) "
                           "AND ended_at IS NULL", (now, device["id"]))
                db.execute("UPDATE sessions SET status='finished',ended_at=?,"
                           "correction_authorized_until=NULL WHERE device_id=? "
                           "AND status IN ('active','paused')", (now, device["id"]))
            db.execute("UPDATE devices SET family_id=?, pairing_code_hash=NULL, pairing_expires_at=NULL WHERE id=?",
                       (family["id"], device["id"]))
    except HTTPException:
        record_failure(key)
        raise
    clear_failures(key)
    if old_family_id and old_family_id != family["id"]:
        broadcast_sync(old_family_id, {"type": "device_transferred"})
    broadcast_sync(family["id"], {"type": "device_paired"})
    return {"paired": True, "device_id": device["id"]}


@app.patch("/v1/families/help-preferences")
def update_parent_help_preferences(body: ParentHelpPreferences,
                                   claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    with transaction(settings.database_path) as db:
        db.execute("UPDATE families SET proactive_help_mode=? WHERE id=?", (body.mode, family["id"]))
    broadcast_sync(family["id"], {"type": "help_preferences_changed"})
    return {"mode": body.mode}


@app.get("/v1/families/ai-preferences")
def get_family_ai_preferences(claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    config = preferences(settings.database_path, family_for(claims)["id"])
    return {**config, "available_models": list(ALLOWED_MODELS)}


@app.patch("/v1/families/ai-preferences")
def update_family_ai_preferences(body: FamilyAiPreferences,
                                 claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family_id = family_for(claims)["id"]
    now = current()
    grading_model = body.grading_model or preferences(settings.database_path, family_id)["grading_model"]
    with transaction(settings.database_path) as db:
        db.execute(
            "INSERT INTO family_ai_preferences(family_id,solver_model,grading_model,locator_model,"
            "tutor_model,precompute_enabled,version,updated_at) VALUES(?,?,?,?,?,?,1,?) "
            "ON CONFLICT(family_id) DO UPDATE SET solver_model=excluded.solver_model,"
            "grading_model=excluded.grading_model,"
            "locator_model=excluded.locator_model,tutor_model=excluded.tutor_model,"
            "precompute_enabled=excluded.precompute_enabled,version=version+1,"
            "updated_at=excluded.updated_at",
            (family_id, body.solver_model, grading_model, body.locator_model, body.tutor_model,
             int(body.precompute_enabled), now),
        )
        db.execute("UPDATE page_preparations SET status='stale' WHERE session_id IN "
                   "(SELECT id FROM sessions WHERE family_id=? AND status IN ('active','paused')) "
                   "AND status IN ('preparing','ready')", (family_id,))
    broadcast_sync(family_id, {"type": "ai_preferences_changed"})
    return {**preferences(settings.database_path, family_id),
            "available_models": list(ALLOWED_MODELS)}


@app.patch("/v1/families/audio-transcription")
def update_audio_transcription_permission(body: AudioTranscriptionPermission,
                                          claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    provider, _ = speech_provider()
    if body.allowed and not provider:
        raise HTTPException(503, "短语音转写服务尚未配置")
    family = family_for(claims)
    with transaction(settings.database_path) as db:
        db.execute("UPDATE families SET audio_transcription_allowed=?, audio_transcription_provider=? WHERE id=?",
                   (int(body.allowed), provider if body.allowed else None,
                    family["id"]))
    broadcast_sync(family["id"], {"type": "audio_transcription_changed"})
    return {"allowed": body.allowed}


@app.patch("/v1/families/voice-preferences")
def update_voice_preferences(body: PremiumVoicePreference,
                             claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    if body.enabled and not configured_tts(settings):
        raise HTTPException(503, "真人感语音服务尚未配置")
    with transaction(settings.database_path) as db:
        db.execute("UPDATE families SET premium_voice_enabled=? WHERE id=?",
                   (int(body.enabled), family["id"]))
    broadcast_sync(family["id"], {"type": "voice_preferences_changed"})
    return {"enabled": body.enabled,
            "used_characters": family["premium_voice_used_chars"],
            "trial_characters": speech_quota_limit(settings),
            "quota_mode": settings.tts_quota_mode}


def _speech_error(status: int, message: str, code: str, param: str | None = None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "message": message, "type": "invalid_request_error" if status < 500 else "server_error",
        "param": param, "code": code,
    }})


@app.post("/v1/audio/speech")
async def create_speech(body: SpeechRequest,
                        authorization: Annotated[str | None, Header()] = None,
                        idempotency_key: Annotated[str | None, Header()] = None
                        ) -> Response:
    """OpenAI Speech API request shape with binary, streamed MP3 or 24 kHz PCM output."""
    token = (authorization or "").removeprefix("Bearer ")
    claims = read_token(settings.app_secret, token)
    if not claims:
        return _speech_error(401, "Authentication required", "invalid_api_key")
    if claims["role"] != "child":
        return _speech_error(403, "A paired child device is required", "forbidden")
    try:
        family = family_for(claims)
    except HTTPException:
        return _speech_error(403, "No paired family", "forbidden")
    for param, actual, expected in (("model", body.model, MODEL_ALIAS),
                                    ("voice", body.voice, VOICE_ALIAS),
                                    ("stream_format", body.stream_format, "audio")):
        if actual != expected:
            return _speech_error(400, f"Unsupported {param}: {actual}",
                                 "unsupported_parameter", param)
    if body.response_format not in {"mp3", "pcm"}:
        return _speech_error(400, "Only mp3 and pcm are supported",
                             "unsupported_format", "response_format")
    if not 0.5 <= body.speed <= 2 or not math.isfinite(body.speed):
        return _speech_error(400, "speed must be between 0.5 and 2.0",
                             "invalid_parameter", "speed")
    if body.instructions is not None:
        return _speech_error(400, "instructions are not supported for this voice",
                             "unsupported_parameter", "instructions")
    if not body.input.strip() or len(body.input) > 4096:
        return _speech_error(400, "input must contain 1 to 4096 characters",
                             "invalid_parameter", "input")
    if idempotency_key and len(idempotency_key) > 100:
        return _speech_error(400, "Idempotency-Key is too long", "invalid_parameter")
    if not configured_tts(settings):
        return _speech_error(503, "Speech service is unavailable", "service_unavailable")
    text = spoken_text(body.input)
    characters = billable_characters(text)
    request_id = uid()
    with transaction(settings.database_path) as db:
        db.execute("DELETE FROM speech_requests WHERE status='reserved' AND created_at<?",
                   (current() - 300,))
        state = db.execute("SELECT premium_voice_enabled,premium_voice_used_chars "
                           "FROM families WHERE id=?", (family["id"],)).fetchone()
        if not state["premium_voice_enabled"]:
            return _speech_error(403, "Premium voice is disabled", "voice_not_enabled")
        if idempotency_key and db.execute(
            "SELECT 1 FROM speech_requests WHERE family_id=? AND request_key=?",
            (family["id"], idempotency_key),
        ).fetchone():
            return _speech_error(409, "Speech request already started", "duplicate_request")
        reserved = db.execute("SELECT COALESCE(SUM(characters),0) FROM speech_requests "
                              "WHERE family_id=? AND status='reserved'",
                              (family["id"],)).fetchone()[0]
        quota_limit = speech_quota_limit(settings)
        if (quota_limit is not None
                and state["premium_voice_used_chars"] + reserved + characters > quota_limit):
            return _speech_error(429, "Premium voice trial has ended", "insufficient_quota")
        db.execute("INSERT INTO speech_requests(id,family_id,request_key,characters,status,created_at) "
                   "VALUES(?,?,?,?,?,?)",
                   (request_id, family["id"], idempotency_key, characters, "reserved", current()))
    chunks = iter(stream_tts(settings, text, body.response_format, body.speed))
    try:
        first = await asyncio.to_thread(lambda: next(chunks, None))
        if not first:
            raise SpeechServiceError("Speech provider returned no audio")
    except Exception as exc:
        chunks.close()
        with transaction(settings.database_path) as db:
            db.execute("DELETE FROM speech_requests WHERE id=? AND status='reserved'", (request_id,))
        if (isinstance(exc, TencentSpeechServiceError) and exc.resource_exhausted
                and not exc.audio_started):
            return _speech_error(429, "Configured speech resource allowances are exhausted",
                                 "insufficient_quota")
        return _speech_error(502, "Speech service is temporarily unavailable", "provider_error")
    with transaction(settings.database_path) as db:
        db.execute("UPDATE speech_requests SET status='charged' WHERE id=?", (request_id,))
        db.execute("UPDATE families SET premium_voice_used_chars=premium_voice_used_chars+? "
                   "WHERE id=?", (characters, family["id"]))

    def audio_stream():
        try:
            yield first
            yield from chunks
        finally:
            chunks.close()

    return StreamingResponse(audio_stream(),
                             media_type="audio/mpeg" if body.response_format == "mp3" else "audio/pcm",
                             headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@app.post("/v1/voice/transcribe")
async def transcribe_voice(audio: UploadFile = File(...),
                           claims: dict = Depends(principal)) -> dict:
    family = family_for(claims)
    provider, _ = speech_provider()
    if not family["audio_transcription_allowed"] or family["audio_transcription_provider"] != provider:
        raise HTTPException(403, "家长尚未允许短语音转写")
    if not provider:
        raise HTTPException(503, "短语音转写服务尚未配置")
    media_type = (audio.content_type or "").split(";", 1)[0].lower()
    if media_type not in {"audio/webm", "audio/mp4", "audio/ogg", "audio/wav"}:
        raise HTTPException(415, "当前语音格式不受支持")
    content = await audio.read(900_001)
    if not content or len(content) > 900_000:
        raise HTTPException(413, "语音片段过长，请分段再说")
    extension = {"audio/webm": "webm", "audio/mp4": "mp4", "audio/ogg": "ogg",
                 "audio/wav": "wav"}[media_type]
    if provider.startswith("local:"):
        try:
            transcript = await asyncio.to_thread(
                local_asr.transcribe, settings.asr_local_model_path, content
            )
            return {"text": to_simplified.convert(transcript.strip())[:1000]}
        except Exception as exc:
            raise HTTPException(502, "本地语音转写暂不可用，请重试") from exc
    headers = ({"Authorization": f"Bearer {settings.asr_api_key}"}
               if settings.asr_api_key else {})
    try:
        async with httpx.AsyncClient(timeout=35, follow_redirects=False) as client:
            response = await client.post(
                settings.asr_api_url,
                headers=headers,
                data={"model": settings.asr_model, "language": "zh"},
                files={"file": (f"voice.{extension}", content, media_type)},
            )
        response.raise_for_status()
        transcript = response.json().get("text", "")
        if not isinstance(transcript, str):
            raise ValueError("Invalid transcription response")
        return {"text": to_simplified.convert(transcript.strip())[:1000]}
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "语音转写服务暂不可用，请重试") from exc


@app.post("/v1/devices/status")
def device_status(body: DeviceClaim) -> dict:
    device = one("SELECT * FROM devices WHERE id=?", (body.device_id,))
    if not device or not body.device_secret or not hmac.compare_digest(
        device["secret_hash"], digest(body.device_secret)
    ):
        raise HTTPException(403, "Invalid device secret")
    return {"paired": bool(device["family_id"]), "family_id": device["family_id"],
            "token": make_token(settings.app_secret, "child", body.device_id) if device["family_id"] else None}


@app.get("/v1/families/dashboard")
def dashboard(offset_minutes: int = Query(480, ge=-720, le=840),
              claims: dict = Depends(principal)) -> dict:
    purge_expired(settings.database_path)
    family = family_for(claims)
    fid = family["id"]
    today, _ = day_bounds(offset_minutes)
    tasks = many("SELECT t.id,t.title,t.subject,t.due_date,t.due_at,t.status,t.version,"
                 "t.created_at,t.first_submitted_at,t.rejection_reason,t.child_feedback,"
                 "t.cancel_reason,t.latest_submission_id,"
                 "s.ai_status,s.ai_summary,s.submitted_at,"
                 "(SELECT COUNT(*) FROM submission_pages p WHERE p.submission_id=s.id) AS page_count "
                 "FROM tasks t LEFT JOIN task_submissions s ON s.id=t.latest_submission_id "
                 "WHERE t.family_id=? AND (?='parent' OR "
                 "t.status NOT IN ('confirmed','canceled') OR t.created_at>=?) "
                 "ORDER BY t.created_at DESC", (fid, claims["role"], today))
    if claims["role"] == "child":
        for task in tasks:
            task["rejection_reason"] = task["child_feedback"] or task["rejection_reason"]
    reminders = (many("SELECT r.task_id,r.event_key,r.snoozed_until,r.spoken_at FROM task_reminder_state r "
                      "JOIN tasks t ON t.id=r.task_id WHERE t.family_id=? AND r.device_id=?",
                      (fid, claims["sub"])) if claims["role"] == "child" else [])
    session = one("SELECT id,device_id,status,started_at,correction_requested_at,correction_authorized_until,timer_goal_seconds,timer_goal_source,timer_last_seq,proactive_help_enabled FROM sessions WHERE family_id=? AND status IN ('active','paused') ORDER BY started_at DESC LIMIT 1", (fid,))
    if session:
        session["correction_access"] = correction_access(session, claims)
        session.pop("device_id")
    observations = many("SELECT o.id,o.session_id,o.task_id,o.kind,o.label,o.evidence,o.confidence,o.progress,o.writing,o.visible_steps,o.review_status,o.created_at FROM observations o JOIN sessions s ON s.id=o.session_id WHERE s.family_id=? ORDER BY o.created_at DESC LIMIT 30", (fid,))
    hints = many("SELECT h.id,h.session_id,h.task_id,h.question,h.response,h.source,h.level,h.created_at,h.teaching_json "
                 "FROM hints h JOIN sessions s ON s.id=h.session_id "
                 "WHERE s.family_id=? AND h.expires_at>? AND s.expires_at>? "
                 "ORDER BY h.created_at DESC,h.rowid DESC LIMIT 30", (fid, current(), current()))
    for saved_hint in hints:
        saved_hint["teaching_scene"] = _read_hint_teaching_scene(saved_hint.pop("teaching_json"))
    corrections = many("SELECT c.id,c.session_id,c.task_id,t.title AS task_title,t.subject,c.subject_label,c.page_label,c.batch_id,c.page_index,c.batch_total,c.summary,c.checks_json,c.confidence,c.review_status,c.created_at,c.image_jpeg IS NOT NULL AS image_available,s.status AS session_status,s.correction_authorized_until FROM corrections c JOIN sessions s ON s.id=c.session_id LEFT JOIN tasks t ON t.id=c.task_id WHERE s.family_id=? ORDER BY c.created_at DESC,c.rowid DESC LIMIT 20", (fid,))
    for correction in corrections:
        correction["checks"] = json.loads(correction.pop("checks_json"))
        if claims["role"] == "child" and correction["session_status"] != "finished" and not correction_authorized({
            "status": correction["session_status"],
            "correction_authorized_until": correction["correction_authorized_until"],
        }):
            correction["hidden"] = True
    corrections = [item for item in corrections if not item.pop("hidden", False)]
    for correction in corrections:
        correction.pop("session_status")
        correction.pop("correction_authorized_until")
    reflections = many("SELECT r.id,r.session_id,r.task_id,r.text,r.source,r.created_at FROM reflections r JOIN sessions s ON s.id=r.session_id WHERE s.family_id=? ORDER BY r.created_at DESC LIMIT 30", (fid,))
    live = one("SELECT id,status FROM live_sessions WHERE family_id=? AND status='active'", (fid,))
    timer_suggestion = suggestion(settings.database_path, family, tasks, current(), offset_minutes)
    archived_unconfirmed = one("SELECT COALESCE(SUM(unconfirmed_count),0) AS count "
                               "FROM task_archive_counts WHERE family_id=?", (fid,))["count"]
    expiring_tasks = [task["id"] for task in tasks
                      if task["status"] not in ("confirmed", "canceled")
                      and task["created_at"] + RETENTION_SECONDS <= current() + 7 * 86400]
    asr_provider, asr_name = speech_provider()
    return {"server_now": current(),
             "family": {**{key: family[key] for key in ("id", "child_name", "age_band", "grade", "textbook", "proactive_help_mode")},
                        "audio_transcription_allowed": int(bool(family["audio_transcription_allowed"] and
                                                                  family["audio_transcription_provider"] == asr_provider)),
                        "premium_voice_enabled": int(family["premium_voice_enabled"]),
                        "premium_voice_used_chars": family["premium_voice_used_chars"],
                        "premium_voice_trial_chars": speech_quota_limit(settings),
                        "premium_voice_quota_mode": settings.tts_quota_mode},
             "tts_available": configured_tts(settings),
             "tts_provider": ("腾讯云" if settings.tts_provider == "tencent" else "阿里云")
             if configured_tts(settings) else None,
             "asr_available": bool(asr_provider),
             "asr_provider": asr_name,
            "tasks": tasks, "task_reminders": reminders,
            "expiring_tasks": expiring_tasks if claims["role"] == "parent" else [],
            "archived_unconfirmed_count": archived_unconfirmed if claims["role"] == "parent" else None,
            "session": session, "observations": observations,
            "timer": snapshot(settings.database_path, session) if session else None,
            "timer_suggestion": timer_suggestion,
            "hints": hints, "corrections": corrections, "reflections": reflections, "live": live,
             "ai_usage": ai_usage_summary(fid, offset_minutes) if claims["role"] == "parent" else None,
             "page_preparation": page_preparation().status(session["id"]) if session else None}


@app.post("/v1/tasks/import")
def import_tasks(body: TaskImport, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    fid = family_for(claims)["id"]
    lines = [line.strip().lstrip("-•0123456789.、 ") for line in body.text.splitlines()]
    titles = [line for line in lines if line]
    if not titles or len(titles) > 50:
        raise HTTPException(422, "Provide 1 to 50 homework lines")
    now = current()
    due_at = (int(datetime(body.due_date.year, body.due_date.month, body.due_date.day,
                           23, 59, tzinfo=UTC).timestamp()) -
              body.timezone_offset_minutes * 60) if body.due_date else None
    if due_at and (due_at <= now or due_at > now + RETENTION_SECONDS):
        raise HTTPException(422, "截止时间须在未来 60 天内")
    records = []
    with transaction(settings.database_path) as db:
        for title in titles:
            task_id = uid()
            db.execute("INSERT INTO tasks(id,family_id,title,subject,due_date,due_at,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?)",
                       (task_id, fid, title[:500], body.subject,
                        str(body.due_date) if body.due_date else None, due_at,
                        now, now + RETENTION_SECONDS))
            records.append({"id": task_id, "title": title[:500], "status": "todo", "version": 1})
    return {"tasks": records}


@app.post("/v1/tasks/batch")
def publish_task_batch(body: TaskBatchPublish, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    now = current()
    due_values = [item.due_at or body.due_at for item in body.items]
    if any(due <= now or due > now + RETENTION_SECONDS for due in due_values):
        raise HTTPException(422, "截止时间须在未来 60 天内")
    records = []
    with transaction(settings.database_path) as db:
        existing = db.execute("SELECT id,title,status,version FROM tasks WHERE family_id=? AND batch_request_id=? ORDER BY batch_index,id",
                              (family["id"], body.request_id)).fetchall()
        if existing:
            return {"tasks": [dict(row) for row in existing]}
        for index, (item, due) in enumerate(zip(body.items, due_values, strict=True)):
            task_id = uid()
            db.execute("INSERT INTO tasks(id,family_id,title,subject,due_date,due_at,status,version,created_at,expires_at,batch_request_id,batch_index) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                       (task_id, family["id"], item.title.strip(), item.subject,
                        datetime.fromtimestamp(due + body.timezone_offset_minutes * 60, UTC).date().isoformat(), due,
                        "todo", 1, now, now + RETENTION_SECONDS, body.request_id, index))
            records.append({"id": task_id, "title": item.title.strip(), "status": "todo", "version": 1})
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"tasks": records}


@app.patch("/v1/tasks/{task_id}/details")
def edit_task_details(task_id: str, body: TaskDetailsUpdate,
                      claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    task = family_task(task_id, family_for(claims)["id"])
    if task["status"] in ("confirmed", "canceled"):
        raise HTTPException(409, "Closed task cannot be edited")
    if body.due_at <= current() or body.due_at > task["created_at"] + RETENTION_SECONDS:
        raise HTTPException(422, "截止时间须在任务发布后 60 天内")
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE tasks SET title=?,subject=?,due_at=?,due_date=?,version=version+1 "
                            "WHERE id=? AND family_id=? AND version=?",
                            (body.title.strip(), body.subject, body.due_at,
                             datetime.fromtimestamp(body.due_at + body.timezone_offset_minutes * 60, UTC).date().isoformat(),
                             task_id, task["family_id"], body.version))
        if result.rowcount != 1:
            raise HTTPException(409, "Task changed; refresh and retry")
    broadcast_sync(task["family_id"], {"type": "tasks_changed"})
    return {"id": task_id, "version": body.version + 1, "due_at": body.due_at}


@app.post("/v1/tasks/{task_id}/cancel")
def cancel_task(task_id: str, body: TaskCancel, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    task = family_task(task_id, family_for(claims)["id"])
    if task["status"] in ("confirmed", "canceled"):
        raise HTTPException(409, "Task is already closed")
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE tasks SET status='canceled',cancel_reason=?,version=version+1 "
                            "WHERE id=? AND family_id=? AND version=?",
                            (body.reason.strip(), task_id, task["family_id"], body.version))
        if result.rowcount != 1:
            raise HTTPException(409, "Task changed; refresh and retry")
    broadcast_sync(task["family_id"], {"type": "tasks_changed"})
    return {"id": task_id, "status": "canceled", "version": body.version + 1}


@app.patch("/v1/tasks/{task_id}")
def update_task(task_id: str, body: TaskUpdate, claims: dict = Depends(principal)) -> dict:
    family = family_for(claims)
    task = family_task(task_id, family["id"])
    allowed = {"parent": {"confirmed"}, "child": {"doing", "todo"}}
    if body.status not in allowed[claims["role"]]:
        raise HTTPException(403, "Status is reserved for the other role")
    if body.status == "confirmed" and task["status"] != "awaiting_parent":
        raise HTTPException(409, "Child must mark task complete first")
    if task["status"] in ("confirmed", "canceled"):
        raise HTTPException(409, "Task is closed")
    if body.status in ("todo", "doing") and task["status"] == "awaiting_parent":
        raise HTTPException(409, "Submitted task cannot be withdrawn")
    now = current()
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE tasks SET status=?, version=version+1, parent_confirmed_at=CASE WHEN ?='confirmed' THEN ? ELSE parent_confirmed_at END WHERE id=? AND family_id=? AND version=?",
                            (body.status, body.status, now, task_id, family["id"], body.version))
        if result.rowcount != 1:
            raise HTTPException(409, "Task changed; refresh and retry")
        if body.status == "confirmed" and task["latest_submission_id"]:
            db.execute("UPDATE task_submissions SET status='accepted',reviewed_at=? "
                       "WHERE id=? AND status='pending'", (now, task["latest_submission_id"]))
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"id": task_id, "status": body.status, "version": body.version + 1}


def run_submission_review(submission_id: str) -> None:
    """Assess visible completeness without turning an AI opinion into a task decision."""
    with transaction(settings.database_path) as db:
        row = db.execute("SELECT s.id,s.task_id,s.family_id,t.title,t.subject,f.age_band "
                         "FROM task_submissions s JOIN tasks t ON t.id=s.task_id "
                         "JOIN families f ON f.id=s.family_id WHERE s.id=? AND s.ai_status='pending'",
                         (submission_id,)).fetchone()
        if not row:
            return
        db.execute("UPDATE task_submissions SET ai_status='processing' WHERE id=?", (submission_id,))
    pages = many("SELECT image_jpeg FROM submission_pages WHERE submission_id=? ORDER BY page_index",
                 (submission_id,))
    if not pages or not (settings.ai_api_url and settings.ai_api_key and settings.ai_model):
        status, summary, details = "unavailable", "AI 暂未评估，请家长查看提交依据。", "[]"
    else:
        try:
            set_usage_family(row["family_id"])
            result = assess_submission(settings, row["title"], row["subject"],
                                       [page["image_jpeg"] for page in pages], row["age_band"])
            status, summary = "ready", result["summary"][:500]
            details = json.dumps(result["pages"], ensure_ascii=False)
        except Exception:
            status, summary, details = "unavailable", "AI 暂未评估，请家长查看提交依据。", "[]"
    with transaction(settings.database_path) as db:
        db.execute("UPDATE task_submissions SET ai_status=?,ai_summary=?,ai_details=? "
                   "WHERE id=? AND ai_status='processing'", (status, summary, details, submission_id))
    broadcast_sync(row["family_id"], {"type": "tasks_changed"})


@app.post("/v1/tasks/{task_id}/submissions")
def submit_task(task_id: str, background_tasks: BackgroundTasks,
                version: int = Form(...), request_id: str = Form(...),
                note: str = Form(""), session_id: str | None = Form(None),
                photo_issue: str = Form(""), images: list[UploadFile] = File(default=[]),
                claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    task = family_task(task_id, family["id"])
    request_id = request_id.strip()[:100]
    if len(request_id) < 8:
        raise HTTPException(422, "Invalid submission request id")
    existing = one("SELECT id,attempt FROM task_submissions WHERE task_id=? AND request_id=?",
                   (task_id, request_id))
    if existing:
        return {"id": existing["id"], "status": "awaiting_parent", "attempt": existing["attempt"]}
    if task["version"] != version or task["status"] not in ("todo", "doing", "needs_revision"):
        raise HTTPException(409, "Task changed; refresh before submitting")
    if session_id:
        session = family_session(session_id, family["id"])
        if session["device_id"] != claims["sub"]:
            raise HTTPException(403, "Session belongs to another device")
    if len(images) > 12 or (not images and not photo_issue.strip()):
        raise HTTPException(422, "请附纸面截图；无法拍摄时说明原因")
    if len(note) > 500 or len(photo_issue) > 200:
        raise HTTPException(422, "提交说明过长")
    image_data = [read_image(image) for image in images]
    now = current()
    submission_id = uid()
    with transaction(settings.database_path) as db:
        latest = db.execute("SELECT COALESCE(MAX(attempt),0)+1 AS next FROM task_submissions WHERE task_id=?",
                            (task_id,)).fetchone()["next"]
        changed = db.execute("UPDATE tasks SET status='awaiting_parent',version=version+1,"
                             "child_checked_at=?,first_submitted_at=COALESCE(first_submitted_at,?),"
                             "rejection_reason=NULL,child_feedback=NULL,latest_submission_id=? "
                             "WHERE id=? AND family_id=? AND version=? AND status IN ('todo','doing','needs_revision')",
                             (now, now, submission_id, task_id, family["id"], version))
        if changed.rowcount != 1:
            raise HTTPException(409, "Task changed; refresh before submitting")
        db.execute("INSERT INTO task_submissions(id,task_id,family_id,session_id,attempt,note,status,"
                   "ai_status,submitted_at,request_id,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (submission_id, task_id, family["id"], session_id, latest,
                    (note.strip() or photo_issue.strip())[:500], "pending",
                    "pending", now, request_id, task["expires_at"]))
        for index, data in enumerate(image_data, 1):
            db.execute("INSERT INTO submission_pages(id,submission_id,page_index,image_jpeg,image_hash,created_at,expires_at) "
                       "VALUES(?,?,?,?,?,?,?)",
                       (uid(), submission_id, index, data, hashlib.sha256(data).hexdigest(),
                        now, task["expires_at"]))
    background_tasks.add_task(run_submission_review, submission_id)
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"id": submission_id, "status": "awaiting_parent", "attempt": latest,
            "page_count": len(image_data), "version": version + 1}


@app.get("/v1/tasks/{task_id}/submissions")
def task_submissions(task_id: str, claims: dict = Depends(principal)) -> dict:
    family = family_for(claims)
    family_task(task_id, family["id"])
    attempts = many("SELECT id,attempt,note,status,ai_status,ai_summary,ai_details,"
                    "submitted_at,reviewed_at,review_reason,child_feedback FROM task_submissions "
                    "WHERE task_id=? AND family_id=? ORDER BY attempt DESC", (task_id, family["id"]))
    for attempt in attempts:
        if claims["role"] == "child":
            attempt["review_reason"] = attempt["child_feedback"] or attempt["review_reason"]
        attempt["pages"] = many("SELECT id,page_index,grade_status,grade_summary,grade_checks_json "
                                 "FROM submission_pages WHERE submission_id=? "
                                 "ORDER BY page_index", (attempt["id"],))
        for page in attempt["pages"]:
            page["grade_checks"] = json.loads(page.pop("grade_checks_json") or "[]")
            if claims["role"] == "child":
                page["grade_checks"] = []
                page["grade_summary"] = ""
        attempt["ai_details"] = json.loads(attempt["ai_details"] or "[]")
    evidence = []
    if claims["role"] == "parent":
        for table, columns in (
            ("hints", "question,response,source,level"),
            ("observations", "kind,label,evidence,progress,writing,visible_steps,review_status"),
            ("corrections", "summary,review_status"),
            ("reflections", "text,source"),
        ):
            rows = many(f"SELECT id,created_at,{columns} FROM {table} "
                        "WHERE task_id=? ORDER BY created_at DESC", (task_id,))
            evidence.extend({"type": table, **row} for row in rows)
        evidence.sort(key=lambda item: item["created_at"], reverse=True)
    return {"submissions": attempts, "evidence": evidence}


def run_submission_page_grade(page_id: str) -> None:
    """Grade a saved final page only after a parent's explicit request."""
    with transaction(settings.database_path) as db:
        row = db.execute("SELECT p.image_jpeg,s.family_id,s.session_id,t.id AS task_id,t.title,t.subject,"
                         "f.age_band,f.grade,f.textbook FROM submission_pages p "
                         "JOIN task_submissions s ON s.id=p.submission_id "
                         "JOIN tasks t ON t.id=s.task_id JOIN families f ON f.id=s.family_id "
                         "WHERE p.id=? AND p.grade_status='pending'", (page_id,)).fetchone()
        if not row:
            return
        db.execute("UPDATE submission_pages SET grade_status='processing' WHERE id=?", (page_id,))
    try:
        image_data = row["image_jpeg"]
        regions = correction_regions(image_data)
        results = []
        teaching_context = (learning_context(row["session_id"], row["task_id"], "correction")
                            if row["session_id"] else "")
        set_usage_family(row["family_id"])
        grader = grading_settings(row["family_id"])
        question_reader = getattr(page_preparation(), "questions_for", None)
        try:
            prepared_items = (question_reader(row["session_id"], image_data)
                              if question_reader and row["session_id"] else [])
        except (OSError, ValueError):
            prepared_items = []
        for name, region_box, detail_image in regions:
            prompt = (f"这是已提交作业《{row['title']}》的{name}。科目：{row['subject'] or '未指定'}。"
                      "检查此区域完整可见的全部书面题，包括确认未填写解题过程和答案的题；"
                      "先读题目要求（包括手写题干和手绘原题图）、条件、孩子答案或确认留白的作答区，"
                      "逐题对照书写步骤与解题思路。看不清或无法判断要明确标记，不猜测。"
                      "开放表达可给讨论建议。为每题返回在该区域中的位置。")
            references = []
            for item in prepared_items:
                answer = item.get("solution") or {}
                box = item["box"]
                center_x, center_y = box[0] + box[2] / 2, box[1] + box[3] / 2
                if (item["verification_status"] in {"verified", "rechecked"} and
                        item["read_confidence"] >= .85 and
                        answer.get("confidence", 0) >= .85 and not answer.get("ambiguous") and
                        region_box[0] <= center_x <= region_box[0] + region_box[2] and
                        region_box[1] <= center_y <= region_box[1] + region_box[3]):
                    references.append({"label": item["label"], "evidence": item["evidence"],
                                       "answer": answer.get("answer", ""),
                                       "steps": answer.get("steps", "")})
            if references:
                prompt += (" 以下是已核对的印刷题参考解法；孩子本次写了什么或是否留白必须"
                           "重新从本次照片读取，不能从参考解法推断。"
                           + json.dumps(references[:12], ensure_ascii=False))
            result = analyze(grader, detail_image, prompt, row["age_band"], row["grade"],
                             row["textbook"], "correction",
                             context_image=image_data if len(regions) > 1 else None,
                             session_context=teaching_context)
            results.append((region_box, result))
        merged = merge_correction_regions(results)
        checks = merged.get("checks", [])
        counts = {status: sum(item["status"] == status for item in checks)
                  for status in ("looks_correct", "check_again", "needs_discussion", "unclear")}
        blank_count = sum(item.get("answer_state") == "blank" and item["status"] == "check_again"
                          for item in checks)
        blank_detail = f"（其中未作答 {blank_count} 项，按错题计）" if blank_count else ""
        summary = (f"已检查 {len(checks)} 项：看起来正确 {counts['looks_correct']}，"
                   f"需要订正 {counts['check_again']}{blank_detail}，"
                   f"适合讨论 {counts['needs_discussion']}，"
                   f"暂无法判断 {counts['unclear']}。") if checks else (
                       "本页没有可可靠批改的书面题，请家长对照原图核对。")
        status = "ready"
    except Exception:
        status, summary, checks = "failed", "完整批改暂不可用，请查看原图或稍后重试。", []
    with transaction(settings.database_path) as db:
        db.execute("UPDATE submission_pages SET grade_status=?,grade_summary=?,grade_checks_json=? "
                   "WHERE id=? AND grade_status='processing'",
                   (status, summary, json.dumps(checks, ensure_ascii=False), page_id))
    broadcast_sync(row["family_id"], {"type": "tasks_changed"})


def run_submission_grades(page_ids: list[str]) -> None:
    for page_id in page_ids:
        run_submission_page_grade(page_id)


@app.post("/v1/tasks/{task_id}/submissions/{submission_id}/grade")
def request_submission_grade(task_id: str, submission_id: str,
                             background_tasks: BackgroundTasks,
                             claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    family_task(task_id, family["id"])
    if not (settings.ai_api_url and settings.ai_api_key and settings.ai_model):
        raise HTTPException(503, "视觉模型尚未配置")
    submission = one("SELECT id FROM task_submissions WHERE id=? AND task_id=? AND family_id=?",
                     (submission_id, task_id, family["id"]))
    if not submission:
        raise HTTPException(404, "Submission not found")
    with transaction(settings.database_path) as db:
        pages = db.execute("SELECT id FROM submission_pages WHERE submission_id=? AND "
                           "grade_status IN ('not_requested','failed') ORDER BY page_index",
                           (submission_id,)).fetchall()
        for page in pages:
            db.execute("UPDATE submission_pages SET grade_status='pending',grade_summary='' WHERE id=?",
                       (page["id"],))
    if pages:
        background_tasks.add_task(run_submission_grades, [page["id"] for page in pages])
    return {"queued_pages": len(pages)}


@app.get("/v1/submission-pages/{page_id}/image")
def submission_page_image(page_id: str, claims: dict = Depends(principal)) -> Response:
    family = family_for(claims)
    page = one("SELECT p.image_jpeg FROM submission_pages p JOIN task_submissions s "
               "ON s.id=p.submission_id WHERE p.id=? AND s.family_id=? AND p.expires_at>?",
               (page_id, family["id"], current()))
    if not page:
        raise HTTPException(404, "Submission page not found")
    return Response(page["image_jpeg"], media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.patch("/v1/submission-pages/{page_id}/checks/{check_index}/parent-review")
def review_submission_check(page_id: str, check_index: int, body: ParentCheckUpdate,
                            claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    note, answer = body.note.strip(), body.answer.strip()
    box = normalized_box(body.box) if body.box is not None else None
    if body.box is not None and box is None:
        raise HTTPException(422, "Invalid question location")
    with transaction(settings.database_path) as db:
        page = db.execute("SELECT p.grade_checks_json,p.grade_status,p.grade_summary FROM submission_pages p "
                          "JOIN task_submissions s ON s.id=p.submission_id "
                          "WHERE p.id=? AND s.family_id=? AND p.expires_at>?",
                          (page_id, family["id"], current())).fetchone()
        if not page or page["grade_status"] != "ready":
            raise HTTPException(404, "Graded page not found")
        checks = json.loads(page["grade_checks_json"])
        if not 0 <= check_index < len(checks):
            raise HTTPException(404, "Question not found")
        check = checks[check_index]
        if body.revision != check.get("parent_revision", 0):
            raise HTTPException(409, "This question changed; reload before saving")
        changed = body.status != check["status"] or answer != check.get("answer", "")
        if changed and len(note) < 2:
            raise HTTPException(422, "Explain the changed judgment or answer")
        previous = {field: check.get(field) for field in ("status", "answer", "box")}
        check.setdefault("original_review", check_review_snapshot(check))
        review = {"status": body.status, "answer": answer, "note": note,
                  "box": box, "revision": body.revision + 1, "updated_at": current()}
        check["parent_review"] = review
        check["parent_revision"] = body.revision + 1
        check.setdefault("parent_review_history", []).append({"previous": previous, "updated": review})
        check["status"], check["answer"], check["box"] = body.status, answer, box
        if changed:
            check["answer_state"] = ("written" if answer else "blank"
                                     if body.status == "check_again" else "uncertain")
        if changed:
            check["reason"] = note
            check["evidence"] = "家长人工校对"
            check["explanation"] = "请以家长校对和原题为准。"
            check["next_step"] = ""
            check["steps"] = []
            check.update(reference_answer="", solution_idea="", solution_steps=[],
                         comparison="", diagram=None)
        summary = updated_correction_summary(page["grade_summary"], checks)
        db.execute("UPDATE submission_pages SET grade_checks_json=?,grade_summary=? WHERE id=?",
                   (json.dumps(checks, ensure_ascii=False), summary, page_id))
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"check": check, "grade_summary": summary}


@app.post("/v1/tasks/{task_id}/feedback-draft")
def task_feedback_draft(task_id: str, body: FeedbackDraftRequest,
                        claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    task = family_task(task_id, family["id"])
    if task["status"] != "awaiting_parent" or task["latest_submission_id"] != body.submission_id:
        raise HTTPException(409, "Task changed; refresh before drafting feedback")
    return {"draft": draft_child_feedback(settings, task["title"], body.reason.strip())}


@app.post("/v1/tasks/{task_id}/reviews")
def review_task(task_id: str, body: TaskReview, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    task = family_task(task_id, family["id"])
    reason = body.reason.strip()
    child_feedback = (body.child_feedback.strip() or reason) if body.decision == "rejected" else ""
    if body.decision == "rejected" and not reason:
        raise HTTPException(422, "退回时请写明需要补做什么")
    if task["version"] != body.version or task["status"] != "awaiting_parent" or task["latest_submission_id"] != body.submission_id:
        raise HTTPException(409, "Task changed; refresh before reviewing")
    now = current()
    status = "confirmed" if body.decision == "accepted" else "needs_revision"
    with transaction(settings.database_path) as db:
        changed = db.execute("UPDATE tasks SET status=?,version=version+1,"
                             "parent_confirmed_at=CASE WHEN ?='confirmed' THEN ? ELSE parent_confirmed_at END,"
                             "rejection_reason=?,child_feedback=? "
                             "WHERE id=? AND family_id=? AND version=? AND latest_submission_id=?",
                             (status, status, now, reason if reason else None,
                              child_feedback or None,
                              task_id, family["id"], body.version, body.submission_id))
        if changed.rowcount != 1:
            raise HTTPException(409, "Task changed; refresh before reviewing")
        db.execute("UPDATE task_submissions SET status=?,reviewed_at=?,review_reason=?,child_feedback=? WHERE id=?",
                   (body.decision, now, reason or None, child_feedback or None, body.submission_id))
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"id": task_id, "status": status, "version": body.version + 1}


@app.post("/v1/tasks/reviews/batch")
def confirm_task_batch(body: BatchReviews, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    if len({item.task_id for item in body.items}) != len(body.items):
        raise HTTPException(422, "Duplicate task in batch")
    now = current()
    with transaction(settings.database_path) as db:
        for item in body.items:
            task = db.execute("SELECT status,version,latest_submission_id FROM tasks "
                              "WHERE id=? AND family_id=?", (item.task_id, family["id"])).fetchone()
            if not task or task["status"] != "awaiting_parent" or task["version"] != item.version or task["latest_submission_id"] != item.submission_id:
                raise HTTPException(409, "待审任务已变化，请刷新后重新确认")
        for item in body.items:
            db.execute("UPDATE tasks SET status='confirmed',version=version+1,parent_confirmed_at=? "
                       "WHERE id=?", (now, item.task_id))
            db.execute("UPDATE task_submissions SET status='accepted',reviewed_at=? "
                       "WHERE id=?", (now, item.submission_id))
    broadcast_sync(family["id"], {"type": "tasks_changed"})
    return {"confirmed": [item.task_id for item in body.items]}


@app.post("/v1/tasks/{task_id}/reminders")
def mark_task_reminder(task_id: str, body: TaskReminderUpdate,
                       claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family_task(task_id, family_for(claims)["id"])
    now = current()
    with transaction(settings.database_path) as db:
        db.execute("INSERT INTO task_reminder_state(task_id,device_id,event_key,snoozed_until,spoken_at) "
                   "VALUES(?,?,?,?,?) ON CONFLICT(task_id,device_id,event_key) DO UPDATE SET "
                   "snoozed_until=excluded.snoozed_until,spoken_at=excluded.spoken_at",
                   (task_id, claims["sub"], body.event_key,
                    now + 1800 if body.action == "snooze" else 0,
                    None if body.action == "snooze" else now))
    return {"task_id": task_id, "event_key": body.event_key,
            "snoozed_until": now + 1800 if body.action == "snooze" else 0}


def read_image(image: UploadFile) -> bytes:
    if image.content_type != "image/jpeg":
        raise HTTPException(415, "JPEG image required")
    data = image.file.read(2_000_001)
    if len(data) > 2_000_000 or not data.startswith(b"\xff\xd8"):
        raise HTTPException(413, "Invalid or oversized image")
    return data


def parse_selected_strokes(raw: str) -> list[list[list[float]]]:
    """Validate normalized pen paths before using them as question-location evidence."""
    try:
        strokes = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "圈画笔迹格式无效，请重新圈画") from exc
    if not isinstance(strokes, list) or not 1 <= len(strokes) <= 12:
        raise HTTPException(422, "圈画笔迹数量无效，请重新圈画")
    clean = []
    total_points = 0
    path_length = 0.0
    for stroke in strokes:
        if not isinstance(stroke, list) or len(stroke) < 2:
            raise HTTPException(422, "圈画笔迹格式无效，请重新圈画")
        total_points += len(stroke)
        if total_points > 1200:
            raise HTTPException(422, "圈画笔迹过长，请清除后重新圈画")
        clean_stroke = []
        for point in stroke:
            if (not isinstance(point, list) or len(point) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, int | float) or
                        not math.isfinite(value) or not 0 <= value <= 1 for value in point)):
                raise HTTPException(422, "圈画坐标无效，请重新圈画")
            clean_stroke.append([float(point[0]), float(point[1])])
        path_length += sum(math.dist(a, b) for a, b in zip(clean_stroke, clean_stroke[1:], strict=False))
        clean.append(clean_stroke)
    if path_length < .045:
        raise HTTPException(422, "圈画笔迹太短，请在题目旁画线、圈题或画箭头")
    return clean


@app.post("/v1/setup/check")
def setup_check(image: UploadFile = File(...), claims: dict = Depends(principal)) -> dict:
    started = time.monotonic()
    require_role(claims, "child")
    family = family_for(claims)
    image_bytes = read_image(image)
    read_ms = round((time.monotonic() - started) * 1000)
    result = analyze(settings, image_bytes, "检查纸面", family["age_band"],
                     family["grade"], family["textbook"], "setup")
    ready = result["ready"] and result["confidence"] >= 0.65
    logger.info("setup_check image_bytes=%d read_ms=%d total_ms=%d ready=%s",
                len(image_bytes), read_ms, round((time.monotonic() - started) * 1000), ready)
    return {"ready": ready, "reason": result["label"], "confidence": result["confidence"],
            "subject": result.get("subject", ""),
            "subject_confidence": result.get("subject_confidence", 0),
            "issue_box": result.get("issue_box") if not ready else None,
            "setup_ticket": make_token(settings.app_secret, "setup", claims["sub"], 120) if ready else None}


@app.post("/v1/sessions")
def start_session(body: SessionStart, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    proof = read_token(settings.app_secret, body.setup_ticket)
    if not proof or proof["role"] != "setup" or proof["sub"] != claims["sub"]:
        raise HTTPException(403, "A fresh readable paper check is required")
    family = family_for(claims)
    fid = family["id"]
    now, sid = current(), uid()
    tasks = many("SELECT subject,status FROM tasks WHERE family_id=? AND status!='confirmed'", (fid,))
    counts = task_counts(tasks)
    proposed = suggestion(settings.database_path, family, tasks, now)
    goal = body.timer_goal_seconds
    try:
        with transaction(settings.database_path) as db:
            existing = db.execute("SELECT id FROM sessions WHERE family_id=? AND status IN ('active','paused')", (fid,)).fetchone()
            if existing:
                raise HTTPException(409, "Session already running")
            db.execute("INSERT INTO sessions(id,family_id,device_id,status,started_at,ended_at,expires_at,timer_goal_seconds,timer_goal_source,timer_task_counts) VALUES(?,?,?,?,?,?,?,?,?,?)",
                       (sid, fid, claims["sub"], "active", now, None, now + RETENTION_SECONDS,
                        goal, "none" if goal is None else "child" if goal != proposed["goal_seconds"] else proposed["source"],
                        json.dumps(counts, ensure_ascii=False)))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Session already running") from exc
    return {"id": sid, "status": "active", "started_at": now,
            "timer_goal_seconds": goal}


@app.post("/v1/sessions/{session_id}/page-prep")
def prepare_session_page(session_id: str, image: UploadFile = File(...),
                         claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active" or session["device_id"] != claims["sub"]:
        raise HTTPException(409, "Session is not active on this device")
    return page_preparation().schedule(session_id, family["id"], read_image(image))


@app.patch("/v1/sessions/{session_id}/timer")
def update_timer_goal(session_id: str, body: TimerGoal,
                      claims: dict = Depends(principal)) -> dict:
    family = family_for(claims)
    with transaction(settings.database_path) as db:
        session = db.execute("SELECT status FROM sessions WHERE id=? AND family_id=?",
                             (session_id, family["id"])).fetchone()
        if not session or session["status"] not in ("active", "paused"):
            raise HTTPException(409, "Session unavailable")
        db.execute("UPDATE sessions SET timer_goal_seconds=?,timer_goal_source=? WHERE id=?",
                   (body.goal_seconds, claims["role"], session_id))
    broadcast_sync(family["id"], {"type": "timer_changed"})
    return {"goal_seconds": body.goal_seconds, "goal_source": claims["role"]}


@app.patch("/v1/sessions/{session_id}/help-preferences")
def update_session_help_preferences(session_id: str, body: SessionHelpPreferences,
                                    claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["device_id"] != claims["sub"] or session["status"] not in ("active", "paused"):
        raise HTTPException(409, "Session unavailable")
    if body.enabled and family["proactive_help_mode"] == "off":
        raise HTTPException(409, "Parent has disabled automatic help")
    with transaction(settings.database_path) as db:
        db.execute("UPDATE sessions SET proactive_help_enabled=? WHERE id=? AND status IN ('active','paused')",
                   (int(body.enabled), session_id))
    broadcast_sync(family["id"], {"type": "help_preferences_changed"})
    return {"enabled": body.enabled, "effective": body.enabled and family["proactive_help_mode"] != "off"}


@app.post("/v1/sessions/{session_id}/timer/events")
def save_timer_events(session_id: str, body: TimerEventBatch,
                      claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    now = current()
    with transaction(settings.database_path) as db:
        session = db.execute("SELECT * FROM sessions WHERE id=? AND family_id=?",
                             (session_id, family["id"])).fetchone()
        if not session or session["device_id"] != claims["sub"]:
            raise HTTPException(404, "Session unavailable")
        last_seq = session["timer_last_seq"] or 0
        last_event = db.execute("SELECT ended_at FROM timer_events WHERE session_id=? "
                                "ORDER BY seq DESC LIMIT 1", (session_id,)).fetchone()
        last_end = last_event["ended_at"] if last_event else session["started_at"]
        existing_ms = db.execute("SELECT COALESCE(SUM(duration_ms),0) AS total "
                                 "FROM timer_events WHERE session_id=?", (session_id,)).fetchone()["total"]
        for event in body.events:
            prior = db.execute("SELECT id,kind,subject_label,duration_ms,ended_at "
                               "FROM timer_events WHERE session_id=? AND seq=?",
                               (session_id, event.seq)).fetchone()
            subject = (event.subject or "").strip()[:60] or None
            if prior:
                if (prior["id"], prior["kind"], prior["subject_label"],
                    prior["duration_ms"], prior["ended_at"]) != (
                    event.id, event.kind, subject, event.duration_ms, event.ended_at
                ):
                    raise HTTPException(409, "Timer event changed; refresh and retry")
                continue
            if event.seq != last_seq + 1:
                raise HTTPException(409, "Timer events must arrive in order")
            if event.ended_at < last_end or event.ended_at > now + 60:
                raise HTTPException(422, "Timer event timestamp is invalid")
            if event.ended_at < session["started_at"] - 10 or (
                session["ended_at"] and event.ended_at > session["ended_at"] + 2
            ):
                raise HTTPException(422, "Timer event is outside the session")
            existing_ms += event.duration_ms
            if existing_ms > (max(0, now - session["started_at"]) + 60) * 1000:
                raise HTTPException(422, "Timer exceeds session duration")
            db.execute("INSERT INTO timer_events(id,session_id,seq,kind,subject_label,"
                       "duration_ms,ended_at,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (event.id, session_id, event.seq, event.kind, subject,
                        event.duration_ms, event.ended_at, now, session["expires_at"]))
            last_seq, last_end = event.seq, event.ended_at
        db.execute("UPDATE sessions SET timer_last_seq=? WHERE id=?", (last_seq, session_id))
    saved_session = family_session(session_id, family["id"])
    return snapshot(settings.database_path, saved_session)


@app.patch("/v1/sessions/{session_id}")
def session_state(session_id: str, body: SessionState, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    session = family_session(session_id, family_for(claims)["id"])
    if session["device_id"] != claims["sub"] or session["status"] not in ("active", "paused"):
        raise HTTPException(409, "Session unavailable")
    if session["status"] == "paused" and body.status == "active":
        proof = read_token(settings.app_secret, body.setup_ticket or "")
        if not proof or proof["role"] != "setup" or proof["sub"] != claims["sub"]:
            raise HTTPException(403, "A fresh readable paper check is required")
    now = current()
    with transaction(settings.database_path) as db:
        previous = db.execute("SELECT status,expires_at FROM sessions WHERE id=?",
                              (session_id,)).fetchone()
        if not previous or previous["status"] not in ("active", "paused"):
            raise HTTPException(409, "Session unavailable")
        if previous["status"] != body.status:
            if body.status == "paused":
                db.execute("INSERT INTO session_pauses VALUES(?,?,?,?,?)",
                           (uid(), session_id, now, None, previous["expires_at"]))
            else:
                db.execute("UPDATE session_pauses SET ended_at=? WHERE session_id=? "
                           "AND ended_at IS NULL", (now, session_id))
        db.execute("UPDATE sessions SET status=?,correction_authorized_until=CASE WHEN ?='paused' THEN NULL ELSE correction_authorized_until END WHERE id=?", (body.status, body.status, session_id))
        if body.status == "paused":
            db.execute("UPDATE live_sessions SET status='ended',ended_at=? WHERE session_id=? AND status='active'", (now, session_id))
    if body.status == "paused":
        broadcast_sync(session["family_id"], {"type": "live_ended"})
    return {"id": session_id, "status": body.status}


@app.post("/v1/sessions/{session_id}/finish")
def finish_session(session_id: str, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    session = family_session(session_id, family_for(claims)["id"])
    if session["device_id"] != claims["sub"] or session["status"] not in ("active", "paused", "finished"):
        raise HTTPException(409, "Session unavailable")
    now = current()
    newly_finished = False
    with transaction(settings.database_path) as db:
        latest = db.execute("SELECT status,device_id FROM sessions WHERE id=? AND family_id=?",
                            (session_id, session["family_id"])).fetchone()
        if (not latest or latest["device_id"] != claims["sub"]
                or latest["status"] not in ("active", "paused", "finished")):
            raise HTTPException(409, "Session unavailable")
        if latest["status"] != "finished":
            db.execute("UPDATE sessions SET status='finished',ended_at=?,correction_authorized_until=NULL WHERE id=?", (now, session_id))
            db.execute("UPDATE session_pauses SET ended_at=? WHERE session_id=? AND ended_at IS NULL",
                       (now, session_id))
            db.execute("UPDATE live_sessions SET status='ended',ended_at=? WHERE session_id=? AND status='active'", (now, session_id))
            newly_finished = True
    if newly_finished:
        broadcast_sync(session["family_id"], {"type": "live_ended"})
    finished = family_session(session_id, session["family_id"])
    timer = snapshot(settings.database_path, finished)
    return {"id": session_id, "status": "finished", "ended_at": finished["ended_at"],
            "duration_seconds": timer["learning_seconds"], "timer": timer,
            "review": session_review(settings.database_path, session["family_id"], session_id, now)}


@app.post("/v1/sessions/{session_id}/correction-request")
def request_correction_access(session_id: str, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    session = family_session(session_id, family_for(claims)["id"])
    if session["status"] != "active" or session["device_id"] != claims["sub"]:
        raise HTTPException(409, "Session is not active on this device")
    access = correction_access(session, claims)
    if access["allowed"]:
        return access
    now = current()
    with transaction(settings.database_path) as db:
        db.execute("UPDATE sessions SET correction_requested_at=? WHERE id=? AND status='active'", (now, session_id))
    broadcast_sync(session["family_id"], {"type": "correction_requested"})
    return {**access, "requested_at": now}


@app.patch("/v1/sessions/{session_id}/correction-access")
def set_correction_access(session_id: str, body: CorrectionAccess,
                          claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    session = family_session(session_id, family_for(claims)["id"])
    if session["status"] != "active":
        raise HTTPException(409, "Session is not active")
    until = current() + 600 if body.allowed else None
    with transaction(settings.database_path) as db:
        db.execute("UPDATE sessions SET correction_authorized_until=?,correction_requested_at=NULL WHERE id=? AND status='active'", (until, session_id))
    broadcast_sync(session["family_id"], {"type": "correction_access_changed"})
    return {"allowed": body.allowed, "authorized_until": until}


@app.post("/v1/sessions/{session_id}/help-candidates")
def help_candidate(session_id: str, image: UploadFile = File(...),
                   reference_image: UploadFile = File(...), activity_box: str = Form(...),
                   claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["device_id"] != claims["sub"] or session["status"] != "active":
        raise HTTPException(409, "Session is not active on this device")
    if family["proactive_help_mode"] == "off" or not session["proactive_help_enabled"]:
        raise HTTPException(409, "Automatic help is disabled")
    try:
        box = normalized_box(json.loads(activity_box))
    except (ValueError, TypeError):
        box = None
    if not box:
        raise HTTPException(422, "Valid activity box required")
    result = detect_help_candidate(settings, read_image(image), read_image(reference_image),
                                   box, family["age_band"], family["grade"])
    if not result["found"]:
        return {"found": False}
    now, candidate_id = current(), uid()
    with transaction(settings.database_path) as db:
        latest = db.execute(
            "SELECT s.status,s.proactive_help_enabled,f.proactive_help_mode FROM sessions s "
            "JOIN families f ON f.id=s.family_id WHERE s.id=?", (session_id,)
        ).fetchone()
        if (not latest or latest["status"] != "active" or not latest["proactive_help_enabled"]
                or latest["proactive_help_mode"] == "off"):
            return {"found": False}
        db.execute("INSERT INTO help_candidates(id,session_id,label,evidence,box_json,confidence,effort,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                   (candidate_id, session_id, result["label"], result["evidence"],
                    json.dumps(result["box"]), result["confidence"], result["effort"],
                    now, now + 180))
    return {"found": True, "id": candidate_id, **result}


@app.post("/v1/sessions/{session_id}/idle-checkin")
def idle_checkin(session_id: str, image: UploadFile = File(...),
                 reference_image: UploadFile = File(...),
                 claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["device_id"] != claims["sub"] or session["status"] != "active":
        raise HTTPException(409, "Session is not active on this device")
    if family["proactive_help_mode"] == "off" or not session["proactive_help_enabled"]:
        raise HTTPException(409, "Automatic help is disabled")
    current_image = read_image(image)
    result = assess_idle_checkin(settings, current_image, read_image(reference_image))
    latest = one("SELECT s.status,s.proactive_help_enabled,f.proactive_help_mode FROM sessions s "
                 "JOIN families f ON f.id=s.family_id WHERE s.id=?", (session_id,))
    if (not latest or latest["status"] != "active" or not latest["proactive_help_enabled"]
            or latest["proactive_help_mode"] == "off"):
        return {"offer": False, "progress": False, "page_changed": False}
    if result["offer"]:
        try:
            prepared = page_preparation().match(session_id, current_image, "", spoken=True)
            if (prepared and prepared.get("progress") in {"blank", "partial"} and
                    prepared["read_confidence"] >= .85):
                located = {"found": True, "box": prepared["box"],
                           "label": prepared["label"], "evidence": prepared["evidence"],
                           "confidence": prepared["read_confidence"]}
            else:
                locator_name = preferences(settings.database_path, family["id"])["locator_model"]
                located = locate_question(
                    replace(settings, ai_model=locator_name), current_image,
                    "请从整页作答进度确定当前唯一、未完成且完整可读的小题",
                    learning_context(session_id, None), True,
                )
        except (HTTPException, OSError, ValueError):
            located = {"found": False}
        if located.get("found") and located.get("confidence", 0) >= .85:
            candidate_id, now = uid(), current()
            with transaction(settings.database_path) as db:
                active = db.execute(
                    "SELECT s.status,s.proactive_help_enabled,f.proactive_help_mode "
                    "FROM sessions s JOIN families f ON f.id=s.family_id WHERE s.id=?",
                    (session_id,),
                ).fetchone()
                if (not active or active["status"] != "active" or
                        not active["proactive_help_enabled"] or
                        active["proactive_help_mode"] == "off"):
                    return {"offer": False, "progress": False, "page_changed": False}
                db.execute("INSERT INTO help_candidates(id,session_id,label,evidence,box_json,confidence,effort,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?)",
                           (candidate_id, session_id, located["label"], located["evidence"],
                            json.dumps(located["box"]), located["confidence"], "extended",
                            now, now + 180))
            result.update(candidate_id=candidate_id, target_box=located["box"],
                          target_label=located["label"])
    return result


@app.post("/v1/sessions/{session_id}/observations")
def observe(session_id: str, image: UploadFile = File(...), previous_image: UploadFile | None = File(None),
            task_id: str | None = Form(None), kind: str = Form("paper_change"),
            claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active":
        raise HTTPException(409, "Session is not active")
    if task_id:
        family_task(task_id, family["id"])
    result = analyze(settings, read_image(image), "比较前后可见的题目进度、书写与纸面步骤；若只有当前图，只描述当前可见内容。所有结论是待复核观察，不可宣称任务完成。",
                     family["age_band"], family["grade"], family["textbook"], "observation",
                     read_image(previous_image) if previous_image else None)
    oid, now = uid(), current()
    with transaction(settings.database_path) as db:
        active = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not active or active["status"] != "active":
            raise HTTPException(409, "Session ended during analysis")
        db.execute("INSERT INTO observations(id,session_id,task_id,kind,label,evidence,confidence,progress,writing,visible_steps,review_status,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (oid, session_id, task_id, kind[:40], result["label"], result["evidence"],
                    result["confidence"], result.get("progress", ""), result.get("writing", ""),
                    result.get("visible_steps", ""), "unreviewed", now, now + RETENTION_SECONDS))
    return {"id": oid, **result}


@app.patch("/v1/observations/{observation_id}")
def review_observation(observation_id: str, body: ObservationUpdate,
                       claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    fid = family_for(claims)["id"]
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE observations SET review_status=? WHERE id=? AND session_id IN (SELECT id FROM sessions WHERE family_id=?)",
                            (body.review_status, observation_id, fid))
        if result.rowcount != 1:
            raise HTTPException(404, "Observation not found")
    return {"id": observation_id, "review_status": body.review_status}


@app.post("/v1/sessions/{session_id}/pages/scan")
def scan_homework_pages(session_id: str, image: UploadFile = File(...),
                        claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    session = family_session(session_id, family_for(claims)["id"])
    if session["status"] != "active" or session["device_id"] != claims["sub"]:
        raise HTTPException(409, "Session is not active on this device")
    return scan_pages(settings, read_image(image))


@app.post("/v1/sessions/{session_id}/corrections")
def correct_page(session_id: str, image: UploadFile = File(...), task_id: str | None = Form(None),
                 subject_label: str | None = Form(None, max_length=60),
                 page_label: str | None = Form(None, max_length=60),
                 selected_box: str | None = Form(None, max_length=80),
                 selected_strokes: str | None = Form(None, max_length=30000),
                 batch_id: str | None = Form(None, max_length=64),
                 page_index: int | None = Form(None, ge=1, le=6),
                 batch_total: int | None = Form(None, ge=1, le=6),
                 claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active" or session["device_id"] != claims["sub"]:
        raise HTTPException(409, "Session is not active on this device")
    if not correction_authorized(session):
        raise HTTPException(403, "Parent approval is required for page correction")
    if task_id:
        family_task(task_id, family["id"])
    if batch_id and (not page_index or not batch_total or page_index > batch_total or not page_label):
        raise HTTPException(422, "Batch pages need page number and label")
    image_data = read_image(image)
    teaching_context = learning_context(session_id, task_id, "correction")
    question_reader = getattr(page_preparation(), "questions_for", None)
    try:
        prepared_items = question_reader(session_id, image_data) if question_reader else []
    except (OSError, ValueError):
        prepared_items = []
    target_box = None
    if selected_strokes is not None:
        marks = parse_selected_strokes(selected_strokes)
        mark_matcher = getattr(page_preparation(), "match_strokes", None)
        try:
            prepared = mark_matcher(session_id, image_data, marks) if mark_matcher else None
        except (OSError, ValueError):
            prepared = None
        location = ({"found": True, "box": prepared["box"]} if prepared else
                    locate_question(settings, image_data, "孩子手指笔迹指向的题目",
                                    teaching_context, False, marks))
        if not location["found"]:
            raise HTTPException(422, "还不能确定圈画指向哪道题，请只标记一道题，或说出题号后重试")
        target_box = location["box"]
    elif selected_box is not None:
        try:
            target_box = normalized_box(json.loads(selected_box))
        except (ValueError, TypeError):
            pass
        if not target_box:
            raise HTTPException(422, "Selected question box is invalid")
    if target_box:
        focus_image, focus_region = focus_question_image(image_data, target_box)
        regions = [("圈画题目", tuple(focus_region), focus_image)]
    else:
        regions = correction_regions(image_data)
    grader = grading_settings(family["id"])

    def grade_region(region: tuple[str, tuple[float, float, float, float], bytes]) -> dict:
        name, region_box, detail_image = region
        references = []
        for item in prepared_items:
            answer = item.get("solution") or {}
            box = item["box"]
            center_x, center_y = box[0] + box[2] / 2, box[1] + box[3] / 2
            if (item["verification_status"] in {"verified", "rechecked"} and
                    item["read_confidence"] >= .85 and
                    answer.get("confidence", 0) >= .85 and not answer.get("ambiguous") and
                    region_box[0] <= center_x <= region_box[0] + region_box[2] and
                    region_box[1] <= center_y <= region_box[1] + region_box[3]):
                references.append({"label": item["label"], "evidence": item["evidence"],
                                   "answer": answer.get("answer", ""),
                                   "steps": answer.get("steps", "")})
        local_target = (
            [(target_box[0] - region_box[0]) / region_box[2],
             (target_box[1] - region_box[1]) / region_box[3],
             target_box[2] / region_box[2], target_box[3] / region_box[3]]
            if target_box else None
        )
        scope = (f"只批改{'笔迹指向' if selected_strokes is not None else '框选'}的这一题，"
                 f"完整题目在当前局部图中的相对坐标为{local_target}；"
                 "邻近题目只作为理解题干的上下文，不生成其批改结论。"
                 if target_box else "检查这张局部 JPEG 里完整可见的书面题，包括确认留白的未作答题。")
        prompt = (f"这是原作业页的{name}。{scope}"
                  "不要引用裁剪外的内容。先核对题目要求（包括手写题干和手绘原题图）、"
                  "条件、图表和全部可见选项；"
                  "证据中写明题目要求。若题干或答案看不清、选项与学科知识冲突、"
                  "或答案与题目要求不符，不能标为正确。"
                  "逐题给出孩子作答或未作答状态、正确解法和与书写步骤的对照；开放题给参考思路。"
                  "坐标相对这张局部图，不要只给笼统提示。")
        if references:
            prompt += (" 以下是已独立核对的印刷题参考解法，只用于核对题意和正确结果；"
                       "孩子当前写了什么、是否留白必须重新看本次照片，不能从参考解法推断。"
                       + json.dumps(references[:12], ensure_ascii=False))
        return analyze(grader, detail_image, prompt,
                       family["age_band"], family["grade"], family["textbook"], "correction",
                       context_image=image_data if target_box or len(regions) > 1 else None,
                       session_context=teaching_context)

    with ThreadPoolExecutor(max_workers=len(regions)) as executor:
        futures = [executor.submit(grade_region, region) for region in regions]
        result = merge_correction_regions(
            (region[1], future.result()) for region, future in zip(regions, futures, strict=True)
        )
    if target_box:
        x, y, width, height = target_box
        margin = .04
        checks_in_box = [check for check in result.get("checks", [])
                         if check.get("box") and
                         x - margin <= check["box"][0] + check["box"][2] / 2 <= x + width + margin and
                         y - margin <= check["box"][1] + check["box"][3] / 2 <= y + height + margin]
        if (not checks_in_box and len(result.get("checks", [])) == 1
                and not result["checks"][0].get("box")):
            checks_in_box = result["checks"]
        result["checks"] = checks_in_box
        result["visible_answer_count"] = len(checks_in_box)
    if not subject_label:
        subject_label = result.get("subject") or None
    if (not page_label or page_label.endswith("张")) and result.get("printed_page"):
        page_label = result["printed_page"]
    checks = result.get("checks", [])
    if checks:
        correct_count = sum(item["status"] == "looks_correct" for item in checks)
        recheck_count = sum(item["status"] == "check_again" for item in checks)
        blank_count = sum(item.get("answer_state") == "blank" and item["status"] == "check_again"
                          for item in checks)
        blank_detail = f"（其中 {blank_count} 项未作答，按错题计）" if blank_count else ""
        discussion_count = sum(item["status"] == "needs_discussion" for item in checks)
        unclear_count = sum(item["status"] == "unclear" for item in checks)
        summary = (f"本次已检查 {len(checks)} 项：{correct_count} 项看起来正确，"
                   f"{recheck_count} 项需要订正{blank_detail}，"
                   f"{discussion_count} 项适合讨论，"
                   f"{unclear_count} 项暂无法判断。")
        remaining = max(0, result.get("visible_answer_count", len(checks)) - len(checks))
        if remaining:
            summary += f"模型估计另有 {remaining} 项书面题尚未覆盖；请分区重拍补查。"
        if recheck_count:
            summary += ("请先查看叉号位置的订正分析。"
                        if any(item["status"] == "check_again" and item.get("box")
                               for item in checks)
                        else "请查看下方逐题文字分析。")
        elif discussion_count:
            summary += "开放表达请查看讨论建议，不以对错结论代替家长复核。"
        elif unclear_count:
            summary += "请拍清问号位置后再试。"
        else:
            summary += "可任选一题，对照题目要求再自查。"
    else:
        summary = ("本页没有可可靠批改的书面题。若是朗读等口头任务，请由家长确认；"
                   "请拍清题干及作答区域后重试。")
    cid, now = uid(), current()
    with transaction(settings.database_path) as db:
        active = db.execute("SELECT status,device_id,correction_authorized_until FROM sessions WHERE id=? AND family_id=?", (session_id, family["id"])).fetchone()
        if not active or active["device_id"] != claims["sub"] or not correction_authorized(active, now):
            raise HTTPException(409, "Correction approval expired or session ended during analysis")
        db.execute("INSERT INTO corrections(id,session_id,task_id,summary,checks_json,confidence,review_status,image_jpeg,subject_label,page_label,batch_id,page_index,batch_total,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (cid, session_id, task_id, summary, json.dumps(checks, ensure_ascii=False),
                    result["confidence"], "unreviewed", image_data, subject_label, page_label,
                    batch_id, page_index, batch_total, now, now + RETENTION_SECONDS))
    return {"id": cid, "session_id": session_id, "task_id": task_id, "summary": summary,
            "checks": checks, "confidence": result["confidence"], "review_status": "unreviewed",
            "image_available": True, "subject_label": subject_label, "page_label": page_label,
            "batch_id": batch_id, "page_index": page_index, "batch_total": batch_total,
            "created_at": now}


@app.get("/v1/corrections/{correction_id}/image")
def correction_image(correction_id: str, claims: dict = Depends(principal)) -> Response:
    fid = family_for(claims)["id"]
    row = one("SELECT c.image_jpeg,s.status,s.correction_authorized_until FROM corrections c JOIN sessions s ON s.id=c.session_id WHERE c.id=? AND s.family_id=? AND c.expires_at>?", (correction_id, fid, current()))
    if not row or row["image_jpeg"] is None:
        raise HTTPException(404, "Correction image not found")
    if claims["role"] == "child" and row["status"] != "finished" and not correction_authorized(row):
        raise HTTPException(403, "Parent approval is required")
    return Response(content=row["image_jpeg"], media_type="image/jpeg",
                    headers={"Cache-Control": "private, no-store"})


def check_review_snapshot(check: dict) -> dict:
    return {field: check.get(field) for field in (
        "status", "answer", "answer_state", "evidence", "reason", "explanation",
        "next_step", "confidence", "steps", "box", "reference_answer",
        "solution_idea", "solution_steps", "comparison", "diagram",
    )}


def updated_correction_summary(previous: str, checks: list[dict]) -> str:
    original = previous
    for marker in ("\n复检后当前结果：", "\n家长校对后当前结果："):
        original = original.split(marker, 1)[0]
    counts = {status: sum(item["status"] == status for item in checks) for status in (
        "looks_correct", "check_again", "needs_discussion", "unclear",
    )}
    blank_count = sum(item.get("answer_state") == "blank" and item["status"] == "check_again"
                      for item in checks)
    blank_detail = f"（其中 {blank_count} 项未作答，按错题计）" if blank_count else ""
    label = "家长校对后当前结果" if any(item.get("parent_review") for item in checks) else "复检后当前结果"
    return (f"{original}\n{label}：{counts['looks_correct']} 项看起来正确，"
            f"{counts['check_again']} 项需要订正{blank_detail}，"
            f"{counts['needs_discussion']} 项适合讨论，"
            f"{counts['unclear']} 项暂无法判断。原判可在单题对照中查看。")


@app.post("/v1/corrections/{correction_id}/checks/{check_index}/recheck")
def recheck_correction_item(correction_id: str, check_index: int,
                            claims: dict = Depends(principal)) -> dict:
    family = family_for(claims)
    row = one("SELECT c.checks_json,c.image_jpeg,s.status,s.device_id,"
              "s.correction_authorized_until FROM corrections c JOIN sessions s "
              "ON s.id=c.session_id WHERE c.id=? AND s.family_id=? AND c.expires_at>?",
              (correction_id, family["id"], current()))
    if not row or row["image_jpeg"] is None:
        raise HTTPException(404, "Correction not found")
    if claims["role"] == "child" and (
        row["device_id"] != claims["sub"] or not correction_authorized(row)
    ):
        raise HTTPException(403, "Parent approval is required for recheck")
    checks = json.loads(row["checks_json"])
    if not 0 <= check_index < len(checks):
        raise HTTPException(404, "Question not found")
    original = checks[check_index]
    if original.get("recheck_count", 0) >= 6:
        raise HTTPException(429, "This question has reached the recheck limit")
    focus_image, focus_box = focus_question_image(row["image_jpeg"], original.get("box"))
    result = recheck_single(grading_settings(family["id"]), row["image_jpeg"], focus_image,
                            original.get("question") or f"第 {check_index + 1} 项",
                            family["age_band"], family["grade"], family["textbook"])
    now = current()
    with transaction(settings.database_path) as db:
        saved = db.execute("SELECT c.checks_json,c.summary,s.status,s.device_id,"
                           "s.correction_authorized_until FROM corrections c JOIN sessions s "
                           "ON s.id=c.session_id WHERE c.id=? AND s.family_id=? AND c.expires_at>?",
                           (correction_id, family["id"], now)).fetchone()
        if not saved:
            raise HTTPException(404, "Correction expired during recheck")
        if claims["role"] == "child" and (
            saved["device_id"] != claims["sub"] or not correction_authorized(saved, now)
        ):
            raise HTTPException(409, "Parent approval expired during recheck")
        checks = json.loads(saved["checks_json"])
        if not 0 <= check_index < len(checks):
            raise HTTPException(404, "Question not found")
        count = checks[check_index].get("recheck_count", 0)
        if count >= 6:
            raise HTTPException(429, "This question has reached the recheck limit")
        check = checks[check_index]
        previous_status = check["status"]
        original_review = check.get("original_review") or check_review_snapshot(check)
        recheck = {
            **result,
            "source_status": original_review["status"],
            "disagrees": result["status"] != original_review["status"],
            "focus_box": focus_box,
            "requested_by": claims["role"],
            "created_at": now,
            "attempt": count + 1,
        }
        check["original_review"] = original_review
        check["recheck"] = recheck
        check["recheck_count"] = count + 1
        if check.get("parent_review"):
            check["ai_review"] = {**result, "box": check.get("ai_review", {}).get("box", check.get("box"))}
            review_status = None
        else:
            check.update({field: result[field] for field in (
                "status", "answer", "evidence", "reason", "explanation", "confidence",
            )})
            check["answer_state"] = result.get("answer_state") or (
                "written" if result.get("answer") else "uncertain")
            check.update(reference_answer=result.get("reference_answer", ""),
                         solution_idea=result.get("solution_idea", ""),
                         solution_steps=result.get("solution_steps", []),
                         comparison=result.get("comparison", ""),
                         diagram=result.get("diagram"))
            check["next_step"] = ""
            check["steps"] = []
            review_status = "unreviewed" if result["status"] != previous_status else None
        summary = updated_correction_summary(saved["summary"], checks)
        db.execute("UPDATE corrections SET checks_json=?,summary=?,"
                   "review_status=COALESCE(?,review_status) WHERE id=?",
                   (json.dumps(checks, ensure_ascii=False), summary,
                    review_status, correction_id))
    broadcast_sync(family["id"], {"type": "correction_rechecked", "correction_id": correction_id})
    return {"correction_id": correction_id, "check_index": check_index,
            "recheck": recheck, "check": check}


@app.patch("/v1/corrections/{correction_id}/checks/{check_index}/parent-review")
def parent_review_correction_item(correction_id: str, check_index: int,
                                  body: ParentCheckUpdate,
                                  claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    family = family_for(claims)
    note, answer = body.note.strip(), body.answer.strip()
    box = normalized_box(body.box) if body.box is not None else None
    if body.box is not None and box is None:
        raise HTTPException(422, "Invalid question location")
    now = current()
    with transaction(settings.database_path) as db:
        saved = db.execute(
            "SELECT c.checks_json,c.summary,c.review_status FROM corrections c "
            "JOIN sessions s ON s.id=c.session_id "
            "WHERE c.id=? AND s.family_id=? AND c.expires_at>?",
            (correction_id, family["id"], now),
        ).fetchone()
        if not saved:
            raise HTTPException(404, "Correction not found")
        checks = json.loads(saved["checks_json"])
        if not 0 <= check_index < len(checks):
            raise HTTPException(404, "Question not found")
        check = checks[check_index]
        if body.revision != check.get("parent_revision", 0):
            raise HTTPException(409, "This question changed; reload before saving")
        changed = body.status != check["status"] or answer != check.get("answer", "")
        if changed and len(note) < 2:
            raise HTTPException(422, "Explain the changed judgment or answer")
        previous = {field: check.get(field) for field in ("status", "answer", "box")}
        check.setdefault("original_review", check_review_snapshot(check))
        check.setdefault("ai_review", check_review_snapshot(check))
        revision = body.revision + 1
        parent_review = {"status": body.status, "answer": answer, "note": note,
                         "box": box, "revision": revision, "updated_at": now}
        check["parent_review"] = parent_review
        check["parent_revision"] = revision
        check.setdefault("parent_review_history", []).append({
            "previous": previous, "updated": parent_review,
        })
        check["status"], check["answer"], check["box"] = body.status, answer, box
        if changed:
            check["answer_state"] = ("written" if answer else "blank"
                                     if body.status == "check_again" else "uncertain")
        if changed:
            check["reason"] = note
            check["evidence"] = ""
            check["explanation"] = ""
            check["next_step"] = ""
            check["steps"] = []
            check.update(reference_answer="", solution_idea="", solution_steps=[],
                         comparison="", diagram=None)
        summary = updated_correction_summary(saved["summary"], checks)
        location_changed = previous["box"] != box
        review_status = "unreviewed" if changed or location_changed else saved["review_status"]
        db.execute("UPDATE corrections SET checks_json=?,summary=?,review_status=? WHERE id=?",
                   (json.dumps(checks, ensure_ascii=False), summary, review_status, correction_id))
    broadcast_sync(family["id"], {"type": "correction_parent_reviewed",
                                "correction_id": correction_id})
    return {"correction_id": correction_id, "check_index": check_index,
            "check": check, "review_status": review_status}


@app.patch("/v1/corrections/{correction_id}")
def review_correction(correction_id: str, body: CorrectionUpdate,
                      claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    fid = family_for(claims)["id"]
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE corrections SET review_status=? WHERE id=? AND session_id IN (SELECT id FROM sessions WHERE family_id=?)",
                            (body.review_status, correction_id, fid))
        if result.rowcount != 1:
            raise HTTPException(404, "Correction not found")
    return {"id": correction_id, "review_status": body.review_status}


def _hint_event(name: str, data: dict) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _teaching_model(config: dict, question: str, student_thought: str = "",
                    level: int | None = None) -> str:
    """Use the family's reasoning role for explanations that must preserve a derivation."""
    role = ("solver_model" if teaching_goal(question, student_thought, level)
            in {"worked_example", "explain_method"} else "tutor_model")
    return config[role]


def _hint_teaching_scene(box: object, label: object, beats: object) -> dict | None:
    """Keep bounded teaching output for board replay, never a camera frame or URL."""
    if not isinstance(beats, list) or not beats:
        return None
    fields = {"speech", "page", "scratch", "teaching_move", "reply_mode", "needs_parent",
              "step_end", "step_id", "step_title", "kind", "box", "text", "role",
              "anchor_id", "anchor_text", "purpose", "label", "from_anchor_id", "to_anchor_id",
              "x", "y", "w", "h", "r", "x1", "y1", "x2", "y2"}

    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items() if key in fields}
        if isinstance(value, list):
            return [clean(item) for item in value[:32]]
        if isinstance(value, str):
            return "" if re.search(r"(?:data:|blob:|https?://|file:)", value, re.I) else value[:500]
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)) and math.isfinite(value):
            return value
        return None

    saved_beats = [clean(beat) for beat in beats[:MAX_TEACHING_BEATS] if isinstance(beat, dict)]
    saved_beats = [beat for beat in saved_beats
                   if beat.get("speech") or beat.get("page") or beat.get("scratch")]
    if not saved_beats:
        return None
    return {"box": normalized_box(box), "label": clean(label)[:160] if isinstance(label, str) else "",
            "beats": saved_beats}


def _read_hint_teaching_scene(payload: str | None) -> dict | None:
    if not payload:
        return None
    try:
        value = json.loads(payload)
        return (_hint_teaching_scene(value.get("box"), value.get("label"), value.get("beats"))
                if isinstance(value, dict) else None)
    except (ValueError, TypeError, RecursionError):
        return None


def _hint_scene_json(result: dict, image_bytes: bytes | None = None,
                     located: dict | None = None) -> str | None:
    scene = _hint_teaching_scene(result.get("target_box"), result.get("target_label"),
                                 result.get("visual_beats"))
    # Keep only a digest and bounded original target evidence, never the photograph.
    # Dashboard replay deliberately strips these private continuation fields.
    confidence = (located or {}).get("read_confidence", (located or {}).get("confidence", 0))
    evidence = (located or {}).get("evidence")
    if (scene and image_bytes is not None and located and located.get("evidence")
            and isinstance(evidence, str) and not re.search(r"(?:data:|blob:|https?://|file:)", evidence, re.I)
            and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
            and math.isfinite(confidence) and .75 <= confidence <= 1):
        scene.update(source_image_sha256=hashlib.sha256(image_bytes).hexdigest(),
                     target_evidence=str(located["evidence"])[:1000],
                     source_read_confidence=confidence)
        if isinstance(located.get("id"), str):
            scene["source_question_id"] = located["id"][:64]
    return json.dumps(scene, ensure_ascii=False, allow_nan=False) if scene else None


def _hint_tier_contract(result: dict, level: int, located: dict | None = None) -> dict:
    """Help tiers are separate from the logical steps inside one explanation."""
    if result.get("needs_parent") or result.get("teaching_move") == "parent_handoff":
        status = "needs_parent"
    elif result.get("teaching_move") == "clarify_target":
        status = "needs_clarification"
    elif result.get("delivery_complete") is False:
        status = "interrupted"
    else:
        status = "ready"
    confidence = (located or {}).get("read_confidence", (located or {}).get("confidence", 0))
    evidence = (located or {}).get("evidence")
    available = bool(result.get("id") and result.get("visual_beats") and located
                     and isinstance(evidence, str) and evidence.strip()
                     and not re.search(r"(?:data:|blob:|https?://|file:)", evidence, re.I)
                     and result.get("target_box")
                     and isinstance(confidence, (int, float)) and not isinstance(confidence, bool)
                     and math.isfinite(confidence) and .75 <= confidence <= 1)
    return {**result, "level": level, "teaching_tier": level,
            "explanation_status": status,
            "continuation_available": available and level == 1 and status == "ready",
            "continuation_hint_id": result.get("id") if available else None}


def _continued_teaching_target(session_id: str, task_id: str | None,
                               previous_hint_id: str, image_bytes: bytes) -> dict:
    """Bind a second explanation to this child's original target and frozen JPEG."""
    source = one("SELECT * FROM hints WHERE id=? AND session_id=? AND expires_at>?",
                 (previous_hint_id, session_id, current()))
    if not source:
        raise HTTPException(404, "原讲解已失效，请重新选择这道题")
    if (source.get("task_id") or None) != (task_id or None):
        raise HTTPException(409, "作业任务已变化，请重新选择这道题")
    try:
        saved = json.loads(source.get("teaching_json") or "null")
    except (ValueError, TypeError):
        saved = None
    if not isinstance(saved, dict):
        raise HTTPException(409, "旧讲解没有可核对的原题，请重新选择这道题")
    digest = saved.get("source_image_sha256")
    evidence = saved.get("target_evidence")
    confidence = saved.get("source_read_confidence", 0)
    box = saved.get("box")
    valid_box = (isinstance(box, list) and len(box) == 4
                 and all(isinstance(n, (int, float)) and not isinstance(n, bool)
                         and math.isfinite(n) and 0 <= n <= 1 for n in box))
    if valid_box:
        valid_box = (box[2] > 0 and box[3] > 0 and box[0] + box[2] <= 1 + 1e-9
                     and box[1] + box[3] <= 1 + 1e-9)
    scene = _read_hint_teaching_scene(source.get("teaching_json"))
    if (not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or not isinstance(evidence, str) or not evidence.strip() or not scene or not valid_box
            or not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
            or not math.isfinite(confidence) or not .75 <= confidence <= 1):
        raise HTTPException(409, "旧讲解没有可核对的原题，请重新选择这道题")
    if not hmac.compare_digest(digest, hashlib.sha256(image_bytes).hexdigest()):
        raise HTTPException(409, "续讲需要同一张原题截图，请重新选择这道题")
    target = {"found": True, "box": scene["box"], "label": scene["label"],
              "evidence": evidence[:1000], "confidence": confidence,
              "read_confidence": confidence, "solution": None,
              "verification_status": "pending", "_continued_hint": True,
              "_previous_response": source["response"][:500]}
    identifier = saved.get("source_question_id")
    if isinstance(identifier, str) and identifier:
        target["id"] = identifier[:64]
    return target


def _save_hint_dialogue(db, hid: str, now: int, session_id: str, task_id: str | None,
                       question: str, response: str, level: int, thought: str = "",
                       source: str = "ai", teaching_result: dict | None = None, *,
                       image_bytes: bytes | None = None, located: dict | None = None) -> None:
    db.execute("INSERT INTO hints(id,session_id,task_id,question,response,source,level,created_at,expires_at,teaching_json) "
               "VALUES(?,?,?,?,?,?,?,?,?,?)",
               (hid, session_id, task_id, question[:500], response, source, level,
                 now, now + RETENTION_SECONDS,
                 _hint_scene_json(teaching_result or {}, image_bytes, located)))
    if thought.strip():
        db.execute("INSERT INTO reflections VALUES(?,?,?,?,?,?,?)",
                   (uid(), session_id, task_id, thought.strip()[:500],
                    "hint_dialogue_voice_or_text", now, now + RETENTION_SECONDS))


def _merge_teaching_anchors(existing: list[dict], added: list[dict], located: dict) -> list[dict]:
    """Keep old word identities while giving new photo geometry unique references."""
    merged, identifiers, seen = [], set(), set()
    for anchor in [*existing, *added]:
        kind = anchor.get("kind", "text")
        if kind == "segment":
            ends = sorted(((anchor["x1"], anchor["y1"]), (anchor["x2"], anchor["y2"])))
            key = (kind, *ends)
        else:
            key = (kind, tuple(anchor["box"]), anchor["text"] if kind == "text" else "")
        if key in seen:
            continue
        seen.add(key)
        identifier = anchor["id"]
        if identifier in identifiers:
            suffix = 1
            while f"extra{suffix}" in identifiers:
                suffix += 1
            identifier = f"extra{suffix}"
        identifiers.add(identifier)
        merged.append({**anchor, "id": identifier})
    return normalized_text_anchors(merged, located["box"], located["evidence"])


def _teaching_anchors(image_bytes: bytes, located: dict, state: dict, family_id: str,
                      question: str = "") -> dict:
    """Only this request owns these coordinates, including a streaming fallback."""
    key = hashlib.sha256(image_bytes + json.dumps(
        [located["box"], located["evidence"]], ensure_ascii=False).encode()).hexdigest()

    def verified(anchors: list[dict]) -> list[dict]:
        try:
            accepted = verify_teaching_anchors(image_bytes, anchors, located["box"], located["evidence"])
            teaching_stage("anchor_pixels", normalized=len(anchors), accepted=len(accepted))
            return accepted
        except (OSError, ValueError, TypeError) as exc:
            teaching_stage("anchor_pixels_failed", error=type(exc).__name__)
            # Unavailable pixel evidence must never license geometry.
            return [anchor for anchor in anchors if anchor.get("kind", "text") == "text"]

    existing = (normalized_text_anchors(located.get("anchors"), located["box"], located["evidence"])
                if located.get("_teaching_anchor_image_key", key) == key else [])
    if state.get("local_image_key") != key:
        state["local_anchors"] = local_teaching_text_anchors(
            state.get("local_text_payload"), image_bytes, located["box"], located["evidence"])
        state["local_image_key"] = key
    existing = _merge_teaching_anchors(existing, state.get("local_anchors", []), located)
    original_ids = {anchor["id"] for anchor in existing}
    existing = verified(existing)
    lost_anchors = original_ids - {anchor["id"] for anchor in existing}
    solution = located.get("solution") if isinstance(located.get("solution"), dict) else {}
    diagrams = [located.get("diagram"), solution.get("diagram")]
    region_needed = teaching_region_needed(located["evidence"], question)
    geometry_needed = teaching_geometry_needed(located["evidence"], question) or (not region_needed and any(
        isinstance(diagram, dict) and isinstance(diagram.get("elements"), list)
        and any(isinstance(item, dict) and item.get("kind") in {"line", "arrow", "rect", "circle"}
                for item in diagram["elements"])
        for diagram in diagrams
    ))
    has_geometry = (any(anchor.get("kind") == "segment" for anchor in existing)
                    or sum(anchor.get("kind") == "point" for anchor in existing) >= 2)
    has_region = any(anchor.get("kind") == "region" for anchor in existing)
    if (existing and not lost_anchors and (not geometry_needed or has_geometry)
            and (not region_needed or has_region)):
        teaching_stage("anchors_reused", accepted=len(existing))
        return {**located, "anchors": existing, "_teaching_anchor_image_key": key}
    if "anchors" in state:
        return {**located, "anchors": verified(_merge_teaching_anchors(existing, state["anchors"], located))
                if state.get("image_key") == key else existing, "_teaching_anchor_image_key": key}
    # Spend the one optional read before cropping or calling, including failures.
    state.update(image_key=key, anchors=existing)
    try:
        crop, region = tight_teaching_anchor_image(image_bytes, located["box"])
        locator = replace(settings, ai_model=preferences(
            settings.database_path, family_id)["locator_model"])
        options = {"geometry_needed": True} if geometry_needed else {}
        if region_needed:
            options["region_needed"] = True
        added = read_teaching_anchors(locator, crop, region, located["box"], located["evidence"], **options)
        added = [anchor for item in added[:12]
                 for anchor in normalized_text_anchors([item], located["box"], located["evidence"])]
        state["anchors"] = verified(_merge_teaching_anchors(existing, added, located))
    except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError) as exc:
        teaching_stage("anchor_refresh_failed", error=type(exc).__name__)
    teaching_stage("anchors_ready", accepted=len(state["anchors"]),
                   text=sum(item.get("kind", "text") == "text" for item in state["anchors"]),
                   regions=sum(item.get("kind") == "region" for item in state["anchors"]))
    return {**located, "anchors": state["anchors"], "_teaching_anchor_image_key": key}


def _answer_feedback(family: dict, image_bytes: bytes, located: dict,
                      thought: str) -> dict | None:
    """Confirm a spoken answer only against a reliably located, checked solution."""
    if not thought.strip() or not located or located.get("read_confidence", 0) < .85:
        return None
    status = located.get("verification_status")
    if status == "uncertain" or located.get("_solution_inflight"):
        return None
    if located.get("_solution_attempted") and not grounded_teaching_solution(
            located.get("solution"), located.get("read_confidence", 0)):
        return None
    solution = located.get("solution") if status in {"verified", "rechecked"} else None
    config = preferences(settings.database_path, family["id"])
    try:
        if not solution:
            located["_solution_attempted"] = True
            solver = replace(settings, ai_model=config["solver_model"])
            solution = solve_question(solver, image_bytes, located)
            # Reuse this request's visual solve when the utterance is not a complete answer.
            located["solution"] = solution
            if (solution["ambiguous"] or solution["confidence"] < .85 or
                    not solution["answer"]):
                return None
        assessor = replace(settings, ai_model=config["locator_model"])
        result = assess_spoken_answer(assessor, located, solution, thought, True)
        # Older integrations supplied the previous affirmative-only result.
        return ({"verdict": "correct", "response": result, "summary": result}
                if isinstance(result, str) else result)
    except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
        return None


def _refresh_teaching_solution(session_id: str, family: dict, image: bytes,
                               target: dict, question: str,
                               student_thought: str = "", *, wait_for_pending: bool = True,
                               level: int | None = None) -> dict:
    """Consume the current shared solve, not its initial pending snapshot."""
    reader = getattr(page_preparation(), "teaching_question", None)
    if not target.get("id") or not callable(reader):
        return target
    complete = teaching_goal(question, student_thought, level) in {"worked_example", "explain_method"}
    wait = (min(45, settings.ai_timeout_seconds) if (
        wait_for_pending and target.get("verification_status") == "pending"
        and (complete or student_thought.strip())) else 0)
    updated = reader(session_id, family["id"], image, target["id"], wait_seconds=wait)
    if not updated:
        raise HTTPException(409, "纸面或作业状态已变化，请重新选择当前题目")
    # A request may already own one checked on-demand solve when no shared job
    # existed. Re-reading a still-pending database row must not erase that result.
    if target.get("_solution_attempted") and not target.get("_solution_inflight"):
        return {**target, "_solution_inflight": False,
                "read_confidence": min(target.get("read_confidence", 0), updated["read_confidence"])}
    return {**target, "solution": updated.get("solution"),
            "solve_confidence": updated.get("solve_confidence"),
            "verification_status": updated["verification_status"],
            "read_confidence": min(target.get("read_confidence", 0), updated["read_confidence"]),
            "_solution_inflight": updated.get("_solution_inflight", False)}


def _resolve_teaching_solution(session_id: str, family: dict, image: bytes,
                               target: dict, question: str,
                               student_thought: str = "", *, level: int | None = None) -> dict:
    target = _refresh_teaching_solution(session_id, family, image, target, question,
                                        student_thought, level=level)
    if (not target.get("_solution_inflight") and not target.get("_solution_attempted")
            and target.get("read_confidence", 0) >= .85
            and not _teaching_solution_ready(target)):
        target["_solution_attempted"] = True
        try:
            config = preferences(settings.database_path, family["id"])
            target["solution"] = solve_question(replace(settings, ai_model=config["solver_model"]),
                                                image, target)
            if grounded_teaching_solution(target["solution"], target["read_confidence"]):
                target.update(verification_status="verified", _solution_on_demand=True)
        except (HTTPException, httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            target["solution"] = {"answer": "", "steps": ""}
            target["_solution_error"] = True
        # Solving may take long enough for a pause, new page or changed model
        # preference. Validate ownership again before spoken-answer confirmation.
        target = _refresh_teaching_solution(session_id, family, image, target, question,
                                            student_thought, wait_for_pending=False, level=level)
    return target


def _teaching_solution_ready(target: dict) -> bool:
    return (target.get("verification_status") in {"verified", "rechecked"}
            and grounded_teaching_solution(target.get("solution"), target.get("read_confidence", 0)))


def _confirmed_answer_result(session_id: str, task_id: str | None, question: str,
                             thought: str, level: int, located: dict, response: str) -> dict:
    hid, now = uid(), current()
    result = {"id": hid, "response": response, "needs_parent": False, "level": level,
              "located": True, "teaching_move": "review_attempt", "reply_mode": "none",
              "answer_assessment": "correct", "target_box": located["box"],
              "target_label": located["label"], "visual_cue": None,
              "visual_beats": [{"speech": response, "page": [], "scratch": [
                  {"kind": "text", "role": "prose", "x": 12, "y": 24, "text": "这一步说对了 ✓"}],
                  "teaching_move": "review_attempt", "reply_mode": "none", "needs_parent": False}]}
    with transaction(settings.database_path) as db:
        active = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not active or active["status"] != "active":
            raise HTTPException(409, "Session ended during answer check")
        _save_hint_dialogue(db, hid, now, session_id, task_id, question, response, level, thought,
                            teaching_result=result)
    return result


def _hint_stream_context(events, family_id: str, trace_id: str):
    """Starlette may copy a fresh context for every next() on this iterator."""
    iterator = iter(events)
    try:
        while True:
            with teaching_call_context(family_id, trace_id):
                try:
                    event = next(iterator)
                except StopIteration:
                    return
            yield event
    finally:
        with teaching_call_context(family_id, trace_id):
            iterator.close()


@app.post("/v1/hints/stream")
def stream_hint(session_id: str = Form(...), question: str = Form(...),
                task_id: str | None = Form(None), target: str | None = Form(None),
                level: int = Form(1, ge=1, le=3),
                candidate_id: str | None = Form(None), selected_region: bool = Form(False),
                selected_strokes: str | None = Form(None),
                spoken_reference: bool = Form(False),
                student_thought: str | None = Form(None),
                previous_hint: str | None = Form(None),
                previous_hint_id: str | None = Form(None, max_length=64),
                local_text_anchors: str | None = Form(None, max_length=65536),
                image: UploadFile = File(...),
                claims: dict = Depends(principal)) -> StreamingResponse:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active":
        raise HTTPException(409, "Session is not active")
    if task_id:
        family_task(task_id, family["id"])
    image_bytes = read_image(image)
    level = resolved_teaching_level(question, student_thought or "", level)
    if candidate_id:
        if family["proactive_help_mode"] == "off" or not session["proactive_help_enabled"]:
            raise HTTPException(409, "Automatic help was turned off")
        if target or selected_region or selected_strokes or level != 1:
            raise HTTPException(422, "Automatic help candidates only support the first hint")
    if previous_hint_id and (candidate_id or selected_region or selected_strokes is not None):
        raise HTTPException(422, "续讲不能同时选择另一道题，请重新选择这道题")
    continued = (_continued_teaching_target(session_id, task_id, previous_hint_id, image_bytes)
                 if previous_hint_id else None)
    if continued:
        previous_hint = continued["_previous_response"]
    request_started = time.monotonic()
    anchor_state: dict = {"local_text_payload": local_text_anchors}
    trace_id = secrets.token_hex(8)
    cached = continued
    if not continued and not (candidate_id or selected_region or selected_strokes is not None):
        try:
            cached = page_preparation().match(
                session_id, image_bytes, target or question,
                spoken=spoken_reference or not bool(target),
                reviewing=any(word in question for word in ("复习", "重看", "回到", "上一题")),
            )
        except (OSError, ValueError):
            pass
    def events():
        yield _hint_event("status", {"label": ("正在核对你的回答…" if student_thought
                                              else "正在准备这道题的讲解…")})
        target_item = (cached if cached and cached["read_confidence"] >= .75 and
                       cached.get("progress") != "occluded" else None)
        if candidate_id or selected_strokes is not None:
            try:
                locator_settings = replace(settings, ai_model=preferences(
                    settings.database_path, family["id"])["locator_model"])
                if candidate_id:
                    candidate = one("SELECT * FROM help_candidates WHERE id=? AND session_id=? AND expires_at>?",
                                    (candidate_id, session_id, current()))
                    if candidate:
                        expected = json.loads(candidate["box_json"])
                        prepared = page_preparation().match(session_id, image_bytes,
                                                            candidate["label"])
                        if prepared and prepared.get("progress") != "occluded":
                            actual = prepared["box"]
                            cx, cy = actual[0] + actual[2] / 2, actual[1] + actual[3] / 2
                            if (expected[0] - .07 <= cx <= expected[0] + expected[2] + .07 and
                                    expected[1] - .07 <= cy <= expected[1] + expected[3] + .07):
                                target_item = prepared
                        located = ({"found": False} if target_item else
                                   locate_question(locator_settings, image_bytes, candidate["label"]))
                        if located["found"]:
                            actual = located["box"]
                            cx, cy = actual[0] + actual[2] / 2, actual[1] + actual[3] / 2
                            if not (expected[0] - .07 <= cx <= expected[0] + expected[2] + .07
                                    and expected[1] - .07 <= cy <= expected[1] + expected[3] + .07):
                                located["found"] = False
                    else:
                        located = {"found": False}
                else:
                    marks = parse_selected_strokes(selected_strokes)
                    mark_matcher = getattr(page_preparation(), "match_strokes", None)
                    prepared = mark_matcher(session_id, image_bytes, marks) if mark_matcher else None
                    target_item = prepared if prepared and prepared["read_confidence"] >= .8 else None
                    located = ({"found": False} if target_item else
                               locate_question(locator_settings, image_bytes, question[:200],
                                               learning_context(session_id, task_id), False, marks))
                if located["found"]:
                    target_item = {**located, "solution": None, "verification_status": "pending",
                                   "read_confidence": located.get("confidence", .8)}
            except (HTTPException, OSError, ValueError, TypeError):
                target_item = None
        if (not target_item and not (candidate_id or selected_region or selected_strokes is not None)):
            try:
                context = learning_context(session_id, task_id)
                locator_settings = replace(settings, ai_model=preferences(
                    settings.database_path, family["id"])["locator_model"])
                located = locate_question(locator_settings, image_bytes, question[:200], context,
                                          spoken_reference or not bool(target))
                if not located["found"] and target:
                    located = locate_question(locator_settings, image_bytes, target[:100])
                if located["found"]:
                    target_item = {**located, "solution": None, "verification_status": "pending",
                                   "read_confidence": located.get("confidence", .8)}
            except HTTPException:
                pass
        if not target_item and not selected_region:
            # Reuse the existing ordinary-path location recovery before choosing
            # the transport. A recovered target still deserves structured teaching;
            # it must not silently become one long ordinary response.
            try:
                prepared = _hint(
                    session_id=session_id, question=question, task_id=task_id,
                    target=target, level=level, candidate_id=candidate_id,
                    selected_region=selected_region, selected_strokes=selected_strokes,
                    spoken_reference=spoken_reference, student_thought=student_thought,
                    previous_hint=previous_hint, previous_hint_id=previous_hint_id,
                    image=UploadFile(file=BytesIO(image_bytes), filename="question.jpg",
                                     headers=Headers({"content-type": "image/jpeg"})),
                    claims=claims, annotation_state=anchor_state, prepare_only=True,
                )
                target_item = prepared.get("_teaching_target")
                if not target_item:
                    yield _hint_event("done", prepared)
                    return
            except HTTPException as exc:
                yield _hint_event("error", {"detail": str(exc.detail)})
                return
        def finish_beats(item: dict, beats: list[dict], delivery_complete: bool = True) -> dict:
            response_text = "".join(beat["speech"] for beat in beats)
            now, hid = current(), uid()
            result = {"id": hid, "response": response_text,
                      **normalized_teaching_turn(beats[-1] if beats else {}, response_text),
                      "level": level, "located": True,
                      "target_box": item["box"], "target_label": item["label"],
                      "question_id": item.get("id"), "visual_cue": None,
                      "visual_beats": beats, "delivery_complete": delivery_complete}
            result = _hint_tier_contract(result, level, item)
            with transaction(settings.database_path) as db:
                active = db.execute("SELECT s.status,s.proactive_help_enabled,f.proactive_help_mode "
                                    "FROM sessions s JOIN families f ON f.id=s.family_id WHERE s.id=?",
                                    (session_id,)).fetchone()
                if not active or active["status"] != "active":
                    raise ValueError("session ended during hint")
                if candidate_id:
                    if not active["proactive_help_enabled"] or active["proactive_help_mode"] == "off":
                        raise ValueError("automatic help was turned off")
                    valid = db.execute("SELECT id FROM help_candidates WHERE id=? AND session_id=? AND expires_at>?",
                                       (candidate_id, session_id, now)).fetchone()
                    if not valid:
                        raise ValueError("help offer expired during hint")
                _save_hint_dialogue(db, hid, now, session_id, task_id, question, response_text,
                                    level, student_thought or "",
                                    "proactive_accepted" if candidate_id else "ai", result,
                                    image_bytes=image_bytes, located=item)
                if candidate_id:
                    db.execute("DELETE FROM help_candidates WHERE id=?", (candidate_id,))
            return result
        if target_item:
            logger.info("hint_target_ready session=%s cached=%s elapsed_ms=%d",
                        session_id, bool(target_item.get("id")),
                        round((time.monotonic() - request_started) * 1000))
            yield _hint_event("target", {"box": target_item["box"],
                                         "label": target_item["label"],
                                         "question_id": target_item.get("id"),
                                         "teaching_tier": level})
            if (target_item.get("id") and target_item.get("verification_status") == "pending"
                    and teaching_goal(question, student_thought or "", level) in {"worked_example", "explain_method"}):
                label = ("正在核对每层积木与搬移位置…" if re.search(
                    r"积木|小正方体|立方体", target_item.get("evidence", "")) else "正在核对解题过程…")
                yield _hint_event("status", {"stage": "checking_solution", "label": label})
            try:
                target_item = _resolve_teaching_solution(
                    session_id, family, image_bytes, target_item, question, student_thought or "", level=level)
            except HTTPException as exc:
                yield _hint_event("error", {"detail": str(exc.detail)})
                return
            if (teaching_goal(question, student_thought or "", level) == "worked_example"
                    and (target_item.get("_solution_inflight") or target_item.get("_solution_error"))
                    and not _teaching_solution_ready(target_item)):
                beat = unresolved_teaching_beat(target_item)
                yield _hint_event("beat", beat)
                yield _hint_event("done", finish_beats(target_item, [beat]))
                return
            feedback = None
            if student_thought:
                feedback = _answer_feedback(family, image_bytes, target_item, student_thought)
                if feedback and feedback.get("verdict") == "correct":
                    try:
                        target_item = _refresh_teaching_solution(
                            session_id, family, image_bytes, target_item, question,
                            student_thought, wait_for_pending=False, level=level)
                        if target_item.get("read_confidence", 0) < .85 or not _teaching_solution_ready(target_item):
                            raise HTTPException(409, "当前题目仍需核对，请重新选择题目")
                        result = _confirmed_answer_result(
                            session_id, task_id, question, student_thought,
                            level, target_item, feedback["response"])
                        result = _hint_tier_contract(result, level)
                    except HTTPException as exc:
                        yield _hint_event("error", {"detail": str(exc.detail)})
                        return
                    yield _hint_event("visual", result["visual_beats"][0])
                    yield _hint_event("sentence", {"text": feedback["response"]})
                    yield _hint_event("done", result)
                    return
            solution = target_item.get("solution") or {"answer": "", "steps": ""}
            if _teaching_solution_ready(target_item):
                target_item = _teaching_anchors(image_bytes, target_item, anchor_state, family["id"], question)
                try:
                    target_item = _refresh_teaching_solution(
                        session_id, family, image_bytes, target_item, question, student_thought or "", level=level)
                except HTTPException as exc:
                    yield _hint_event("error", {"detail": str(exc.detail)})
                    return
                solution = target_item.get("solution") or {"answer": "", "steps": ""}
                spoken_beats = []
                first_beat_logged = False
                try:
                    seed = str(solution.get("hint_seed") or "").strip()
                    crop, crop_region = focus_question_image(image_bytes, target_item["box"])
                    tutor_name = _teaching_model(preferences(settings.database_path, family["id"]),
                                                 question, student_thought or "", level)
                    for kind, beat in stream_teaching_beats(
                        replace(settings, ai_model=tutor_name), image_bytes, crop,
                        crop_region, target_item, solution, family["grade"], level,
                        previous_hint or seed, student_thought or "",
                        question, learning_context(session_id, task_id),
                        family["age_band"], feedback,
                        _teaching_solution_ready(target_item),
                        stream_model, family.get("textbook"),
                    ):
                        if kind != "beat" or quoted_target_conflict(beat["speech"], target_item["evidence"]):
                            continue
                        spoken_beats.append(beat)
                        if not first_beat_logged:
                            logger.info("hint_first_beat session=%s cached=%s elapsed_ms=%d",
                                        session_id, bool(target_item.get("id")),
                                        round((time.monotonic() - request_started) * 1000))
                            first_beat_logged = True
                        yield _hint_event("beat", beat)
                    if spoken_beats:
                        yield _hint_event("done", finish_beats(target_item, spoken_beats))
                        return
                except (HTTPException, httpx.HTTPError, ValueError, KeyError, TypeError):
                    if spoken_beats:
                        try:
                            yield _hint_event("done", finish_beats(target_item, spoken_beats, False))
                        except ValueError:
                            yield _hint_event("error", {"detail": "图上讲解中断，请重试"})
                        return
            config = preferences(settings.database_path, family["id"])
            text_beats = []
            try:
                crop, region = focus_question_image(image_bytes, target_item["box"])
                target_item = _teaching_anchors(image_bytes, target_item, anchor_state, family["id"], question)
                target_item = _refresh_teaching_solution(
                    session_id, family, image_bytes, target_item, question, student_thought or "", level=level)
                solution = target_item.get("solution") or {"answer": "", "steps": ""}
                for kind, beat in stream_teaching_beats(
                    replace(settings, ai_model=_teaching_model(config, question, student_thought or "", level)), image_bytes, crop,
                    region, target_item, solution, family["grade"], level, previous_hint or "",
                    student_thought or "", question, learning_context(session_id, task_id),
                    family["age_band"], feedback,
                    _teaching_solution_ready(target_item),
                    stream_model, family.get("textbook"),
                ):
                    if kind != "beat" or quoted_target_conflict(beat["speech"], target_item["evidence"]):
                        continue
                    text_beats.append(beat)
                    yield _hint_event("beat", beat)
                if text_beats:
                    yield _hint_event("done", finish_beats(target_item, text_beats))
                    return
            except (HTTPException, httpx.HTTPError, ValueError, KeyError, TypeError):
                if text_beats:
                    yield _hint_event("done", finish_beats(target_item, text_beats, False))
                    return
        try:
            fallback_image = UploadFile(file=BytesIO(image_bytes), filename="question.jpg",
                                        headers=Headers({"content-type": "image/jpeg"}))
            result = _hint(session_id=session_id, question=question, task_id=task_id,
                          target=target, level=level, candidate_id=candidate_id,
                          selected_region=selected_region, selected_strokes=selected_strokes,
                          spoken_reference=spoken_reference, student_thought=student_thought,
                          previous_hint=previous_hint, previous_hint_id=previous_hint_id,
                          image=fallback_image, claims=claims,
                          annotation_state=anchor_state,
                          prepared_target=target_item if not (
                              candidate_id or selected_strokes is not None or selected_region) else None)
            if result.get("located") and result.get("target_box") and not result.get("answer_assessment"):
                beats = fallback_teaching_beats(result["response"], result["target_box"],
                                                result.get("visual_cue"), result)
                result["visual_beats"] = beats
                if result.get("id"):
                    with transaction(settings.database_path) as db:
                        db.execute("UPDATE hints SET teaching_json=? WHERE id=? AND session_id=?",
                                   (_hint_scene_json(result, image_bytes, target_item), result["id"], session_id))
                for beat in beats:
                    yield _hint_event("beat", beat)
            yield _hint_event("done", result)
        except HTTPException as exc:
            yield _hint_event("error", {"detail": str(exc.detail)})

    return StreamingResponse(_hint_stream_context(events(), family["id"], trace_id),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/v1/hints")
def hint(session_id: str = Form(...), question: str = Form(...), task_id: str | None = Form(None),
          target: str | None = Form(None), level: int = Form(1, ge=1, le=3),
          candidate_id: str | None = Form(None, max_length=64),
          selected_region: bool = Form(False),
          selected_strokes: str | None = Form(None, max_length=30000),
          spoken_reference: bool = Form(False),
          student_thought: str | None = Form(None),
          previous_hint: str | None = Form(None),
          previous_hint_id: str | None = Form(None, max_length=64),
          local_text_anchors: str | None = Form(None, max_length=65536),
          image: UploadFile = File(...),
          claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    with teaching_call_context(family["id"], secrets.token_hex(8)):
        return _hint(session_id=session_id, question=question, task_id=task_id, target=target,
                     level=level, candidate_id=candidate_id, selected_region=selected_region,
                     selected_strokes=selected_strokes, spoken_reference=spoken_reference,
                     student_thought=student_thought, previous_hint=previous_hint,
                     previous_hint_id=previous_hint_id,
                     image=image, claims=claims,
                     annotation_state={"local_text_payload": local_text_anchors})


def _hint(*, session_id: str, question: str, task_id: str | None, target: str | None,
          level: int, candidate_id: str | None, selected_region: bool,
          selected_strokes: str | None, spoken_reference: bool,
          student_thought: str | None, previous_hint: str | None,
          image: UploadFile, claims: dict, annotation_state: dict,
          prepare_only: bool = False, prepared_target: dict | None = None,
          previous_hint_id: str | None = None) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active":
        raise HTTPException(409, "Session is not active")
    if task_id:
        family_task(task_id, family["id"])
    image_bytes = read_image(image)
    level = resolved_teaching_level(question, student_thought or "", level)
    if previous_hint_id and (candidate_id or selected_region or selected_strokes is not None):
        raise HTTPException(422, "续讲不能同时选择另一道题，请重新选择这道题")
    continued = (_continued_teaching_target(session_id, task_id, previous_hint_id, image_bytes)
                 if previous_hint_id else None)
    if continued:
        previous_hint = continued["_previous_response"]
    family_ai = preferences(settings.database_path, family["id"])
    locator_settings = replace(settings, ai_model=family_ai["locator_model"])
    teaching_context = learning_context(session_id, task_id)
    cached = continued
    if continued:
        pass
    elif prepared_target is not None and not (candidate_id or selected_strokes is not None or selected_region):
        cached = prepared_target
    elif not (candidate_id or selected_strokes is not None or selected_region):
        try:
            cached = page_preparation().match(
                session_id, image_bytes, target or question,
                spoken=spoken_reference or not bool(target),
                reviewing=any(word in question for word in ("复习", "重看", "回到", "上一题")),
            )
        except (OSError, ValueError):
            cached = None
    if cached and cached.get("progress") == "occluded":
        cached = None
    location = None
    target_from_progress = False
    reviewing_old_question = False
    focus_image = image_bytes
    focus_region = [0, 0, 1, 1]
    if prepared_target is not None and not (candidate_id or selected_strokes is not None or selected_region):
        location = {**prepared_target}
        target_from_progress = spoken_reference or not bool(target)
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    elif candidate_id:
        if family["proactive_help_mode"] == "off" or not session["proactive_help_enabled"]:
            raise HTTPException(409, "Automatic help was turned off")
        if target or selected_region or selected_strokes or level != 1:
            raise HTTPException(422, "Automatic help candidates only support the first hint")
        candidate = one("SELECT * FROM help_candidates WHERE id=? AND session_id=? AND expires_at>?",
                        (candidate_id, session_id, current()))
        if not candidate:
            raise HTTPException(410, "This help offer has expired")
        location = locate_question(locator_settings, image_bytes, candidate["label"])
        expected = json.loads(candidate["box_json"])
        if location["found"]:
            actual = location["box"]
            center_x, center_y = actual[0] + actual[2] / 2, actual[1] + actual[3] / 2
            if not (expected[0] - .07 <= center_x <= expected[0] + expected[2] + .07
                    and expected[1] - .07 <= center_y <= expected[1] + expected[3] + .07):
                location["found"] = False
        if not location["found"]:
            return {"located": False, "target_box": None, "level": 1,
                    "response": "纸面或当前题目已经变化了。请说题号，或在画面上圈画要辅导的题。"}
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    elif selected_strokes is not None:
        marks = parse_selected_strokes(selected_strokes)
        mark_matcher = getattr(page_preparation(), "match_strokes", None)
        try:
            cached = mark_matcher(session_id, image_bytes, marks) if mark_matcher else None
        except (OSError, ValueError):
            cached = None
        location = ({"found": True, "box": cached["box"], "label": cached["label"],
                     "evidence": cached["evidence"], "confidence": cached["read_confidence"]}
                    if cached else locate_question(locator_settings, image_bytes, question[:200],
                                                   teaching_context, False, marks))
        if not location["found"]:
            return {"located": False, "target_box": None, "level": level,
                    "response": "还不能确定笔迹指向哪道题。请只在一道题下画线、圈住题目、画箭头，或直接说出题号。"}
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    elif cached and cached.get("progress") != "occluded":
        location = {"found": True, "box": cached["box"], "label": cached["label"],
                    "evidence": cached["evidence"],
                    "confidence": cached["read_confidence"]}
        target_from_progress = spoken_reference or not bool(target)
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    elif target:
        reviewing_old_question = any(term in question for term in (
            "复习", "再看", "重看", "回到", "回头", "上一题", "前一题", "刚才那题", "之前那题",
        ))
        if spoken_reference and not reviewing_old_question:
            location = locate_question(locator_settings, image_bytes, question[:200],
                                       teaching_context, True)
            target_from_progress = location["found"]
        if not location or not location["found"]:
            location = locate_question(locator_settings, image_bytes, target[:100])
        if not location["found"]:
            return {"located": False, "target_box": None, "level": level,
                    "response": f"没能在当前纸面可靠找到{target[:50]}。如果有两页，请连同左页或右页一起说题号；也可读题目开头几个字，或在画面上圈画。"}
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    elif not selected_region:
        location = locate_question(locator_settings, image_bytes, question[:200], teaching_context, True)
        if not location["found"]:
            return {"located": False, "target_box": None, "level": level,
                    "response": "我看了整页，还不能确定你指哪一题。请说题号、读一句题干，或在画面上圈画。"}
        focus_image, focus_region = focus_question_image(image_bytes, location["box"])
    if prepare_only:
        if not location:
            return {"located": False, "target_box": None, "level": level,
                    "response": "还不能可靠确认当前题目。请说题号，或在画面上圈画。"}
        return {"_teaching_target": {
            **(cached or {}), **location,
            "read_confidence": (cached or {}).get("read_confidence", location.get("confidence", 0)),
            "verification_status": (cached or {}).get("verification_status", "pending"),
        }}
    if location and not cached and teaching_goal(question, student_thought or "", level) in {
            "worked_example", "explain_method"}:
        cached = {**location,
                  "read_confidence": location.get("read_confidence", location.get("confidence", 0)),
                  "verification_status": location.get("verification_status", "pending")}
    if cached:
        cached = _resolve_teaching_solution(
            session_id, family, image_bytes, cached, question, student_thought or "", level=level)
    thought = (student_thought or "").strip()[:500]
    feedback = None
    if thought and location:
        answer_target = cached if cached else {
            **location, "read_confidence": location.get("confidence", 0),
        }
        feedback = _answer_feedback(family, image_bytes, answer_target, thought)
        if feedback and feedback.get("verdict") == "correct":
            answer_target = _refresh_teaching_solution(
                session_id, family, image_bytes, answer_target, question,
                thought, wait_for_pending=False, level=level)
            if answer_target.get("read_confidence", 0) < .85 or not _teaching_solution_ready(answer_target):
                raise HTTPException(409, "当前题目仍需核对，请重新选择题目")
            return _hint_tier_contract(_confirmed_answer_result(
                session_id, task_id, question, thought, level, answer_target, feedback["response"]), level)
    if location:
        location = _teaching_anchors(image_bytes, location, annotation_state, family["id"], question)
        if cached:
            cached = _refresh_teaching_solution(
                session_id, family, image_bytes, cached, question, thought, level=level)
    goal = teaching_goal(question, thought, level)
    teaching_move = teaching_scope_guidance(level, goal)
    prompt = f"第{level}层教师式辅导；{teaching_move} 问题：{question[:500]}"
    if location:
        prompt = (f"第{level}层教师式辅导；{teaching_move} "
                  f"本轮需要辅导的题干线索：{location['evidence']}；"
                  f"纸面定位：{location['label']}。请只核对并辅导局部图中与这段题干线索一致的题，"
                  "无需重新推定裁剪图之外的大题编号。"
                  "用户本轮表达：" + question[:200] + "。")
        if target_from_progress:
            prompt += ("语音识别出的题号只作求助线索；若与整页作答进度冲突，"
                       "以刚确认的纸面题干和题号为准，不要改回语音题号。")
    earlier = (previous_hint or "").strip()[:500]
    if earlier:
        prompt += f" 上一轮老师给过的提示：{earlier}。请在此基础上推进，避免重复。"
    if thought:
        prompt += (f" 孩子刚刚口述的想法：{thought}。这是口述，不代表已经写在纸上；"
                    "先判断这句话是在求助、回答、解释、质疑还是表示要自己尝试，再自然回应。"
                    "如果孩子仍不理解上一步，换种说法弄清卡点，不要机械推进层级；"
                    "如果已有可行想法，用简短话语回应后让孩子自己尝试，不追问固定口令。"
                    "孩子刚说的内容可能只是计划或理由，不代表已经写对或掌握。")
    if feedback:
        prompt += f" 口述核验供选择本次卡点，不是掌握证明：{json.dumps(feedback, ensure_ascii=False)}。"
    if location:
        prompt += (" 当次照片文字锚点：" + json.dumps(location.get("anchors") or [], ensure_ascii=False)
                   + "。精确划线或圈词必须引用对应anchor_id，并在提示中说出原词；无锚点时visual_cue=null。")
    tutor_settings = replace(settings, ai_model=_teaching_model(family_ai, question, thought, level))
    allow_final_answer = False
    if cached and cached.get("solution") and cached["verification_status"] in {"verified", "rechecked"}:
        solution = cached["solution"]
        allow_final_answer = (goal == "worked_example" and not withholds_final_answer(question, thought)
                              and grounded_teaching_solution(solution, cached.get("read_confidence", 0)))
        if allow_final_answer:
            prompt += (" 本轮答案许可：孩子明确要求完整示范，当前题目与解法已可靠核对；"
                       "可展示已核对最终结果并解释检验，把条件、方法、步骤与结论的因果完整讲清。"
                       f" 教师已核对的解题步骤：{str(solution.get('steps') or '')[:1600]}；"
                       f"教师已核对的最终结果：{str(solution.get('answer') or '')[:300]}。")
        else:
            prompt += (" 本轮答案许可：不展示最终答案；按孩子的请求讲清当前方法的完整因果，"
                       "不要把控制求解范围误解为省略必要依据。")
        prompt += f" 教师内部入手线索：{solution.get('hint_seed', '')}。"
    if (cached and goal == "worked_example" and not allow_final_answer
            and (cached.get("_solution_inflight") or cached.get("_solution_error"))):
        pending = unresolved_teaching_beat(cached)
        result = {**pending, "response": pending["speech"], "visual_cue": None}
    else:
        result = analyze(tutor_settings, focus_image, prompt, family["age_band"], family["grade"],
                         family["textbook"], "hint_located" if location else "hint",
                         context_image=image_bytes if location else None,
                         session_context=teaching_context,
                         **({"allow_final_answer": True} if allow_final_answer else {}))
    if location and result.get("target_mismatch"):
        if previous_hint_id:
            # A continued explanation must never silently select a different problem.
            return {"located": False, "target_box": location["box"], "level": level,
                    "teaching_tier": level, "continuation_available": False,
                    "response": "同一张原题的关键内容还需核对，请重新选择这道题后再讲解。"}
        # A crop can omit the parent question number or nearby completed work.
        # Reconcile against the full page before asking the child to locate it again.
        try:
            if selected_strokes is not None:
                rechecked = locate_question(locator_settings, image_bytes, question[:200],
                                            teaching_context, False, marks)
            elif candidate_id:
                rechecked = locate_question(locator_settings, image_bytes, candidate["label"])
            elif target and (reviewing_old_question or not spoken_reference):
                rechecked = locate_question(locator_settings, image_bytes, target[:100])
            else:
                rechecked = locate_question(locator_settings, image_bytes, question[:200],
                                            teaching_context, True)
                target_from_progress = True
        except HTTPException:
            rechecked = {"found": False}
        if not rechecked["found"]:
            return {"located": False, "target_box": None, "level": level,
                    "response": "我还不能从整页确定你现在做到哪一小题。请读出这题开头几个字，或说出完整题号？"}
        location = rechecked
        allow_final_answer = False
        recovery_prompt = (
            f"第{level}层辅导；{teaching_move}。只按整页重新确认的题干给一步提示，"
            "本轮语音题号和旧提示可能属于另一题，不能据此改写题干。"
        )
        if thought:
            recovery_prompt += f"孩子刚才口述的想法：{thought}；只有与本题相关时才参考。"
        try:
            grounded_hint = teach_from_evidence(tutor_settings, location["evidence"], recovery_prompt,
                                                family["age_band"], family["grade"])
        except HTTPException:
            return {"located": False, "target_box": location["box"], "level": level,
                    "response": "我找到了可能正在做的小题，但还不能可靠读出完整题干。请读出题目开头几个字？"}
        result = {"response": grounded_hint, "needs_parent": False,
                  "teaching_move": "give_clue", "visual_cue": None}
    if location and quoted_target_conflict(result["response"], location["evidence"]):
        try:
            result["response"] = teach_from_evidence(tutor_settings, location["evidence"], prompt,
                                                      family["age_band"], family["grade"],
                                                      **({"allow_final_answer": True} if allow_final_answer else {}))
        except HTTPException:
            return {"located": False, "target_box": location["box"], "level": level,
                    "response": "我读出的关键字与定位时不一致。请核对画面中框出的题目，再重试提示。"}
    response_text = result["response"]
    if (not allow_final_answer and cached and cached.get("solution")
            and reveals_final_answer(response_text, cached["solution"]["answer"])):
        response_text = teach_from_evidence(tutor_settings, location["evidence"],
                                            prompt.split(" 教师内部入手线索：")[0],
                                            family["age_band"], family["grade"])
    if location and (not target or target_from_progress):
        response_text = f"我先按{location['label']}来理解：{response_text}"
    hid, now = uid(), current()
    cue = None
    if location and result.get("visual_cue"):
        proposed = result["visual_cue"]
        anchor = next((item for item in location.get("anchors", [])
                       if item["id"] == proposed.get("anchor_id")), None)
        spoken = spoken_anchor_text(anchor["text"], response_text) if anchor else None
        if spoken and "box" in anchor:
            cue = {**proposed, "box": anchor["box"], "text": anchor["text"],
                   **({"anchor_text": spoken} if spoken != anchor["text"] else {})}
    turn = normalized_teaching_turn(result, response_text)
    beats = fallback_teaching_beats(response_text, location["box"], cue, turn) if location else []
    saved_result = {"id": hid, "response": response_text, **turn,
                    "level": level, "located": True,
                    **({"answer_assessment": feedback["verdict"]} if feedback else {}),
                    "target_box": location["box"] if location else None,
                    "target_label": location["label"] if location else "",
                    "visual_cue": cue,
                    "visual_beats": beats}
    saved_result = _hint_tier_contract(saved_result, level, location)
    with transaction(settings.database_path) as db:
        active = db.execute(
            "SELECT s.status,s.proactive_help_enabled,f.proactive_help_mode "
            "FROM sessions s JOIN families f ON f.id=s.family_id WHERE s.id=?", (session_id,)
        ).fetchone()
        if not active or active["status"] != "active":
            raise HTTPException(409, "Session ended during analysis")
        if candidate_id:
            if not active["proactive_help_enabled"] or active["proactive_help_mode"] == "off":
                raise HTTPException(409, "Automatic help was turned off")
            still_valid = db.execute(
                "SELECT id FROM help_candidates WHERE id=? AND session_id=? AND expires_at>?",
                (candidate_id, session_id, now),
            ).fetchone()
            if not still_valid:
                raise HTTPException(410, "This help offer has expired")
        _save_hint_dialogue(db, hid, now, session_id, task_id, question, response_text,
                            level, thought, "proactive_accepted" if candidate_id else "ai", saved_result,
                            image_bytes=image_bytes, located=location)
        if candidate_id:
            db.execute("DELETE FROM help_candidates WHERE id=?", (candidate_id,))
    return saved_result


@app.post("/v1/hints/{hint_id}/attempt")
def check_hint_attempt(hint_id: str, session_id: str = Form(...),
                        before_image: UploadFile = File(...), image: UploadFile = File(...),
                        target_box: str | None = Form(None, max_length=100),
                        background: bool = Form(False),
                        claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active" or session["device_id"] != claims["sub"]:
        raise HTTPException(409, "Session is not active on this device")
    recent = one("SELECT id,task_id,question,response FROM hints WHERE session_id=? "
                 "AND expires_at>? ORDER BY created_at DESC,rowid DESC LIMIT 1",
                 (session_id, current()))
    if not recent or recent["id"] != hint_id:
        raise HTTPException(409, "Use the latest hint for this paper check")
    box = None
    if target_box is not None:
        try:
            box = normalized_box(json.loads(target_box))
        except (ValueError, TypeError):
            pass
        if not box:
            raise HTTPException(422, "Target box is invalid")
    result = assess_hint_attempt(settings, read_image(before_image), read_image(image),
                                 recent["question"], recent["response"], box)
    now, oid = current(), uid()
    labels = {"new_work": "目标处有新书写", "no_visible_change": "目标处暂无可见新书写",
              "changed_page": "纸面可能已更换", "unclear": "目标处变化不确定"}
    with transaction(settings.database_path) as db:
        active = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not active or active["status"] != "active":
            raise HTTPException(409, "Session ended during analysis")
        if not background or result["status"] == "new_work":
            db.execute("INSERT INTO observations(id,session_id,task_id,kind,label,evidence,confidence,"
                       "progress,writing,visible_steps,review_status,created_at,expires_at) "
                       "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (oid, session_id, recent["task_id"], "hint_attempt", labels[result["status"]],
                        result["evidence"], result["confidence"],
                        result["evidence"] if result["status"] == "new_work" else "", "", "",
                        "unreviewed", now, now + RETENTION_SECONDS))
    return {"id": oid if not background or result["status"] == "new_work" else None, **result}


@app.post("/v1/sessions/{session_id}/reflections")
def record_reflection(session_id: str, body: Reflection, claims: dict = Depends(principal)) -> dict:
    require_role(claims, "child")
    family = family_for(claims)
    session = family_session(session_id, family["id"])
    if session["status"] != "active":
        raise HTTPException(409, "Session is not active")
    if body.task_id:
        family_task(body.task_id, family["id"])
    rid, now = uid(), current()
    with transaction(settings.database_path) as db:
        active = db.execute("SELECT status FROM sessions WHERE id=?", (session_id,)).fetchone()
        if not active or active["status"] != "active":
            raise HTTPException(409, "Session ended")
        db.execute("INSERT INTO reflections VALUES(?,?,?,?,?,?,?)", (rid, session_id, body.task_id,
                   body.text.strip(), "child_voice_or_text", now, now + RETENTION_SECONDS))
    return {"id": rid, "text": body.text.strip(), "source": "child_voice_or_text"}


@app.get("/v1/reports/daily")
def daily_report(offset_minutes: int = Query(480, ge=-720, le=840),
                 claims: dict = Depends(principal)) -> dict:
    fid = family_for(claims)["id"]
    since, until = day_bounds(offset_minutes)
    tasks = many("SELECT status,COUNT(*) AS count FROM tasks WHERE family_id=? AND created_at>=? AND created_at<? GROUP BY status", (fid, since, until))
    timer = daily_summary(settings.database_path, fid, since, until, current())
    help_count = one("SELECT COUNT(*) AS count FROM hints h JOIN sessions s ON s.id=h.session_id WHERE s.family_id=? AND h.created_at>=? AND h.created_at<?", (fid, since, until))
    review_count = one("SELECT COUNT(*) AS count FROM observations o JOIN sessions s ON s.id=o.session_id WHERE s.family_id=? AND o.review_status='unreviewed'", (fid,))
    correction_count = one("SELECT COUNT(*) AS count FROM corrections c JOIN sessions s ON s.id=c.session_id WHERE s.family_id=? AND c.created_at>=? AND c.created_at<?", (fid, since, until))
    return {"tasks": {item["status"]: item["count"] for item in tasks},
            **timer,
            "hint_count": help_count["count"], "correction_count": correction_count["count"],
            "unreviewed_count": review_count["count"],
            **daily_report_details(settings.database_path, fid, since, until,
                                   offset_minutes, current(), claims["role"],
                                   correction_require_parent_approval=settings.correction_require_parent_approval)}


@app.post("/v1/data/delete")
def delete_history(claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    fid = family_for(claims)["id"]
    with transaction(settings.database_path) as db:
        db.execute("DELETE FROM family_question_cache WHERE family_id=?", (fid,))
        db.execute("DELETE FROM family_page_cache WHERE family_id=?", (fid,))
        db.execute("DELETE FROM prepared_questions WHERE page_id IN "
                   "(SELECT p.id FROM page_preparations p JOIN sessions s ON s.id=p.session_id "
                   "WHERE s.family_id=?)", (fid,))
        db.execute("DELETE FROM page_preparations WHERE session_id IN "
                   "(SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM ai_usage_totals WHERE family_id=?", (fid,))
        db.execute("DELETE FROM submission_pages WHERE submission_id IN "
                   "(SELECT id FROM task_submissions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM task_submissions WHERE family_id=?", (fid,))
        db.execute("DELETE FROM task_reminder_state WHERE task_id IN "
                   "(SELECT id FROM tasks WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM task_archive_counts WHERE family_id=?", (fid,))
        db.execute("DELETE FROM ai_usage WHERE family_id=?", (fid,))
        db.execute("DELETE FROM session_pauses WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM timer_events WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM corrections WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM hints WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM help_candidates WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM reflections WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM observations WHERE session_id IN (SELECT id FROM sessions WHERE family_id=?)", (fid,))
        db.execute("DELETE FROM live_sessions WHERE family_id=?", (fid,))
        db.execute("DELETE FROM sessions WHERE family_id=?", (fid,))
        db.execute("DELETE FROM tasks WHERE family_id=?", (fid,))
    broadcast_sync(fid, {"type": "history_deleted"})
    return {"deleted": True}


@app.get("/v1/ice")
def ice_servers(claims: dict = Depends(principal)) -> dict:
    family_for(claims)
    servers = []
    if settings.turn_host and settings.turn_shared_secret:
        servers.append({"urls": [f"stun:{settings.turn_host}:{settings.turn_port}"]})
        username = f"{current() + 3600}:{claims['sub']}"
        credential = base64.b64encode(hmac.new(settings.turn_shared_secret.encode(),
                      username.encode(), hashlib.sha1).digest()).decode()
        servers.append({"urls": [f"turn:{settings.turn_host}:{settings.turn_port}?transport=udp",
                                  f"turn:{settings.turn_host}:{settings.turn_port}?transport=tcp"],
                        "username": username, "credential": credential})
    return {"iceServers": servers, "turn_configured": len(servers) > 1}


@app.post("/v1/live-sessions")
def start_live(claims: dict = Depends(principal)) -> dict:
    require_role(claims, "parent")
    fid = family_for(claims)["id"]
    session = one("SELECT * FROM sessions WHERE family_id=? AND status='active'", (fid,))
    if not session:
        raise HTTPException(409, "Child is not in an active homework session")
    live = one("SELECT * FROM live_sessions WHERE family_id=? AND status='active'", (fid,))
    if live:
        return {"id": live["id"], "status": "active"}
    lid = uid()
    with transaction(settings.database_path) as db:
        db.execute("INSERT INTO live_sessions VALUES(?,?,?,?,?,?)", (lid, fid, session["id"], "active", current(), None))
    broadcast_sync(fid, {"type": "live_started", "id": lid})
    return {"id": lid, "status": "active"}


@app.post("/v1/live-sessions/{live_id}/finish")
def finish_live(live_id: str, claims: dict = Depends(principal)) -> dict:
    fid = family_for(claims)["id"]
    with transaction(settings.database_path) as db:
        result = db.execute("UPDATE live_sessions SET status='ended',ended_at=? WHERE id=? AND family_id=? AND status='active'",
                            (current(), live_id, fid))
        if result.rowcount != 1:
            raise HTTPException(404, "Live session not found")
    broadcast_sync(fid, {"type": "live_ended"})
    return {"status": "ended"}


@app.post("/v1/realtime/ticket")
def realtime_ticket(claims: dict = Depends(principal)) -> dict:
    fid = family_for(claims)["id"]
    nonce = secrets.token_urlsafe(24)
    ticket = make_token(settings.app_secret, claims["role"], f"{fid}:{nonce}", 60)
    return {"ticket": ticket}


rooms: dict[str, dict[str, WebSocket]] = defaultdict(dict)
main_loop: asyncio.AbstractEventLoop | None = None


async def broadcast(family_id: str, message: dict, exclude: str | None = None) -> None:
    for role, socket in list(rooms.get(family_id, {}).items()):
        if role != exclude:
            try:
                await socket.send_json(message)
            except RuntimeError:
                pass


def broadcast_sync(family_id: str, message: dict) -> None:
    if main_loop and main_loop.is_running():
        asyncio.run_coroutine_threadsafe(broadcast(family_id, message), main_loop)


@app.websocket("/v1/realtime")
async def realtime(socket: WebSocket) -> None:
    global main_loop
    protocols = [part.strip() for part in socket.headers.get("sec-websocket-protocol", "").split(",")]
    ticket = protocols[1] if len(protocols) == 2 and protocols[0] == "k12" else ""
    claims = read_token(settings.app_secret, ticket)
    if not claims or ":" not in claims["sub"]:
        await socket.close(code=4401)
        return
    fid = claims["sub"].split(":", 1)[0]
    if not one("SELECT id FROM families WHERE id=?", (fid,)):
        await socket.close(code=4403)
        return
    await socket.accept(subprotocol="k12")
    main_loop = asyncio.get_running_loop()
    role = claims["role"]
    old = rooms[fid].get(role)
    if old:
        await old.close()
    rooms[fid][role] = socket
    await broadcast(fid, {"type": "peer_ready", "role": role}, exclude=role)
    try:
        while True:
            raw = await socket.receive_text()
            if len(raw) > 100_000:
                await socket.close(code=1009)
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind not in {"offer", "answer", "ice", "annotation", "clear_annotation", "voice_on", "voice_off"}:
                continue
            if kind in {"offer", "answer", "ice", "annotation", "voice_on", "voice_off"}:
                live = one("SELECT id FROM live_sessions WHERE family_id=? AND status='active'", (fid,))
                if not live:
                    continue
            if kind in {"annotation", "clear_annotation"} and role != "parent":
                continue
            if kind in {"voice_on", "voice_off"} and role != "parent":
                continue
            if kind == "annotation":
                points = message.get("points", [])
                if not isinstance(points, list) or len(points) > 300 or any(
                    not isinstance(p, list) or len(p) != 2 or any(not isinstance(n, int | float) or n < 0 or n > 1 for n in p)
                    for p in points
                ):
                    continue
                message = {"type": kind, "points": points}
            await broadcast(fid, message, exclude=role)
    except Exception:
        pass
    finally:
        if rooms.get(fid, {}).get(role) is socket:
            del rooms[fid][role]
            await broadcast(fid, {"type": "peer_left", "role": role})
