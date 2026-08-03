"""P0-4 事实归并的合同校验、变形指标与验收报告。

本模块只验证事实归并本身（requirement instance → canonical requirement
→ unique mapping），不涉及任何层级关系。验收维度：

- 数据合同与完整覆盖：coverage、重复映射、未知引用、空 cluster；
- 多次运行稳定性：positive-pair Jaccard、canonical 数量漂移、
  singleton 比例漂移；
- 非语义因素不变性：输入顺序、分块大小（变形比较）；
- 人工检查所有多成员 cluster。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.models import JobConsolidation
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    RequirementMapping,
)


@dataclass(frozen=True)
class PersistedConsolidationResult:
    """保存指定持久化批次的可复现身份与重建后的归并结果。"""

    consolidation_id: int
    scope_key: str
    consolidator_version: str
    extractor_version: str
    input_fingerprint: str
    result: RequirementConsolidationResult


def load_persisted_consolidation_result(
    session_factory: sessionmaker[Session],
    consolidation_id: int,
) -> PersistedConsolidationResult:
    """按显式批次ID加载映射，重建可供离线验证的归并结果。"""
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

        result = RequirementConsolidationResult(
            canonical_requirements=[
                CanonicalRequirement(
                    canonical_requirement_id=item.canonical_requirement_id,
                    canonical_name=item.canonical_name,
                    source_requirement_ids=list(
                        item.source_requirement_ids or []
                    ),
                    rationale=item.rationale,
                    confidence=item.confidence,
                )
                for item in record.canonical_requirements
            ],
            mappings=[
                RequirementMapping(
                    requirement_id=item.requirement_id,
                    canonical_requirement_id=item.canonical_requirement_id,
                    rationale=item.rationale,
                    confidence=item.confidence,
                )
                for item in record.mappings
            ],
        )
        return PersistedConsolidationResult(
            consolidation_id=record.id,
            scope_key=record.scope_key,
            consolidator_version=record.consolidator_version,
            extractor_version=record.extractor_version,
            input_fingerprint=record.input_fingerprint,
            result=result,
        )


@dataclass(frozen=True)
class ContractViolations:
    """P0-4 数据合同与结构违规计数（Hard gate 输入）。"""

    coverage: float
    duplicate_mapping_count: int
    unknown_reference_count: int
    empty_cluster_count: int

    @property
    def structural_violation_count(self) -> int:
        """返回全部结构违规的总数。"""
        return (
            self.duplicate_mapping_count
            + self.unknown_reference_count
            + self.empty_cluster_count
        )


def validate_contract(
    result: RequirementConsolidationResult,
    expected_requirement_count: int | None = None,
    expected_ids: set[int] | None = None,
) -> ContractViolations:
    """独立计算 P0-4 合同违规计数，不抛出异常。

    与 Pydantic 合同校验互补：这里把每个违规维度显式量化，供验收报告
    展示。正常持久化批次各违规维度应为 0。expected_ids 优先；不可得时
    用 expected_requirement_count 计算覆盖。
    """
    actual_ids = [mapping.requirement_id for mapping in result.mappings]
    duplicate_mapping_count = len(actual_ids) - len(set(actual_ids))
    covered_ids = set(actual_ids)
    if expected_ids is not None:
        coverage = (
            len(expected_ids & covered_ids) / len(expected_ids)
            if expected_ids
            else 0.0
        )
    elif expected_requirement_count is not None:
        coverage = (
            len(covered_ids) / expected_requirement_count
            if expected_requirement_count
            else 0.0
        )
    else:
        coverage = 0.0

    known_ids = {item.canonical_requirement_id for item in result.canonical_requirements}
    unknown_reference_count = sum(
        1
        for mapping in result.mappings
        if mapping.canonical_requirement_id not in known_ids
    )

    empty_cluster_count = sum(
        1
        for item in result.canonical_requirements
        if not any(
            mapping.canonical_requirement_id == item.canonical_requirement_id
            for mapping in result.mappings
        )
    )

    return ContractViolations(
        coverage=coverage,
        duplicate_mapping_count=duplicate_mapping_count,
        unknown_reference_count=unknown_reference_count,
        empty_cluster_count=empty_cluster_count,
    )


def mapping_clusters(
    result: RequirementConsolidationResult,
) -> dict[int, tuple[str, float]]:
    """把映射结果投影为 {requirement_id: (canonical_id, confidence)}。"""
    return {
        mapping.requirement_id: (
            mapping.canonical_requirement_id,
            mapping.confidence,
        )
        for mapping in result.mappings
    }


def _cluster_members(mapping: dict[int, tuple[str, float]]) -> list[list[int]]:
    """把映射投影为 canonical 成员列表（成员身份 = requirement_id，跨运行稳定）。"""
    clusters: dict[str, list[int]] = {}
    for requirement_id, (canonical_id, _) in mapping.items():
        clusters.setdefault(canonical_id, []).append(requirement_id)
    return sorted((sorted(members) for members in clusters.values()), key=len, reverse=True)


def _same_cluster_pairs(mapping: dict[int, tuple[str, float]]) -> set[frozenset[int]]:
    """同一 canonical 的实例对集合（同簇正实例对，不依赖临时 canonical ID）。"""
    pairs: set[frozenset[int]] = set()
    for members in _cluster_members(mapping):
        for first_index in range(len(members)):
            for second_index in range(first_index + 1, len(members)):
                pairs.add(frozenset({members[first_index], members[second_index]}))
    return pairs


def positive_pair_jaccard(
    first: dict[int, tuple[str, float]],
    second: dict[int, tuple[str, float]],
) -> float:
    """同簇正实例对的 Jaccard：|同簇对交集| / |同簇对并集|。

    对 cluster 拆分/合并比总 pairwise agreement 更敏感：拆分会让大量
    原本同簇的实例对在另一次运行中不再同簇（交集缩小），合并会让并集
    显著增大。
    """
    first_pairs = _same_cluster_pairs(first)
    second_pairs = _same_cluster_pairs(second)
    union = first_pairs | second_pairs
    if not union:
        return 1.0
    return len(first_pairs & second_pairs) / len(union)


def singleton_and_canonical_drift(
    mappings: list[dict[int, tuple[str, float]]],
) -> dict[str, Any]:
    """报告每次运行的 canonical 数量、singleton 比例与漂移范围。"""
    canonical_counts: list[int] = []
    singleton_ratios: list[float] = []
    for mapping in mappings:
        members = _cluster_members(mapping)
        canonical_counts.append(len(members))
        singletons = sum(1 for cluster in members if len(cluster) == 1)
        singleton_ratios.append(singletons / len(members) if members else 0.0)
    drift = {
        "canonical_counts": canonical_counts,
        "canonical_count_min": min(canonical_counts) if canonical_counts else 0,
        "canonical_count_max": max(canonical_counts) if canonical_counts else 0,
        "canonical_count_range": (max(canonical_counts) - min(canonical_counts))
        if canonical_counts
        else 0,
        "singleton_ratios": [round(ratio, 4) for ratio in singleton_ratios],
    }
    return drift


def build_acceptance_report(
    *,
    input_identity: dict[str, Any],
    contract: ContractViolations,
    stability: dict[str, Any] | None = None,
    metamorphic: dict[str, Any] | None = None,
    hard_gate_failures: list[str] | None = None,
    warnings: list[str] | None = None,
    diagnostics: list[str] | None = None,
) -> dict[str, Any]:
    """生成机器可读验收报告：合同、稳定性、变形与人工复核记录。"""
    hard_gate_failures = hard_gate_failures or []
    warnings = warnings or []
    diagnostics = diagnostics or []
    return {
        "input_identity": input_identity,
        "p0_4_contract": {
            "coverage": contract.coverage,
            "duplicate_mapping_count": contract.duplicate_mapping_count,
            "unknown_reference_count": contract.unknown_reference_count,
            "empty_cluster_count": contract.empty_cluster_count,
            "structural_violation_count": contract.structural_violation_count,
        },
        "p0_4_stability": stability or {},
        "metamorphic": metamorphic or {},
        "hard_gate_failures": hard_gate_failures,
        "warnings": warnings,
        "diagnostics": diagnostics,
    }


def write_acceptance_report(
    report: dict[str, Any],
    path: Path,
) -> None:
    """把验收报告写入JSON文件（脱敏：只含统计与诊断，不含真实证据）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
