"""Observable failures independent of the backend and the LLM reviewer."""
from __future__ import annotations


def hard_checks(record: dict) -> list[dict]:
    response = record.get("response") or {}
    mode = record["mode"]
    issues = []
    if record.get("error"):
        return [{"severity": "major", "path": "request", "quote": "",
                 "reason": record["error"], "source": "deterministic_gate"}]
    if mode in {"hint1", "hint2"}:
        if not response.get("target_box") or not response.get("visual_beats"):
            issues.append({"severity": "major", "path": "visual_beats/target_box", "quote": "",
                           "reason": "Successful teaching has no bound original question or structured teaching beats.",
                           "source": "deterministic_gate"})
        if response.get("delivery_complete") is False:
            issues.append({"severity": "major", "path": "delivery_complete", "quote": "",
                           "reason": "Explanation delivery was incomplete.", "source": "deterministic_gate"})
        if mode == "hint1" and response.get("explanation_status") == "ready" and response.get("continuation_available") is not True:
            issues.append({"severity": "major", "path": "continuation_available", "quote": "",
                           "reason": "First-tier explanation is ready but its second tier cannot be continued.", "source": "deterministic_gate"})
    if mode == "blank":
        for index, check in enumerate(response.get("checks") or []):
            if check.get("answer_state") == "written" and str(check.get("answer") or "").strip():
                issues.append({"severity": "critical", "path": f"checks[{index}].answer", "quote": str(check["answer"]),
                               "reason": "The simulated answer area is empty; a written student answer was invented.",
                               "source": "deterministic_gate"})
    return issues


def apply(record: dict, assessment: dict) -> dict:
    issues = hard_checks(record)
    assessment["deterministic_checks"] = issues
    if issues:
        assessment["verdict"] = "fail"
        assessment["findings"] = [*[f for f in assessment.get("findings", []) if f.get("source") != "deterministic_gate"], *issues]
    return assessment
