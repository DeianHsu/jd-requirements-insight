"""该模块验证实验脚本导入安全性、私有输出边界和真实调用显式确认。"""

import sys
from pathlib import Path

import pytest

from app.schemas import (
    JobExtractionResult,
    RequirementCategory,
    RequirementImportance,
    RequirementItem,
    RoleFamily,
    Seniority,
)
from scripts.experiments.p0_3 import evaluate_two_stage_results
from scripts.experiments.p0_3 import run_two_stage_extraction

VALIDATION_CASE = {
    "case_id": "case_test_001",
    "dataset_split": "validation",
    "source_file": "jd_test.md",
    "sentence": "需要熟悉Python与Go语言",
    "annotation_target": "requirements",
    "expected": {
        "requirements": [
            {
                "raw_name": "Python",
                "category": "programming_language",
                "importance": "must",
                "evidence": "需要熟悉Python与Go语言",
                "confidence": 1.0,
            },
            {
                "raw_name": "Go",
                "category": "programming_language",
                "importance": "must",
                "evidence": "需要熟悉Python与Go语言",
                "confidence": 1.0,
            },
        ]
    },
}


def _prediction(
    names: list[str],
) -> JobExtractionResult:
    """按给定要求名称构造最小合法的两段式预测结果。"""
    return JobExtractionResult(
        role_family=RoleFamily.LLM_APPLICATION,
        seniority=Seniority.MID,
        responsibilities=[],
        requirements=[
            RequirementItem(
                raw_name=name,
                category=RequirementCategory.PROGRAMMING_LANGUAGE,
                importance=RequirementImportance.MUST,
                evidence="需要熟悉Python与Go语言",
                confidence=1.0,
            )
            for name in names
        ],
    )


def test_two_stage_experiment_requires_explicit_execute(
    monkeypatch,
) -> None:
    """验证真实实验在读取配置和数据库前要求显式execute确认。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_two_stage_extraction", "--use-project-database"],
    )

    with pytest.raises(SystemExit, match="--execute"):
        run_two_stage_extraction.main()


def test_experiment_defaults_keep_raw_results_private() -> None:
    """验证原始模型结果默认留在私有目录，报告进入实验报告目录。"""
    assert run_two_stage_extraction.DEFAULT_OUTPUT_PATH.is_relative_to(
        Path("data/private/experiments")
    )
    assert evaluate_two_stage_results.DEFAULT_RESULTS_PATH.is_relative_to(
        Path("data/private/experiments")
    )
    assert evaluate_two_stage_results.DEFAULT_OUTPUT_PATH.is_relative_to(
        Path("reports/experiments")
    )


def test_two_stage_run_supports_job_id_selection(monkeypatch) -> None:
    """验证--job-id可重复指定并限制实验范围，缺省为空代表全部JD。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_two_stage_extraction",
            "--use-project-database",
            "--job-id",
            "1",
            "--job-id",
            "3",
        ],
    )
    args = run_two_stage_extraction.parse_args()
    assert args.job_id == [1, 3]

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_two_stage_extraction", "--use-project-database"],
    )
    assert run_two_stage_extraction.parse_args().job_id is None


def test_two_stage_evaluation_covers_all_splits() -> None:
    """验证离线评测报告覆盖开发、回归和未见验证三个数据分组。"""
    report = evaluate_two_stage_results.build_report(
        {"cases": [VALIDATION_CASE]},
        {"jd_test.md": _prediction(["Python", "Go"])},
    )
    for split in ("development", "regression", "validation"):
        assert f"## {split}" in report
    assert "## V2.3.1 基线对比" in report


def test_two_stage_evaluation_reports_failures_without_private_names() -> None:
    """验证失败案例表只输出case_id与计数，不复制私有名称内容。"""
    report = evaluate_two_stage_results.build_report(
        {"cases": [VALIDATION_CASE]},
        {"jd_test.md": _prediction(["Python"])},
    )
    assert "| case_test_001 | requirements | 2 | 1 | 否 | 1 | 0 | - |" in report
    assert "Python" not in report
    assert "Go" not in report


def test_two_stage_evaluation_marks_missing_prediction_source() -> None:
    """验证预测缺失来源的case在失败案例表中标记为缺失来源。"""
    report = evaluate_two_stage_results.build_report(
        {"cases": [VALIDATION_CASE]}, {}
    )
    assert "| case_test_001 | requirements | - | - | 否 | - | - | 缺失来源 |" in report


def test_two_stage_evaluation_omits_fully_matched_cases() -> None:
    """验证全部匹配的case不出现在失败案例表中。"""
    report = evaluate_two_stage_results.build_report(
        {"cases": [VALIDATION_CASE]},
        {"jd_test.md": _prediction(["Python", "Go"])},
    )
    assert "case_test_001" not in report.split("### validation")[1]
