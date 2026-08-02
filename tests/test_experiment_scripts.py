"""该模块验证实验脚本导入安全性、私有输出边界和真实调用显式确认。"""

import sys
from pathlib import Path

import pytest

from scripts.experiments.p0_3 import evaluate_two_stage_results
from scripts.experiments.p0_3 import run_two_stage_extraction


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
