"""Acceptance reports preserve provenance, failures and safe local evidence links."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "acceptance" / "report.py"
SPEC = importlib.util.spec_from_file_location("acceptance_report", MODULE_PATH)
report = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(report)

MOCK_SOURCE = {"device": "browser", "input": "synthetic", "model": "mock", "audio": "mock"}
REAL_SOURCE = {"device": "android", "input": "live", "model": "live", "audio": "live"}


def audit_with(checks, **round_fields):
    return {
        "schema_version": 1,
        "run_id": "teaching-check",
        "started_at": "2026-10-23T10:00:00Z",
        "updated_at": "2026-10-23T10:00:01Z",
        "status": "passed",
        "rounds": [
            {
                "id": "baseline-01",
                "phase": "baseline",
                "status": "passed",
                "scope": "browser",
                "source": MOCK_SOURCE,
                "checks": checks,
                "timeline": [],
                **round_fields,
            }
        ],
    }


def test_mock_cannot_pass_real_gate_and_input_audit_is_not_mutated(tmp_path):
    audit = audit_with(
        [
            {
                "id": "real-voice",
                "label": "真人口述",
                "status": "passed",
                "requires_real": True,
                "required": True,
            }
        ]
    )
    paths = report.write_report(tmp_path, audit)
    saved = json.loads(Path(paths["audit_json"]).read_text(encoding="utf-8"))
    check = saved["rounds"][0]["checks"][0]
    assert check["status"] == "blocked" and check["reported_status"] == "passed"
    assert saved["status"] == "blocked"
    assert audit["rounds"][0]["checks"][0]["status"] == "passed"
    html = Path(paths["report_html"]).read_text(encoding="utf-8")
    assert "需要真实证据" in html and "来源门槛尚未满足" in html
    assert 'http-equiv="refresh" content="2"' in html
    assert "cdn" not in html.lower() and "<script" not in html


def test_real_gate_allows_real_evidence_and_latest_rerun_preserves_failed_baseline(tmp_path):
    audit = audit_with([{"id": "layout", "status": "failed", "detail": "标签偏移"}])
    audit["rounds"].append(
        {
            "id": "fix-01",
            "phase": "fix",
            "status": "passed",
            "source": REAL_SOURCE,
            "checks": [],
            "changes": ["统一坐标"],
        }
    )
    audit["rounds"].append(
        {
            "id": "rerun-01",
            "phase": "rerun",
            "status": "passed",
            "scope": "browser",
            "supersedes": ["baseline-01"],
            "source": REAL_SOURCE,
            "checks": [
                {
                    "id": "layout",
                    "label": "标注位置",
                    "status": "passed",
                    "requires_real": True,
                    "evidence": ["screen.png"],
                }
            ],
        }
    )
    (tmp_path / "screen.png").write_bytes(b"test-image")
    normalized = report.normalize_audit(audit)
    assert [item["status"] for item in normalized["rounds"]] == ["failed", "blocked", "passed"]
    assert normalized["status"] == "passed"
    html = report.render_html(tmp_path, audit)
    assert "标签偏移" in html and "统一坐标" in html
    assert "基线" in html and "修复" in html and "复验" in html


def test_browser_success_cannot_hide_model_failure_and_partial_model_rerun_only_replaces_same_id():
    live_source = {"device": "none", "input": "synthetic", "model": "live", "audio": "none"}
    audit = audit_with(
        [{"id": "case-a:answer", "status": "failed"}, {"id": "case-b:anchor", "status": "failed"}],
        scope="model",
        source=live_source,
    )
    audit["rounds"].append(
        {
            "id": "browser",
            "scope": "browser",
            "source": MOCK_SOURCE,
            "checks": [{"id": "browser-workflow", "status": "passed"}],
        }
    )
    assert report.normalize_audit(audit)["status"] == "failed"
    audit["rounds"].append(
        {
            "id": "model-rerun",
            "scope": "model",
            "source": live_source,
            "checks": [{"id": "case-a:answer", "status": "passed"}],
        }
    )
    normalized = report.normalize_audit(audit)
    effective = report.effective_checks(normalized)
    assert {(item["id"], item["status"]) for item in effective} == {
        ("case-a:answer", "passed"),
        ("case-b:anchor", "failed"),
        ("browser-workflow", "passed"),
    }
    assert normalized["status"] == "failed"


def test_mock_and_pending_reruns_do_not_supersede_completed_live_gates():
    audit = audit_with([{"id": "answer", "status": "failed"}], scope="model", source=REAL_SOURCE)
    audit["rounds"].append(
        {
            "id": "mock-rerun",
            "scope": "model",
            "supersedes": ["baseline-01"],
            "source": MOCK_SOURCE,
            "checks": [{"id": "answer", "status": "passed"}],
        }
    )
    assert report.normalize_audit(audit)["status"] == "failed"
    audit["rounds"][-1]["source"] = REAL_SOURCE
    audit["rounds"][-1]["checks"][0]["status"] = "pending"
    assert report.normalize_audit(audit)["status"] == "failed"
    assert len(report.effective_checks(report.normalize_audit(audit))) == 2


def test_browser_round_requires_explicit_supersession_and_no_required_gate_downgrade():
    audit = audit_with([{"id": "click", "status": "failed"}])
    audit["rounds"].append(
        {
            "id": "new-browser",
            "scope": "browser",
            "source": MOCK_SOURCE,
            "checks": [{"id": "click", "status": "passed"}],
        }
    )
    assert report.normalize_audit(audit)["status"] == "failed"
    audit["rounds"][-1]["supersedes"] = ["baseline-01"]
    assert report.normalize_audit(audit)["status"] == "passed"
    audit["rounds"][-1]["checks"][0]["required"] = False
    assert report.normalize_audit(audit)["status"] == "failed"


@pytest.mark.parametrize(
    "source",
    [
        {},
        {"device": "android"},
        {**REAL_SOURCE, "model": "mock"},
    ],
)
def test_real_gate_rejects_missing_or_partly_simulated_provenance(source):
    audit = audit_with([{"id": "live", "status": "passed", "requires_real": True}], source=source)
    assert report.normalize_audit(audit)["status"] == "blocked"


def test_synthetic_paper_with_real_model_is_valid_real_model_evaluation():
    source = {"device": "browser", "input": "synthetic", "model": "live", "audio": "mock"}
    audit = audit_with(
        [{"id": "teaching", "status": "passed", "requires_real": True}], source=source
    )
    assert report.normalize_audit(audit)["status"] == "passed"
    audit["rounds"][0]["checks"][0]["requires_source"] = {"input": "live"}
    assert report.normalize_audit(audit)["status"] == "blocked"


def test_source_requirements_are_independent_for_device_and_audio():
    audit = audit_with(
        [{"id": "device", "status": "passed", "requires_source": {"device": "android"}}],
        source={**MOCK_SOURCE, "device": "android"},
    )
    assert report.normalize_audit(audit)["status"] == "passed"
    audit["rounds"][0]["checks"][0]["requires_source"] = {"device": "android", "audio": "live"}
    assert report.normalize_audit(audit)["status"] == "blocked"
    audit["rounds"][0]["source"]["audio"] = "live"
    assert report.normalize_audit(audit)["status"] == "passed"


def test_html_escapes_audit_content_and_rejects_external_or_traversing_evidence(tmp_path):
    attack = '<script>alert("bad")</script>'
    audit = audit_with(
        [
            {
                "id": attack,
                "label": attack,
                "status": "passed",
                "detail": attack,
                "evidence": [
                    "../outside.png",
                    "https://example.com/tracker.png",
                    "/outside.png",
                    "C:\\secret.png",
                ],
            }
        ],
        id=attack,
        phase=attack,
        changes=[attack],
        commands=[{"command": attack, "exit_code": 0}],
        timeline=[{"type": "speech", "text": attack, "at": attack, "screenshot": "../outside.png"}],
    )
    audit["run_id"] = attack
    html = report.render_html(tmp_path, audit)
    assert attack not in html and "&lt;script&gt;" in html
    assert 'href="../' not in html and 'src="http' not in html
    assert "证据缺失或路径无效" in html and "截图缺失或路径无效" in html


def test_screenshot_and_speech_timeline_keep_sources_and_encoded_relative_paths(tmp_path):
    directory = tmp_path / "images"
    directory.mkdir()
    (directory / "first 'frame'.png").write_bytes(b"image")
    audit = audit_with(
        [{"id": "paper", "status": "passed", "evidence": ["images/first 'frame'.png"]}],
        timeline=[
            {"at": "10:01:00", "type": "speech", "text": "先圈出已知条件。", "source": REAL_SOURCE},
            {
                "at": "10:01:01",
                "type": "screenshot",
                "text": "条件勾不是批改正确对勾",
                "screenshot": "images/first 'frame'.png",
            },
        ],
    )
    html = report.render_html(tmp_path, audit)
    assert 'src="images/first%20%27frame%27.png"' in html
    assert 'class="transcript"' in html and "先圈出已知条件。" in html
    assert "Android 真机" in html and "人工构造" in html
    assert html.index("10:01:00") < html.index("10:01:01")


def test_evidence_resolving_outside_run_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"private")
    link = root / "linked.png"
    link.write_bytes(b"link-placeholder")
    original_resolve = Path.resolve

    def resolve_link(path, *args, **kwargs):
        # Inject a link resolver without requiring Windows symlink privileges.
        if path == link:
            return original_resolve(outside)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_link)
    assert report._artifact(root.resolve(), "linked.png") is None


def test_private_storage_backup_can_never_be_linked_into_report(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir()
    (private / "storage.json").write_text('{"child_token":"secret"}', encoding="utf-8")
    audit = audit_with(
        [{"id": "storage", "status": "passed", "evidence": ["private/storage.json"]}]
    )
    html = report.render_html(tmp_path, audit)
    assert 'href="private/storage.json"' not in html
    assert "证据缺失或路径无效" in html and "secret" not in html
    alias = tmp_path / "public-alias.json"
    original_resolve = Path.resolve

    def resolve_alias(path, *args, **kwargs):
        if path == alias:
            return original_resolve(private / "storage.json")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_alias)
    assert report._artifact(tmp_path.resolve(), "public-alias.json") is None


def test_optional_failure_does_not_hide_unverified_required_gate(tmp_path):
    audit = audit_with(
        [
            {"id": "optional", "status": "failed", "required": False},
            {"id": "must-run", "status": "skipped", "required": True},
        ]
    )
    assert report.normalize_audit(audit)["status"] == "blocked"
    assert "尚未验证 / 阻塞" in report.render_html(tmp_path, audit)
    assert report.normalize_audit({"rounds": []})["status"] == "blocked"


def test_atomic_write_failure_keeps_previous_file_and_cleans_temporary(tmp_path, monkeypatch):
    existing = tmp_path / "audit.json"
    existing.write_text("previous", encoding="utf-8")

    def fail_replace(*args):
        raise OSError("controlled replacement failure")

    monkeypatch.setattr(report.os, "replace", fail_replace)
    with pytest.raises(OSError, match="controlled"):
        report.write_report(tmp_path, audit_with([]))
    assert existing.read_text(encoding="utf-8") == "previous"
    assert not list(tmp_path.glob("*.tmp"))


def test_report_cli_refreshes_existing_audit(tmp_path, capsys):
    (tmp_path / "audit.json").write_text(json.dumps(audit_with([])), encoding="utf-8")
    assert report.main([str(tmp_path)]) == 0
    assert (tmp_path / "report.html").is_file()
    assert "report.html" in capsys.readouterr().out


def test_real_camera_screen_medium_does_not_satisfy_physical_paper_gate(tmp_path):
    audit = audit_with(
        [{"id": "paper", "status": "passed", "requires_source": {"input": "live"}}],
        source={**REAL_SOURCE, "input": "screen", "medium": "external_screen"},
    )
    assert report.normalize_audit(audit)["status"] == "blocked"
    assert "屏幕题图（真实相机）" in report.render_html(tmp_path, audit)
