"""市场统计：把 P0-4 归并批次投影为可排序的 canonical requirement 市场数据。

本模块是 P0-5（市场统计、证据追溯与 Markdown 报告）的业务入口，只依赖
已持久化的归并批次（requirement instance → canonical requirement →
unique mapping），输出结构直接供后续 `generate-report` 命令消费：

- 每个 canonical requirement 的实例数量与独立 JD 数量（同一 JD 多个
  实例只计一次）；
- must / preferred / mentioned / unknown 分布；
- 来源 JD 集合、对应原始 requirement 与 evidence（证据追溯）；
- 稳定排序（实例数降序、名称升序，不依赖数据库行序）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import (
    JobConsolidation,
    JobRequirement,
    RequirementMappingRecord,
)


@dataclass(frozen=True)
class CanonicalMarketStats:
    """一个 canonical requirement 的市场统计与证据追溯信息。"""

    canonical_requirement_id: str
    canonical_name: str
    instance_count: int
    distinct_job_count: int
    importance_counts: dict[str, int]
    source_job_ids: tuple[int, ...]
    source_requirements: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class MarketStatistics:
    """一次归并批次的完整市场统计。"""

    consolidation_id: int
    scope_key: str
    consolidator_version: str
    extractor_version: str
    input_fingerprint: str
    occurrence_count: int
    canonical_count: int
    canonical_items: tuple[CanonicalMarketStats, ...]

    def to_dict(self) -> dict[str, Any]:
        """序列化为机器可读统计（脱敏：evidence 保留原文用于追溯）。"""
        return {
            "consolidation_id": self.consolidation_id,
            "scope_key": self.scope_key,
            "consolidator_version": self.consolidator_version,
            "extractor_version": self.extractor_version,
            "input_fingerprint": self.input_fingerprint,
            "occurrence_count": self.occurrence_count,
            "canonical_count": self.canonical_count,
            "canonical_items": [
                {
                    "canonical_requirement_id": item.canonical_requirement_id,
                    "canonical_name": item.canonical_name,
                    "instance_count": item.instance_count,
                    "distinct_job_count": item.distinct_job_count,
                    "importance_counts": item.importance_counts,
                    "source_job_ids": list(item.source_job_ids),
                    "source_requirements": list(item.source_requirements),
                }
                for item in self.canonical_items
            ],
        }


def build_market_statistics(
    session_factory: sessionmaker[Session],
    consolidation_id: int,
) -> MarketStatistics:
    """按显式归并批次ID计算市场统计；批次不存在时抛出 ValueError。

    统计只依赖（实例 → canonical）映射与实例来源，结果稳定可复现：
    canonical 按（实例数降序、canonical_name 升序）排序，来源 JD 集合
    与原始 requirement/evidence 按 requirement_id 升序。
    """
    with session_factory() as session:
        record = session.scalar(
            select(JobConsolidation)
            .options(
                selectinload(JobConsolidation.canonical_requirements),
                selectinload(JobConsolidation.mappings),
            )
            .where(JobConsolidation.id == consolidation_id)
        )
        if record is None:
            raise ValueError(f"归并批次不存在：{consolidation_id}")

        canonical_by_id = {
            item.canonical_requirement_id: item
            for item in record.canonical_requirements
        }
        mappings: list[RequirementMappingRecord] = list(record.mappings)

        requirement_ids = [mapping.requirement_id for mapping in mappings]
        requirements: dict[int, JobRequirement] = {}
        if requirement_ids:
            rows = session.scalars(
                select(JobRequirement).where(JobRequirement.id.in_(requirement_ids))
            ).all()
            requirements = {row.id: row for row in rows}

        # 每个 canonical 的实例清单（requirement_id → 来源信息）。
        members: dict[str, list[int]] = {}
        for mapping in mappings:
            members.setdefault(mapping.canonical_requirement_id, []).append(
                mapping.requirement_id
            )

        job_by_requirement: dict[int, int] = {}
        for mapping in mappings:
            requirement = requirements.get(mapping.requirement_id)
            if requirement is not None:
                job_by_requirement[mapping.requirement_id] = requirement.extraction_id
        # 通过 extraction → job 定位来源 JD。
        extraction_job: dict[int, int] = {}
        if job_by_requirement:
            from app.models import JobExtraction

            extraction_rows = session.execute(
                select(JobExtraction.id, JobExtraction.job_id).where(
                    JobExtraction.id.in_(set(job_by_requirement.values()))
                )
            ).all()
            extraction_job = {row[0]: row[1] for row in extraction_rows}

        items: list[CanonicalMarketStats] = []
        for canonical_id, member_ids in members.items():
            canonical = canonical_by_id[canonical_id]
            member_ids_sorted = sorted(member_ids)
            importance_counts: dict[str, int] = {}
            source_jobs: set[int] = set()
            source_requirements: list[dict[str, Any]] = []
            for requirement_id in member_ids_sorted:
                requirement = requirements.get(requirement_id)
                if requirement is None:
                    continue
                importance = requirement.importance
                importance_counts[importance] = importance_counts.get(importance, 0) + 1
                extraction_id = job_by_requirement.get(requirement_id)
                job_id = extraction_job.get(extraction_id) if extraction_id else None
                if job_id is not None:
                    source_jobs.add(job_id)
                source_requirements.append(
                    {
                        "requirement_id": requirement_id,
                        "job_id": job_id,
                        "raw_name": requirement.raw_name,
                        "category": requirement.category,
                        "importance": requirement.importance,
                        "proficiency": requirement.proficiency,
                        "evidence": requirement.evidence,
                        "confidence": requirement.confidence,
                    }
                )
            items.append(
                CanonicalMarketStats(
                    canonical_requirement_id=canonical.canonical_requirement_id,
                    canonical_name=canonical.canonical_name,
                    instance_count=len(member_ids_sorted),
                    distinct_job_count=len(source_jobs),
                    importance_counts=importance_counts,
                    source_job_ids=tuple(sorted(source_jobs)),
                    source_requirements=tuple(source_requirements),
                )
            )

        # 稳定排序：实例数降序 → 名称升序。
        items.sort(
            key=lambda item: (-item.instance_count, item.canonical_name)
        )
        return MarketStatistics(
            consolidation_id=record.id,
            scope_key=record.scope_key,
            consolidator_version=record.consolidator_version,
            extractor_version=record.extractor_version,
            input_fingerprint=record.input_fingerprint,
            occurrence_count=record.occurrence_count,
            canonical_count=len(items),
            canonical_items=tuple(items),
        )
