"""[legacy] 该模块离线评测P0-3两段式抽取结果，并生成开发集、回归集与验证集对照报告。

legacy protocol（DEC-015）：仅用于历史比较和案例分析，不属于当前正式
验收，不得用于批准新的 Prompt。当前正式验收见
scripts/experiments/p0_3/run_acceptance.py。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.evaluation import evaluate_annotation_cases
from app.schemas import JobExtractionResult

DEFAULT_CASES_PATH = Path("data/private/annotation_cases.json")
DEFAULT_RESULTS_PATH = Path(
    "data/private/experiments/p0_3/two_stage_results.json"
)
DEFAULT_OUTPUT_PATH = Path(
    "reports/experiments/p0_3/two_stage_evaluation.md"
)

SPLITS = ("development", "regression", "validation")


def parse_args() -> argparse.Namespace:
    """解析私有标注、实验结果、JD原文和评测报告的显式或默认路径。"""
    parser = argparse.ArgumentParser(description="评测P0-3两段式抽取结果")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS_PATH)
    parser.add_argument(
        "--source-texts",
        type=Path,
        default=None,
        help="私有JD原文JSON（{source_file: 全文}），提供后才会统计证据存在率",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def _failure_rows(
    cases: dict[str, Any],
    predictions: dict[str, JobExtractionResult],
    split: str,
) -> list[list[str]]:
    """按case粒度重算数量与匹配，返回只含计数、不含私有名称的失败明细。"""
    rows: list[list[str]] = []
    for raw_case in cases.get("cases", []):
        if raw_case.get("dataset_split") != split:
            continue
        case_id = str(raw_case.get("case_id", "unknown"))
        target = str(raw_case.get("annotation_target", ""))
        metrics = evaluate_annotation_cases({"cases": [raw_case]}, predictions)
        if metrics.evaluated_cases == 0:
            rows.append(
                [case_id, target, "-", "-", "否", "-", "-", "缺失来源"]
            )
            continue
        if target == "requirements":
            expected = metrics.requirement_metrics.expected
            predicted = metrics.requirement_metrics.predicted
            matched = metrics.requirement_metrics.matched
        else:
            expected = metrics.responsibility_metrics.expected
            predicted = metrics.responsibility_metrics.predicted
            matched = metrics.responsibility_metrics.matched
        exact = "是" if expected == predicted else "否"
        if exact == "是" and matched == expected:
            continue
        rows.append(
            [
                case_id,
                target,
                str(expected),
                str(predicted),
                exact,
                str(expected - matched),
                str(predicted - matched),
                "-",
            ]
        )
    return rows


def build_report(
    cases: dict[str, Any],
    predictions: dict[str, JobExtractionResult],
    source_texts: dict[str, str] | None = None,
) -> str:
    """把两段式预测按数据集分组评测并格式化为Markdown报告。"""
    lines = [
        "# P0-3 两段式（发现+判断）困难样例评测\n",
        f"参与来源：{sorted(predictions.keys())}\n",
    ]
    for split in SPLITS:
        split_cases = [
            case
            for case in cases.get("cases", [])
            if case.get("dataset_split") == split
        ]
        if not split_cases:
            lines.extend([f"## {split}\n", "- 无样例。\n", ""])
            continue
        metrics = evaluate_annotation_cases(
            cases, predictions, source_texts=source_texts, dataset_split=split
        )
        requirement = metrics.requirement_metrics
        responsibility = metrics.responsibility_metrics
        evidence_note = "" if source_texts is not None else "（未提供JD原文，未统计）"
        lines.extend(
            [
                f"## {split}\n",
                f"- 样例数：{metrics.discovered_cases}"
                f"（评测{metrics.evaluated_cases}，"
                f"缺失{len(metrics.missing_sources)}）",
                f"- 要求名称代理 F1：{requirement.f1:.2%}"
                f"（P={requirement.precision:.2%} "
                f"R={requirement.recall:.2%}）",
                f"- 职责名称代理 F1：{responsibility.f1:.2%}"
                f"（P={responsibility.precision:.2%} "
                f"R={responsibility.recall:.2%}）",
                f"- 原子项数量一致：{metrics.exact_count_cases}/"
                f"{metrics.evaluated_cases}",
                f"- importance准确率：{metrics.importance_accuracy:.2%}"
                f"（{metrics.importance_correct}/{metrics.importance_total}）",
                f"- proficiency准确率：{metrics.proficiency_accuracy:.2%}"
                f"（{metrics.proficiency_correct}/{metrics.proficiency_total}）",
                f"- category准确率：{metrics.category_accuracy:.2%}"
                f"（{metrics.category_correct}/{metrics.category_total}）",
                f"- years准确率：{metrics.years_correct}/{metrics.years_total}",
                f"- any_of准确率：{metrics.any_of_groups_correct}/"
                f"{metrics.any_of_groups_total}",
                f"- 证据存在率：{metrics.evidence_correct}/"
                f"{metrics.evidence_total}{evidence_note}",
                "",
            ]
        )
        failures = _failure_rows(cases, predictions, split)
        lines.append(f"### {split} 失败案例（case_id级，不含私有内容）")
        if failures:
            lines.extend(
                [
                    "",
                    "| case | 目标 | 预期项 | 预测项 | 数量一致 | 漏项 | 多项 | 备注 |",
                    "|---|---|---:|---:|---|---:|---:|---|",
                ]
            )
            lines.extend("| " + " | ".join(row) + " |" for row in failures)
        else:
            lines.append("\n- 无。")
        lines.append("")

    lines.extend(
        [
            "## V2.3.1 基线对比\n",
            "| 指标 | V2.3.1开发集 | V2.3.1回归集 | "
            "两段式开发集 | 两段式回归集 | 两段式验证集 |",
            "|---|---:|---:|---:|---:|---:|",
            "| 要求名称F1 | 100% | - | 见上 | 见上 | 见上 |",
            "| 职责名称F1 | 100% | 78.26% | 见上 | 见上 | 见上 |",
            "| 数量一致 | 10/10 | 2/5 | 见上 | 见上 | 见上 |",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    """读取私有标注和实验结果，跳过失败来源并写出离线评测报告。"""
    args = parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    raw_results = json.loads(args.results.read_text(encoding="utf-8"))
    predictions = {
        source_file: JobExtractionResult.model_validate(payload)
        for source_file, payload in raw_results.items()
        if "error" not in payload
    }
    source_texts = None
    if args.source_texts is not None:
        source_texts = json.loads(
            args.source_texts.read_text(encoding="utf-8")
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        build_report(cases, predictions, source_texts=source_texts),
        encoding="utf-8",
    )
    print(f"报告已写入 {args.output}")


if __name__ == "__main__":
    main()
