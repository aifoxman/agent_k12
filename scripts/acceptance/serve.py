"""Run the real API against a dedicated, disposable acceptance database."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def database_for_run(directory: Path) -> Path:
    """Reject an existing database symlink that escapes the disposable run."""
    directory = directory.resolve()
    database = (directory / "api.sqlite3").resolve()
    if not database.is_relative_to(directory):
        raise ValueError("Acceptance database must resolve inside its run directory")
    return database


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8016)
    parser.add_argument("--frontend-port", type=int, default=5186)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or args.port == 8003:
        parser.error("Use a dedicated acceptance port; port 8003 is excluded")
    directory = args.output.resolve()
    allowed = (ROOT / "output" / "acceptance").resolve()
    if not directory.is_relative_to(allowed) or directory == allowed:
        parser.error("output must be a run directory inside output/acceptance")
    directory.mkdir(parents=True, exist_ok=True)
    from dotenv import dotenv_values

    config = dotenv_values(ROOT / ".env") if args.live else {}
    for name, value in config.items():
        if value is not None:
            os.environ[name] = value
    if not args.live:
        os.environ["AI_API_URL"] = ""
        os.environ["AI_API_KEY"] = ""
        os.environ["AI_MODEL"] = ""
    os.environ["APP_SECRET"] = secrets.token_urlsafe(48)
    os.environ["DATABASE_PATH"] = str(database_for_run(directory))
    os.environ["ALLOWED_ORIGINS"] = ",".join(
        (
            f"http://127.0.0.1:{args.frontend_port}",
            f"http://localhost:{args.frontend_port}",
            "https://appassets.androidplatform.net",
        )
    )
    # Instrument only this isolated process; never record credentials or image payloads.
    from k12_server import ai

    log_lock = threading.Lock()

    def event(kind: str, **data) -> None:
        record = {"at": datetime.now(UTC).isoformat(), "type": kind, **data}
        with log_lock, (directory / "server-events.jsonl").open("a", encoding="utf-8") as target:
            target.write(json.dumps(record, ensure_ascii=False) + "\n")

    original_post, original_stream = ai.post_model, ai.stream_model

    def post(settings, payload, *positionals, **kwargs):
        started = time.monotonic()
        identifier = secrets.token_hex(6)
        event(
            "model_start",
            call_id=identifier,
            configured_model=payload.get("model"),
            transport="live",
        )
        try:
            result = original_post(settings, payload, *positionals, **kwargs)
            event(
                "model_done",
                call_id=identifier,
                response_model=result.get("model"),
                duration_seconds=round(time.monotonic() - started, 3),
                usage=result.get("usage"),
            )
            return result
        except Exception as exc:
            event(
                "model_error",
                call_id=identifier,
                error_type=type(exc).__name__,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            raise

    def stream(settings, payload, *positionals, **kwargs):
        started = time.monotonic()
        identifier = secrets.token_hex(6)
        event(
            "model_start",
            call_id=identifier,
            configured_model=payload.get("model"),
            transport="live",
            streaming=True,
        )
        characters = 0
        captured = []
        captured_length = 0
        try:
            for chunk in original_stream(settings, payload, *positionals, **kwargs):
                characters += len(chunk)
                if captured_length < 64000:
                    clipped = chunk[:64000 - captured_length]
                    captured.append(clipped)
                    captured_length += len(clipped)
                event("model_delta", call_id=identifier, characters=len(chunk))
                yield chunk
            event(
                "model_done",
                call_id=identifier,
                response_model=None,
                duration_seconds=round(time.monotonic() - started, 3),
                characters=characters,
            )
        except Exception as exc:
            event("model_error", call_id=identifier, error_type=type(exc).__name__)
            raise
        finally:
            if captured:
                private = directory / "private"
                private.mkdir(exist_ok=True)
                (private / f"provider-stream-{identifier}.txt").write_text(
                    "".join(captured), encoding="utf-8"
                )

    ai.post_model, ai.stream_model = post, stream
    original_locate = ai.locate_question

    def locate(*positionals, **kwargs):
        started = time.monotonic()
        result = original_locate(*positionals, **kwargs)
        event(
            "question_location",
            duration_seconds=round(time.monotonic() - started, 3),
            found=result.get("found"),
            ambiguous=result.get("ambiguous"),
            confidence=result.get("confidence"),
            box=result.get("box"),
            anchor_count=len(result.get("anchors") or []),
        )
        return result

    ai.locate_question = locate
    original_anchor_read = ai.read_teaching_anchors
    original_anchor_verify = ai.verify_teaching_anchors

    def anchor_summary(anchors):
        return [
            {name: item[name] for name in ("id", "kind", "box", "x1", "y1", "x2", "y2") if name in item}
            for item in anchors if isinstance(item, dict)
        ]

    def anchor_read(*positionals, **kwargs):
        started = time.monotonic()
        result = original_anchor_read(*positionals, **kwargs)
        event("anchor_read", duration_seconds=round(time.monotonic() - started, 3),
              anchors=anchor_summary(result), geometry_needed=kwargs.get("geometry_needed", False))
        return result

    def anchor_verify(image, anchors, *positionals, **kwargs):
        started = time.monotonic()
        result = original_anchor_verify(image, anchors, *positionals, **kwargs)
        event("anchor_verification", duration_seconds=round(time.monotonic() - started, 3),
              candidates=anchor_summary(anchors), accepted=anchor_summary(result))
        return result

    # Transparent diagnostics only in the isolated server. Never save image bytes,
    # word contents, prompts, credentials, or a replacement result.
    ai.read_teaching_anchors = anchor_read
    ai.verify_teaching_anchors = anchor_verify
    from k12_server.app import app

    @app.middleware("http")
    async def observe(request, call_next):
        started = time.monotonic()
        response = await call_next(request)
        response.headers["X-K12-Acceptance-Run"] = directory.name
        event(
            "http",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_seconds=round(time.monotonic() - started, 3),
        )
        return response

    import uvicorn

    event("server_start", synthetic=True, live_model=args.live, port=args.port)
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
