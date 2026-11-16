"""Report coverage and uncertainty without claiming teacher quality from unit tests."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from scripts.question_bank.budget import Budget
from scripts.question_bank.corpus import dump, read
from scripts.question_bank.simulation import percentile


def report(output: Path, tag: str, split="development"):
    base = output if split == "development" else output / "private" / "holdout"
    records = [read(p) for p in (base / "results" / tag).glob("*.json")]
    reviews = {p.name: read(p) for p in (base / "reviews" / tag).glob("*.json")}
    groups, timing = defaultdict(Counter), defaultdict(list)
    for record in records:
        filename = f"{record['case_id']}.{record['mode']}.{record['variant']}.json"
        review = reviews.get(filename, {})
        status = review.get("verdict", "unreviewed")
        for key in ("all", f"grade:{record['grade']}", f"subject:{record['subject']}", f"mode:{record['mode']}", f"variant:{record['variant']}", f"type:{record.get('type')}"):
            groups[key][status] += 1
            groups[key]["total"] += 1
            if any(f.get("severity") == "critical" for f in review.get("findings", [])):
                groups[key]["critical"] += 1
        timing[f"{record['mode']}:total_ms"].append(record["total_ms"])
        for key, value in record.get("timings", {}).items():
            if key.endswith("_ms") and isinstance(value, int | float):
                timing[f"{record['mode']}:{record['variant']}:{key}"].append(value)
    result = {"tag": tag, "split": split, "simulated_camera": True, "real_handwriting": False,
              "groups": {key: dict(counts) for key, counts in groups.items()},
              "timings": {key: {"n": len(values), "median": percentile(values, .5), "p95": percentile(values, .95)} for key, values in timing.items()},
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
    lines += ["", "## 响应耗时", "", "以下为接口事件/语音数据包耗时，不代表实际屏幕绘制或真人听感。", "",
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
    common = sorted(left.keys() & right.keys())
    new_critical = [key for key in common if any(f.get("severity") == "critical" for f in right[key].get("findings", []))
                    and not any(f.get("severity") == "critical" for f in left[key].get("findings", []))]
    value = {"baseline": baseline, "candidate": candidate, "split": split,
             "paired_tasks": len(common), "unpaired_baseline": sorted(left.keys() - right.keys()),
             "unpaired_candidate": sorted(right.keys() - left.keys()),
             "new_critical": new_critical,
             "improved": [k for k in common if left[k]["verdict"] != "pass" and right[k]["verdict"] == "pass"],
             "regressed": [k for k in common if left[k]["verdict"] == "pass" and right[k]["verdict"] != "pass"],
             "baseline_counts": a["groups"].get("all"), "candidate_counts": b["groups"].get("all"),
             "quality_gate": "not_established" if not common else "failed" if new_critical else "requires_evidence_review"}
    dump(base / "reports" / f"{baseline}-vs-{candidate}.json", value)
    return value
