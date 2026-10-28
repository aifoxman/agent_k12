"""Render a local, continuously refreshed acceptance audit without running checks.

Schema v1: {schema_version:1, run_id, started_at, updated_at, status, rounds:[
  {id, phase:'baseline|fix|rerun', scope:'model|browser|android|code', supersedes?:[],
   status, source:{device,input,model,audio},
   checks:[{id,label,status,required:true,requires_real:false,requires_source?:{},detail,evidence:[]}],
   timeline:[{at,type,text,source?:{},screenshot?:'relative/file.png'}],
   changes:[], commands:[]}]}
Check/event source overrides round source. requires_source checks declared fields
independently, e.g. {model:'live'} allows synthetic paper with a real model.
Legacy requires_real means requires_source.model='live'; physical paper, Android
and live audio are required only when input/device/audio constraints declare them.
Evidence links must resolve to existing files inside run_dir, including symlinks.
The runner owns execution, round creation and fixes; this module never edits prompts.

Use write_report(run_dir, audit), or python scripts/acceptance/report.py RUN_DIR
to regenerate report.html from RUN_DIR/audit.json. Each file is replaced atomically.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

STATUSES = {"pending", "running", "passed", "failed", "blocked", "skipped"}
LABELS = {
    "pending": "待验证",
    "running": "进行中",
    "passed": "已通过",
    "failed": "未通过",
    "blocked": "未验证",
    "skipped": "已跳过",
}
PHASES = {"baseline": "基线", "fix": "修复", "rerun": "复验"}
SOURCE_LABELS = {"device": "环境", "input": "输入", "model": "模型", "audio": "音频"}
SOURCE_VALUES = {
    "browser": "浏览器",
    "android": "Android 真机",
    "none": "未使用",
    "synthetic": "人工构造",
    "provided": "提供的材料",
    "screen": "屏幕题图（真实相机）",
    "live": "真实来源",
    "mock": "模拟",
    "unknown": "来源未记录",
}


def _escape(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _source(base: Mapping | None, override: Mapping | None = None) -> dict:
    values = dict(base or {})
    values.update(override or {})
    return {key: values.get(key, "unknown") for key in SOURCE_LABELS}


def _requirements(check: Mapping) -> dict:
    requirements = {"model": "live"} if check.get("requires_real") else {}
    explicit = check.get("requires_source")
    if isinstance(explicit, Mapping):
        requirements.update(explicit)
    return requirements


def _status(value: object) -> str:
    return value if isinstance(value, str) and value in STATUSES else "pending"


def _round_status(checks: list[dict], requested: str) -> str:
    required = [check for check in checks if check.get("required", True)]
    statuses = {check["status"] for check in required}
    if "failed" in statuses:
        return "failed"
    if "running" in statuses or requested == "running":
        return "running"
    if not required or statuses & {"blocked", "skipped"}:
        return "blocked"
    return "passed" if statuses == {"passed"} else "pending"


def _compatible_source(previous: Mapping, current: Mapping) -> bool:
    for key in ("model", "audio", "input"):
        if previous.get(key) == "live" and current.get(key) != "live":
            return False
    if previous.get("device") == "android" and current.get("device") != "android":
        return False
    return not (
        previous.get("input") == "provided" and current.get("input") in {"synthetic", "unknown"}
    )


def effective_checks(audit: Mapping) -> list[dict]:
    """Merge independent gates; only explicit reruns or same model IDs replace history.

    Source downgrades never supersede real evidence. Pending/skipped results do
    not erase a completed earlier gate while a replacement run is in progress.
    """
    effective: list[dict] = []
    for round_ in audit.get("rounds", []):
        scope = round_.get("scope") or "unspecified"
        incoming = [
            {
                **check,
                "round_id": round_["id"],
                "scope": check.get("scope") or scope,
                "source": _source(round_.get("source"), check.get("source")),
            }
            for check in round_.get("checks", [])
        ]
        completed = bool(incoming) and all(
            check.get("status") in {"passed", "failed", "blocked"}
            for check in incoming
            if check.get("required", True)
        )
        supersedes = round_.get("supersedes") or []
        retained = []
        for previous in effective:
            replaced = False
            for current in incoming:
                if previous.get("required", True) and not current.get("required", True):
                    continue
                if current["scope"] != previous["scope"] or not _compatible_source(
                    previous["source"], current["source"]
                ):
                    continue
                full_round = completed and previous["round_id"] in supersedes
                same_model = (
                    current["scope"] == "model"
                    and current.get("id")
                    and current.get("id") == previous.get("id")
                    and current.get("status") in {"passed", "failed", "blocked"}
                )
                if full_round or same_model:
                    replaced = True
                    break
            if not replaced:
                retained.append(previous)
        effective = retained + incoming
    return effective


def normalize_audit(audit: Mapping) -> dict:
    """Return a JSON copy whose verdict cannot hide missing or simulated gates."""
    result = json.loads(json.dumps(audit, ensure_ascii=False, allow_nan=False))
    if not isinstance(result, dict) or result.get("schema_version", 1) != 1:
        raise ValueError("Acceptance audit must use schema_version 1")
    result["schema_version"] = 1
    rounds = result.setdefault("rounds", [])
    if not isinstance(rounds, list):
        raise ValueError("rounds must be a list")
    for index, round_ in enumerate(rounds):
        if not isinstance(round_, dict):
            raise ValueError("Each round must be an object")
        round_.setdefault("id", f"round-{index + 1}")
        checks = round_.setdefault("checks", [])
        if not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks):
            raise ValueError("checks must be a list of objects")
        for check in checks:
            check["status"] = _status(check.get("status"))
            source = _source(round_.get("source"), check.get("source"))
            missing = [
                key
                for key, expected in _requirements(check).items()
                if source.get(key, "unknown") != expected
            ]
            if missing and check["status"] == "passed":
                check["reported_status"] = "passed"
                check["status"] = "blocked"
                fields = "、".join(SOURCE_LABELS.get(key, key) for key in missing)
                check["verification_note"] = (
                    f"来源门槛尚未满足：{fields}为其他来源或未记录，不能计为通过。"
                )
        round_["status"] = _round_status(checks, _status(round_.get("status")))
    result["status"] = _round_status(effective_checks(result), "pending")
    return result


def _badge(status: str) -> str:
    return f'<span class="badge {status}">{LABELS[status]}</span>'


def _provenance(source: Mapping) -> str:
    parts = [
        f"<span>{label} <b>{_escape(SOURCE_VALUES.get(source[key], source[key]))}</b></span>"
        for key, label in SOURCE_LABELS.items()
    ]
    return '<div class="provenance">' + "".join(parts) + "</div>"


def _artifact(root: Path, value: object) -> tuple[str, str] | None:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return None
    path = Path(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or any(part.lower() in {"private", "_private"} for part in path.parts)
    ):
        return None
    try:
        target = (root / path).resolve()
        relative = target.relative_to(root)
        if any(part.lower() in {"private", "_private"} for part in relative.parts):
            return None
        if not target.is_file():
            return None
    except (OSError, ValueError):
        return None
    return quote(relative.as_posix(), safe="/"), target.suffix.lower()


def _evidence(root: Path, values: object) -> str:
    if not isinstance(values, list) or not values:
        return '<span class="muted">未附证据</span>'
    links = []
    for value in values:
        artifact = _artifact(root, value)
        if artifact:
            links.append(
                f'<a href="{_escape(artifact[0])}" target="_blank" rel="noopener">'
                f"{_escape(value)}</a>"
            )
        else:
            links.append('<span class="missing">证据缺失或路径无效</span>')
    return '<div class="evidence-links">' + "".join(links) + "</div>"


def _check_rows(root: Path, round_: dict) -> str:
    rows = []
    for check in round_["checks"]:
        required = "硬性门槛" if check.get("required", True) else "观察项"
        constraints = _requirements(check)
        real = " · 需要真实证据" if constraints else ""
        if constraints:
            real += (
                "（"
                + "、".join(
                    f"{SOURCE_LABELS.get(key, key)}={SOURCE_VALUES.get(value, value)}"
                    for key, value in constraints.items()
                )
                + "）"
            )
        detail = _escape(check.get("detail", ""))
        if check.get("verification_note"):
            detail += '<p class="missing">' + _escape(check["verification_note"]) + "</p>"
        rows.append(
            f'<tr><th scope="row">{_escape(check.get("label") or check.get("id"))}'
            f"<small>{required}{_escape(real)}</small></th><td>{_badge(check['status'])}</td>"
            f"<td>{detail}{_provenance(_source(round_.get('source'), check.get('source')))}</td>"
            f"<td>{_evidence(root, check.get('evidence'))}</td></tr>"
        )
    return (
        "".join(rows) or '<tr><td colspan="4" class="empty">尚未执行检查，不能计为通过。</td></tr>'
    )


def _timeline(root: Path, round_: dict) -> str:
    events = []
    for event in round_.get("timeline") or []:
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "action")
        label = {
            "speech": "播报",
            "screenshot": "画面",
            "action": "操作",
            "model": "模型",
            "gate": "门槛",
            "error": "异常",
        }.get(kind, kind)
        media = ""
        if event.get("screenshot"):
            artifact = _artifact(root, event["screenshot"])
            if artifact and artifact[1] in {".png", ".jpg", ".jpeg", ".webp"}:
                url = _escape(artifact[0])
                media = (
                    f'<a href="{url}" target="_blank" rel="noopener"><img src="{url}" '
                    f'alt="{_escape(event.get("text") or "验收截图")}" loading="lazy"></a>'
                )
            else:
                media = '<p class="missing">截图缺失或路径无效</p>'
        events.append(
            f'<li><div class="event-meta"><time>{_escape(event.get("at", "时间未记录"))}</time>'
            f'<span>{_escape(label)}</span></div><div class="event-body">'
            f'<p class="{"transcript" if kind == "speech" else "event-text"}">'
            f"{_escape(event.get('text', ''))}</p>{media}"
            f"{_provenance(_source(round_.get('source'), event.get('source')))}</div></li>"
        )
    return "".join(events) or '<li class="empty">本轮尚未记录截图或播报，不视为已验收。</li>'


def _commands(commands: object) -> str:
    items = []
    for command in commands if isinstance(commands, list) else []:
        if isinstance(command, str):
            items.append(f"<li><code>{_escape(command)}</code><small>结果未记录</small></li>")
        elif isinstance(command, dict):
            text = command.get("command") or command.get("argv") or command.get("label") or ""
            if isinstance(text, list):
                text = " ".join(map(str, text))
            items.append(
                f"<li><code>{_escape(text)}</code><small>退出码 "
                f"{_escape(command.get('exit_code', '未记录'))} · "
                f"{_escape(command.get('duration_seconds', '—'))} 秒</small></li>"
            )
    return "".join(items) or '<li class="muted">尚未记录命令。</li>'


def render_html(run_dir: str | Path, audit: Mapping) -> str:
    """Produce a standalone HTML report with no CDN or executable audit content."""
    root = Path(run_dir).resolve()
    data = normalize_audit(audit)
    rounds = data["rounds"]
    hard = [check for check in effective_checks(data) if check.get("required", True)]
    passed = sum(check["status"] == "passed" for check in hard)
    failed = sum(check["status"] == "failed" for check in hard)
    unverified = len(hard) - passed - failed
    cards, sections = [], []
    for index, round_ in enumerate(rounds, 1):
        phase = PHASES.get(round_.get("phase"), round_.get("phase") or "未分类")
        identifier = f"round-{index}"
        cards.append(
            f'<a class="round-link" href="#{identifier}"><span>{index:02d} / '
            f"{_escape(phase)}</span><strong>{_escape(round_['id'])}</strong>"
            f"{_badge(round_['status'])}</a>"
        )
        changes = "".join(f"<li>{_escape(change)}</li>" for change in round_.get("changes") or [])
        sections.append(
            f'<section id="{identifier}" class="round"><div class="section-heading">'
            f'<div><span class="eyebrow">ROUND {index:02d} · {_escape(phase)}</span>'
            f"<h2>{_escape(round_['id'])}</h2></div>{_badge(round_['status'])}</div>"
            f"{_provenance(_source(round_.get('source')))}"
            '<div class="table-wrap"><table><thead><tr><th>检查项</th><th>结论</th>'
            "<th>依据与来源</th><th>证据</th></tr></thead><tbody>"
            + _check_rows(root, round_)
            + '</tbody></table></div><div class="round-columns"><div><h3>画面与播报时间线</h3>'
            '<ol class="timeline">' + _timeline(root, round_) + "</ol></div><aside>"
            '<h3>本轮改动</h3><ul class="changes">'
            + (changes or "<li>未记录改动。</li>")
            + '</ul><h3>执行记录</h3><ul class="commands">'
            + _commands(round_.get("commands"))
            + "</ul></aside></div></section>"
        )
    run_id = _escape(data.get("run_id", root.name))
    status = data["status"]
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta http-equiv="refresh" content="2">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &#39;none&#39;; '
        "img-src &#39;self&#39;; style-src &#39;unsafe-inline&#39;; base-uri &#39;none&#39;; "
        'form-action &#39;none&#39;">'
        f"<title>纸间 · 教师验收记录 / {run_id}</title><style>{STYLE}</style></head><body>"
        '<header><a class="brand" href="#top"><span>纸</span><b>纸间<small>TEACHING OBSERVATORY</small>'
        '</b></a><div class="refresh"><i></i>每 2 秒刷新 · 本地验收记录</div></header>'
        '<main id="top"><div class="hero"><div><span class="eyebrow">从观察，到证据，再到改进</span>'
        "<h1>每一轮讲解，<br>都有迹可循。</h1><p>先保留基线，再记录修复，最后独立复验。"
        '模拟通过不代替真实教学验收。</p></div><div class="run-card"><span>当前运行</span>'
        f"<strong>{run_id}</strong>{_badge(status)}<dl><dt>开始</dt>"
        f"<dd>{_escape(data.get('started_at', '未记录'))}</dd><dt>最近更新</dt>"
        f"<dd>{_escape(data.get('updated_at', '未记录'))}</dd></dl></div></div>"
        '<div class="score-strip"><div><strong>'
        + str(passed)
        + "</strong><span>当前有效硬门槛通过</span>"
        "</div><div><strong>"
        + str(failed)
        + "</strong><span>明确失败</span></div><div><strong>"
        + str(unverified)
        + "</strong><span>尚未验证 / 阻塞</span></div><div><strong>"
        + str(len(rounds))
        + '</strong><span>保留轮次</span></div></div><nav class="round-nav" aria-label="验收轮次">'
        + ("".join(cards) or '<p class="empty">尚无验收轮次。</p>')
        + "</nav>"
        + "".join(sections)
        + "<footer>报告只呈现证据与门槛，不自动修改提示词。没有执行、没有可靠来源的项目始终保留为未验证。"
        '<a href="audit.json">查看审计 JSON</a></footer></main></body></html>'
    )


STYLE = """
:root{--paper:#f5f1e8;--ink:#243b30;--muted:#748379;--line:#dedbd0;--accent:#e16a4c;--green:#dce9d9}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--ink);background:var(--paper);font:14px/1.65 'Noto Sans SC','Microsoft YaHei',sans-serif}
a{color:inherit}header{height:84px;display:flex;align-items:center;justify-content:space-between;padding:0 5vw;border-bottom:1px solid var(--line);background:#faf7ef}.brand{display:flex;align-items:center;gap:12px;text-decoration:none}.brand>span{display:grid;place-items:center;width:42px;height:42px;background:var(--accent);border-radius:12px;color:white;font:700 25px 'STSong',serif}.brand b{font-size:21px;line-height:1.2}.brand small{display:block;font-size:8px;letter-spacing:2px;color:var(--muted);margin-top:5px}.refresh{font-size:11px;color:var(--muted)}.refresh i{display:inline-block;width:6px;height:6px;background:#73966c;border-radius:50%;margin-right:8px}
main{max-width:1440px;padding:48px 5vw 28px;margin:auto}.hero{display:grid;grid-template-columns:1.6fr 1fr;gap:8vw;align-items:center;padding-bottom:38px}.eyebrow{font-size:10px;font-weight:700;letter-spacing:2.5px;color:var(--accent)}h1{font:600 clamp(34px,4.6vw,64px)/1.16 'Noto Serif SC','STSong','SimSun',serif;letter-spacing:-1px;margin:18px 0 20px}.hero p{color:var(--muted);max-width:490px;margin:0}.run-card{padding:25px 28px;border:1px solid var(--line);border-radius:18px;background:#fffcf5;box-shadow:0 8px 28px #35463208}.run-card>span:first-child{color:var(--muted);font-size:11px}.run-card>strong{display:block;font-size:18px;margin:6px 0 14px;overflow-wrap:anywhere}dl{font-size:11px;display:grid;grid-template-columns:60px 1fr;gap:6px;margin:18px 0 0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
.badge{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:7px;background:#ebe9e0;color:#6f756d;white-space:nowrap;font-size:11px;font-weight:700}.badge:before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor}.passed{background:#dfebd9;color:#426548}.failed{background:#f7dfd7;color:#a34b36}.running{background:#f7ebcc;color:#8c702a}.blocked,.skipped{background:#ede6dc;color:#8c7152}.pending{background:#e9ede8;color:#727d73}
.score-strip{border-top:1px solid var(--line);border-bottom:1px solid var(--line);display:grid;grid-template-columns:repeat(4,1fr);padding:22px 0;margin-bottom:26px}.score-strip>div{padding-left:22px;border-right:1px solid var(--line)}.score-strip>div:first-child{padding-left:0}.score-strip>div:last-child{border:0}.score-strip strong{font:500 36px/1.2 'Noto Serif SC','STSong',serif}.score-strip span{display:block;font-size:10px;color:var(--muted);margin-top:5px}
.round-nav{display:flex;gap:12px;overflow:auto;margin-bottom:38px}.round-link{flex:1;min-width:185px;padding:16px 18px;border:1px solid var(--line);border-radius:12px;background:#faf7ef;text-decoration:none}.round-link>span:first-child{font-size:9px;letter-spacing:1.5px;color:var(--muted)}.round-link strong{display:block;font-size:14px;margin:7px 0 12px;overflow-wrap:anywhere}.round-link:hover{border-color:var(--accent)}.round{margin-bottom:40px;background:#fffdf8;border:1px solid var(--line);border-radius:18px;padding:26px;scroll-margin-top:20px}.section-heading{display:flex;align-items:center;justify-content:space-between;gap:20px}h2{font-size:23px;margin:4px 0 0;overflow-wrap:anywhere}h3{font-size:14px;margin:25px 0 16px}.provenance{display:flex;flex-wrap:wrap;gap:5px 15px;font-size:9px;color:var(--muted);margin:12px 0}.provenance b{font-weight:500;color:#4e6656;margin-left:3px}
.table-wrap{overflow:auto;margin-top:20px}table{border-collapse:collapse;width:100%;min-width:700px;font-size:11px}th,td{text-align:left;vertical-align:top;padding:14px 12px;border-bottom:1px solid #e9e6dd}thead th{background:#f3f3eb;color:#6d7b6b;font-size:9px;letter-spacing:1px}tbody th{width:23%;font-weight:600}th small{display:block;margin-top:4px;font-size:9px;font-weight:400;color:var(--muted)}td:nth-child(2){width:90px}td:nth-child(3){min-width:200px}td .provenance{margin-bottom:0}.evidence-links{display:flex;flex-direction:column;gap:5px;overflow-wrap:anywhere}.evidence-links a{color:#427561}.missing{color:#a2553c;font-size:10px}.muted,.empty{color:var(--muted)}.empty{padding:22px}.round-columns{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(200px,1fr);gap:32px}.round-columns aside{border-left:1px solid var(--line);padding-left:24px}
.timeline{list-style:none;padding:0;margin:0}.timeline li{position:relative;padding:0 0 22px 20px;border-left:1px solid var(--line)}.timeline li:before{content:'';position:absolute;width:7px;height:7px;border:2px solid #f8faf2;border-radius:50%;background:#82a077;left:-4px;top:5px}.event-meta{display:flex;gap:12px;align-items:center;color:var(--muted);font-size:9px}.event-meta span{color:#b06a4b}.event-body p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0;font-size:12px}.event-body .transcript{border-left:3px solid #d9a765;padding:10px 14px;background:#fcf5e7;border-radius:0 8px 8px 0;font-size:14px;line-height:1.8}.event-body img{display:block;max-width:100%;max-height:420px;object-fit:contain;object-position:left top;border:1px solid var(--line);border-radius:10px;margin:12px 0}.changes,.commands{padding-left:17px;font-size:11px}.changes li,.commands li{margin:10px 0}.commands code{font:10px/1.6 Consolas,monospace;overflow-wrap:anywhere;white-space:pre-wrap}.commands small{display:block;color:var(--muted);font-size:9px}footer{font-size:10px;color:var(--muted);padding:12px 0 24px;border-top:1px solid var(--line)}footer a{float:right;margin-left:12px}
@media(max-width:760px){header{height:70px}.refresh{font-size:9px}main{padding-top:28px}.hero{grid-template-columns:1fr;gap:22px}.run-card{padding:20px}.score-strip{grid-template-columns:repeat(2,1fr);gap:18px}.score-strip>div{padding-left:0;border:0}.round{padding:18px}.round-columns{grid-template-columns:1fr;gap:0}.round-columns aside{border-left:0;border-top:1px solid var(--line);padding-left:0}.round-nav{margin-bottom:24px}footer a{float:none;display:block;margin:10px 0 0}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}@media print{header .refresh{display:none}main{padding:0}.round{break-inside:avoid}.round-nav{display:none}.event-body img{max-height:300px}}
"""


def _atomic_write(path: Path, content: str) -> None:
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def write_report(run_dir: str | Path, audit: Mapping) -> dict[str, str]:
    root = Path(run_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    normalized = normalize_audit(audit)
    normalized.setdefault("updated_at", datetime.now(UTC).isoformat())
    document = render_html(root, normalized)
    audit_path, report_path = root / "audit.json", root / "report.html"
    _atomic_write(audit_path, json.dumps(normalized, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(report_path, document)
    return {"audit_json": str(audit_path), "report_html": str(report_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Refresh a local teacher acceptance report")
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args(argv)
    with (args.run_dir / "audit.json").open(encoding="utf-8") as stream:
        audit = json.load(stream)
    paths = write_report(args.run_dir, audit)
    print(paths["report_html"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
