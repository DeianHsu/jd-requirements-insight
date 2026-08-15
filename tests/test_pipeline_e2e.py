"""主线端到端测试：import → extract → consolidate → statistics。

从虚构 Markdown JD 开始，用假 LLM 客户端走完整条主线，验证市场统计
模块能消费归并批次并输出可排序的市场数据。同时验证文档中出现的命令
与文件路径真实存在（文档合同）。
"""

import json
import sys
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
            {
                "raw_name": "能力丙",
                "category": "other",
                "importance": "mentioned",
                "proficiency": "unknown",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "具备能力丙使用经验",
                "confidence": 0.8,
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
                "source_requirement_ids": [1, 4],
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
                "canonical_requirement_id": "cr-skill-c",
                "canonical_name": "能力丙",
                "source_requirement_ids": [3],
                "rationale": "独立条件",
                "confidence": 0.85,
            },
            {
                "canonical_requirement_id": "cr-skill-d",
                "canonical_name": "能力丁",
                "source_requirement_ids": [5],
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
                "canonical_requirement_id": "cr-skill-c",
                "rationale": "独立",
                "confidence": 0.85,
            },
            {
                "requirement_id": 4,
                "canonical_requirement_id": "cr-tech",
                "rationale": "同条件",
                "confidence": 0.95,
            },
            {
                "requirement_id": 5,
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
    """返回单次 canonical 聚类响应（mappings 由确定性代码生成）。"""

    def __init__(self, settings) -> None:
        """保存模型名。"""
        self.model_name = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回单次合法 canonical 聚类响应（任务提示只请求标准要求项分区，
        mappings 由确定性代码生成，模型不输出）。"""
        return json.dumps(
            {
                "canonical_requirements": _consolidation_payload()[
                    "canonical_requirements"
                ]
            },
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
    """最小合成闭环：候选不入库，审核定稿后才可统计。"""
    jd_dir = tmp_path / "jds"
    jd_dir.mkdir()
    _write_jd_files(jd_dir)
    database_path = tmp_path / "e2e.db"

    database_args = ["--database-url", f"sqlite:///{database_path.as_posix()}"]

    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleExtractionClient", FakeExtractionClient
    )
    monkeypatch.setattr(
        cli_module, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    # 1. import-jds
    result = runner.invoke(cli, ["import-jds", str(jd_dir), *database_args])
    assert result.exit_code == 0, result.output

    # 2. 模型入口只生成候选，不写正式抽取表。
    extraction_candidate = tmp_path / "extraction-candidate.json"
    result = runner.invoke(
        cli,
        [
            "extract-jds",
            "--all",
            "--execute",
            "--candidate-output",
            str(extraction_candidate),
            *database_args,
        ],
    )
    assert result.exit_code == 0, result.output
    assert extraction_candidate.exists()  # 候选落盘（预检产物，不消费）
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        assert session.query(JobExtraction).count() == 0
    engine.dispose()

    # 3. 真实验收：run_real_jd_acceptance（多次运行 + 合同检查 + 人工审核）
    #    生成 report/raw；随后逐 JD 离线定稿（批量验收产物，整轮
    #    identity job_ids 含全部 JD）。
    import scripts.experiments.p0_3.run_real_jd_acceptance as acceptance

    monkeypatch.setattr(acceptance, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance, "OpenAICompatibleExtractionClient", FakeExtractionClient
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_jd_acceptance",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--all",
            "--runs",
            "3",
            "--report-dir",
            str(tmp_path),
            "--raw-output-dir",
            str(tmp_path),
            "--run-tag",
            "e2e-acceptance",
            "--execute",
        ],
    )
    assert acceptance.main() == 0

    acceptance_report = tmp_path / "e2e-acceptance-report.json"
    acceptance_raw = tmp_path / "e2e-acceptance-raw.json"
    assert acceptance_report.exists() and acceptance_raw.exists()

    # 人工审核（模拟人工步骤，作用于真实验收产物）：批准每份 JD 的
    # run0 并记录结果指纹；之后 finalize 核对审核身份。
    report = json.loads(acceptance_report.read_text(encoding="utf-8"))
    raw = json.loads(acceptance_raw.read_text(encoding="utf-8"))
    for entry in report["jobs"]:
        job_id = entry["job_id"]
        entry["manual_review"] = {
            "reviewed_by": "synthetic-reviewer",
            "reviewed_at": "2026-08-06T00:00:00+00:00",
            "approved_run_index": 0,
            "approved_result_fingerprint": raw[
                f"job{job_id}_run0"
            ]["result_fingerprint"],
            "conclusion": "合成验收人工批准 run0",
        }
    acceptance_report.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        job_ids = [job.id for job in session.query(JobDescription).all()]
    engine.dispose()
    for job_id in job_ids:
        result = runner.invoke(
            cli,
            [
                "finalize-extraction",
                "--report",
                str(acceptance_report),
                "--raw-output",
                str(acceptance_raw),
                "--job-id",
                str(job_id),
                *database_args,
            ],
        )
        assert result.exit_code == 0, result.output

    # 4. 归并入口同样只生成候选。
    consolidation_candidate = tmp_path / "consolidation-candidate.json"
    result = runner.invoke(
        cli,
        [
            "consolidate-requirements",
            "--all",
            "--execute",
            "--candidate-output",
            str(consolidation_candidate),
            *database_args,
        ],
    )
    assert result.exit_code == 0, result.output
    consolidation = json.loads(
        consolidation_candidate.read_text(encoding="utf-8")
    )
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        assert session.query(JobConsolidation).count() == 0
    engine.dispose()

    # 5. 真实验收：run_acceptance（独立运行 + 顺序变形 + 合同门禁）→
    #    人工审核决定（模拟人工）→ apply_review_decisions → 定稿。
    import scripts.experiments.p0_4.run_acceptance as run_acceptance

    monkeypatch.setattr(
        run_acceptance, "load_llm_settings", lambda: FakeSettings()
    )
    monkeypatch.setattr(
        run_acceptance,
        "OpenAICompatibleConsolidationClient",
        FakeConsolidationClient,
    )
    acceptance_report = tmp_path / "acceptance-report.json"
    acceptance_raw = tmp_path / "acceptance-raw.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--runs",
            "2",
            "--report",
            str(acceptance_report),
            "--raw-output",
            str(acceptance_raw),
            "--execute",
        ],
    )
    assert run_acceptance.main() == 0

    # 人工审核决定（模拟人工步骤：批准来源运行，不附加合并/拆分裁决）。
    raw = json.loads(acceptance_raw.read_text(encoding="utf-8"))
    decisions_path = tmp_path / "review-decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": raw["selected_job_ids"],
                "model": raw["model"],
                "prompt_version": raw["prompt_version"],
                "schema_version": raw["schema_version"],
                "reviewed_by": "synthetic-reviewer",
                "reviewed_at": "2026-08-06T00:00:00+00:00",
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    import scripts.experiments.p0_4.apply_review_decisions as apply_decisions

    final_result = tmp_path / "final-consolidation.json"
    apply_report = tmp_path / "apply-report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_review_decisions",
            "--raw-output",
            str(acceptance_raw),
            "--review-decisions",
            str(decisions_path),
            "--output",
            str(final_result),
            "--report",
            str(apply_report),
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
        ],
    )
    assert apply_decisions.main() == 0

    # 人工 cluster 审核（模拟人工步骤，作用于真实验收报告）：批准
    # run-0 并记录结果指纹；之后 finalize 核对审核身份。
    acceptance_report = tmp_path / "acceptance-report.json"
    report = json.loads(acceptance_report.read_text(encoding="utf-8"))
    report["manual_cluster_review"] = {
        "clusters": report["manual_cluster_review"]["clusters"],
        "reviewed_by": "synthetic-reviewer",
        "reviewed_at": "2026-08-06T00:00:00+00:00",
        "approved_run_index": 0,
        "approved_result_fingerprint": raw["runs"][0]["result_fingerprint"],
        "conclusion": "合成验收人工批准 run-0",
        "notes": "",
    }
    acceptance_report.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    result = runner.invoke(
        cli,
        [
            "finalize-consolidation",
            "--report",
            str(acceptance_report),
            "--raw-output",
            str(acceptance_raw),
            "--final-result",
            str(final_result),
            "--review-decisions",
            str(decisions_path),
            *database_args,
        ],
    )
    assert result.exit_code == 0, result.output

    # 6. statistics：正式批次可统计。
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

    # 7. 生产主线终点：真实 generate-report CLI 生成 Markdown 报告。
    #    全链定稿（抽取 fully_bound + 归并完整审核元数据）→ 无来源绑定标注。
    report_output = tmp_path / "market-report.md"
    result = runner.invoke(
        cli,
        [
            "generate-report",
            "--consolidation-id",
            str(consolidation_id),
            "--output",
            str(report_output),
            *database_args,
        ],
    )
    assert result.exit_code == 0, result.output
    report_text = report_output.read_text(encoding="utf-8")
    assert "岗位要求市场分析报告" in report_text
    assert "跨 JD 共同要求" in report_text
    assert "证据追溯" in report_text
    assert "技术甲" in report_text
    assert "**上游来源绑定**：" not in report_text  # 全链 fully_bound


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
    database_args = ["--database-url", f"sqlite:///{database_path.as_posix()}"]
    result = runner.invoke(cli, ["import-jds", str(jd_dir), *database_args])
    assert result.exit_code == 0, result.output

    result = runner.invoke(cli, ["extract-jds", "--all", *database_args])
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

    result = runner.invoke(
        cli,
        [
            "consolidate-requirements",
            "--all",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
        ],
    )
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
        "app.extraction_finalization",
        "app.consolidation_finalization",
        "scripts.experiments.p0_3.run_acceptance",
        "scripts.experiments.p0_3.run_real_jd_acceptance",
        "scripts.experiments.p0_4.run_acceptance",
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
    assert Path("docs/GLOSSARY.md").exists()
    assert Path("docs/annotation/REQUIREMENTS.md").exists()
    assert Path("docs/annotation/VALIDATION.md").exists()

    # 关键示例命令包含脚本要求的必要参数（文档合同）。
    readme = Path("README.md").read_text(encoding="utf-8")
    normalized_readme = " ".join(
        line.strip().removesuffix("`").strip() for line in readme.splitlines()
    )
    normalized_readme = " ".join(normalized_readme.split())
    assert "extract-jds --all --candidate-output" in normalized_readme
    assert "consolidate-requirements --all --candidate-output" in normalized_readme
    assert "finalize-extraction --report" in normalized_readme
    assert "finalize-consolidation --report" in normalized_readme
    assert "--use-project-database" in normalized_readme
    assert (
        "run_real_jd_acceptance --use-project-database --all --execute"
        in normalized_readme
    )
    assert (
        "scripts.experiments.p0_4.run_acceptance --database-url"
        in normalized_readme
    )
    assert "--raw-output data/private/consolidation-acceptance-raw.json" in (
        normalized_readme
    )
    assert "run_acceptance --use-project-database --all --execute" not in (
        normalized_readme
    )
    current_state = Path("docs/CURRENT_STATE.md").read_text(encoding="utf-8")
    assert "单次" in current_state
    assert "不作为 finalize" in current_state
    validation = Path("docs/annotation/VALIDATION.md").read_text(
        encoding="utf-8"
    )
    assert "--use-project-database" in validation
    assert "--database-url" in validation
