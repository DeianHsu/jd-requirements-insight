"""该模块验证JD导入、列表查看和评测指标格式化的用户行为。"""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import cli, format_accuracy, format_item_metrics, select_visible_issues
from app.evaluation import ItemMatchMetrics

runner = CliRunner()


def test_cli_import_and_list(tmp_path: Path, monkeypatch) -> None:
    """验证CLI能把一份Markdown JD导入临时数据库并在列表中显示。"""
    # 动态创建测试JD，使测试不依赖项目中的真实招聘数据。
    jd_directory = tmp_path / "jds"
    jd_directory.mkdir()
    (jd_directory / "jd.md").write_text(
        """---
collected_at: 2026-07-21
company: CLI测试公司
title: RAG工程师
city: 上海
salary: 15-25K
---

# RAG工程师

负责知识库检索与评测。
""",
        encoding="utf-8",
    )
    # 通过环境变量把CLI切换到临时数据库，防止污染真实数据文件。
    database_path = tmp_path / "cli.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    # 使用Typer测试运行器模拟用户连续执行导入和列表命令。
    imported = runner.invoke(cli, ["import-jds", str(jd_directory)])
    listed = runner.invoke(cli, ["list-jds"])

    assert imported.exit_code == 0
    assert "成功导入 1" in imported.stdout
    assert listed.exit_code == 0
    assert "CLI测试公司" in listed.stdout
    assert "RAG工程师" in listed.stdout


def test_cli_formats_not_applicable_evaluation_metrics() -> None:
    """验证没有适用样本时显示N/A，而不把它误报为真实的0%准确率。"""
    assert format_accuracy(0.0, 0) == "N/A"
    assert format_item_metrics(ItemMatchMetrics()) == "N/A"
    assert format_accuracy(0.5, 2) == "50.00%"


def test_cli_limits_issue_output_and_exposes_safe_extraction_scope() -> None:
    """验证评测错误只显示摘要，并提供小批量、指定ID和显式全量抽取选项。"""
    visible, omitted = select_visible_issues(["问题1", "问题2", "问题3"], 2)
    help_result = runner.invoke(cli, ["extract-jds", "--help"])

    assert visible == ["问题1", "问题2"]
    assert omitted == 1
    assert help_result.exit_code == 0
    assert "--limit" in help_result.stdout
    assert "--job-id" in help_result.stdout
    assert "--all" in help_result.stdout


def test_cli_consolidate_help_exposes_scope_options() -> None:
    """验证归并命令的选项覆盖指定JD、全量和重试次数。"""
    help_result = runner.invoke(cli, ["consolidate-requirements", "--help"])

    assert help_result.exit_code == 0
    assert "--job-id" in help_result.stdout
    assert "--all" in help_result.stdout
    assert "--max-attempts" in help_result.stdout


def test_cli_consolidate_rejects_conflicting_options() -> None:
    """验证--all与--job-id互斥并给出明确错误。"""
    result = runner.invoke(
        cli, ["consolidate-requirements", "--all", "--job-id", "1"]
    )

    assert result.exit_code == 2
    assert "--all不能与--job-id同时使用" in result.stdout


def test_cli_consolidate_reports_empty_pool_error(
    tmp_path: Path, monkeypatch
) -> None:
    """验证空数据库时归并命令输出明确错误并以非零码退出。"""
    from app.config import LLMSettings

    database_path = tmp_path / "cli_consolidate.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setattr(
        "app.cli.load_llm_settings",
        lambda: LLMSettings(api_key="test-key", model="test-model"),
    )

    result = runner.invoke(cli, ["consolidate-requirements"])

    assert result.exit_code == 1
    assert "没有可归并的要求实例" in result.stdout
