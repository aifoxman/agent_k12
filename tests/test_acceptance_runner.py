"""Acceptance orchestration must preserve failures and isolate synthetic writes."""

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    return importlib.import_module("scripts.acceptance.run"), importlib.import_module(
        "scripts.acceptance.bootstrap"
    )


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_selected_model_rerun_does_not_invent_pending_cases(modules, tmp_path):
    runner, _ = modules
    folder = tmp_path / "model-rerun-123"
    write(
        folder / "manifest.json",
        {
            "cases": [
                {"id": "selected", "image": "papers/selected.jpg"},
                {"id": "omitted", "image": "papers/omitted.jpg"},
            ]
        },
    )
    write(folder / "round.json", {"cases": ["selected"], "changes": ["Reuse solution"]})
    write(
        folder / "results/selected.json",
        {
            "assessment": {
                "checks": [{"name": "latency", "status": "pass", "reason": "One solve only"}]
            }
        },
    )
    audit = runner.collect(tmp_path)
    assert len(audit["rounds"][0]["checks"]) == 1
    assert audit["rounds"][0]["changes"] == ["Reuse solution"]
    assert audit["rounds"][0]["scope"] == "model"


def test_prepared_but_unrequested_model_is_not_a_live_evaluation(modules, tmp_path):
    runner, _ = modules
    write(tmp_path / "model/manifest.json", {"cases": []})
    assert runner.collect(tmp_path)["rounds"] == []


def test_browser_rerun_never_supersedes_another_source(modules, tmp_path):
    runner, _ = modules
    for folder, mode in [
        ("browser-controlled", "controlled"),
        ("browser-controlled-rerun", "controlled"),
        ("browser-live", "live"),
    ]:
        write(
            tmp_path / folder / "report.json",
            {
                "mode": mode,
                "finished_at": "2026-10-22T00:00:00Z",
                "stages": [{"name": "tutoring", "status": "passed"}],
            },
        )
    rounds = runner.collect(tmp_path)["rounds"]
    assert rounds[1]["supersedes"] == ["browser-controlled"]
    assert "supersedes" not in rounds[2]


@pytest.mark.parametrize("url", ["http://127.0.0.1:8003", "https://example.com"])
def test_bootstrap_rejects_real_or_remote_service_before_network(modules, tmp_path, url):
    _, bootstrap = modules
    with pytest.raises(ValueError, match="dedicated loopback"):
        bootstrap.bootstrap(url, "http://127.0.0.1:5186", tmp_path, tmp_path / "paper.jpg")


def test_partial_jsonl_is_ignored_without_losing_prior_events(modules, tmp_path):
    runner, _ = modules
    path = tmp_path / "events.jsonl"
    path.write_text('{"type":"speech","text":"hello"}\n{"type":', encoding="utf-8")
    assert runner.records(path) == [{"type": "speech", "text": "hello"}]


@pytest.mark.parametrize(
    ("codes", "status", "expected"),
    [
        ([0], "blocked", 2),
        ([0], "running", 2),
        ([0], "passed", 0),
        ([1], "passed", 1),
        ([0], "failed", 1),
    ],
)
def test_missing_teaching_evidence_cannot_produce_a_success_exit(modules, codes, status, expected):
    runner, _ = modules
    assert runner.acceptance_exit_code(codes, status) == expected
