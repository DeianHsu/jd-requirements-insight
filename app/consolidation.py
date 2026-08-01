"""装配跨JD原子要求归并的输入：从数据库读取要求实例并保留来源定位。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementOccurrence,
)
from app.schemas import RequirementItem


def load_requirement_occurrences(
    session: Session,
    job_ids: set[int] | None = None,
) -> RequirementConsolidationInput:
    """读取选定JD范围内的全部要求实例，装配为P0-4统一输入。

    `job_ids`为None时读取全部JD；每份JD只取最新抽取结果（按抽取记录ID），
    避免同一要求实例因抽取器版本并存而重复进入归并语料池。本函数只做
    数据搬运与来源定位，不涉及任何领域技能判断。
    """
    if job_ids is not None and not job_ids:
        raise ValueError("job_ids不能为空集合")

    # 每份JD只保留最新抽取记录ID，旧版本结果不参与归并。
    latest_extraction_ids = (
        select(func.max(JobExtraction.id))
        .group_by(JobExtraction.job_id)
        .scalar_subquery()
    )
    query = (
        select(JobRequirement, JobExtraction.job_id, JobDescription.source_file)
        .join(JobExtraction, JobRequirement.extraction_id == JobExtraction.id)
        .join(JobDescription, JobExtraction.job_id == JobDescription.id)
        .where(JobExtraction.id.in_(latest_extraction_ids))
    )
    if job_ids is not None:
        query = query.where(JobDescription.id.in_(job_ids))

    # 按JD和原始要求排序，保证同一语料池的装配结果可复现。
    rows = session.execute(
        query.order_by(JobDescription.id, JobRequirement.id)
    ).all()

    occurrences = [
        RequirementOccurrence(
            requirement_id=requirement.id,
            job_id=job_id,
            source_file=source_file,
            requirement=RequirementItem(
                raw_name=requirement.raw_name,
                category=requirement.category,
                importance=requirement.importance,
                proficiency=requirement.proficiency,
                group_id=requirement.group_id,
                group_logic=requirement.group_logic,
                min_years=requirement.min_years,
                max_years=requirement.max_years,
                years_text=requirement.years_text,
                evidence=requirement.evidence,
                confidence=requirement.confidence,
            ),
        )
        for requirement, job_id, source_file in rows
    ]
    if not occurrences:
        raise ValueError("选定范围内没有可归并的要求实例")

    return RequirementConsolidationInput(occurrences=occurrences)
