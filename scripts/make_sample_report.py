"""生成公开市场报告样例（合成数据，不泄露任何真实 JD）。

样例与真实报告使用同一渲染逻辑（app.market_report.build_market_report），
数据为脚本内构造的虚构 JD 与归并批次；输出默认写入
examples/market-report-sample.md（可提交仓库的可查看样例）。

用法：

    python -m scripts.make_sample_report [--output examples/market-report-sample.md]
"""
from __future__ import annotations

import argparse
import tempfile
from datetime import date
from pathlib import Path

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
    ConsolidatorMetadata,
    load_consolidation_selection,
    persist_consolidation,
    scope_key_for,
)
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.market_analysis import build_market_statistics
from app.market_report import build_market_report
from app.models import JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)

# 虚构岗位数据：公司、岗位与要求均为合成，不代表任何真实招聘信息。
_SAMPLE_JOBS = (
    ("示例科技", "大模型应用工程师", "北京", (
        (
            "编程语言",
            "programming_language",
            "must",
            "basic",
            "1. 熟悉主流编程语言。",
        ),
        (
            "大模型应用开发经验",
            "experience",
            "must",
            "unknown",
            "2. 有 LLM 应用落地经验。",
        ),
        (
            "数据分析经验",
            "experience",
            "preferred",
            "unknown",
            "3. 有数据分析经验者优先。",
        ),
    )),
    ("示例智能", "Agent 开发工程师", "上海", (
        (
            "编程语言",
            "programming_language",
            "must",
            "advanced",
            "1. 掌握常用编程语言。",
        ),
        (
            "大模型应用开发经验",
            "experience",
            "preferred",
            "unknown",
            "2. 具备大模型应用开发经验者加分。",
        ),
        (
            "团队协作能力",
            "soft_skill",
            "must",
            "unknown",
            "3. 具备跨团队协作能力。",
        ),
    )),
    ("示例数据", "RAG 平台工程师", "深圳", (
        (
            "编程语言",
            "programming_language",
            "preferred",
            "basic",
            "1. 熟悉编程语言者加分。",
        ),
        (
            "RAG 应用开发",
            "rag",
            "must",
            "basic",
            "2. 熟悉 RAG 应用开发。",
        ),
        (
            "本科及以上学历",
            "education",
            "must",
            "unknown",
            "3. 本科及以上学历。",
        ),
    )),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("examples/market-report-sample.md"),
        help="样例报告输出路径（默认 examples/market-report-sample.md）",
    )
    args = parser.parse_args(argv)

    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "sample.db"
        engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
        try:
            initialize_database(engine)
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                requirement_ids: list[int] = []
                job_ids: set[int] = set()
                for index, (company, title, city, requirements) in enumerate(
                    _SAMPLE_JOBS, start=1
                ):
                    job = JobDescription(
                        source_hash=f"sample{index}" + "b" * 57,
                        source_file=f"sample-{index}.md",
                        source_type="sample",
                        collected_at=date(2026, 8, 1),
                        company=company,
                        title=title,
                        city=city,
                        company_type="medium_company",
                        tags=[],
                        extra_metadata={},
                        raw_text=f"# {title}\n\n（合成样例正文）",
                    )
                    session.add(job)
                    session.flush()
                    job_ids.add(job.id)
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
                    for (
                        raw_name,
                        category,
                        importance,
                        proficiency,
                        evidence,
                    ) in requirements:
                        requirement = JobRequirement(
                            extraction_id=extraction.id,
                            raw_name=raw_name,
                            category=category,
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
                        requirement_ids.append(requirement.id)
                session.commit()

            # 归并：按 raw_name 同名聚合为 canonical（合成确定性规则）。
            by_name: dict[str, list[int]] = {}
            with session_factory() as session:
                rows = session.query(JobRequirement).all()
                for row in rows:
                    by_name.setdefault(row.raw_name, []).append(row.id)
            canonicals = [
                CanonicalRequirement(
                    canonical_requirement_id=f"cr-{index}",
                    canonical_name=name,
                    source_requirement_ids=sorted(ids),
                    rationale="合成数据",
                    confidence=0.9,
                )
                for index, (name, ids) in enumerate(
                    sorted(by_name.items()), start=1
                )
            ]
            result = RequirementConsolidationResult(
                canonical_requirements=canonicals,
                mappings=build_mappings_from_canonical_partition(canonicals),
            )
            with session_factory() as session:
                selection = load_consolidation_selection(
                    session, job_ids=job_ids
                )
                metadata = ConsolidatorMetadata(
                    model_name="test-model",
                    prompt_version=CONSOLIDATION_PROMPT_VERSION,
                    schema_version=CONSOLIDATION_SCHEMA_VERSION,
                )
                persist_consolidation(
                    session,
                    selection,
                    result,
                    {"model_response": {"canonical_requirements": []}},
                    metadata,
                    scope_key_for(job_ids or None),
                )
            # 取批次 ID 用于统计。
            from app.models import JobConsolidation

            with session_factory() as session:
                consolidation_id = session.query(JobConsolidation).one().id

            stats = build_market_statistics(session_factory, consolidation_id)
        finally:
            engine.dispose()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_market_report(stats), encoding="utf-8")
    print(f"公开样例已生成：{args.output}")
    print(
        f"JD {stats.total_job_count}、实例 {stats.occurrence_count}、"
        f"canonical {stats.canonical_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
