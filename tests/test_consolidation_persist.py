"""验证P0-4归并持久化：幂等、字段合同、版本/范围隔离与失败不落库。"""

import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.consolidation import (
    ConsolidatorMetadata,
    consolidate_requirements,
)
from app.database import create_database_engine, create_session_factory, initialize_database
from app.models import (
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
)


class FakeConsolidationClient:
    """按预设顺序返回JSON文本，并记录用户提示，替代真实且有费用的LLM调用。"""

    def __init__(
        self, responses: list[dict[str, object]]
    ) -> None:
        """保存待返回响应并初始化调用记录。"""
        self.responses = responses
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应。"""
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


def make_database(tmp_path: Path):
    """创建临时SQLite数据库并返回引擎与会话工厂。"""
    engine = create_database_engine(f"sqlite:///{tmp_path / 'test.db'}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def make_job(job_id: int, source_file: str) -> JobDescription:
    """构造一份虚构JD记录。"""
    return JobDescription(
        source_hash=f"{job_id:064x}",
        source_file=source_file,
        source_type="test",
        collected_at=date(2026, 8, 3),
        company=f"公司{job_id}",
        title=f"岗位{job_id}",
        company_type="medium_company",
        tags=[],
        extra_metadata={},
        raw_text=f"# 岗位{job_id}\\n\\n熟悉技术甲。",
    )


def make_extraction(extraction_id: int, job_id: int) -> JobExtraction:
    """构造一份v0.8 + Schema V3抽取记录。"""
    return JobExtraction(
        id=extraction_id,
        job_id=job_id,
        extractor_version="test-model|prompt:0.9|schema:3.0",
        model_name="test-model",
        prompt_version="1.0",
        schema_version="3.0",
        role_family="other",
        seniority="unknown",
        raw_response={"role_family": "other", "seniority": "unknown", "requirements": []},
    )


def make_requirement(
    requirement_id: int, extraction_id: int, raw_name: str
) -> JobRequirement:
    """构造一条要求实例。"""
    return JobRequirement(
        id=requirement_id,
        extraction_id=extraction_id,
        raw_name=raw_name,
        category="other",
        importance="must",
        proficiency="basic",
        group_id=None,
        group_logic="standalone",
        min_years=None,
        max_years=None,
        years_text=None,
        evidence=f"熟悉{raw_name}",
        confidence=0.9,
    )


def valid_result_payload() -> dict[str, object]:
    """生成两个实例归并到不同标准要求项的合法响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "source_requirement_ids": [1],
                "rationale": "两条要求在各自证据中指向同一招聘条件",
                "confidence": 0.95,
            },
            {
                "canonical_requirement_id": "requirement-b",
                "canonical_name": "能力乙",
                "source_requirement_ids": [2],
                "rationale": "另一项独立能力",
                "confidence": 0.9,
            },
        ],
        "mappings": [
            {
                "requirement_id": 1,
                "canonical_requirement_id": "requirement-a",
                "rationale": "表述不同但招聘条件相同",
                "confidence": 0.95,
            },
            {
                "requirement_id": 2,
                "canonical_requirement_id": "requirement-b",
                "rationale": "独立条件",
                "confidence": 0.9,
            },
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
        session.add(make_requirement(2, 2, "能力乙"))
        session.commit()


def consolidation_count(session_factory) -> int:
    """返回数据库中归并批次记录数。"""
    with session_factory() as session:
        return session.scalar(select(func.count(JobConsolidation.id)))


def test_second_run_is_skipped_without_model_call(tmp_path: Path) -> None:
    """验证同范围、同版本且同输入时跳过模型调用且不新增记录。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    first = consolidate_requirements(session_factory, client, metadata)
    second = consolidate_requirements(session_factory, client, metadata)

    assert first.consolidated == 2
    assert second.skipped == 2
    assert second.consolidated == 0
    assert client.calls == 1
    assert first.consolidation_id == second.consolidation_id == 1
    assert first.input_fingerprint == second.input_fingerprint
    assert consolidation_count(session_factory) == 1


def test_changed_input_creates_new_batch_with_same_consolidator(
    tmp_path: Path,
) -> None:
    """验证新增JD后同范围同归并器版本不会错误复用旧批次。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    updated_payload = valid_result_payload()
    updated_payload["canonical_requirements"].append(
        {
            "canonical_requirement_id": "requirement-c",
            "canonical_name": "能力丙",
            "source_requirement_ids": [3],
            "rationale": "新增独立条件",
            "confidence": 0.9,
        }
    )
    updated_payload["mappings"].append(
        {
            "requirement_id": 3,
            "canonical_requirement_id": "requirement-c",
            "rationale": "新增要求映射",
            "confidence": 0.9,
        }
    )
    client = FakeConsolidationClient(
        stage_payloads(valid_result_payload()) + stage_payloads(updated_payload)
    )
    metadata = ConsolidatorMetadata(model_name="test-model")

    first = consolidate_requirements(session_factory, client, metadata)
    with session_factory() as session:
        session.add(make_job(3, "job-c.md"))
        session.add(make_extraction(3, 3))
        session.add(make_requirement(3, 3, "能力丙"))
        session.commit()
    second = consolidate_requirements(session_factory, client, metadata)

    assert first.consolidated == 2
    assert second.consolidated == 3
    assert second.skipped == 0
    assert client.calls == 2
    assert first.consolidation_id == 1
    assert second.consolidation_id == 2
    assert first.input_fingerprint != second.input_fingerprint
    assert consolidation_count(session_factory) == 2


def test_persisted_fields_match_contract(tmp_path: Path) -> None:
    """验证入库的标准要求项和映射字段与合同结果一致。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    consolidate_requirements(session_factory, client, metadata)

    with session_factory() as session:
        consolidation = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.scope_key == "all",
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
            )
        )
        assert consolidation is not None
        assert consolidation.occurrence_count == 2
        assert consolidation.model_name == "test-model"
        assert len(consolidation.input_fingerprint) == 64
        assert consolidation.extractor_version == (
            "test-model|prompt:0.9|schema:3.0"
        )
        assert consolidation.selected_job_ids == [1, 2]
        assert consolidation.extraction_ids == [1, 2]
        assert len(consolidation.canonical_requirements) == 2
        assert len(consolidation.mappings) == 2
        first_mapping = consolidation.mappings[0]
        assert first_mapping.canonical_requirement_id == "requirement-a"
        # mappings 由来源分区确定性生成：rationale 带确定性命名的前缀。
        assert first_mapping.rationale.startswith("由标准要求项来源分区确定：")
        assert first_mapping.confidence == 0.95


def test_mapping_traces_back_to_original_requirement(tmp_path: Path) -> None:
    """验证映射的requirement_id可回溯到job_requirements中的原始要求。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    consolidate_requirements(session_factory, client, metadata)

    with session_factory() as session:
        mapping = session.scalar(
            select(RequirementMappingRecord).where(
                RequirementMappingRecord.requirement_id == 1
            )
        )
        assert mapping is not None
        original = session.get(JobRequirement, mapping.requirement_id)
        assert original is not None
        assert original.raw_name == "能力甲使用经验"


def test_different_versions_coexist(tmp_path: Path) -> None:
    """验证不同归并器版本的结果各自独立保存。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(
        stage_payloads(valid_result_payload()) * 2
    )

    consolidate_requirements(
        session_factory,
        client,
        ConsolidatorMetadata(model_name="model-v1", prompt_version="1.0"),
    )
    consolidate_requirements(
        session_factory,
        client,
        ConsolidatorMetadata(model_name="model-v2", prompt_version="2.0"),
    )

    assert consolidation_count(session_factory) == 2
    with session_factory() as session:
        versions = set(
            session.scalars(
                select(JobConsolidation.consolidator_version)
            ).all()
        )
    assert versions == {
        "model-v1|prompt:1.0|schema:3.0",
        "model-v2|prompt:2.0|schema:3.0",
    }


def test_old_consolidator_version_does_not_share_idempotency(
    tmp_path: Path,
) -> None:
    """旧归并版本批次不被新版本幂等复用（同输入分别产生批次）。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()) * 2)

    old_metadata = ConsolidatorMetadata(
        model_name="test-model", prompt_version="4.0", schema_version="2.0"
    )
    new_metadata = ConsolidatorMetadata(model_name="test-model")

    first = consolidate_requirements(session_factory, client, old_metadata)
    second = consolidate_requirements(session_factory, client, new_metadata)

    assert first.consolidated == 2
    assert second.consolidated == 2  # 新版本不跳过旧版本批次
    assert second.skipped == 0
    assert consolidation_count(session_factory) == 2
    with session_factory() as session:
        versions = set(
            session.scalars(
                select(JobConsolidation.consolidator_version)
            ).all()
        )
    assert versions == {
        "test-model|prompt:4.0|schema:2.0",
        "test-model|prompt:4.1|schema:3.0",
    }


def test_different_scopes_coexist(tmp_path: Path) -> None:
    """验证不同JD范围的归并结果按scope_key各自独立保存。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    payload_only_job1 = valid_result_payload()
    payload_only_job1["mappings"] = payload_only_job1["mappings"][:1]
    payload_only_job1["canonical_requirements"] = payload_only_job1[
        "canonical_requirements"
    ][:1]
    client.responses = (
        stage_payloads(valid_result_payload())
        + stage_payloads(payload_only_job1)
    )

    consolidate_requirements(session_factory, client, metadata)
    consolidate_requirements(session_factory, client, metadata, job_ids={1})

    assert consolidation_count(session_factory) == 2
    with session_factory() as session:
        scopes = set(session.scalars(select(JobConsolidation.scope_key)).all())
    assert scopes == {"all", "job_ids=1"}


def test_failed_run_persists_nothing(tmp_path: Path) -> None:
    """验证模型调用失败时不产生任何归并记录。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient([{"bad": True}])
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, max_attempts=1
    )

    assert summary.failed == 1
    assert consolidation_count(session_factory) == 0


def seed_large_pool(session_factory, instance_count: int) -> None:
    """向三份JD写入指定数量的要求实例，构成跨块映射语料池。"""
    job_count = 3
    with session_factory() as session:
        for job_id in range(1, job_count + 1):
            session.add(make_job(job_id, f"job-{job_id}.md"))
            session.add(make_extraction(job_id, job_id))
        for requirement_id in range(1, instance_count + 1):
            job_id = requirement_id % job_count + 1
            session.add(
                make_requirement(requirement_id, job_id, f"能力{requirement_id}")
            )
        session.commit()
