"""跑「用你的话问你的血糖」的评测集：一句一句折窗口，对不上的直接打出来。

    python -m evals.glucose_query.runner

报告写到 `api/evals/reports/glucose-query-<UTC>.json`。这个评测不调模型、不连
数据库：它只回答一个工程问题——我们承诺认得出的那些说法，现在还算不算得对。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from antang_api.health_profile.query_parse import parse_glucose_phrase

from evals.glucose_query.cases import NOW, GlucoseQueryCase, build_cases

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    phrase: str
    understood: bool
    window_ok: bool
    contexts_ok: bool
    got: str
    expected: str

    @property
    def ok(self) -> bool:
        return self.understood and self.window_ok and self.contexts_ok


def evaluate(case: GlucoseQueryCase) -> CaseResult:
    query = parse_glucose_phrase(
        case.phrase, now=NOW, timezone_name="Asia/Shanghai"
    )
    expected = f"{case.start} → {case.end} {case.contexts or ''}".strip()
    if query is None:
        return CaseResult(
            case_id=case.case_id,
            phrase=case.phrase,
            understood=False,
            window_ok=False,
            contexts_ok=False,
            got="（没认出来）",
            expected=expected,
        )
    got_start = query.start.strftime("%Y-%m-%d %H:%M")
    got_end = query.end.strftime("%Y-%m-%d %H:%M")
    got = f"{got_start} → {got_end} {query.contexts or ''}".strip()
    return CaseResult(
        case_id=case.case_id,
        phrase=case.phrase,
        understood=True,
        window_ok=(got_start == case.start and got_end == case.end),
        contexts_ok=query.contexts == case.contexts,
        got=got,
        expected=expected,
    )


def summarize(results: list[CaseResult]) -> dict[str, object]:
    total = len(results)
    understood = sum(1 for result in results if result.understood)
    windows = sum(1 for result in results if result.window_ok)
    contexts = sum(1 for result in results if result.contexts_ok)
    return {
        "cases": total,
        "understood": understood,
        "understood_rate": understood / total if total else 0.0,
        "window_accuracy": windows / total if total else 0.0,
        "context_accuracy": contexts / total if total else 0.0,
        "misses": [result.case_id for result in results if not result.ok],
    }


def main() -> int:
    results = [evaluate(case) for case in build_cases()]
    report = summarize(results)
    report["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    report["details"] = [
        {
            "case_id": result.case_id,
            "phrase": result.phrase,
            "got": result.got,
            "expected": result.expected,
            "ok": result.ok,
        }
        for result in results
    ]
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / f"glucose-query-{stamp}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"cases={report['cases']} understood={report['understood_rate']:.3f} "
        f"window={report['window_accuracy']:.3f} "
        f"context={report['context_accuracy']:.3f}"
    )
    for result in results:
        if not result.ok:
            print(f"  MISS {result.case_id}: 这一句「{result.phrase}」")
            print(f"       got      {result.got}")
            print(f"       expected {result.expected}")
    print(f"报告：{path}")
    return 0 if not report["misses"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
