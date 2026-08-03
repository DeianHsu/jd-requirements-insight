"""验证P0-4批量归并执行：装配、模型调用、失败隔离与汇总摘要。"""

import json
from datetime import date
from pathlib import Path

from app.consolidation import (
    ConsolidatorMetadata,
    consolidate_requirements,
)
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import JobDescription, JobExtraction, JobRequirement


class FakeConsolidationClient:
    """按预设顺序返回JSON文本，用于替代真实且有费用的LLM调用。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存待返回响应并初始化调用次数。"""
        self.responses = responses
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应。"""
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


def make_database(tmp_path: Path):
    """创建包含全部抽取数据表的临时SQLite数据库。"""
    database_path = tmp_path / "consolidation_run.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def make_job(job_id: int, source_file: str) -> JobDescription:
    """创建使用中性虚构领域名称的JD记录。"""
    return JobDescription(
        id=job_id,
        source_hash=f"{job_id:064x}",
        source_file=source_file,
        source_type="test",
        collected_at=date(2026, 7, 21),
        company="示例公司",
        title="示例岗位",
        company_type="medium_company",
        tags=[],
        extra_metadata={},
        raw_text="# 示例岗位\n\n具备能力甲使用经验。",
    )


def make_extraction(extraction_id: int, job_id: int) -> JobExtraction:
    """创建一份抽取主记录。"""
    return JobExtraction(
        id=extraction_id,
        job_id=job_id,
        extractor_version="test-model|prompt:0.8|schema:3.0",
        model_name="test-model",
        prompt_version="1.0",
        schema_version="3.0",
        role_family="other",
        seniority="unknown",
        raw_response={},
    )


def make_requirement(
    requirement_id: int, extraction_id: int, raw_name: str
) -> JobRequirement:
    """创建一条要求记录。"""
    return JobRequirement(
        id=requirement_id,
        extraction_id=extraction_id,
        raw_name=raw_name,
        category="other",
        importance="must",
        proficiency="unknown",
        group_id=None,
        group_logic="standalone",
        min_years=None,
        max_years=None,
        years_text=None,
        evidence=f"具备{raw_name}使用经验。",
        confidence=0.9,
    )


def valid_result_payload() -> dict[str, object]:
    """生成两个实例归并到同一标准要求项的合法响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "source_requirement_ids": [1, 2],
                "rationale": "两条要求在各自证据中指向同一招聘条件",
                "confidence": 0.95,
            }
        ],
        "mappings": [
            {
                "requirement_id": requirement_id,
                "canonical_requirement_id": "requirement-a",
                "rationale": "表述不同但招聘条件相同",
                "confidence": 0.95,
            }
            for requirement_id in (1, 2)
        ],
    }


def stage_payloads(payload: dict[str, object]) -> list[dict[str, object]]:
    """返回单次 canonical 聚类响应（mappings 由确定性代码生成）。"""
    return [
        {"canonical_requirements": payload["canonical_requirements"]},
    ]


def seed_two_jobs(session_factory) -> None:
    """向数据库写入两份JD各一条要求实例。"""
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_job(2, "job-b.md"))
        session.add(make_extraction(1, 1))
        session.add(make_extraction(2, 2))
        session.add(make_requirement(1, 1, "能力甲使用经验"))
        session.add(make_requirement(2, 2, "具备能力甲的使用经验"))
        session.commit()


def test_successful_run_reports_counts(tmp_path: Path) -> None:
    """验证成功归并的摘要包含发现、归并和标准项数量。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(session_factory, client, metadata)

    assert summary.discovered == 2
    assert summary.consolidated == 2
    assert summary.canonical_count == 1
    assert summary.failed == 0
    assert client.calls == 1


def test_failed_run_isolates_error_without_raising(tmp_path: Path) -> None:
    """验证标准项阶段失败被记录到摘要且不中断抛出。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient([{"bad": True}])
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, max_attempts=1
    )

    assert summary.failed == 1
    assert summary.consolidated == 0
    assert "不符合归并合同" in summary.errors[0].message
    assert summary.errors[0].scope == "all"


def test_empty_database_reports_assembly_error(tmp_path: Path) -> None:
    """验证空数据库的装配失败被记录为错误而非抛出。"""
    _, session_factory = make_database(tmp_path)
    client = FakeConsolidationClient([])
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(session_factory, client, metadata)

    assert summary.failed == 1
    assert summary.discovered == 0
    assert "选定范围内没有JD" in summary.errors[0].message


def test_job_ids_filter_applies_to_scope(tmp_path: Path) -> None:
    """验证job_ids过滤只归并选定JD，且失败摘要记录对应范围。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    payload = valid_result_payload()
    payload["mappings"] = payload["mappings"][:1]
    payload["canonical_requirements"][0]["source_requirement_ids"] = [1]
    client = FakeConsolidationClient(stage_payloads(payload))
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, job_ids={1}
    )

    assert summary.discovered == 1
    assert summary.consolidated == 1
    assert summary.failed == 0


def test_failed_scope_reports_selected_job_ids(tmp_path: Path) -> None:
    """验证指定范围失败时摘要记录job_ids范围。"""
    _, session_factory = make_database(tmp_path)
    client = FakeConsolidationClient([{"bad": True}])
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, job_ids={7}, max_attempts=1
    )

    assert summary.failed == 1
    assert summary.errors[0].scope == "job_ids=7"
