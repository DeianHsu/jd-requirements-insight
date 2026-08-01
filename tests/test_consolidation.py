"""验证P0-4输入装配：从数据库读取要求实例并完整保留抽取数据合同字段。"""

from datetime import date
from pathlib import Path

import pytest

from app.consolidation import (
    load_consolidation_selection,
    load_requirement_occurrences,
)
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import RequirementConsolidationInput


def make_database(tmp_path: Path):
    """创建包含全部抽取数据表的临时SQLite数据库。"""
    database_path = tmp_path / "consolidation.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def make_job(job_id: int, source_file: str) -> JobDescription:
    """创建使用中性虚构领域名称的JD记录，避免把装配测试绑定到具体岗位技能。"""
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


def make_extraction(
    extraction_id: int, job_id: int, version: str = "1.0"
) -> JobExtraction:
    """创建一份带抽取器版本身份的抽取主记录。"""
    return JobExtraction(
        id=extraction_id,
        job_id=job_id,
        extractor_version=f"test-model|prompt:{version}|schema:2.0",
        model_name="test-model",
        prompt_version=version,
        schema_version="2.0",
        role_family="other",
        seniority="unknown",
        raw_response={},
    )


def make_requirement(
    requirement_id: int, extraction_id: int, raw_name: str
) -> JobRequirement:
    """创建一条携带完整抽取数据合同字段的要求记录。"""
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


def test_loads_occurrences_from_multiple_jobs_without_field_loss(
    tmp_path: Path,
) -> None:
    """验证多JD装配保留合同字段并正确记录来源定位。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_job(2, "job-b.md"))
        session.add(make_extraction(1, 1))
        session.add(make_extraction(2, 2))
        session.add(make_requirement(1, 1, "能力甲"))
        session.add(make_requirement(2, 1, "能力乙"))
        session.add(make_requirement(3, 2, "能力甲"))
        session.commit()

        result = load_requirement_occurrences(session)

    assert isinstance(result, RequirementConsolidationInput)
    assert len(result.occurrences) == 3
    by_id = {occ.requirement_id: occ for occ in result.occurrences}
    assert by_id[1].job_id == 1
    assert by_id[1].extraction_id == 1
    assert by_id[1].extractor_version == "test-model|prompt:1.0|schema:2.0"
    assert by_id[1].source_hash == f"{1:064x}"
    assert by_id[1].source_file == "job-a.md"
    assert by_id[1].requirement.raw_name == "能力甲"
    assert by_id[1].requirement.evidence == "具备能力甲使用经验。"
    assert by_id[1].requirement.importance.value == "must"
    assert by_id[1].requirement.proficiency.value == "unknown"
    assert by_id[1].requirement.group_logic.value == "standalone"
    assert by_id[1].requirement.confidence == 0.9
    assert by_id[3].source_file == "job-b.md"


def test_explicit_extraction_version_is_loaded(tmp_path: Path) -> None:
    """验证同一JD并存多个抽取器版本时按显式版本装配要求实例。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_extraction(1, 1, version="1.0"))
        session.add(make_extraction(2, 1, version="2.0"))
        session.add(make_requirement(1, 1, "能力甲"))
        session.add(make_requirement(2, 2, "能力甲"))
        session.commit()

        result = load_requirement_occurrences(
            session,
            extractor_version="test-model|prompt:2.0|schema:2.0",
        )

    assert [occ.requirement_id for occ in result.occurrences] == [2]


def test_multiple_common_versions_require_explicit_selection(tmp_path: Path) -> None:
    """验证多个共同抽取器版本并存时拒绝隐式选择。"""
    _, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_extraction(1, 1, version="1.0"))
        session.add(make_extraction(2, 1, version="2.0"))
        session.add(make_requirement(1, 1, "能力甲"))
        session.add(make_requirement(2, 2, "能力乙"))
        session.commit()

        with pytest.raises(ValueError, match="存在多个共同抽取器版本"):
            load_consolidation_selection(session)


def test_selection_fingerprint_is_stable_and_changes_with_input(
    tmp_path: Path,
) -> None:
    """验证相同输入生成稳定指纹，要求字段变化会生成不同指纹。"""
    _, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_extraction(1, 1))
        session.add(make_requirement(1, 1, "能力甲"))
        session.commit()

        first = load_consolidation_selection(session)
        second = load_consolidation_selection(session)
        assert first.input_fingerprint == second.input_fingerprint
        assert first.selected_job_ids == (1,)
        assert first.extraction_ids == (1,)

        requirement = session.get(JobRequirement, 1)
        assert requirement is not None
        requirement.raw_name = "能力乙"
        session.commit()
        changed = load_consolidation_selection(session)

    assert changed.input_fingerprint != first.input_fingerprint


def test_job_ids_filter_selects_only_requested_jobs(tmp_path: Path) -> None:
    """验证job_ids过滤只装配选定JD范围内的要求实例。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_job(2, "job-b.md"))
        session.add(make_extraction(1, 1))
        session.add(make_extraction(2, 2))
        session.add(make_requirement(1, 1, "能力甲"))
        session.add(make_requirement(2, 2, "能力乙"))
        session.commit()

        result = load_requirement_occurrences(session, job_ids={1})

    assert [occ.requirement_id for occ in result.occurrences] == [1]


def test_assembled_input_has_unique_requirement_ids(tmp_path: Path) -> None:
    """验证装配结果通过合同的要求实例ID唯一性校验。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_extraction(1, 1))
        session.add(make_requirement(1, 1, "能力甲"))
        session.add(make_requirement(2, 1, "能力乙"))
        session.commit()

        result = load_requirement_occurrences(session)

    requirement_ids = [occ.requirement_id for occ in result.occurrences]
    assert len(requirement_ids) == len(set(requirement_ids))


def test_empty_database_raises_error(tmp_path: Path) -> None:
    """验证没有任何JD时抛出明确错误。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        with pytest.raises(ValueError, match="选定范围内没有JD"):
            load_requirement_occurrences(session)


def test_job_without_extraction_raises_error(tmp_path: Path) -> None:
    """验证有JD但没有抽取结果时抛出明确错误。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.commit()

        with pytest.raises(ValueError, match="JD缺少抽取结果"):
            load_requirement_occurrences(session)


def test_partial_extraction_scope_is_rejected(tmp_path: Path) -> None:
    """验证选定范围中任一JD缺少抽取结果时拒绝生成部分语料池。"""
    _, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_job(2, "job-b.md"))
        session.add(make_extraction(1, 1))
        session.add(make_requirement(1, 1, "能力甲"))
        session.commit()

        with pytest.raises(ValueError, match=r"JD缺少抽取结果：\[2\]"):
            load_consolidation_selection(session)


def test_unknown_requested_job_is_rejected(tmp_path: Path) -> None:
    """验证显式范围包含不存在的JD时拒绝静默缩小范围。"""
    _, session_factory = make_database(tmp_path)
    with session_factory() as session:
        session.add(make_job(1, "job-a.md"))
        session.add(make_extraction(1, 1))
        session.add(make_requirement(1, 1, "能力甲"))
        session.commit()

        with pytest.raises(ValueError, match=r"指定JD不存在：\[2\]"):
            load_consolidation_selection(session, job_ids={1, 2})


def test_empty_job_ids_set_raises_error(tmp_path: Path) -> None:
    """验证空job_ids集合被拒绝，避免语义模糊的调用。"""
    engine, session_factory = make_database(tmp_path)
    with session_factory() as session:
        with pytest.raises(ValueError, match="job_ids不能为空集合"):
            load_requirement_occurrences(session, job_ids=set())
