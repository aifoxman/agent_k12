"""Exercise genuine tutoring/correction APIs with a reference-free request allowlist."""
from __future__ import annotations

import json
import hashlib
import re
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from scripts.acceptance.bootstrap import bootstrap, verify_acceptance_api
from scripts.question_bank.budget import Budget
from scripts.question_bank.corpus import dump, read
from scripts.question_bank.gates import apply as apply_gates
from scripts.question_bank.simulation import rendered_text_regions, safe_image
from scripts.question_bank.teacher import RUBRIC, RUBRIC_VERSION, assess_review, call_teacher, distill, evaluation_contract, reference_view

SUBJECT_LABELS = {"math": "数学", "chinese": "语文", "english": "英语", "science": "科学"}
GRADE_LABELS = dict(enumerate(["", *[n + "年级" for n in "一二三四五六"]]))


def safe_base(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.port == 8003 or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Only a dedicated loopback acceptance API is allowed")
    return value.rstrip("/")


def valid_tag(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", value):
        raise ValueError("Use a bounded artifact tag without path separators")
    return value


def forms(mode: str, session: str, previous_id: str | None = None) -> dict:
    """The sole production-input boundary: no oracle, answer, source text or coordinates."""
    if mode not in {"hint1", "hint2"}:
        raise ValueError("Unknown tutoring mode")
    result = {"session_id": session, "question": "请先读题审题，讲清这道题的关键条件、关系和做法。",
              "level": 1}
    if mode == "hint2":
        if not previous_id:
            raise ValueError("Second tier requires the genuine first hint ID")
        result.update(level=2, previous_hint_id=previous_id,
                      question="我还不明白，请把这道题完整解答讲清楚，包括每步为什么、结论和检查。")
    else:
        result["selected_region"] = "true"
    return result


def events(lines):
    name, data = "", []
    for line in lines:
        if line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif not line and data:
            yield name, json.loads("\n".join(data))
            name, data = "", []
    # An unterminated SSE event must not be treated as a completed delivery.


def meaningful(beat: dict, timings: dict, elapsed: float):
    if not isinstance(beat, dict):
        return
    if any(m.get("text") and m.get("kind") not in {"locator", "question"} for m in beat.get("page", []) if isinstance(m, dict)):
        timings.setdefault("first_effective_mark_ms", elapsed)
    if any(m.get("text") for m in beat.get("scratch", []) if isinstance(m, dict)):
        timings.setdefault("first_thinking_ms", elapsed)
    if beat.get("speech"):
        timings.setdefault("first_speech_text_ms", elapsed)


def speech(base: str, headers: dict, text: str, destination: Path, start: float):
    timings, chunks = {}, []
    with httpx.Client(base_url=base, timeout=70, trust_env=False) as client:
        with client.stream("POST", "/v1/audio/speech", headers={**headers, "Idempotency-Key": uuid.uuid4().hex},
                           json={"model": "k12-tts", "voice": "teacher-warm-female", "input": text[:180],
                                 "response_format": "pcm", "stream_format": "audio", "speed": 1}) as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                if chunk:
                    timings.setdefault("first_audio_packet_ms", round((time.monotonic() - start) * 1000))
                    chunks.append(chunk)
    if not chunks:
        raise ValueError("TTS returned no audio")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"".join(chunks))
    return {**timings, "pcm_bytes": sum(map(len, chunks)), "human_listening": False}


def prepared_form(index: dict) -> dict:
    questions = index.get("questions") or []
    if len(questions) != 1:
        raise ValueError("Warm selection must be an unambiguous backend-indexed single question")
    question = questions[0]
    x, y, width, height = question["box"]
    return {"prepared_question_id": question["id"],
            "prepared_geometry": json.dumps({"page_id": index["page_id"], "frame_sha256": index["frame_sha256"],
                                               "matrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]}),
            "selected_strokes": json.dumps([[[x + width * .15, y + height * .5], [x + width * .75, y + height * .5]]])}


def prewarm(client, session, headers, image):
    start = time.monotonic()
    prepared = client.post(f"/v1/sessions/{session}/page-prep", headers=headers,
                           files={"image": ("simulated.jpg", image, "image/jpeg")})
    prepared.raise_for_status()
    snapshot = None
    with client.stream("POST", f"/v1/sessions/{session}/page-prep/{prepared.json()['page_id']}/stream", headers=headers) as response:
        response.raise_for_status()
        for name, data in events(response.iter_lines()):
            if name in {"index", "done"}:
                snapshot = data
                questions = data.get("questions") or []
                if (len(questions) == 1 and questions[0].get("reading_ready")
                        and (questions[0].get("reading") or {}).get("page")):
                    break  # Match a child asking once scan-time reading is ready.
    if not snapshot or snapshot.get("frame_sha256") != hashlib.sha256(image).hexdigest():
        raise ValueError("Prepared index does not match this exact simulated frame")
    questions = snapshot.get("questions") or []
    if len(questions) != 1 or not questions[0].get("reading_ready") or not (questions[0].get("reading") or {}).get("page"):
        raise ValueError("A genuine warm reading cache was not available")
    return prepared_form(snapshot), {"preparation_ms": round((time.monotonic() - start) * 1000),
                                      "index": snapshot}


def hint(client, mode, session, headers, image, previous=None, *, audio_path=None, prepared=None):
    start, final, timeline, timings = time.monotonic(), None, [], {}
    audio_future = None
    with ThreadPoolExecutor(max_workers=1) as audio_pool:
        form = forms(mode, session, previous)
        if prepared and mode == "hint1":
            form.pop("selected_region", None)
            form.update(prepared)
        with client.stream("POST", "/v1/hints/stream", headers=headers, data=form,
                           files={"image": ("simulated.jpg", image, "image/jpeg")}) as response:
            response.raise_for_status()
            for name, payload in events(response.iter_lines()):
                elapsed = round((time.monotonic() - start) * 1000)
                timeline.append({"event": name, "elapsed_ms": elapsed, "data": payload})
                if name == "target":
                    meaningful(payload.get("reading") or {}, timings, elapsed)
                if name in {"beat", "visual", "reading_marks"}:
                    meaningful(payload, timings, elapsed)
                    text = payload.get("speech")
                    if audio_path and text and audio_future is None:
                        audio_future = audio_pool.submit(speech, str(client.base_url), headers, text, audio_path, start)
                if name == "done":
                    final = payload
                if name == "error":
                    timings["stream_error"] = True
        if audio_future:
            try:
                timings.update(audio_future.result())
            except Exception as exc:
                timings["audio_error"] = type(exc).__name__
    if final is None:
        raise ValueError("Hint stream did not deliver a done event")
    timings["total_ms"] = round((time.monotonic() - start) * 1000)
    return final, timeline, timings


def manifest(output: Path, split: str):
    path = output / "manifest.json" if split == "development" else output / "private" / "holdout" / "manifest.json"
    value = read(path)
    if value.get("simulated_camera") is not True or value.get("split") != split or value.get("run_id") != output.name:
        raise ValueError("Mismatched corpus manifest")
    return value


def bootstrap_bounded(base: str, api_run: Path, case: dict, image: Path, scope_name: str):
    """Retry a temporary setup failure once; persist negative vision evidence."""
    setup_errors = api_run / "private" / "setup-errors" / f"{scope_name}.json"
    attempts = read(setup_errors) if setup_errors.exists() else []
    if len(attempts) >= 2 or any(a.get("terminal") for a in attempts):
        raise RuntimeError("Visual setup previously failed; evidence retained, no further automatic retries")
    for attempt in range(len(attempts), 2):
        try:
            state = bootstrap(base, "http://127.0.0.1:5186/app/", api_run,
                              image,
                              state_name=f"{scope_name}.json", setup_result_name=f"setup-{scope_name}.json",
                              grade=GRADE_LABELS[case["grade"]], evaluation_trace=f"setup:{case['id']}")
            return state
        except (httpx.HTTPError, RuntimeError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            temporary = isinstance(exc, httpx.RequestError) or status in {502, 503, 504}
            attempts.append({"attempt": attempt + 1, "error": type(exc).__name__, "status": status,
                             "terminal": not temporary, "at": time.time()})
            dump(setup_errors, attempts)
            if not temporary or attempt == 1:
                raise


def run_case(output: Path, base: str, api_run: Path, case: dict, round_id: int, tag: str,
             modes: list[str], variant="clean", audio=False, cache="cold"):
    base = safe_base(base)
    valid_tag(tag)
    budget = Budget(output / "budget.sqlite3")
    split = case["split"]
    result_root = output / "results" if split == "development" else output / "private" / "holdout" / "results"
    scope_name = f"corpus-{tag}-{case['id']}-{variant}-{cache}"
    state_path = api_run / "private" / f"{scope_name}.json"
    with httpx.Client(base_url=base, timeout=240, trust_env=False) as client:
        verify_acceptance_api(client, api_run.name)
        health = client.get("/health")
        actual_source = health.headers.get("X-K12-Acceptance-Source")
        if split == "holdout":
            from scripts.question_bank.seal import verify
            verify(output, tag, Path(__file__).resolve().parents[2] / "src" / "k12_server", actual_source)
        if state_path.exists():
            prior = read(state_path)
            if prior.get("synthetic") is not True or prior.get("run_id") != api_run.name or prior.get("base_url") != base:
                raise ValueError("Saved state does not belong to this isolated API")
            dashboard = client.get("/v1/families/dashboard", headers={"Authorization": "Bearer " + prior["child_token"]})
            active = (dashboard.json().get("session") or {}) if dashboard.is_success else {}
            if active.get("id") != prior.get("session_id") or active.get("status") != "active":
                state_path.replace(state_path.with_name(state_path.stem + ".expired-" + uuid.uuid4().hex[:8] + ".json"))
    # Real camera/setup preflight and pairing; no copied family or forged ticket.
    if state_path.exists():
        state = read(state_path)
    else:
        state = bootstrap_bounded(base, api_run, case, safe_image(output, case["images"]["hint"]), scope_name)
    if not state_path.exists():
        original = api_run / f"{scope_name}.json"
        state_path.parent.mkdir(exist_ok=True)
        original.replace(state_path)
    if state.get("synthetic") is not True or state.get("run_id") != api_run.name or state.get("base_url") != base:
        raise ValueError("State is not owned by this isolated run")
    parent = {"Authorization": "Bearer " + state["parent_token"]}
    child = {"Authorization": "Bearer " + state["child_token"]}
    if audio:
        with httpx.Client(base_url=base, timeout=20, trust_env=False) as audio_client:
            permission = audio_client.patch("/v1/families/voice-preferences", headers=parent, json={"enabled": True})
            permission.raise_for_status()
    previous, prepared, preparation = None, None, None
    results = []
    for mode in modes:
        identifier = f"{case['id']}.{mode}.{variant}" + (".warm" if cache == "warm" else "")
        path = result_root / tag / f"{identifier}.json"
        if path.exists():
            record = read(path)
            if record.get("completed"):
                if mode == "hint1": previous = record.get("response", {}).get("id")
                results.append(record)
                continue
        task_reservation = budget.reserve(round_id, "task", f"{tag}:{identifier}")
        trace = f"{tag}:{identifier}"
        image_key = variant if mode.startswith("hint") and variant != "clean" else "hint" if mode.startswith("hint") else mode
        image_path = safe_image(output, case["images"][image_key])
        input_bytes = image_path.read_bytes()
        input_copy = path.with_suffix(".input.jpg")
        input_copy.parent.mkdir(parents=True, exist_ok=True)
        input_copy.write_bytes(input_bytes)
        record = {"case_id": case["id"], "mode": mode, "variant": variant, "tag": tag, "trace": trace,
                  "split": split, "grade": case["grade"], "subject": case["subject"], "type": case["type"], "cache": cache,
                  "source_sha256": case["source_sha256"], "image": input_copy.relative_to(output).as_posix(),
                  "input_sha256": hashlib.sha256(input_bytes).hexdigest(), "result_key": path.name,
                  "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  "backend_source_hash": actual_source,
                  "state_file": str(state_path),
                  "completed": False, "response": {}}
        dump(path, record)
        start = time.monotonic()
        stage = "request"
        try:
            with httpx.Client(base_url=base, timeout=240, trust_env=False) as client:
                headers = {**child, "X-K12-Evaluation-Trace": trace, "X-K12-Evaluation-Task": str(task_reservation)}
                if mode in {"hint1", "hint2"}:
                    if cache == "warm" and mode == "hint1":
                        stage = "reading_preparation"
                        prepared, preparation = prewarm(client, state["session_id"], {**headers, "X-K12-Evaluation-Trace": f"prep:{trace}"}, input_bytes)
                        record["preparation"] = preparation
                    stage = "hint_stream"
                    response, timeline, timings = hint(client, mode, state["session_id"], headers,
                        input_bytes, previous, audio_path=path.with_suffix(".pcm") if audio and mode == "hint1" else None,
                        prepared=prepared)
                    record.update(response=response, events=timeline, timings=timings)
                    if mode == "hint1": previous = response.get("id")
                    if response.get("delivery_complete") is False or not response.get("located"):
                        record["error"] = "incomplete_or_unlocated_teaching"
                else:
                    stage = "correction"
                    approval = client.patch(f"/v1/sessions/{state['session_id']}/correction-access", headers=parent, json={"allowed": True})
                    approval.raise_for_status()
                    response = client.post(f"/v1/sessions/{state['session_id']}/corrections", headers=headers,
                        data={"subject_label": SUBJECT_LABELS[case["subject"]]},
                        files={"image": ("simulated.jpg", input_bytes, "image/jpeg")})
                    response.raise_for_status()
                    record["response"] = response.json()
        except Exception as exc:
            record["error"] = type(exc).__name__
            record["error_stage"] = stage
            if isinstance(exc, ValueError) and str(exc) in {
                "Prepared index does not match this exact simulated frame",
                "A genuine warm reading cache was not available",
                "Hint stream did not deliver a done event",
                "Warm selection must be an unambiguous backend-indexed single question",
            }:
                record["error_detail"] = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                record["http_status"] = exc.response.status_code
        record.update(completed=True, total_ms=round((time.monotonic() - start) * 1000))
        dump(path, record)
        print(json.dumps({"case": case["id"], "mode": mode, "error": record.get("error"), "ms": record["total_ms"]}), flush=True)
        results.append(record)
    return results


def run(output: Path, base: str, api_run: Path, round_id: int, tag: str, *, split="development",
        modes=None, selected=None, limit=60, variant="clean", audio=False, cache="cold"):
    cases = [c for c in manifest(output, split)["cases"] if not selected or c["id"] in selected][:limit]
    if not cases:
        raise ValueError("No cases selected")
    modes = modes or ["hint1", "hint2", "correct", "wrong", "blank"]
    results = []
    base_output = output if split == "development" else output / "private" / "holdout"
    plan = {"cases": [c["id"] for c in cases], "modes": modes, "variant": variant, "cache": cache,
            "expected_tasks": len(cases) * len(modes)}
    dump(base_output / "plans" / f"{valid_tag(tag)}.json", plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(run_case, output, base, api_run, case, round_id, tag, modes, variant, audio, cache): case for case in cases}
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception as exc:
                from scripts.question_bank.budget import BudgetExceeded
                if isinstance(exc, BudgetExceeded):
                    raise
                dump(base_output / "private" / "case-setup-failures" / tag / f"{futures[future]['id']}.json",
                     {"case_id": futures[future]["id"], "error": type(exc).__name__, "task_requests_not_started": True})
                print({"case": futures[future]["id"], "setup_failed": type(exc).__name__}, flush=True)
    return results


def review(output: Path, settings, round_id: int, tag: str, *, split="development", limit=300):
    valid_tag(tag)
    if split == "holdout":
        from scripts.question_bank.seal import verify
        verify(output, tag, Path(__file__).resolve().parents[2] / "src" / "k12_server")
    base = output if split == "development" else output / "private" / "holdout"
    def review_one(path):
        record = read(path)
        if not record.get("completed"):
            return None
        destination = base / "reviews" / tag / path.name
        if destination.exists():
            prior = read(destination)
            if prior.get("rubric_version") == RUBRIC_VERSION:
                assessment = apply_gates(record, prior)
                dump(destination, assessment)
                return assessment
            archive = base / "private" / "prior-reviews" / tag / (path.stem + "." + prior.get("rubric_version", "legacy") + ".json")
            dump(archive, prior)
        from scripts.question_bank.gates import hard_checks
        if record.get("error") or hard_checks(record):
            assessment = {"verdict": "fail", "reference_reliable": False, "scores": {},
                          "findings": [], "mean_score": None,
                          "unassessed": ["Content not scored because observable delivery gates failed."]}
        else:
            oracle = read(base / "private" / "oracles" / f"{record['case_id']}.json")
            teacher_reference = reference_view(oracle)
            teacher_reference["expected_text_regions_in_current_input"] = rendered_text_regions(
                oracle, safe_image(output, record["image"]), output, record["variant"])
            assessment = None
            errors = []
            for _attempt in range(2):
                try:
                    assessment = call_teacher(settings, Budget(output / "budget.sqlite3"), round_id,
                        f"review:{tag}:{record['case_id']}:{record['mode']}", RUBRIC,
                        {"evaluation_contract": evaluation_contract(record["mode"]),
                         "task_kind": evaluation_contract(record["mode"])["task"],
                         "grade": record["grade"], "subject": record["subject"],
                         "reference": teacher_reference, "observed": record["response"], "api_timings": record.get("timings"),
                         "visual_evidence": "API structure only; actual display and audio synchrony unassessed"},
                         safe_image(output, record["image"]))
                    assessment = assess_review(assessment, record["response"], record["mode"])
                    if not assessment.get("mode_conflict") or _attempt == 1:
                        break
                    errors.append({"attempt": _attempt + 1, "error": "teacher_mode_conflict"})
                    dump(base / "private" / "review-adjudications" / tag / (path.stem + f".attempt-{_attempt + 1}.json"), assessment)
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                    errors.append({"attempt": _attempt + 1, "error": type(exc).__name__})
                    dump(base / "private" / "review-errors" / tag / path.name, errors)
            if assessment is None:
                assessment = {"verdict": "needs_review", "reference_reliable": False, "scores": {},
                              "findings": [], "mean_score": None, "review_service_errors": errors}
        assessment = apply_gates(record, assessment)
        if record["mode"] == "mixed":
            from scripts.question_bank.batch import score
            coverage = score(record["response"], read(base / "private" / "oracles" / f"{record['case_id']}.json"))
            assessment["batch_coverage"] = coverage
            if coverage["status"] == "fail":
                assessment["verdict"] = "fail"
                assessment.setdefault("findings", []).append({"severity": "major", "path": "checks",
                    "reason": "Independent spatial coverage/state checks failed", "source": "batch_gate"})
            elif coverage["status"] == "needs_review" and assessment["verdict"] == "pass":
                assessment["verdict"] = "needs_review"
        assessment.update(case_id=record["case_id"], mode=record["mode"], tag=tag, image=record["image"], rubric_version=RUBRIC_VERSION)
        dump(destination, assessment)
        if split == "development":
            distill(record, assessment, output / "distillation" / tag / path.name)
        return assessment
    results = []
    paths = sorted((base / "results" / tag).glob("*.json"))[:limit]
    with ThreadPoolExecutor(max_workers=2) as pool:
        for future in as_completed([pool.submit(review_one, path) for path in paths]):
            assessment = future.result()
            if assessment:
                results.append(assessment)
                print({"reviewed": len(results), "case": assessment["case_id"], "mode": assessment["mode"], "verdict": assessment["verdict"]}, flush=True)
    return results
