"""验证P0-4归并持久化：幂等保存、字段无损、外键追溯与版本/范围共存。"""

import json
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app.consolidation import (
    ConsolidatorMetadata,
    consolidate_requirements,
)
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import (
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
)


class FakeConsolidationClient:
    """按预设顺序返回JSON文本，并记录调用次数。"""

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
    """创建包含全部数据表的临时SQLite数据库。"""
    database_path = tmp_path / "consolidation_persist.db"
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
        extractor_version="test-model|prompt:1.0|schema:2.0",
        model_name="test-model",
        prompt_version="1.0",
        schema_version="2.0",
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
    """生成两个实例归并到同一标准要求项并带一条关系的合法响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "rationale": "两条要求在各自证据中指向同一招聘条件",
                "confidence": 0.95,
            },
            {
                "canonical_requirement_id": "requirement-b",
                "canonical_name": "能力乙",
                "rationale": "另一项独立能力",
                "confidence": 0.9,
            },
        ],
        "mappings": [
            {
                "requirement_id": 1,
                "status": "mapped",
                "canonical_requirement_id": "requirement-a",
                "candidate_requirement_ids": [],
                "rationale": "表述不同但招聘条件相同",
                "confidence": 0.95,
            },
            {
                "requirement_id": 2,
                "status": "mapped",
                "canonical_requirement_id": "requirement-b",
                "candidate_requirement_ids": [],
                "rationale": "独立条件",
                "confidence": 0.9,
            },
        ],
        "relations": [
            {
                "source_requirement_id": "requirement-b",
                "target_requirement_id": "requirement-a",
                "relation_type": "broader_than",
                "rationale": "能力乙是能力甲使用经验的上位概念",
                "confidence": 0.7,
            }
        ],
    }


def stage_payloads(payload: dict[str, object]) -> list[dict[str, object]]:
    """把完整归并结果拆成标准项、映射和关系三个阶段的独立响应。"""
    return [
        {"canonical_requirements": payload["canonical_requirements"]},
        {"mappings": payload["mappings"]},
        {"relations": payload["relations"]},
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
    assert client.calls == 2
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
            "rationale": "新增独立条件",
            "confidence": 0.9,
        }
    )
    updated_payload["mappings"].append(
        {
            "requirement_id": 3,
            "status": "mapped",
            "canonical_requirement_id": "requirement-c",
            "candidate_requirement_ids": [],
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
    assert client.calls == 5
    assert first.consolidation_id == 1
    assert second.consolidation_id == 2
    assert first.input_fingerprint != second.input_fingerprint
    assert consolidation_count(session_factory) == 2


def test_persisted_fields_match_contract(tmp_path: Path) -> None:
    """验证入库的标准要求项、映射和关系字段与合同结果一致。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))
    metadata = ConsolidatorMetadata(model_name="test-model")

    consolidate_requirements(
        session_factory, client, metadata, include_relations=True
    )

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
            "test-model|prompt:1.0|schema:2.0"
        )
        assert consolidation.selected_job_ids == [1, 2]
        assert consolidation.extraction_ids == [1, 2]
        assert len(consolidation.canonical_requirements) == 2
        assert len(consolidation.mappings) == 2
        assert len(consolidation.relations) == 1
        first_mapping = consolidation.mappings[0]
        assert first_mapping.status == "mapped"
        assert first_mapping.canonical_requirement_id == "requirement-a"
        assert first_mapping.rationale == "表述不同但招聘条件相同"
        assert first_mapping.confidence == 0.95
        assert (
            consolidation.relations[0].relation_type == "broader_than"
        )


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
        "model-v1|prompt:1.0|schema:2.0",
        "model-v2|prompt:2.0|schema:2.0",
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
    payload_only_job1["relations"] = []
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


def test_mapping_chunk_failure_persists_nothing(tmp_path: Path) -> None:
    """验证映射阶段中途某块失败时，不留下任何部分批次记录。"""
    _, session_factory = make_database(tmp_path)
    seed_large_pool(session_factory, 101)
    canonical_payload = {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "cr-1",
                "canonical_name": "统一测试条件",
                "rationale": "测试归并",
                "confidence": 0.9,
            }
        ]
    }
    chunk_one_mappings = {
        "mappings": [
            {
                "requirement_id": requirement_id,
                "status": "mapped",
                "canonical_requirement_id": "cr-1",
                "candidate_requirement_ids": [],
                "rationale": "测试映射",
                "confidence": 0.9,
            }
            for requirement_id in range(1, 51)
        ]
    }
    client = FakeConsolidationClient(
        [canonical_payload, chunk_one_mappings, {"bad": True}]
    )
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, max_attempts=1
    )

    assert summary.failed == 1
    assert summary.consolidated == 0
    assert "映射生成" in summary.errors[0].message
    assert consolidation_count(session_factory) == 0


def test_relation_stage_failure_saves_facts_with_failed_hierarchy(
    tmp_path: Path,
) -> None:
    """验证关系阶段失败时：P0-4A 事实层仍原子落库，层级标记为 failed。

    P0-4B 失败不阻塞 P0-4A 与 P0-6 统计，但不允许伪装成整批成功。
    """
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    payload = valid_result_payload()
    client = FakeConsolidationClient(
        [
            {"canonical_requirements": payload["canonical_requirements"]},
            {"mappings": payload["mappings"]},
            {"bad": True},
        ]
    )
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory,
        client,
        metadata,
        max_attempts=1,
        include_relations=True,
    )

    assert summary.failed == 0
    assert summary.consolidated == 2
    assert summary.hierarchy_status == "failed"
    with session_factory() as session:
        consolidation = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.scope_key == "all",
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
            )
        )
        assert consolidation is not None
        assert consolidation.hierarchy_status == "failed"
        assert len(consolidation.canonical_requirements) == 2
        assert len(consolidation.mappings) == 2
        assert len(consolidation.relations) == 0


def test_uncertain_relations_create_no_persisted_edges(tmp_path: Path) -> None:
    """验证uncertain判断只进入审计输出，不创建正式关系记录。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    payload = valid_result_payload()
    payload["relations"] = payload["relations"] + [
        {
            "source_requirement_id": "requirement-a",
            "target_requirement_id": "requirement-b",
            "relation_type": "uncertain",
            "rationale": "名称抽象，无法判断包含方向",
            "confidence": 0.5,
        }
    ]
    client = FakeConsolidationClient(stage_payloads(payload))
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, include_relations=True
    )

    assert summary.relation_count == 1
    assert summary.uncertain_count == 1
    with session_factory() as session:
        consolidation = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.scope_key == "all",
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
            )
        )
        assert consolidation is not None
        assert len(consolidation.relations) == 1
        assert consolidation.relations[0].relation_type == "broader_than"
        assert len(consolidation.raw_response["uncertain_relations"]) == 1


def test_none_relations_persist_no_records(tmp_path: Path) -> None:
    """验证无包含关系的批次不产生任何持久化关系记录。"""
    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)
    payload = valid_result_payload()
    payload["relations"] = []
    client = FakeConsolidationClient(stage_payloads(payload))
    metadata = ConsolidatorMetadata(model_name="test-model")

    summary = consolidate_requirements(
        session_factory, client, metadata, include_relations=True
    )

    assert summary.relation_count == 0
    assert summary.uncertain_count == 0
    with session_factory() as session:
        consolidation = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.scope_key == "all",
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
            )
        )
        assert consolidation is not None
        assert len(consolidation.relations) == 0


def test_hierarchy_relations_do_not_change_statistics(tmp_path: Path) -> None:
    """验证层级关系只用于报告组织，不改变独立JD高频统计。"""
    from app.requirement_consolidation import (
        CanonicalRequirement,
        RequirementConsolidationResult,
        RequirementMapping,
        RequirementMappingStatus,
        RequirementRelation,
        RequirementRelationType,
    )

    _, session_factory = make_database(tmp_path)
    seed_two_jobs(session_factory)

    canonical_a = CanonicalRequirement(
        canonical_requirement_id="cr-a",
        canonical_name="能力甲使用经验",
        rationale="来源证据",
        confidence=0.9,
    )
    canonical_b = CanonicalRequirement(
        canonical_requirement_id="cr-b",
        canonical_name="能力乙",
        rationale="独立条件",
        confidence=0.9,
    )
    mappings = [
        RequirementMapping(
            requirement_id=1,
            status=RequirementMappingStatus.MAPPED,
            canonical_requirement_id="cr-a",
            rationale="同义归并",
            confidence=0.9,
        ),
        RequirementMapping(
            requirement_id=2,
            status=RequirementMappingStatus.MAPPED,
            canonical_requirement_id="cr-b",
            rationale="独立条件",
            confidence=0.9,
        ),
    ]
    base = RequirementConsolidationResult(
        canonical_requirements=[canonical_a, canonical_b],
        mappings=mappings,
    )
    with_hierarchy = RequirementConsolidationResult(
        canonical_requirements=[canonical_a, canonical_b],
        mappings=mappings,
        relations=[
            RequirementRelation(
                source_requirement_id="cr-b",
                target_requirement_id="cr-a",
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale="乙是甲的具体类型",
                confidence=0.8,
            )
        ],
    )
    # 独立JD高频统计只由（实例->标准项）映射计算，与层级关系无关。
    occurrence_jobs = {1: 101, 2: 102}

    def jd_counts(result: RequirementConsolidationResult) -> dict[str, set[int]]:
        counts: dict[str, set[int]] = {}
        for mapping in result.mappings:
            if (
                mapping.status is RequirementMappingStatus.MAPPED
                and mapping.canonical_requirement_id is not None
            ):
                counts.setdefault(mapping.canonical_requirement_id, set()).add(
                    occurrence_jobs[mapping.requirement_id]
                )
        return counts

    assert jd_counts(base) == jd_counts(with_hierarchy) == {
        "cr-a": {101},
        "cr-b": {102},
    }
