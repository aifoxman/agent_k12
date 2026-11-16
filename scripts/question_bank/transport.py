"""Isolated-server instrumentation at each physical HTTP attempt, including retries."""
from __future__ import annotations

import contextvars
import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx

from scripts.question_bank.budget import Budget

TRACE = contextvars.ContextVar("question_bank_trace", default="background")


def install(budget_path: Path, round_id: int, log_path: Path):
    budget = Budget(budget_path)
    lock = threading.Lock()
    original_post, original_stream = httpx.post, httpx.stream
    original_submit = ThreadPoolExecutor.submit

    def event(kind, **value):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with lock, log_path.open("a", encoding="utf-8") as target:
            target.write(json.dumps({"type": kind, "trace": TRACE.get(), **value}) + "\n")

    def submit(self, function, /, *args, **kwargs):
        # New context per submission, never share an entered context between workers.
        context = contextvars.copy_context()
        return original_submit(self, context.run, function, *args, **kwargs)

    def reserve(kwargs):
        payload = kwargs.get("json") or {}
        identifier = budget.reserve(round_id, "upstream", TRACE.get())
        event("upstream_start", reservation=identifier, configured_model=payload.get("model"),
              started=time.time())
        return identifier

    def post(*args, **kwargs):
        identifier, start = reserve(kwargs), time.monotonic()
        try:
            response = original_post(*args, **kwargs)
            try:
                data = response.json()
            except ValueError:
                data = {}
            budget.record(identifier, data.get("model", "unknown"), data.get("usage"))
            event("upstream_done", reservation=identifier, status=response.status_code,
                  model=data.get("model"), elapsed_ms=round((time.monotonic() - start) * 1000), usage=data.get("usage"))
            return response
        except Exception as exc:
            event("upstream_error", reservation=identifier, error=type(exc).__name__)
            raise

    @contextmanager
    def stream(*args, **kwargs):
        identifier, start = reserve(kwargs), time.monotonic()
        try:
            with original_stream(*args, **kwargs) as response:
                iterator = response.iter_lines
                seen_model = None
                def lines():
                    nonlocal seen_model
                    for line in iterator():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:])
                                if data.get("usage") or data.get("model") and data["model"] != seen_model:
                                    seen_model = data.get("model") or seen_model
                                    budget.record(identifier, data.get("model", "unknown"), data.get("usage"))
                                    event("upstream_metadata", reservation=identifier, model=data.get("model"), usage=data.get("usage"))
                            except (ValueError, AttributeError):
                                pass
                        yield line
                response.iter_lines = lines
                yield response
                event("upstream_done", reservation=identifier, status=response.status_code,
                      elapsed_ms=round((time.monotonic() - start) * 1000))
        except Exception as exc:
            event("upstream_error", reservation=identifier, error=type(exc).__name__)
            raise

    httpx.post, httpx.stream, ThreadPoolExecutor.submit = post, stream, submit
    from k12_server import tts
    original_tencent = tts.tencent_audio
    def tencent(*args, **kwargs):
        identifier = budget.reserve(round_id, "upstream", TRACE.get())
        event("tts_start", reservation=identifier)
        try:
            yield from original_tencent(*args, **kwargs)
            event("tts_done", reservation=identifier)
        except Exception as exc:
            event("tts_error", reservation=identifier, error=type(exc).__name__)
            raise
    tts.tencent_audio = tencent
    original_provider = tts.provider_audio
    def provider(*args, **kwargs):
        identifier = budget.reserve(round_id, "upstream", TRACE.get())
        event("tts_start", reservation=identifier)
        yield from original_provider(*args, **kwargs)
        event("tts_done", reservation=identifier)
    tts.provider_audio = provider
    return event


def valid_trace(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value):
        raise ValueError("Invalid evaluation trace")
    return value
