"""验证 app/market_analysis.py：独立JD计数、importance分布、证据追溯与稳定排序。

覆盖：同一 JD 多个实例只计一次；must/preferred/mentioned 分布；来源 JD
集合与原始 requirement/evidence 可追溯；排序稳定（实例数降序、名称升序）。
"""

from datetime import date
from pathlib import Path

from app.database import create_database_engine, create_session_factory, initialize_database
from app.market_analysis import build_market_statistics
from app.models import (
    CanonicalRequirementRecord,
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
)

# 新测试使用 seed_batch 外的辅助：无需额外 import（全部来自 app.models）。


def make_database(tmp_path: Path):
    """创建临时SQLite数据库。"""
    engine = create_database_engine(f"sqlite:///{tmp_path / 'market.db'}")
    initialize_database(engine)
    return engine, create_session_factory(engine)


def seed_batch(session_factory) -> int:
    """写入一份含两条JD、三个canonical的归并批次，返回批次ID。"""
    with session_factory() as session:
        jobs = []
        for job_id in range(1, 3):
            job = JobDescription(
                source_hash=f"{job_id:064x}",
                source_file=f"job-{job_id}.md",
                source_type="test",
                collected_at=date(2026, 8, 3),
                company=f"公司{job_id}",
                title=f"岗位{job_id}",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text=f"# 岗位{job_id}\n\n熟悉技术甲。",
            )
            session.add(job)
            session.flush()
            jobs.append(job)

        extractions = []
        for job_id, job in enumerate(jobs, start=1):
            extraction = JobExtraction(
                job_id=job.id,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                model_name="test-model",
                prompt_version="0.8",
                schema_version="3.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
            session.add(extraction)
            session.flush()
            extractions.append(extraction)

        # 实例：job1 两条"技术甲"（must/basic + preferred/basic）、
        # job2 一条"技术甲"（must/advanced）、一条"能力乙"（mentioned）。
        specs = [
            (extractions[0].id, "技术甲", "must", "basic", "熟悉技术甲"),
            (extractions[0].id, "技术甲", "preferred", "basic", "熟悉技术甲"),
            (extractions[1].id, "技术甲", "must", "advanced", "熟练掌握技术甲"),
            (extractions[1].id, "能力乙", "mentioned", "unknown", "了解能力乙"),
        ]
        requirements = []
        for extraction_id, raw_name, importance, proficiency, evidence in specs:
            requirement = JobRequirement(
                extraction_id=extraction_id,
                raw_name=raw_name,
                category="other",
                importance=importance,
                proficiency=proficiency,
                group_id=None,
                group_logic="standalone",
                min_years=None,
                max_years=None,
                years_text=None,
                evidence=evidence,
                confidence=0.9,
            )
            session.add(requirement)
            session.flush()
            requirements.append(requirement)

        consolidation = JobConsolidation(
            scope_key="all",
            consolidator_version="test-model|prompt:4.0|schema:2.0",
            input_fingerprint="f" * 64,
            extractor_version="test-model|prompt:0.8|schema:3.0",
            selected_job_ids=[jobs[0].id, jobs[1].id],
            extraction_ids=[extractions[0].id, extractions[1].id],
            model_name="test-model",
            prompt_version="4.0",
            schema_version="2.0",
            occurrence_count=4,
            raw_response={},
        )
        session.add(consolidation)
        session.flush()

        # canonical：技术甲（3实例/2 JD）、能力乙（1实例/1 JD）。
        canonical_tech = CanonicalRequirementRecord(
            consolidation_id=consolidation.id,
            canonical_requirement_id="cr-tech",
            canonical_name="技术甲",
            rationale="测试",
            confidence=0.95,
        )
        canonical_skill = CanonicalRequirementRecord(
            consolidation_id=consolidation.id,
            canonical_requirement_id="cr-skill",
            canonical_name="能力乙",
            rationale="测试",
            confidence=0.9,
        )
        session.add_all([canonical_tech, canonical_skill])
        session.flush()

        session.add_all(
            [
                RequirementMappingRecord(
                    consolidation_id=consolidation.id,
                    requirement_id=requirements[0].id,
                    canonical_requirement_id="cr-tech",
                    rationale="同条件",
                    confidence=0.95,
                ),
                RequirementMappingRecord(
                    consolidation_id=consolidation.id,
                    requirement_id=requirements[1].id,
                    canonical_requirement_id="cr-tech",
                    rationale="同条件",
                    confidence=0.95,
                ),
                RequirementMappingRecord(
                    consolidation_id=consolidation.id,
                    requirement_id=requirements[2].id,
                    canonical_requirement_id="cr-tech",
                    rationale="同条件",
                    confidence=0.95,
                ),
                RequirementMappingRecord(
                    consolidation_id=consolidation.id,
                    requirement_id=requirements[3].id,
                    canonical_requirement_id="cr-skill",
                    rationale="独立条件",
                    confidence=0.9,
                ),
            ]
        )
        session.commit()
        return consolidation.id


def test_distinct_job_count_counts_each_job_once(tmp_path: Path) -> None:
    """同一 JD 多个实例只计一次独立 JD 数。"""
    engine, session_factory = make_database(tmp_path)
    try:
        consolidation_id = seed_batch(session_factory)

        stats = build_market_statistics(session_factory, consolidation_id)

        tech = next(
            item for item in stats.canonical_items if item.canonical_name == "技术甲"
        )
        assert tech.instance_count == 3
        assert tech.distinct_job_count == 2  # 3 个实例来自 2 份 JD
    finally:
        engine.dispose()


def test_importance_counts_and_evidence_traceability(tmp_path: Path) -> None:
    """实例级与 JD 级 importance 分布与来源 requirement/evidence 可追溯。"""
    engine, session_factory = make_database(tmp_path)
    try:
        consolidation_id = seed_batch(session_factory)

        stats = build_market_statistics(session_factory, consolidation_id)

        tech = next(
            item for item in stats.canonical_items if item.canonical_name == "技术甲"
        )
        # 实例级：job1 两条（must+preferred）+ job2 一条（must）。
        assert tech.importance_instance_counts == {"must": 2, "preferred": 1}
        # JD 级：job1 按优先级归并为 must，job2 为 must。
        assert tech.importance_job_counts == {"must": 2}
        # JD 级总数不超过 distinct_job_count。
        assert sum(tech.importance_job_counts.values()) == tech.distinct_job_count == 2
        assert len(tech.source_requirements) == 3
        evidences = {
            source["evidence"] for source in tech.source_requirements
        }
        assert "熟悉技术甲" in evidences
        assert "熟练掌握技术甲" in evidences
        # 证据追溯包含原始 raw_name 与 importance。
        assert all(
            "raw_name" in source and "importance" in source
            for source in tech.source_requirements
        )
    finally:
        engine.dispose()


def test_stable_sorting_by_distinct_job_count_first(tmp_path: Path) -> None:
    """canonical 按独立 JD 数降序、实例数降序、名称升序稳定排序。"""
    engine, session_factory = make_database(tmp_path)
    try:
        consolidation_id = seed_batch(session_factory)

        stats = build_market_statistics(session_factory, consolidation_id)

        names = [item.canonical_name for item in stats.canonical_items]
        # 技术甲（3实例/2 JD）排在能力乙（1实例/1 JD）前。
        assert names == ["技术甲", "能力乙"]
        job_counts = [item.distinct_job_count for item in stats.canonical_items]
        assert job_counts == sorted(job_counts, reverse=True)
    finally:
        engine.dispose()


def test_sorting_prefers_job_count_over_instance_count(tmp_path: Path) -> None:
    """市场高频以独立 JD 数为准：实例多但只覆盖 1 份 JD 的排在后面。"""
    engine, session_factory = make_database(tmp_path)
    try:
        # 要求甲：5 个实例只来自 1 份 JD；要求乙：3 个实例来自 3 份 JD。
        with session_factory() as session:
            jobs = []
            for job_id in range(1, 4):
                job = JobDescription(
                    source_hash=f"{job_id:064x}",
                    source_file=f"job-{job_id}.md",
                    source_type="test",
                    collected_at=date(2026, 8, 3),
                    company=f"公司{job_id}",
                    title=f"岗位{job_id}",
                    company_type="medium_company",
                    tags=[],
                    extra_metadata={},
                    raw_text=f"# 岗位{job_id}\n\n熟悉技术乙。",
                )
                session.add(job)
                session.flush()
                jobs.append(job)

            extractions = []
            for job_id, job in enumerate(jobs, start=1):
                extraction = JobExtraction(
                    job_id=job.id,
                    extractor_version="test-model|prompt:0.8|schema:3.0",
                    model_name="test-model",
                    prompt_version="0.8",
                    schema_version="3.0",
                    role_family="other",
                    seniority="unknown",
                    raw_response={},
                )
                session.add(extraction)
                session.flush()
                extractions.append(extraction)

            # 要求甲：5 个实例全部在 job1；要求乙：job1~job3 各一个实例。
            requirements = []
            for _ in range(5):
                requirement = JobRequirement(
                    extraction_id=extractions[0].id,
                    raw_name="要求甲",
                    category="other",
                    importance="must",
                    proficiency="basic",
                    group_id=None,
                    group_logic="standalone",
                    min_years=None,
                    max_years=None,
                    years_text=None,
                    evidence="熟悉要求甲",
                    confidence=0.9,
                )
                session.add(requirement)
                session.flush()
                requirements.append(requirement)
            for job_index in range(3):
                requirement = JobRequirement(
                    extraction_id=extractions[job_index].id,
                    raw_name="要求乙",
                    category="other",
                    importance="must",
                    proficiency="basic",
                    group_id=None,
                    group_logic="standalone",
                    min_years=None,
                    max_years=None,
                    years_text=None,
                    evidence="熟悉要求乙",
                    confidence=0.9,
                )
                session.add(requirement)
                session.flush()
                requirements.append(requirement)

            consolidation = JobConsolidation(
                scope_key="all",
                consolidator_version="test-model|prompt:4.0|schema:2.0",
                input_fingerprint="e" * 64,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                selected_job_ids=[jobs[0].id, jobs[1].id, jobs[2].id],
                extraction_ids=[e.id for e in extractions],
                model_name="test-model",
                prompt_version="4.0",
                schema_version="2.0",
                occurrence_count=8,
                raw_response={},
            )
            session.add(consolidation)
            session.flush()

            canonical_a = CanonicalRequirementRecord(
                consolidation_id=consolidation.id,
                canonical_requirement_id="cr-a",
                canonical_name="要求甲",
                rationale="测试",
                confidence=0.95,
            )
            canonical_b = CanonicalRequirementRecord(
                consolidation_id=consolidation.id,
                canonical_requirement_id="cr-b",
                canonical_name="要求乙",
                rationale="测试",
                confidence=0.95,
            )
            session.add_all([canonical_a, canonical_b])
            session.flush()

            for requirement in requirements[:5]:
                session.add(
                    RequirementMappingRecord(
                        consolidation_id=consolidation.id,
                        requirement_id=requirement.id,
                        canonical_requirement_id="cr-a",
                        rationale="同条件",
                        confidence=0.95,
                    )
                )
            for requirement in requirements[5:]:
                session.add(
                    RequirementMappingRecord(
                        consolidation_id=consolidation.id,
                        requirement_id=requirement.id,
                        canonical_requirement_id="cr-b",
                        rationale="同条件",
                        confidence=0.95,
                    )
                )
            session.commit()
            consolidation_id = consolidation.id

        stats = build_market_statistics(session_factory, consolidation_id)

        # 要求乙（3 JD）排在要求甲（1 JD）前，尽管甲实例数更多。
        names = [item.canonical_name for item in stats.canonical_items]
        assert names == ["要求乙", "要求甲"]
        by_name = {item.canonical_name: item for item in stats.canonical_items}
        assert by_name["要求甲"].instance_count == 5
        assert by_name["要求甲"].distinct_job_count == 1
        assert by_name["要求乙"].instance_count == 3
        assert by_name["要求乙"].distinct_job_count == 3
    finally:
        engine.dispose()


def test_importance_job_counts_merge_by_priority(tmp_path: Path) -> None:
    """同一 JD 内多个 importance 按 must > preferred > mentioned > unknown 归并。"""
    engine, session_factory = make_database(tmp_path)
    try:
        with session_factory() as session:
            jobs = []
            for job_id in range(1, 3):
                job = JobDescription(
                    source_hash=f"{job_id:064x}",
                    source_file=f"job-{job_id}.md",
                    source_type="test",
                    collected_at=date(2026, 8, 3),
                    company=f"公司{job_id}",
                    title=f"岗位{job_id}",
                    company_type="medium_company",
                    tags=[],
                    extra_metadata={},
                    raw_text=f"# 岗位{job_id}\n\n熟悉技术甲。",
                )
                session.add(job)
                session.flush()
                jobs.append(job)

            extractions = []
            for job_id, job in enumerate(jobs, start=1):
                extraction = JobExtraction(
                    job_id=job.id,
                    extractor_version="test-model|prompt:0.8|schema:3.0",
                    model_name="test-model",
                    prompt_version="0.8",
                    schema_version="3.0",
                    role_family="other",
                    seniority="unknown",
                    raw_response={},
                )
                session.add(extraction)
                session.flush()
                extractions.append(extraction)

            # job1：must + preferred；job2：preferred。
            specs = [
                (extractions[0].id, "must"),
                (extractions[0].id, "preferred"),
                (extractions[1].id, "preferred"),
            ]
            requirements = []
            for extraction_id, importance in specs:
                requirement = JobRequirement(
                    extraction_id=extraction_id,
                    raw_name="技术甲",
                    category="other",
                    importance=importance,
                    proficiency="basic",
                    group_id=None,
                    group_logic="standalone",
                    min_years=None,
                    max_years=None,
                    years_text=None,
                    evidence="熟悉技术甲",
                    confidence=0.9,
                )
                session.add(requirement)
                session.flush()
                requirements.append(requirement)

            consolidation = JobConsolidation(
                scope_key="all",
                consolidator_version="test-model|prompt:4.0|schema:2.0",
                input_fingerprint="d" * 64,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                selected_job_ids=[jobs[0].id, jobs[1].id],
                extraction_ids=[e.id for e in extractions],
                model_name="test-model",
                prompt_version="4.0",
                schema_version="2.0",
                occurrence_count=3,
                raw_response={},
            )
            session.add(consolidation)
            session.flush()
            canonical = CanonicalRequirementRecord(
                consolidation_id=consolidation.id,
                canonical_requirement_id="cr-tech",
                canonical_name="技术甲",
                rationale="测试",
                confidence=0.95,
            )
            session.add(canonical)
            session.flush()
            for requirement in requirements:
                session.add(
                    RequirementMappingRecord(
                        consolidation_id=consolidation.id,
                        requirement_id=requirement.id,
                        canonical_requirement_id="cr-tech",
                        rationale="同条件",
                        confidence=0.95,
                    )
                )
            session.commit()
            consolidation_id = consolidation.id

        stats = build_market_statistics(session_factory, consolidation_id)

        tech = next(
            item for item in stats.canonical_items if item.canonical_name == "技术甲"
        )
        assert tech.importance_instance_counts == {"must": 1, "preferred": 2}
        # job1 归并为 must（优先级更高），job2 为 preferred。
        assert tech.importance_job_counts == {"must": 1, "preferred": 1}
        assert sum(tech.importance_job_counts.values()) == tech.distinct_job_count == 2
    finally:
        engine.dispose()


def test_missing_consolidation_raises_value_error(tmp_path: Path) -> None:
    """不存在的批次ID抛出 ValueError。"""
    engine, session_factory = make_database(tmp_path)
    try:
        try:
            build_market_statistics(session_factory, 999)
            raise AssertionError("应抛出 ValueError")
        except ValueError as exc:
            assert "归并批次不存在" in str(exc)
    finally:
        engine.dispose()
