"""跑双风险对照集：规则判出来的档位与人工标的答案比对。

纯规则、不调模型，所以随时能跑：

    python -m evals.dual_risk.runner

输出三件事：`dangerous`（这个数当时是不是该处理）的准确率、四档标签的准确率、
以及 `silent` 这一档的召回。`silent` 召回低于 1.0 直接返回非零退出码——漏掉一次
「低了却没感觉」，比多问一句的代价大得多。

结果 JSON 写进 `evals/reports/`（该目录只保留 .gitignore，不提交结果）。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antang_api.psychosocial.dual_risk import classify_label, physiological_risk

from evals.dual_risk.cases import DualRiskEvalCase, build_cases

# 这一档不能漏：血糖已经偏离、他却什么都没感觉到。
_REQUIRED_RECALL = 1.0
_MIN_ACCURACY = 0.9


def evaluate(case: DualRiskEvalCase) -> dict[str, Any]:
    """按规则算一条，并把与人工答案的差异挑出来。"""

    predicted_physiological = physiological_risk(
        case.value_mg_dl,
        target_low_mg_dl=case.target_low_mg_dl,
        target_high_mg_dl=case.target_high_mg_dl,
    )
    predicted_dangerous = predicted_physiological != "none"
    predicted_label = classify_label(predicted_physiological, case.felt)
    problems: list[str] = []
    if predicted_dangerous != case.dangerous:
        problems.append(
            f"该不该处理：规则 {predicted_dangerous} / 人工 {case.dangerous}"
        )
    if predicted_label != case.label:
        problems.append(f"档位：规则 {predicted_label} / 人工 {case.label}")
    return {
        **asdict(case),
        "predicted_physiological": predicted_physiological,
        "predicted_dangerous": predicted_dangerous,
        "predicted_label": predicted_label,
        "match": not problems,
        "problems": problems,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    dangerous_hits = sum(
        1 for row in results if row["predicted_dangerous"] == row["dangerous"]
    )
    label_hits = sum(1 for row in results if row["predicted_label"] == row["label"])
    silent_rows = [row for row in results if row["label"] == "silent"]
    silent_hits = sum(1 for row in silent_rows if row["predicted_label"] == "silent")
    return {
        "cases": total,
        "dangerous_accuracy": dangerous_hits / total,
        "label_accuracy": label_hits / total,
        "silent_cases": len(silent_rows),
        "silent_recall": (silent_hits / len(silent_rows)) if silent_rows else 1.0,
        "mismatches": [row["case_id"] for row in results if not row["match"]],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑双风险对照集")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "reports",
        help="结果 JSON 落到哪个目录",
    )
    args = parser.parse_args(argv)

    results = [evaluate(case) for case in build_cases()]
    summary = summarize(results)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"dual-risk-{stamp}.json"
    path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"cases={summary['cases']}")
    print(f"dangerous_accuracy={summary['dangerous_accuracy']:.3f}")
    print(f"label_accuracy={summary['label_accuracy']:.3f}")
    print(f"silent_recall={summary['silent_recall']:.3f}")
    if summary["mismatches"]:
        print("mismatches=" + ", ".join(summary["mismatches"]))
    print(f"report={path}")

    ok = (
        summary["label_accuracy"] >= _MIN_ACCURACY
        and summary["silent_recall"] >= _REQUIRED_RECALL
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
