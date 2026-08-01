"""该模块验证JD导入、列表查看和评测指标格式化的用户行为。"""

import json
from datetime import date
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
    assert "--extractor-version" in help_result.stdout
    assert "--max-attempts" in help_result.stdout


def test_cli_consolidate_rejects_conflicting_options() -> None:
    """验证--all与--job-id互斥并给出明确错误。"""
    result = runner.invoke(
        cli, ["consolidate-requirements", "--all", "--job-id", "1"]
    )

    assert result.exit_code == 2
    assert "必须且只能选择--all或--job-id之一" in result.stdout


def test_cli_consolidate_requires_explicit_scope() -> None:
    """验证归并命令不允许隐式选择全部JD。"""
    result = runner.invoke(cli, ["consolidate-requirements"])

    assert result.exit_code == 2
    assert "必须且只能选择--all或--job-id之一" in result.stdout


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

    result = runner.invoke(cli, ["consolidate-requirements", "--all"])

    assert result.exit_code == 1
    assert "选定范围内没有JD" in result.stdout


def test_cli_list_consolidations_empty_and_with_records(
    tmp_path: Path, monkeypatch
) -> None:
    """验证list-consolidations在空库提示无记录，有批次时显示摘要。"""
    from app.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )
    from app.models import (
        CanonicalRequirementRecord,
        JobConsolidation,
        JobDescription,
        JobExtraction,
        JobRequirement,
        RequirementMappingRecord,
    )

    database_path = tmp_path / "cli_list.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    engine = create_database_engine()
    initialize_database(engine)
    session_factory = create_session_factory(engine)

    empty = runner.invoke(cli, ["list-consolidations"])
    assert empty.exit_code == 0
    assert "还没有归并批次" in empty.stdout

    with session_factory() as session:
        session.add(
            JobDescription(
                id=1,
                source_hash="a" * 64,
                source_file="job-a.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="示例公司",
                title="示例岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="具备能力甲",
            )
        )
        session.add(
            JobExtraction(
                id=1,
                job_id=1,
                extractor_version="test-model|prompt:1.0|schema:2.0",
                model_name="test-model",
                prompt_version="1.0",
                schema_version="2.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
        )
        session.add(
            JobRequirement(
                id=1,
                extraction_id=1,
                raw_name="能力甲",
                category="other",
                importance="must",
                proficiency="unknown",
                group_logic="standalone",
                evidence="具备能力甲",
                confidence=0.9,
            )
        )
        record = JobConsolidation(
            scope_key="all",
            consolidator_version="test-model|prompt:1.4|schema:1.0",
            input_fingerprint="a" * 64,
            extractor_version="test-model|prompt:1.0|schema:2.0",
            selected_job_ids=[1],
            extraction_ids=[1],
            model_name="test-model",
            prompt_version="1.4",
            schema_version="1.0",
            occurrence_count=2,
            raw_response={},
        )
        session.add(record)
        session.flush()
        record.canonical_requirements.append(
            CanonicalRequirementRecord(
                canonical_requirement_id="c1",
                canonical_name="能力甲",
                rationale="测试",
                confidence=0.9,
            )
        )
        record.mappings.append(
            RequirementMappingRecord(
                requirement_id=1,
                status="mapped",
                canonical_requirement_id="c1",
                candidate_requirement_ids=[],
                rationale="测试",
                confidence=0.9,
            )
        )
        session.commit()
        engine.dispose()

    listed = runner.invoke(cli, ["list-consolidations"])
    assert listed.exit_code == 0
    assert "已持久化归并批次" in listed.stdout
    assert "all" in listed.stdout
    assert "标准项" in listed.stdout

    cases_path = tmp_path / "consolidation_cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "expected": {
                    "canonical_requirements": [
                        {
                            "canonical_requirement_id": "expected-c1",
                            "canonical_name": "能力甲",
                        }
                    ],
                    "mappings": [
                        {
                            "requirement_id": 1,
                            "status": "mapped",
                            "canonical_requirement_id": "expected-c1",
                        }
                    ],
                    "relations": [],
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    evaluated = runner.invoke(
        cli,
        [
            "evaluate-consolidation",
            str(cases_path),
            "--consolidation-id",
            "1",
        ],
    )
    missing = runner.invoke(
        cli,
        [
            "evaluate-consolidation",
            str(cases_path),
            "--consolidation-id",
            "999",
        ],
    )

    assert evaluated.exit_code == 0
    assert "归并批次ID 1" in evaluated.stdout
    assert "映射准确率 100.00% (1/1)" in evaluated.stdout
    assert "关系Precision N/A" in evaluated.stdout
    assert missing.exit_code == 1
    assert "归并批次不存在：999" in missing.stdout
