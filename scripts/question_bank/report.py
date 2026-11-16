"""Report coverage and uncertainty without claiming teacher quality from unit tests."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from scripts.question_bank.budget import Budget
from scripts.question_bank.corpus import dump, read
from scripts.question_bank.simulation import percentile


def report(output: Path, tag: str, split="development"):
    from scripts.question_bank.runner import valid_tag
    valid_tag(tag)
    base = output if split == "development" else output / "private" / "holdout"
    all_records = [read(p) for p in (base / "results" / tag).glob("*.json")]
    records = [record for record in all_records if record.get("completed")]
    plan_path = base / "plans" / f"{tag}.json"
    plan = read(plan_path) if plan_path.exists() else {}
    reviews = {p.name: read(p) for p in (base / "reviews" / tag).glob("*.json")}
    groups, timing, failures, verified_timing = defaultdict(Counter), defaultdict(list), defaultdict(list), defaultdict(list)
    for record in records:
        filename = record.get("result_key") or f"{record['case_id']}.{record['mode']}.{record['variant']}.json"
        review = reviews.get(filename, {})
        status = review.get("verdict", "unreviewed")
        for key in ("all", f"grade:{record['grade']}", f"subject:{record['subject']}", f"mode:{record['mode']}", f"variant:{record['variant']}", f"type:{record.get('type')}"):
            groups[key][status] += 1
            groups[key]["total"] += 1
            if review.get("verdict") == "fail" and not review.get("excluded_from_quality_comparison") and any(f.get("severity") == "critical" for f in review.get("findings", [])):
                groups[key]["critical"] += 1
        from scripts.question_bank.gates import hard_checks
        if record.get("error") or hard_checks(record):
            failures[f"{record['mode']}:total_ms"].append(record["total_ms"])
            continue
        timing[f"{record['mode']}:total_ms"].append(record["total_ms"])
        if review.get("verdict") == "pass":
            verified_timing[f"{record['mode']}:total_ms"].append(record["total_ms"])
        for key, value in record.get("timings", {}).items():
            if key.endswith("_ms") and isinstance(value, int | float):
                timing[f"{record['mode']}:{record['variant']}:{record.get('cache', 'cold')}:{key}"].append(value)
                if review.get("verdict") == "pass":
                    verified_timing[f"{record['mode']}:{record['variant']}:{record.get('cache', 'cold')}:{key}"].append(value)
    result = {"tag": tag, "split": split, "simulated_camera": True, "real_handwriting": False,
              "running_tasks": sum(not r.get("completed") for r in all_records),
              "expected_tasks": plan.get("expected_tasks"),
              "completed_tasks": len(records),
              "missing_or_not_completed_tasks": max(0, plan.get("expected_tasks", len(records)) - len(records)),
              "groups": {key: dict(counts) for key, counts in groups.items()},
              "timings": {key: {"n": len(values), "median": percentile(values, .5), "p95": percentile(values, .95)} for key, values in timing.items()},
              "failure_timings": {key: {"n": len(values), "median": percentile(values, .5), "p95": percentile(values, .95)} for key, values in failures.items()},
              "teacher_passed_timings": {key: {"n": len(values), "median": percentile(values, .5), "p95": percentile(values, .95)} for key, values in verified_timing.items()},
              "budget": Budget(output / "budget.sqlite3").summary(),
              "limitations": ["LLM teacher reviews are evidence-based suggestions, not a human-certified accuracy guarantee.",
                              "Simulated camera and typed answer overlays do not establish physical camera or child handwriting accuracy.",
                              "API event times do not prove rendered visibility, audio playback, or synchrony."]}
    dump(base / "reports" / f"{tag}.json", result)
    lines = [f"# 题库评测：{tag}", "", f"样本分组：{split}。模拟拍摄、模拟学生作答；未评审不能计为通过。", "",
             "| 分层 | 任务数 | 通过 | 失败 | 待核查 | 未评审 | 严重错误 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for key, counts in sorted(groups.items()):
        lines.append(f"| {key.replace('|', '/')} | {counts['total']} | {counts['pass']} | {counts['fail']} | {counts['needs_review']} | {counts['unreviewed']} | {counts['critical']} |")
    lines += ["", "## 响应耗时", "", "以下为有效交付的接口事件/语音数据包耗时，不包含超时、定位失败、续讲失败；不代表实际屏幕绘制或真人听感。教师通过样本的耗时另存JSON。", "",
              "| 指标 | 样本数 | 中位数 ms | P95 ms |", "|---|---:|---:|---:|"]
    for key, values in sorted(result["timings"].items()):
        lines.append(f"| {key} | {values['n']} | {values['median']} | {values['p95']} |")
    lines += ["", "## 结论边界", "", "未获得可靠参考的题目单列，不按正确处理；原始失败记录保留。所有分数均须结合具体教师证据解释。"]
    path = base / "reports" / f"{tag}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return result


def compare(output: Path, baseline: str, candidate: str, split="development"):
    base = output if split == "development" else output / "private" / "holdout"
    a = report(output, baseline, split)
    b = report(output, candidate, split)
    # Pair by actual case/mode/variant, not by totals (which may cover different cases).
    def entries(tag):
        return {p.name: read(p) for p in (base / "reviews" / tag).glob("*.json")}
    left, right = entries(baseline), entries(candidate)
    def outputs(tag):
        return {p.name: read(p) for p in (base / "results" / tag).glob("*.json")}
    left_outputs, right_outputs = outputs(baseline), outputs(candidate)
    input_or_rubric_mismatch = []
    common = []
    for key in sorted(left.keys() & right.keys()):
        left_hash, right_hash = left_outputs.get(key, {}).get("input_sha256"), right_outputs.get(key, {}).get("input_sha256")
        if not left_hash or left_hash != right_hash or left[key].get("rubric_version") != right[key].get("rubric_version"):
            input_or_rubric_mismatch.append(key)
        else:
            common.append(key)
    def critical(item):
        return item.get("verdict") == "fail" and not item.get("excluded_from_quality_comparison") and any(f.get("severity") == "critical" for f in item.get("findings", []))
    observed_critical = [key for key in common if critical(right[key])]
    unassessed_baseline = [key for key in observed_critical
                          if not left[key].get("scores") or left[key].get("reference_reliable") is not True]
    new_critical = [key for key in observed_critical if key not in unassessed_baseline and not critical(left[key])]
    value = {"baseline": baseline, "candidate": candidate, "split": split,
             "paired_tasks": len(common), "unpaired_baseline": sorted(left.keys() - right.keys()),
             "input_or_rubric_mismatch": input_or_rubric_mismatch,
             "unpaired_candidate": sorted(right.keys() - left.keys()),
             "new_critical": new_critical,
             "observed_candidate_critical": observed_critical,
             "candidate_critical_with_unassessed_baseline": unassessed_baseline,
             "improved": [k for k in common if left[k]["verdict"] != "pass" and right[k]["verdict"] == "pass"],
             "regressed": [k for k in common if left[k]["verdict"] == "pass" and right[k]["verdict"] != "pass"],
             "baseline_counts": a["groups"].get("all"), "candidate_counts": b["groups"].get("all"),
             "quality_gate": "not_established" if not common else "failed" if observed_critical else "requires_evidence_review"}
    dump(base / "reports" / f"{baseline}-vs-{candidate}.json", value)
    return value
