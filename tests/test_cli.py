"""该模块验证JD导入和列表查看命令的端到端用户行为。"""

from pathlib import Path

from typer.testing import CliRunner

from app.cli import cli

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
