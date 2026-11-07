"""跑两稿比对的小集子：`ensure_same_numbers` 的判断与人工标注是否一致。

不调模型，随时能跑：

    python -m evals.two_draft.runner

要求两件事同时成立才算过：全部样例判对（`agreement == 1.0`），
以及「该拦下的」一条都没漏（`blocked_rate == 1.0`）——漏放一次改过数字的稿子，
等于让他看到一句我们没核对过的话。

结果 JSON 写进 `evals/reports/`（该目录只保留 .gitignore，不提交结果）。
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from antang_api.psychosocial.two_draft import DraftMismatch, ensure_same_numbers

from evals.two_draft.cases import TwoDraftEvalCase, build_cases

_MIN_AGREEMENT = 1.0


def evaluate(case: TwoDraftEvalCase) -> dict[str, Any]:
    """判一条：不抛错 = 两稿数字一致。"""

    changed: str | None = None
    try:
        ensure_same_numbers(case.first, case.second)
    except DraftMismatch as error:
        changed = str(error)
    predicted_same = changed is None
    return {
        **asdict(case),
        "predicted_same": predicted_same,
        "mismatch": changed,
        "match": predicted_same == case.same,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    matched = sum(1 for item in results if item["match"])
    should_block = [item for item in results if not item["same"]]
    blocked = sum(1 for item in should_block if not item["predicted_same"])
    misses = [item["case_id"] for item in results if not item["match"]]
    return {
        "cases": total,
        "agreement": round(matched / total, 3) if total else 0.0,
        "should_block": len(should_block),
        "blocked_rate": round(blocked / len(should_block), 3) if should_block else 0.0,
        "misses": misses,
    }


def _write_report(summary: dict[str, Any], results: list[dict[str, Any]]) -> Path:
    directory = Path(__file__).resolve().parent.parent / "reports"
    directory.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"two-draft-{stamp}.json"
    path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="两稿数字比对的回归集")
    parser.add_argument("--verbose", action="store_true", help="打印每一条")
    arguments = parser.parse_args()

    results = [evaluate(case) for case in build_cases()]
    summary = summarize(results)
    for item in results:
        if arguments.verbose or not item["match"]:
            mark = "ok" if item["match"] else "WRONG"
            print(f"{mark} {item['case_id']}: {item['why']}")
    print(json.dumps(summary, ensure_ascii=False))
    path = _write_report(summary, results)
    print(f"报告：{path}")

    if summary["agreement"] < _MIN_AGREEMENT or summary["blocked_rate"] < _MIN_AGREEMENT:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
