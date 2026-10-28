"""Run repeatable acceptance rounds and refresh a source-labelled evidence report."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.acceptance.bootstrap import bootstrap  # noqa: E402
from scripts.acceptance.report import write_report  # noqa: E402


def now() -> str:
    return datetime.now(UTC).isoformat()


def load(path: Path, fallback=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fallback


def relative(path: Path, directory: Path) -> str:
    return path.resolve().relative_to(directory.resolve()).as_posix()


def records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                result.append(value)
        except ValueError:
            pass  # A producer may still be writing the last line.
    return result


def acceptance_exit_code(command_codes: list[int], status: str) -> int:
    if any(command_codes) or status == "failed":
        return 1
    return 0 if status == "passed" else 2


def collect(directory: Path) -> dict:
    """Rebuild the report from immutable per-round outputs, including running cases."""
    previous = load(directory / "audit.json", {})
    audit = {
        "schema_version": 1,
        "run_id": directory.name,
        "started_at": previous.get("started_at", now()),
        "updated_at": now(),
        "rounds": [],
    }
    for folder in sorted(directory.glob("model*")):
        manifest = load(folder / "manifest.json")
        plan = load(folder / "round.json", {})
        if not manifest or not (
            plan or (folder / "results").exists() or (folder / "summary.json").exists()
        ):
            continue
        source = {"device": "none", "input": "synthetic", "model": "live", "audio": "none"}
        round_ = {
            "id": folder.name,
            "scope": "model",
            "phase": "rerun" if "rerun" in folder.name else "baseline",
            "source": source,
            "checks": [],
            "timeline": [],
            "changes": [],
            "commands": [],
        }
        setup = load(folder / "summary.json", {})
        if setup.get("status") == "setup_failed":
            round_["checks"].append(
                {
                    "id": "acceptance_setup",
                    "label": "独立验收服务身份与配置",
                    "status": "failed",
                    "required": True,
                    "source": {"model": "none"},
                    "detail": "尚未调用模型：验收身份或配置读取失败 "
                    + setup.get("setup_error", ""),
                    "evidence": [relative(folder / "summary.json", directory)],
                }
            )
        selected = plan.get("cases", [case["id"] for case in manifest["cases"]])
        for case in [case for case in manifest["cases"] if case["id"] in selected]:
            path = folder / "results" / f"{case['id']}.json"
            result = load(path, {})
            evidence = [relative(folder / case["image"], directory)]
            if path.exists():
                evidence.append(relative(path, directory))
            checks = result.get("assessment", {}).get("checks") or [
                {
                    "name": "case",
                    "status": result.get("status", "pending"),
                    "reason": "等待本题执行或核查结果。",
                }
            ]
            for check in checks:
                status = {"pass": "passed", "fail": "failed", "needs_review": "blocked"}.get(
                    check.get("status"), check.get("status", "pending")
                )
                round_["checks"].append(
                    {
                        "id": f"{case['id']}:{check['name']}",
                        "label": f"{case['id']} · {check['name']}",
                        "status": status,
                        "required": True,
                        "requires_source": {"model": "live"},
                        "detail": check.get("reason", ""),
                        "evidence": evidence,
                    }
                )
        for event in records(folder / "events.jsonl"):
            data = event.get("data") or {}
            speech = data.get("speech") or data.get("text")
            if speech or event.get("event") in {"started", "completed", "error"}:
                round_["timeline"].append(
                    {
                        "at": event.get("at"),
                        "type": "speech" if speech else "model",
                        "text": speech or f"{event.get('case_id')} · {event.get('event')}",
                    }
                )
        round_.update({key: plan[key] for key in ("changes", "commands") if key in plan})
        audit["rounds"].append(round_)
    browser_previous = {}
    browser_folders = sorted(
        directory.glob("browser-*"),
        key=lambda path: (load(path / "report.json", {}).get("started_at", ""), path.name),
    )
    for folder in browser_folders:
        result = load(folder / "report.json")
        if not result:
            continue
        controlled = result.get("mode") != "live"
        source = {
            "device": "browser",
            "input": "synthetic",
            "model": "mock" if controlled else "live",
            "audio": "mock",
        }
        scope = "browser-controlled" if controlled else "browser-live"
        round_ = {
            "id": folder.name,
            "scope": scope,
            "phase": "rerun" if "rerun" in folder.name else "baseline",
            "source": source,
            "checks": [],
            "timeline": [],
            "changes": [],
            "commands": [],
        }
        metadata = load(folder / "round.json", {})
        round_.update({key: metadata[key] for key in ("changes", "commands") if key in metadata})
        for index, stage in enumerate(result.get("stages", [])):
            round_["checks"].append(
                {
                    "id": stage.get("name", f"browser-{index}"),
                    "label": stage.get("name", str(index)),
                    "status": stage.get("status", "pending"),
                    "required": True,
                    "detail": stage.get("detail") or stage.get("error", ""),
                    "evidence": [relative(folder / "report.json", directory)],
                }
            )
        for picture in sorted(folder.glob("*.png")):
            round_["timeline"].append(
                {
                    "at": now(),
                    "type": "screenshot",
                    "text": picture.stem,
                    "screenshot": relative(picture, directory),
                }
            )
        if result.get("infrastructure_error"):
            round_["checks"].append(
                {
                    "id": "browser-error",
                    "label": "浏览器执行",
                    "status": "failed",
                    "required": True,
                    "detail": result["infrastructure_error"],
                    "evidence": [relative(folder / "report.json", directory)],
                }
            )
        for event in result.get("timeline", []):
            kind = event.get("kind", "")
            if kind not in {"speech-request", "speech-error", "paper-speech"}:
                continue
            detail = event.get("detail") or {}
            text_ = event.get("text") or detail.get("spoken") or detail.get("text")
            if text_:
                round_["timeline"].append(
                    {
                        "at": event.get("at"),
                        "type": "error" if kind == "speech-error" else "speech",
                        "text": str(text_)[:4000]
                        + (f" · {event.get('error')}" if kind == "speech-error" else ""),
                    }
                )
        # A complete rerun supersedes the earlier flow, never a different source.
        if result.get("finished_at") and scope in browser_previous:
            round_["supersedes"] = [browser_previous[scope]]
        browser_previous[scope] = folder.name
        audit["rounds"].append(round_)
    android = directory / "android"
    if android.exists():
        result = load(android / "report.json", {})
        timeline = [event for path in sorted(android.glob("*.jsonl")) for event in records(path)]
        audit["rounds"].append(
            {
                "id": "android-observation",
                "scope": "android",
                "phase": "baseline",
                "source": {
                    "device": "android",
                    "input": "synthetic",
                    "model": "live",
                    "audio": "unknown",
                },
                "checks": result.get("checks")
                or [
                    {
                        "id": "physical-observation",
                        "label": "平板观察证据",
                        "status": "running",
                        "required": True,
                        "detail": "设备回调与截图单独核查，不冒充真实纸面或真人实听。",
                    }
                ],
                "timeline": timeline,
                "changes": result.get("changes", []),
            }
        )
    audio = load(directory / "audio-report.json")
    if audio:
        audit["rounds"].append(
            {
                "id": "audio-roundtrip",
                "scope": "audio",
                "phase": "baseline",
                "source": audio.get("source", {"audio": "none"}),
                "checks": audio.get("checks", []),
                "timeline": audio.get("timeline", []),
            }
        )
    audit["status"] = "running"
    write_report(directory, audit)
    return audit


def execute(command: list[str], directory: Path, name: str, env=None) -> int:
    log = directory / f"{name}.log"
    with log.open("w", encoding="utf-8") as target:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=env,
            stdout=target,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        while process.poll() is None:
            collect(directory)
            time.sleep(2)
        collect(directory)
        return process.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "report", "watch"], nargs="?", default="run")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8016")
    parser.add_argument("--frontend-url", default="http://127.0.0.1:5186/app/")
    parser.add_argument(
        "--live", action="store_true", help="Explicitly invoke the configured real model"
    )
    parser.add_argument("--round", choices=["baseline", "rerun"], default="baseline")
    parser.add_argument("--limit", type=int, default=6)
    parser.add_argument("--cases", nargs="*", default=[])
    parser.add_argument(
        "--browser", choices=["none", "controlled", "live", "both"], default="controlled"
    )
    parser.add_argument("--seconds", type=int, default=120)
    args = parser.parse_args()
    directory = args.output.resolve()
    allowed = (ROOT / "output" / "acceptance").resolve()
    if not directory.is_relative_to(allowed) or directory == allowed:
        parser.error("output must be inside output/acceptance")
    if not 1 <= args.limit <= 6:
        parser.error("limit must be between 1 and 6")
    directory.mkdir(parents=True, exist_ok=True)
    if args.action in {"report", "watch"}:
        deadline = time.monotonic() + (args.seconds if args.action == "watch" else 0)
        while True:
            collect(directory)
            if time.monotonic() >= deadline:
                break
            time.sleep(2)
        print(str(directory / "report.html"))
        return
    model = directory / (
        "model"
        if args.round == "baseline"
        else "model-rerun-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    )
    if args.round == "baseline" and (
        (args.live and (model / "summary.json").exists())
        or any(
            (directory / f"browser-{mode}" / "report.json").exists()
            for mode in ("controlled", "live")
            if args.browser in {mode, "both"}
        )
    ):
        parser.error("This baseline already exists; use --round rerun to preserve its evidence")
    if not (model / "manifest.json").exists():
        subprocess.run(
            [sys.executable, "scripts/acceptance/evaluate.py", "prepare", "--output", str(model)],
            cwd=ROOT,
            check=True,
        )
    paper = model / "papers" / "arithmetic_wrong.jpg"
    codes = []
    if args.live:
        manifest = load(model / "manifest.json")
        selected = [
            case["id"] for case in manifest["cases"] if not args.cases or case["id"] in args.cases
        ][: args.limit]
        if not selected or set(args.cases) - {case["id"] for case in manifest["cases"]}:
            parser.error("--cases must name cases from the manifest")
        (model / "round.json").write_text(json.dumps({"cases": selected}), encoding="utf-8")
        state = bootstrap(args.base_url, args.frontend_url, directory, paper)
        env = dict(
            os.environ,
            ACCEPTANCE_CHILD_TOKEN=state["child_token"],
            ACCEPTANCE_PARENT_TOKEN=state["parent_token"],
            ACCEPTANCE_SESSION_ID=state["session_id"],
        )
        command = [
            sys.executable,
            "scripts/acceptance/evaluate.py",
            "run",
            "--base-url",
            args.base_url,
            "--output",
            str(model),
            "--limit",
            str(args.limit),
        ]
        if args.cases:
            for case in args.cases:
                command.extend(["--case", case])
        codes.append(execute(command, directory, model.name, env))
    for mode in ("controlled", "live"):
        if args.browser not in {mode, "both"}:
            continue
        if mode == "live" and not args.live:
            parser.error("--browser live requires --live")
        browser_name = f"browser-{mode}" + (
            "-rerun-" + datetime.now().strftime("%H%M%S-%f") if args.round == "rerun" else ""
        )
        command = [
            "node",
            "scripts/acceptance/browser.mjs",
            "--mode",
            mode,
            "--output",
            str(directory / browser_name),
            "--base-url",
            args.frontend_url,
        ]
        if mode == "live":
            bootstrap(args.base_url, args.frontend_url, directory, paper, "browser-state.json")
            command.extend(
                ["--state-file", str(directory / "browser-state.json"), "--paper", str(paper)]
            )
        codes.append(execute(command, directory, browser_name))
    collect(directory)
    status = load(directory / "audit.json", {}).get("status", "blocked")
    print(
        json.dumps(
            {"report": str(directory / "report.html"), "exit_codes": codes, "status": status}
        )
    )
    raise SystemExit(acceptance_exit_code(codes, status))


if __name__ == "__main__":
    main()
