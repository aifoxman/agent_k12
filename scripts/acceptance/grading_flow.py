"""Exercise grading permissions and review on a separate synthetic family.

Camera input is a saved fixture. Generated arithmetic sheets are explicitly
simulated student work. This is API evidence, not a live camera or UI verdict.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from bootstrap import bootstrap, verify_acceptance_api
from PIL import Image, ImageDraw, ImageFont


def now() -> str:
    return datetime.now(UTC).isoformat()


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def safe_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1"}
            or parsed.port == 8003 or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"}):
        raise ValueError("Use a dedicated loopback acceptance API; 8003 is excluded")
    return value.rstrip("/")


def prepare(directory: Path, source: Path) -> dict:
    """Keep the camera source untouched and render unrelated code-native examples."""
    output = directory / "grading"
    output.mkdir(parents=True, exist_ok=True)
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 38) if font_path.exists() else ImageFont.load_default(38)
    answer_font = ImageFont.truetype(str(font_path), 44) if font_path.exists() else font
    image = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 35), "模拟作业 · 非真实学生数据", font=font, fill="#333333")
    rows = [
        {"id": 1, "printed": "1. 8 + 7 =", "written": "15", "reference": str(8 + 7),
         "expected_status": "looks_correct", "y": 235},
        {"id": 2, "printed": "2. 24 ÷ 6 =", "written": "5", "reference": str(24 // 6),
         "expected_status": "check_again", "y": 465},
        {"id": 3, "printed": "3. 9 × 4 =", "written": "", "reference": str(9 * 4),
         "expected_status": "blank", "y": 695},
    ]
    for row in rows:
        y = row["y"]
        draw.text((100, y), row["printed"], font=font, fill="black")
        draw.rectangle((590, y - 10, 880, y + 75), outline="#777777", width=2)
        if row["written"]:
            draw.text((640, y), row["written"], font=answer_font, fill="#1741a1")
        row["answer_box"] = [590 / 1200, (y - 10) / 900, 290 / 1200, 85 / 900]
    sheet = output / "simulated-answers.jpg"
    image.save(sheet, quality=96)
    manifest = {
        "schema_version": 1, "prepared_at": now(), "run_id": directory.name,
        "mode": "saved_camera_and_code_native_simulation", "synthetic_family": True,
        "source_camera": str(source.resolve()),
        "source_camera_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "source_has_student_answer": False, "simulated_sheet": str(sheet.resolve()),
        "oracle_boundary": "Rows/reference values are evaluator-only, never sent in model prompts.",
        "rows": rows,
    }
    write(output / "manifest.json", manifest)
    return manifest


def score_blank(result: dict) -> list[dict]:
    checks = result.get("checks") or []
    findings = []
    if not checks:
        return [{"id": "blank_recognition", "status": "needs_review",
                 "detail": "No check returned; nonjudgment is safe but blank coverage is unproved."}]
    invented = [check for check in checks if check.get("answer_state") == "written"
                or str(check.get("answer") or "").strip() not in {"", "未作答", "空白", "无作答"}
                or check.get("status") == "looks_correct"]
    findings.append({"id": "blank_not_invented", "status": "failed" if invented else "passed",
                     "detail": "Printed givens must not become a student's answer."})
    if any(check.get("answer_state") == "blank" and check.get("status") == "check_again" for check in checks):
        findings.append({"id": "blank_policy", "status": "passed",
                         "detail": "Disclosed policy: confirmed blank work is counted as unfinished/incorrect; "
                                   "this is not evidence that the student wrote an incorrect calculation."})
    return findings


def match_row(check: dict, rows: list[dict]) -> int | None:
    question = str(check.get("question") or "")
    number = re.search(r"(?:第\s*)?([123])\s*(?:[.、题项]|$)", question)
    if number:
        return int(number[1])
    box = check.get("box")
    if isinstance(box, list) and len(box) == 4:
        center = float(box[1]) + float(box[3]) / 2
        candidates = sorted((abs(center - (row["y"] + 30) / 900), row["id"]) for row in rows)
        if candidates[0][0] <= .09:
            return candidates[0][1]
    return None


def score_sheet(result: dict, rows: list[dict]) -> list[dict]:
    mapped: dict[int, list[dict]] = {}
    for check in result.get("checks") or []:
        row_id = match_row(check, rows)
        if row_id:
            mapped.setdefault(row_id, []).append(check)
    findings = []
    for row in rows:
        matches = mapped.get(row["id"], [])
        if len(matches) != 1:
            findings.append({"id": f"row_{row['id']}", "status": "needs_review",
                             "detail": "Could not uniquely identify this row in returned checks."})
            continue
        check = matches[0]
        if row["expected_status"] == "blank":
            findings.extend({**item, "id": f"row_3_{item['id']}"} for item in score_blank({"checks": [check]}))
            continue
        if check.get("status") == "unclear":
            status, detail = "needs_review", "Model abstained on a legible simulated answer."
        else:
            correct = check.get("status") == row["expected_status"] and str(check.get("answer")) == row["written"]
            reference = str(check.get("reference_answer") or "")
            if reference and not re.search(rf"(?<!\d){re.escape(row['reference'])}(?!\d)", reference):
                correct = False
            status, detail = ("passed" if correct else "failed"), "Compare independently calculated truth and visible answer."
        findings.append({"id": f"row_{row['id']}", "status": status, "detail": detail})
    return findings


class Flow:
    def __init__(self, directory: Path, state: dict, timeout: float):
        self.directory, self.state = directory, state
        self.output = directory / "grading"
        self.client = httpx.Client(base_url=safe_base_url(state["base_url"]), timeout=timeout, follow_redirects=False)
        self.secrets = [str(state.get(key) or "") for key in ("parent_token", "child_token", "device_secret")]
        self.report = {"schema_version": 1, "started_at": now(), "synthetic_family": True,
                       "mode": "saved_fixture_live_model_api", "ui_evaluated": False,
                       "camera_live": False, "status": "running", "stages": [], "findings": []}

    def clean(self, value):
        if isinstance(value, dict):
            return {key: ("[REDACTED]" if key in {"token", "setup_ticket", "device_secret"} else self.clean(item))
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            for secret in self.secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
        return value

    def save(self) -> None:
        write(self.output / "report.json", self.clean(self.report))

    def request(self, name: str, method: str, path: str, role: str, expected: int = 200, image: Path | None = None,
                **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {self.state[role + '_token']}"}
        if image:
            kwargs["files"] = {"image": (image.name, image.read_bytes(), "image/jpeg")}
        started, clock = now(), time.monotonic()
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": started, "type": "request_started", "name": name,
                                     "method": method, "path": path, "role": role}) + "\n")
        response = self.client.request(method, path, headers=headers, **kwargs)
        try:
            raw = response.json()
        except ValueError:
            raw = {"content_type": response.headers.get("content-type"), "bytes": len(response.content)}
        event = self.clean({"at": started, "completed_at": now(), "name": name, "method": method, "path": path,
                           "role": role, "http_status": response.status_code,
                           "duration_seconds": round(time.monotonic() - clock, 3), "response": raw})
        with (self.output / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        write(self.output / (name + ".json"), event)
        self.report["stages"].append({"name": name, "status": "passed" if response.status_code == expected else "failed",
                                      "http_status": response.status_code, "expected": expected})
        self.save()
        if response.status_code != expected:
            raise RuntimeError(f"{name}: HTTP {response.status_code}, expected {expected}")
        return raw

    def gate(self, identifier: str, passed: bool, detail: str) -> None:
        self.report["findings"].append({"id": identifier, "status": "passed" if passed else "failed", "detail": detail})
        self.save()

    def execute(self, manifest: dict) -> dict:
        verify_acceptance_api(self.client, self.directory.name)  # No credentials before run identity.
        sid = self.state["session_id"]
        session_path = f"/v1/sessions/{sid}"
        source, sheet = Path(manifest["source_camera"]), Path(manifest["simulated_sheet"])
        correction_path = session_path + "/corrections"
        self.request("before-approval", "POST", correction_path, "child", 403, image=source)
        self.request("child-request", "POST", session_path + "/correction-request", "child")
        parent_dash = self.request("parent-sees-request", "GET", "/v1/families/dashboard", "parent")
        self.gate("request_visible", bool((parent_dash.get("session") or {}).get("correction_requested_at")),
                  "Parent dashboard records a pending grading request.")
        self.request("child-cannot-authorize", "PATCH", session_path + "/correction-access", "child", 403,
                     json={"allowed": True})
        self.request("parent-authorizes", "PATCH", session_path + "/correction-access", "parent", json={"allowed": True})
        blank = self.request("camera-blank-grading", "POST", correction_path, "child", image=source)
        self.report["findings"].extend(score_blank(blank))
        self.save()
        graded = self.request("simulated-answers-grading", "POST", correction_path, "child", image=sheet)
        self.report["findings"].extend(score_sheet(graded, manifest["rows"]))
        self.save()
        cid = graded["id"]
        self.report["correction_ids"] = {"blank": blank["id"], "simulated": cid}
        checks = graded.get("checks") or []
        candidate = next(((index, item) for index, item in enumerate(checks)
                          if match_row(item, manifest["rows"]) == 2), None)
        if candidate:
            index, original = candidate
            check_path = f"/v1/corrections/{cid}/checks/{index}"
            recheck = self.request("parent-recheck", "POST", check_path + "/recheck", "parent")
            recheck_truth = score_sheet({"checks": [recheck["check"]]}, [manifest["rows"][1]])
            self.report["findings"].extend({**item, "id": "recheck_" + item["id"]} for item in recheck_truth)
            self.gate("original_review_retained", bool(recheck.get("check", {}).get("original_review")),
                      "Independent recheck retains the initial judgment.")
            current = recheck["check"]
            body = {"status": "check_again", "answer": "5", "note": "模拟家长核对：24除以6应为4，纸上填写5。",
                    "box": manifest["rows"][1]["answer_box"], "revision": current.get("parent_revision", 0)}
            self.request("child-cannot-parent-review", "PATCH", check_path + "/parent-review", "child", 403, json=body)
            corrected = self.request("parent-corrects", "PATCH", check_path + "/parent-review", "parent", json=body)
            saved = corrected.get("check") or {}
            self.gate("review_history_retained", bool(saved.get("original_review") and saved.get("ai_review")
                                                       and saved.get("parent_review_history")),
                      "Parent review preserves AI evidence and revision history.")
            self.request("stale-parent-revision", "PATCH", check_path + "/parent-review", "parent", 409, json=body)
        else:
            self.report["findings"].append({"id": "review_flow", "status": "needs_review",
                                             "detail": "Second row not reliably identified; no arbitrary row edited."})
        self.request("parent-accepts", "PATCH", f"/v1/corrections/{cid}", "parent", json={"review_status": "accepted"})
        self.request("parent-revokes", "PATCH", session_path + "/correction-access", "parent", json={"allowed": False})
        hidden = self.request("revoked-child-dashboard", "GET", "/v1/families/dashboard", "child")
        self.gate("revoked_results_hidden", not hidden.get("corrections"), "Revoked grading results are hidden from the child.")
        self.request("revoked-child-image", "GET", f"/v1/corrections/{cid}/image", "child", 403)
        self.request("parent-reallows", "PATCH", session_path + "/correction-access", "parent", json={"allowed": True})
        self.request("child-pauses", "PATCH", session_path, "child", json={"status": "paused"})
        self.request("paused-child-cannot-grade", "POST", correction_path, "child", 409, image=sheet)
        paused = self.request("paused-dashboard", "GET", "/v1/families/dashboard", "child")
        self.gate("pause_revokes_approval", (paused.get("session") or {}).get("status") == "paused"
                  and not (paused.get("session") or {}).get("correction_authorized_until") and not paused.get("corrections"),
                  "Pausing revokes permission and hides active grading results.")
        self.request("resume-needs-preflight", "PATCH", session_path, "child", 403, json={"status": "active"})
        setup = self.request("resume-visual-preflight", "POST", "/v1/setup/check", "child", image=source)
        if setup.get("ready") and setup.get("setup_ticket"):
            self.request("resume-with-proof", "PATCH", session_path, "child",
                         json={"status": "active", "setup_ticket": setup["setup_ticket"]})
            self.request("resume-still-needs-approval", "POST", correction_path, "child", 403, image=sheet)
        else:
            self.report["findings"].append({"id": "resume", "status": "needs_review", "detail": "Visual preflight did not pass."})
        self.request("finish-session", "POST", session_path + "/finish", "child")
        parent_final = self.request("parent-final-dashboard", "GET", "/v1/families/dashboard", "parent")
        final = next((item for item in parent_final.get("corrections", []) if item.get("id") == cid), None)
        self.gate("parent_review_persisted", bool(final and final.get("review_status") == "accepted"),
                  "Accepted results remain in the parent dashboard after finishing.")
        return self.report


def run(directory: Path, base_url: str, frontend_url: str, source: Path, timeout: float) -> dict:
    base_url = safe_base_url(base_url)
    manifest = prepare(directory, source)
    private = directory / "private" / "grading-state.json"
    private.parent.mkdir(parents=True, exist_ok=True)
    # Each execution finishes its session. Provision a fresh isolated family on
    # the next execution instead of mutating the tutoring state or reusing a
    # finished session. Bootstrap's temporary own-state file is moved private.
    state = bootstrap(base_url, frontend_url, directory, source, "grading-state.json", "grading-setup-result.json")
    (directory / "grading-state.json").replace(private)
    private.chmod(0o600)
    flow = Flow(directory, state, timeout)
    try:
        flow.execute(manifest)
    except Exception as error:
        flow.report["error"] = flow.clean(f"{type(error).__name__}: {error}")
    finally:
        flow.client.close()
        flow.report["completed_at"] = now()
        results = flow.report["stages"] + flow.report["findings"]
        flow.report["status"] = ("failed" if flow.report.get("error") or any(item["status"] == "failed" for item in results)
                                  else "needs_review" if any(item["status"] == "needs_review" for item in results) else "passed")
        flow.save()
    return flow.report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--source-camera", required=True, type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:8017")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5187/app/")
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    directory = args.run_dir.resolve()
    allowed = Path(__file__).resolve().parents[2] / "output" / "acceptance"
    if not directory.is_relative_to(allowed.resolve()) or directory == allowed.resolve():
        parser.error("run-dir must be a dedicated directory within output/acceptance")
    result = prepare(directory, args.source_camera) if args.command == "prepare" else run(
        directory, args.base_url, args.frontend_url, args.source_camera, args.timeout)
    print(json.dumps({"status": result.get("status", "prepared"), "output": str(directory / "grading"),
                      "synthetic_family": True, "ui_evaluated": False}))


if __name__ == "__main__":
    main()
