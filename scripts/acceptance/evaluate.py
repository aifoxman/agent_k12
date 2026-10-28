"""Generate six synthetic worksheets, then exercise an isolated local API.

Credentials are read from ACCEPTANCE_CHILD_TOKEN, ACCEPTANCE_PARENT_TOKEN and
ACCEPTANCE_SESSION_ID. They are never included in artifacts or console output.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from bootstrap import verify_acceptance_api
from cases import definitions, render
from scoring import evaluate


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def append_event(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": utc_now(), **value}, ensure_ascii=False) + "\n")
        stream.flush()


def prepare(output: Path, font: str | None = None) -> dict:
    cases = [render(case, output / "papers" / f"{case['id']}.jpg", font) for case in definitions()]
    manifest = {
        "version": 1,
        "synthetic": True,
        "run_id": output.resolve().parent.name,
        "created_at": utc_now(),
        "image_size": [1200, 900],
        "cases": cases,
        "oracle_boundary": "Oracle is evaluator-only; never send it in production requests.",
    }
    write_json(output / "manifest.json", manifest)
    return manifest


def _safe_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.port == 8003
    ):
        raise ValueError("Use the isolated local acceptance API; port 8003 is explicitly excluded.")
    return value.rstrip("/")


def _stream_hint(
    client: httpx.Client,
    case: dict,
    image: bytes,
    session_id: str,
    child_headers: dict,
    event_path: Path,
) -> dict:
    # This allowlist is the boundary preventing evaluator truth from reaching the model.
    form = {
        "session_id": session_id,
        "question": case["prompt"],
        "target": case["target"],
        "level": 1,
    }
    if case.get("student_thought"):
        form["student_thought"] = case["student_thought"]
    final, beats, event_name, data_lines = None, [], "", []
    with client.stream(
        "POST",
        "/v1/hints/stream",
        data=form,
        files={"image": ("synthetic.jpg", image, "image/jpeg")},
        headers=child_headers,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif not line and data_lines:
                payload = json.loads("\n".join(data_lines))
                append_event(
                    event_path, {"case_id": case["id"], "event": event_name, "data": payload}
                )
                if event_name in {"beat", "visual"}:
                    beats.append(payload)
                elif event_name == "done":
                    final = payload
                elif event_name == "error":
                    raise RuntimeError(str(payload.get("detail") or "Stream failed"))
                data_lines = []
    if final is None:
        raise RuntimeError("The hint stream ended without a done event")
    if not final.get("visual_beats") and beats:
        final["visual_beats"] = beats
    return final


def _model_configuration(client: httpx.Client, parent_headers: dict) -> dict:
    if not parent_headers:
        return {"configured": None, "actual_upstream_models": None}
    response = client.get("/v1/families/ai-preferences", headers=parent_headers)
    response.raise_for_status()
    data = response.json()
    return {
        "configured": {
            key: data.get(key)
            for key in ("solver_model", "grading_model", "locator_model", "tutor_model")
        },
        "actual_upstream_models": None,
        "limitation": "Per-call upstream model IDs are not exposed by this API; attach the isolated server trace.",
    }


def attach_trace(output: Path, trace_path: Path) -> dict:
    """Attach only allowlisted metadata, without replaying a model request."""
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True:
        raise ValueError("Only synthetic acceptance artifacts are supported.")
    events = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
            if event.get("type") in {
                "model_start",
                "model_done",
                "model_error",
                "stream_start",
                "stream_done",
            }:
                events.append(
                    {
                        key: event[key]
                        for key in (
                            "at",
                            "type",
                            "call_id",
                            "configured_model",
                            "response_model",
                            "duration_seconds",
                            "usage",
                            "transport",
                        )
                        if key in event
                    }
                )
        except (ValueError, TypeError):
            continue
    attached = {}
    for case in manifest["cases"]:
        path = output / "results" / f"{case['id']}.json"
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if not record.get("completed_at"):
            continue
        start = datetime.fromisoformat(record["started_at"])
        end = datetime.fromisoformat(record["completed_at"])
        call_ids = {
            event.get("call_id")
            for event in events
            if event.get("at")
            and event["type"] in {"model_start", "stream_start"}
            and start <= datetime.fromisoformat(event["at"]) <= end
        }
        calls = [event for event in events if event.get("call_id") in call_ids]
        models = sorted({event["response_model"] for event in calls if event.get("response_model")})
        record["models"]["actual_upstream_models"] = models
        resolved_ids = {
            event.get("call_id")
            for event in calls
            if event.get("response_model") or event["type"] == "model_error"
        }
        record["models"]["unresolved_call_ids"] = sorted(call_ids - resolved_ids)
        record["models"]["upstream_identity_complete"] = bool(call_ids) and call_ids <= resolved_ids
        record["model_trace"] = {
            "file": str(trace_path),
            "association": "call start within this case's time window",
            "limitation": "Background preparation may overlap; this temporal association is not a request-ID trace.",
            "calls": calls,
        }
        write_json(path, record)
        attached[case["id"]] = models
    write_json(output / "model-trace-index.json", {"attached_at": utc_now(), "cases": attached})
    return attached


def rescore(output: Path) -> dict:
    """Recheck saved output with the current independent oracle; never call the API."""
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True:
        raise ValueError("Only synthetic acceptance artifacts are supported.")
    results = []
    for case in manifest["cases"]:
        path = output / "results" / f"{case['id']}.json"
        if not path.is_file():
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if not record.get("completed_at"):
            continue
        old = record.get("assessment") or {}
        error = next(
            (
                check["reason"]
                for check in old.get("checks", [])
                if check["name"] == "completed" and check["status"] == "fail"
            ),
            None,
        )
        record.setdefault("initial_assessment", old)
        record["assessment"] = evaluate(case, record.get("raw_api_response") or {}, error=error)
        record["status"] = record["assessment"]["status"]
        record["rescored_at"] = utc_now()
        write_json(path, record)
        results.append(
            {
                "case_id": case["id"],
                "status": record["status"],
                "result": str(path.relative_to(output)),
            }
        )
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary.setdefault("initial_counts", summary.get("counts"))
    summary["results"] = results
    summary["counts"] = {
        status: sum(item["status"] == status for item in results)
        for status in ("pass", "fail", "needs_review")
    }
    summary["rescored_at"] = utc_now()
    write_json(summary_path, summary)
    return summary


def run(output: Path, base_url: str, selected: list[str], limit: int, timeout: float) -> dict:
    base_url = _safe_base_url(base_url)
    token, session_id = os.getenv("ACCEPTANCE_CHILD_TOKEN"), os.getenv("ACCEPTANCE_SESSION_ID")
    parent_token = os.getenv("ACCEPTANCE_PARENT_TOKEN")
    if not token or not session_id:
        raise ValueError("ACCEPTANCE_CHILD_TOKEN and ACCEPTANCE_SESSION_ID are required.")
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("synthetic") is not True or manifest.get("version") != 1:
        raise ValueError("Only the generated synthetic manifest is supported.")
    cases = [case for case in manifest["cases"] if not selected or case["id"] in selected][:limit]
    if not cases:
        raise ValueError("No synthetic cases were selected.")
    child_headers = {"Authorization": "Bearer " + token}
    parent_headers = {"Authorization": "Bearer " + parent_token} if parent_token else {}
    summary = {
        "started_at": utc_now(),
        "completed_at": None,
        "synthetic": True,
        "base_url": base_url,
        "results": [],
        "counts": {},
        "scoring": "Independent hard gates and explicit needs_review; no model self-score or teacher-level total.",
    }
    write_json(output / "summary.json", summary)
    with httpx.Client(base_url=base_url, timeout=timeout) as client:
        try:
            verify_acceptance_api(client, manifest.get("run_id") or output.resolve().parent.name)
            summary["models"] = _model_configuration(client, parent_headers)
        except (httpx.HTTPError, ValueError) as exc:
            summary.update(
                completed_at=utc_now(), status="setup_failed", setup_error=type(exc).__name__
            )
            write_json(output / "summary.json", summary)
            raise
        for case in cases:
            started_at, started = utc_now(), time.monotonic()
            result_path = output / "results" / f"{case['id']}.json"
            record = {
                "case_id": case["id"],
                "mode": case["mode"],
                "started_at": started_at,
                "completed_at": None,
                "status": "running",
                "models": summary["models"],
            }
            write_json(result_path, record)
            append_event(output / "events.jsonl", {"case_id": case["id"], "event": "started"})
            raw, error = {}, None
            try:
                image_path = (output / case["image"]).resolve()
                if not image_path.is_relative_to((output / "papers").resolve()):
                    raise ValueError(
                        "Synthetic image must remain under the generated papers directory."
                    )
                image = image_path.read_bytes()
                if case["mode"] == "hint":
                    raw = _stream_hint(
                        client, case, image, session_id, child_headers, output / "events.jsonl"
                    )
                else:
                    if parent_headers:
                        approval = client.patch(
                            f"/v1/sessions/{session_id}/correction-access",
                            json={"allowed": True},
                            headers=parent_headers,
                        )
                        approval.raise_for_status()
                    response = client.post(
                        f"/v1/sessions/{session_id}/corrections",
                        data={"subject_label": case["subject"]},
                        files={"image": ("synthetic.jpg", image, "image/jpeg")},
                        headers=child_headers,
                    )
                    response.raise_for_status()
                    raw = response.json()
            except (httpx.HTTPError, ValueError, RuntimeError, OSError) as exc:
                status = (
                    exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                )
                error = f"{type(exc).__name__}" + (f" HTTP {status}" if status else "")
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        raw = {"http_error": status, "body": exc.response.json()}
                    except ValueError:
                        raw = {"http_error": status}
            assessment = evaluate(case, raw, error=error)
            record.update(
                completed_at=utc_now(),
                elapsed_seconds=round(time.monotonic() - started, 3),
                status=assessment["status"],
                assessment=assessment,
                raw_api_response=raw,
            )
            write_json(result_path, record)
            summary["results"].append(
                {
                    "case_id": case["id"],
                    "status": record["status"],
                    "result": str(result_path.relative_to(output)),
                }
            )
            summary["counts"] = {
                status: sum(item["status"] == status for item in summary["results"])
                for status in ("pass", "fail", "needs_review")
            }
            write_json(output / "summary.json", summary)
            append_event(
                output / "events.jsonl",
                {"case_id": case["id"], "event": "completed", "status": record["status"]},
            )
            print(
                json.dumps(
                    {
                        "case_id": case["id"],
                        "status": record["status"],
                        "elapsed_seconds": record["elapsed_seconds"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
    summary["completed_at"] = utc_now()
    write_json(output / "summary.json", summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser(
        "prepare", help="Render the six synthetic worksheets without model calls."
    )
    builder.add_argument("--output", type=Path, required=True)
    builder.add_argument("--font")
    runner = commands.add_parser("run", help="Evaluate an isolated local synthetic session.")
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--base-url", required=True)
    runner.add_argument("--case", action="append", default=[])
    runner.add_argument("--limit", type=int, choices=range(1, 7), default=6)
    runner.add_argument("--timeout", type=float, default=300)
    tracer = commands.add_parser(
        "attach-trace", help="Attach the isolated server's model metadata without model calls."
    )
    tracer.add_argument("--output", type=Path, required=True)
    tracer.add_argument("--model-trace", type=Path, required=True)
    reviewer = commands.add_parser(
        "rescore", help="Recheck saved responses without API or model calls."
    )
    reviewer.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            manifest = prepare(args.output, args.font)
            print(
                json.dumps(
                    {
                        "manifest": str(args.output / "manifest.json"),
                        "cases": [case["id"] for case in manifest["cases"]],
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "attach-trace":
            attached = attach_trace(args.output, args.model_trace)
            print(json.dumps({"attached_cases": list(attached)}, ensure_ascii=False))
            return 0
        if args.command == "rescore":
            summary = rescore(args.output)
            print(json.dumps({"counts": summary["counts"]}, ensure_ascii=False))
            return 1 if summary["counts"].get("fail") else 0
        summary = run(args.output, args.base_url, args.case, args.limit, args.timeout)
        return 1 if summary["counts"].get("fail") else 0
    except (ValueError, OSError, httpx.HTTPError) as exc:
        # Do not print exception URLs, request headers, environment or credentials.
        print(
            json.dumps(
                {
                    "error": type(exc).__name__,
                    "message": "Acceptance setup failed; check isolated API, manifest and credential environment.",
                }
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
