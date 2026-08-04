"""主线端到端测试：import → extract → consolidate → statistics。

从虚构 Markdown JD 开始，用假 LLM 客户端走完整条主线，验证市场统计
模块能消费归并批次并输出可排序的市场数据。同时验证文档中出现的命令
与文件路径真实存在（文档合同）。
"""

import json
from datetime import date
from pathlib import Path

from app.cli import cli
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.market_analysis import build_market_statistics
from app.models import (
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
)
from typer.testing import CliRunner

runner = CliRunner()


def _write_jd_files(directory: Path) -> None:
    """写入两份带 frontmatter 的虚构 JD 的 Markdown 文件。"""
    jd_1 = """---
collected_at: 2026-08-03
company: 示例公司甲
title: 岗位甲
company_type: unknown
---

# 岗位甲

负责技术甲体系建设。

熟悉技术甲和框架乙。

具备能力丙使用经验。
"""
    jd_2 = """---
collected_at: 2026-08-03
company: 示例公司乙
title: 岗位乙
company_type: unknown
---

# 岗位乙

负责技术甲平台运维。

熟悉技术甲。

了解能力丁。
"""
    (directory / "jd-1.md").write_text(jd_1, encoding="utf-8")
    (directory / "jd-2.md").write_text(jd_2, encoding="utf-8")


def _discovery_payload(raw_text: str) -> dict:
    """按 JD 分句生成合法发现段响应。"""
    from app.extraction_two_stage import split_sentences

    sentences = split_sentences(raw_text)
    blocks = []
    for index, sentence in enumerate(sentences):
        if index == 0:
            kind = "excluded"
        elif "负责" in sentence:
            kind = "responsibility"
        else:
            kind = "requirement"
        blocks.append(
            {
                "block_id": f"b{index}",
                "sentence_indexes": [index],
                "kind": kind,
                "source_span": sentence,
                "note": "测试",
            }
        )
    return {"role_family": "other", "seniority": "unknown", "blocks": blocks}


def _judge_payload(raw_text: str, job_id: int) -> dict:
    """按 JD 内容生成合法判断段响应（只输出 requirements）。"""
    requirements = []
    if "熟悉技术甲和框架乙" in raw_text:
        requirements = [
            {
                "raw_name": "技术甲",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "熟悉技术甲和框架乙",
                "confidence": 0.9,
            },
            {
                "raw_name": "框架乙",
                "category": "agent_framework",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "熟悉技术甲和框架乙",
                "confidence": 0.9,
            },
        ]
    elif "熟悉技术甲" in raw_text and "了解能力丁" in raw_text:
        requirements = [
            {
                "raw_name": "技术甲",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "熟悉技术甲",
                "confidence": 0.9,
            },
            {
                "raw_name": "能力丁",
                "category": "other",
                "importance": "mentioned",
                "proficiency": "unknown",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "了解能力丁",
                "confidence": 0.8,
            },
        ]
    return {"role_family": "other", "seniority": "unknown", "requirements": requirements}


def _consolidation_payload() -> dict:
    """把全部实例归并为两个 canonical 的合法响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "cr-tech",
                "canonical_name": "技术甲",
                "source_requirement_ids": [1, 3],
                "rationale": "多份JD要求技术甲",
                "confidence": 0.95,
            },
            {
                "canonical_requirement_id": "cr-framework",
                "canonical_name": "框架乙",
                "source_requirement_ids": [2],
                "rationale": "独立条件",
                "confidence": 0.9,
            },
            {
                "canonical_requirement_id": "cr-skill-d",
                "canonical_name": "能力丁",
                "source_requirement_ids": [4],
                "rationale": "独立条件",
                "confidence": 0.85,
            },
        ],
        "mappings": [
            {
                "requirement_id": 1,
                "canonical_requirement_id": "cr-tech",
                "rationale": "同条件",
                "confidence": 0.95,
            },
            {
                "requirement_id": 2,
                "canonical_requirement_id": "cr-framework",
                "rationale": "独立",
                "confidence": 0.9,
            },
            {
                "requirement_id": 3,
                "canonical_requirement_id": "cr-tech",
                "rationale": "同条件",
                "confidence": 0.95,
            },
            {
                "requirement_id": 4,
                "canonical_requirement_id": "cr-skill-d",
                "rationale": "独立",
                "confidence": 0.85,
            },
        ],
    }


class FakeExtractionClient:
    """按 JD 内容返回两段式抽取响应。"""

    def __init__(self, settings) -> None:
        """保存模型名。"""
        self.model_name = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回发现段或判断段响应。"""
        payload = json.loads(user_prompt)
        raw_text = "\n".join(
            item["text"] for item in payload.get("sentences", [])
        )
        if "全局发现" in system_prompt:
            return json.dumps(_discovery_payload(raw_text), ensure_ascii=False)
        job_id = hash(raw_text) % 100
        return json.dumps(_judge_payload(raw_text, job_id), ensure_ascii=False)


class FakeConsolidationClient:
    """返回单次 canonical 聚类响应。"""

    def __init__(self, settings) -> None:
        """保存模型名。"""
        self.model_name = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回单次合法 canonical 聚类响应（mappings 由确定性代码生成）。"""
        payload = json.loads(user_prompt)
        task = payload.get("task", "")
        if "只输出canonical_requirements" in task:
            return json.dumps(
                {"canonical_requirements": _consolidation_payload()["canonical_requirements"]},
                ensure_ascii=False,
            )
        return json.dumps(
            {"mappings": _consolidation_payload()["mappings"]},
            ensure_ascii=False,
        )


class FakeSettings:
    """最小LLM配置（不发起真实调用）。"""

    model = "test-model"
    api_key = "test-key"
    base_url = None

    def missing_fields(self) -> list[str]:
        """没有缺失字段。"""
        return []


def test_full_pipeline_import_extract_consolidate_statistics(
    tmp_path: Path, monkeypatch
) -> None:
    """主线端到端：import → extract → consolidate → statistics。"""
    jd_dir = tmp_path / "jds"
    jd_dir.mkdir()
    _write_jd_files(jd_dir)
    database_path = tmp_path / "e2e.db"

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleExtractionClient", FakeExtractionClient
    )
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    # 1. import-jds
    result = runner.invoke(cli, ["import-jds", str(jd_dir)])
    assert result.exit_code == 0, result.output

    # 2. extract-jds（全部；付费调用显式 --execute）
    result = runner.invoke(cli, ["extract-jds", "--all", "--execute"])
    assert result.exit_code == 0, result.output

    # 3. consolidate-requirements（付费调用显式 --execute）
    result = runner.invoke(cli, ["consolidate-requirements", "--all", "--execute"])
    assert result.exit_code == 0, result.output

    # 4. statistics：读取归并批次并验证独立 JD 计数。
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            consolidation = session.scalar(
                select_latest_consolidation()
            )
            assert consolidation is not None
            consolidation_id = consolidation.id
            extraction_count = len(
                session.query(JobExtraction).all()
            )
            job_count = len(session.query(JobDescription).all())
        assert job_count == 2
        assert extraction_count == 2

        stats = build_market_statistics(session_factory, consolidation_id)
        tech = next(
            item for item in stats.canonical_items if item.canonical_name == "技术甲"
        )
        # 技术甲在 2 份 JD 中各出现一次 → 独立 JD 数 = 2。
        assert tech.distinct_job_count == 2
        assert tech.instance_count == 2
        # 两套 importance 口径：实例级与 JD 级一致（每 JD 仅 must）。
        assert tech.importance_instance_counts == {"must": 2}
        assert tech.importance_job_counts == {"must": 2}
        # 稳定排序：技术甲（2 JD）最前（独立 JD 数优先）。
        assert stats.canonical_items[0].canonical_name == "技术甲"
    finally:
        engine.dispose()


def select_latest_consolidation():
    """返回最新归并批次的查询（按ID降序取第一条）。"""
    from sqlalchemy import select

    return select(JobConsolidation).order_by(JobConsolidation.id.desc()).limit(1)


def test_extract_jds_requires_execute_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    """extract-jds 无 --execute 时不初始化客户端、不调用模型并返回非零。"""
    jd_dir = tmp_path / "jds"
    jd_dir.mkdir()
    _write_jd_files(jd_dir)
    database_path = tmp_path / "execute.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "load_llm_settings", lambda: FakeSettings())

    # 若没有 --execute 仍会调用客户端，则下面断言失败（客户端构造会报错）。
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleExtractionClient", FakeExtractionClient
    )
    result = runner.invoke(cli, ["import-jds", str(jd_dir)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["extract-jds", "--all"])
    assert result.exit_code == 2
    assert "未执行" in result.output
    assert "--execute" in result.output
    assert "模型" in result.output


def test_consolidate_requires_execute_confirmation(
    tmp_path: Path, monkeypatch
) -> None:
    """consolidate-requirements 无 --execute 时返回非零并打印计划。"""
    import app.cli as cli_module

    database_path = tmp_path / "consolidate_execute.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setattr(cli_module, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    # 构造一份含 v0.10 抽取结果的数据库，使计划阶段可完成。
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="b" * 64,
                source_file="job-x.md",
                source_type="test",
                collected_at=date(2026, 8, 3),
                company="示例公司",
                title="示例岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# 示例岗位\n\n熟悉技术甲。",
            )
            session.add(job)
            session.flush()
            extraction = JobExtraction(
                job_id=job.id,
                extractor_version="test-model|prompt:0.10|schema:3.0",
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
            session.add(extraction)
            session.flush()
            session.add(
                JobRequirement(
                    extraction_id=extraction.id,
                    raw_name="技术甲",
                    category="other",
                    importance="must",
                    proficiency="basic",
                    group_id=None,
                    group_logic="standalone",
                    min_years=None,
                    max_years=None,
                    years_text=None,
                    evidence="熟悉技术甲",
                    confidence=0.9,
                )
            )
            session.commit()
    finally:
        engine.dispose()

    result = runner.invoke(cli, ["consolidate-requirements", "--all"])
    assert result.exit_code == 2
    assert "未执行" in result.output
    assert "--execute" in result.output
    assert "1 条要求实例" in result.output


def test_documented_commands_and_paths_exist() -> None:
    """文档中出现的命令与文件路径真实存在（文档合同）。"""
    import importlib

    import app.cli as cli_module
    import app.extraction

    # 文档中的命令入口模块可导入。
    for module_name in (
        "app.market_analysis",
        "app.requirement_consolidation",
        "scripts.experiments.p0_3.run_acceptance",
        "scripts.experiments.p0_3.run_real_jd_acceptance",
        "scripts.experiments.p0_4.run_acceptance",
        "scripts.experiments.p0_4.run_small_scale_precheck",
    ):
        importlib.import_module(module_name)

    assert cli_module.cli is not None
    assert app.extraction.PROMPT_VERSION == "0.10"
    assert app.extraction.SCHEMA_VERSION == "3.0"

    # 文档引用路径存在。
    assert Path("app/market_analysis.py").exists()
    assert Path("data/rule_scenarios/extraction_metamorphic_cases.json").exists()
    assert Path("docs/ARCHITECTURE.md").exists()
    assert Path("docs/CURRENT_STATE.md").exists()
    assert Path("docs/PROJECT_PLAN.md").exists()
    assert Path("docs/GLOSSARY.md").exists()
    assert Path("docs/annotation/REQUIREMENTS.md").exists()
    assert Path("docs/annotation/VALIDATION.md").exists()

    # 关键示例命令包含脚本要求的必要参数（文档合同）。
    readme = Path("README.md").read_text(encoding="utf-8")
    assert "extract-jds --all --execute" in readme
    assert "consolidate-requirements --all --execute" in readme
    assert (
        "run_real_jd_acceptance --use-project-database --all --execute"
        in readme
    )
    current_state = Path("docs/CURRENT_STATE.md").read_text(encoding="utf-8")
    assert (
        "run_real_jd_acceptance --use-project-database --all --execute"
        in current_state
    )
    validation = Path("docs/annotation/VALIDATION.md").read_text(
        encoding="utf-8"
    )
    assert "--use-project-database" in validation
    assert "--database-url" in validation
