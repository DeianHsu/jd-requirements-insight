"""P0-4 合同校验、变形指标、下游统计投影与验收报告。

本模块不依赖任何人工 Gold 数据集（data/consolidation_cases.json 已删除，
来源和审核过程无法确认，不再作为项目资产和验收依据）。验收体系验证：

- 数据合同与完整覆盖（Hard gate）；
- 非语义因素不变性：输入顺序、分块大小（Metamorphic，Hard gate 或
  Diagnostic 按调用方口径）；
- 多次运行稳定性：co-clustering agreement、edge Jaccard（Hard gate）；
- 关系图稀疏性与结构正确性（Hard gate + Warning）；
- 下游统计不变性：层级关系不改变核心事实计数（Hard gate）；
- 异常可诊断性：机器可读验收报告（Hard gate / Warning / Diagnostic 分级）。
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
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementMappingStatus,
    RequirementRelation,
    RequirementRelationType,
    normalize_requirement_name,
)


@dataclass(frozen=True)
class PersistedConsolidationResult:
    """保存指定持久化批次的可复现身份与重建后的归并结果。"""

    consolidation_id: int
    scope_key: str
    consolidator_version: str
    extractor_version: str
    input_fingerprint: str
    hierarchy_status: str
    result: RequirementConsolidationResult


def load_persisted_consolidation_result(
    session_factory: sessionmaker[Session],
    consolidation_id: int,
) -> PersistedConsolidationResult:
    """按显式批次ID加载映射和关系，重建可供离线验证的归并结果。"""
    with session_factory() as session:
        record = session.scalar(
            select(JobConsolidation)
            .options(
                selectinload(JobConsolidation.canonical_requirements),
                selectinload(JobConsolidation.mappings),
                selectinload(JobConsolidation.relations),
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
                    rationale=item.rationale,
                    confidence=item.confidence,
                )
                for item in record.canonical_requirements
            ],
            mappings=[
                RequirementMapping(
                    requirement_id=item.requirement_id,
                    status=RequirementMappingStatus(item.status),
                    canonical_requirement_id=item.canonical_requirement_id,
                    candidate_requirement_ids=(
                        item.candidate_requirement_ids or []
                    ),
                    rationale=item.rationale,
                    confidence=item.confidence,
                )
                for item in record.mappings
            ],
            relations=[
                RequirementRelation(
                    source_requirement_id=item.source_requirement_id,
                    target_requirement_id=item.target_requirement_id,
                    relation_type=RequirementRelationType(item.relation_type),
                    rationale=item.rationale,
                    confidence=item.confidence,
                )
                for item in record.relations
            ],
            hierarchy_status=record.hierarchy_status,
        )
        return PersistedConsolidationResult(
            consolidation_id=record.id,
            scope_key=record.scope_key,
            consolidator_version=record.consolidator_version,
            extractor_version=record.extractor_version,
            input_fingerprint=record.input_fingerprint,
            hierarchy_status=record.hierarchy_status,
            result=result,
        )


@dataclass(frozen=True)
class ContractViolations:
    """P0-4A 数据合同与结构违规计数（Hard gate 输入）。"""

    coverage: float
    duplicate_mapping_count: int
    unknown_reference_count: int
    empty_cluster_count: int
    self_loop_count: int
    duplicate_edge_count: int
    direction_conflict_count: int
    cycle_count: int

    @property
    def structural_violation_count(self) -> int:
        """返回全部结构违规的总数。"""
        return (
            self.duplicate_mapping_count
            + self.unknown_reference_count
            + self.empty_cluster_count
            + self.self_loop_count
            + self.duplicate_edge_count
            + self.direction_conflict_count
            + self.cycle_count
        )


def validate_contract(
    result: RequirementConsolidationResult,
    expected_requirement_count: int | None = None,
    expected_ids: set[int] | None = None,
) -> ContractViolations:
    """独立计算 P0-4A 合同与 P0-4B 结构违规计数，不抛出异常。

    与 Pydantic 合同校验互补：这里把每个违规维度显式量化，供验收报告
    分级展示。正常持久化批次各违规维度应为 0。expected_ids 优先；
    不可得时用 expected_requirement_count 计算覆盖。
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
    unknown_reference_count = 0
    for mapping in result.mappings:
        referenced = (
            [mapping.canonical_requirement_id]
            if mapping.canonical_requirement_id is not None
            else mapping.candidate_requirement_ids
        )
        unknown_reference_count += sum(
            1 for requirement_id in referenced if requirement_id not in known_ids
        )

    mapped_requirements = {
        mapping.requirement_id
        for mapping in result.mappings
        if mapping.status is RequirementMappingStatus.MAPPED
        and mapping.canonical_requirement_id is not None
    }
    empty_cluster_count = sum(
        1
        for item in result.canonical_requirements
        if not any(
            mapping.canonical_requirement_id == item.canonical_requirement_id
            and mapping.requirement_id in mapped_requirements
            for mapping in result.mappings
        )
    )

    self_loop_count = 0
    duplicate_edge_count = 0
    direction_conflict_count = 0
    seen_edges: set[tuple[str, str]] = set()
    for relation in result.relations:
        if relation.relation_type is not RequirementRelationType.BROADER_THAN:
            continue
        source, target = relation.source_requirement_id, relation.target_requirement_id
        if source == target:
            self_loop_count += 1
        if (source, target) in seen_edges:
            duplicate_edge_count += 1
        seen_edges.add((source, target))
        if source != target and (target, source) in seen_edges:
            direction_conflict_count += 1

    graph: dict[str, set[str]] = {}
    for relation in result.relations:
        if relation.relation_type is not RequirementRelationType.BROADER_THAN:
            continue
        graph.setdefault(relation.source_requirement_id, set()).add(
            relation.target_requirement_id
        )
        graph.setdefault(relation.target_requirement_id, set())
    cycle_count = _count_cycles(graph)

    return ContractViolations(
        coverage=coverage,
        duplicate_mapping_count=duplicate_mapping_count,
        unknown_reference_count=unknown_reference_count,
        empty_cluster_count=empty_cluster_count,
        self_loop_count=self_loop_count,
        duplicate_edge_count=duplicate_edge_count,
        direction_conflict_count=direction_conflict_count,
        cycle_count=cycle_count,
    )


def _count_cycles(graph: dict[str, set[str]]) -> int:
    """统计有向图中能够返回起点的环数量（每个环按起点计一次）。"""
    cycle_count = 0
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        """深度优先搜索；返回该节点子树是否存在环。"""
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        has_cycle = any(visit(target) for target in graph.get(node, set()))
        visiting.remove(node)
        visited.add(node)
        if has_cycle:
            nonlocal cycle_count
            cycle_count += 1
        return has_cycle

    for node in graph:
        visit(node)
    return cycle_count


def mapping_clusters(
    result: RequirementConsolidationResult,
) -> dict[int, tuple[str, float]]:
    """把映射结果投影为 {requirement_id: (canonical_id, confidence)}。"""
    return {
        mapping.requirement_id: (
            mapping.canonical_requirement_id or "",
            mapping.confidence,
        )
        for mapping in result.mappings
        if mapping.status is RequirementMappingStatus.MAPPED
        and mapping.canonical_requirement_id is not None
    }


def co_clustering_agreement(
    first: dict[int, tuple[str, float]],
    second: dict[int, tuple[str, float]],
    high_confidence_threshold: float = 0.9,
) -> tuple[float, float]:
    """计算两次运行实例分组的 pairwise co-clustering agreement。

    对全部实例对 (i, j)，一致 = 两次运行中 (same_cluster(i,j)) 相同。
    返回 (高置信实例对一致率, 全部实例对一致率)。高置信实例对指两次
    运行中两个实例的映射 confidence 均不低于阈值。canonical ID 差异
    不影响比较（只比较是否同簇），因此不依赖模型生成的临时 ID。
    """
    instance_ids = sorted(set(first) | set(second))
    overall_matches = 0
    overall_pairs = 0
    high_confidence_matches = 0
    high_confidence_pairs = 0
    for index, first_id in enumerate(instance_ids):
        for second_id in instance_ids[index + 1 :]:
            if first_id not in first or first_id not in second:
                continue
            if second_id not in first or second_id not in second:
                continue
            same_in_first = first[first_id][0] == first[second_id][0]
            same_in_second = second[first_id][0] == second[second_id][0]
            overall_pairs += 1
            if same_in_first == same_in_second:
                overall_matches += 1
            high_confidence = (
                first[first_id][1] >= high_confidence_threshold
                and first[second_id][1] >= high_confidence_threshold
                and second[first_id][1] >= high_confidence_threshold
                and second[second_id][1] >= high_confidence_threshold
            )
            if high_confidence:
                high_confidence_pairs += 1
                if same_in_first == same_in_second:
                    high_confidence_matches += 1
    return (
        high_confidence_matches / high_confidence_pairs if high_confidence_pairs else 1.0,
        overall_matches / overall_pairs if overall_pairs else 1.0,
    )


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


def merge_pair_metrics(
    reference: dict[int, tuple[str, float]],
    predicted: dict[int, tuple[str, float]],
) -> dict[str, float]:
    """以 reference 的同簇实例对为正例、predicted 的同簇对为预测，计算 P/R/F1。

    - predicted 合并了两个 cluster：产生 reference 中不存在的跨簇对，
      precision 下降；
    - predicted 拆开了某个 cluster：丢失 reference 中的对内同簇对，
      recall 下降。
    只用于稳定性诊断，不描述语义准确率。
    """
    reference_pairs = _same_cluster_pairs(reference)
    predicted_pairs = _same_cluster_pairs(predicted)
    true_positive = len(reference_pairs & predicted_pairs)
    false_positive = len(predicted_pairs - reference_pairs)
    false_negative = len(reference_pairs - predicted_pairs)
    precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 1.0
    recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 1.0
    return {"precision": precision, "recall": recall, "f1": f1}


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


def instance_neighbor_stability(
    first: dict[int, tuple[str, float]],
    second: dict[int, tuple[str, float]],
) -> float:
    """每个实例的同簇邻居集合（其他同簇实例）的 Jaccard 均值。

    识别"某个实例的邻居在两次运行间大幅变化"（如 cluster 被拆开后
    部分成员离散）；孤立实例（无邻居）计为一致。
    """
    def neighbors(mapping: dict[int, tuple[str, float]]) -> dict[int, frozenset[int]]:
        result: dict[int, frozenset[int]] = {}
        for members in _cluster_members(mapping):
            member_set = frozenset(members)
            for member in members:
                result[member] = member_set - {member}
        return result

    first_neighbors = neighbors(first)
    second_neighbors = neighbors(second)
    shared_ids = set(first_neighbors) & set(second_neighbors)
    scores: list[float] = []
    for requirement_id in shared_ids:
        first_set = first_neighbors[requirement_id]
        second_set = second_neighbors[requirement_id]
        if not first_set and not second_set:
            scores.append(1.0)
        else:
            scores.append(len(first_set & second_set) / len(first_set | second_set))
    return sum(scores) / len(scores) if scores else 1.0


def top_cluster_membership_stability(
    first: dict[int, tuple[str, float]],
    second: dict[int, tuple[str, float]],
    k: int = 10,
) -> dict[str, float]:
    """最大 cluster 成员集合稳定性：按 canonical 成员数取 Top-K 比较集合 Jaccard。

    canonical 身份 = 成员实例集合（requirement_id 跨运行稳定），不依赖
    临时 canonical ID；Jaccard < 1 表示最大 cluster 的成员集合发生变化。
    注意：本指标按实例数排序，不等于市场 Top-K（P0-6 按 distinct job
    count 统计后另行实现真正的 Top-K stability）。
    """
    def top_k(mapping: dict[int, tuple[str, float]], limit: int) -> set[frozenset[int]]:
        clusters = _cluster_members(mapping)
        return {frozenset(members) for members in clusters[:limit]}

    first_top = top_k(first, k)
    second_top = top_k(second, k)
    union = first_top | second_top
    if not union:
        return {"jaccard": 1.0, "top_count": 0}
    return {
        "jaccard": len(first_top & second_top) / len(union),
        "top_count": min(len(first_top), len(second_top)),
    }


@dataclass(frozen=True)
class RelationGraphStats:
    """汇总归并关系图的稀疏度与过度生成警戒指标（Warning/Diagnostic）。"""

    edge_count: int
    node_count: int
    edge_node_ratio: float
    max_out_degree: int
    max_in_degree: int
    root_node_count: int
    isolated_node_count: int
    low_confidence_edge_count: int
    uncertain_count: int


def relation_graph_stats(
    result: RequirementConsolidationResult,
    low_confidence_threshold: float = 0.6,
) -> RelationGraphStats:
    """统计正式broader_than关系图的规模、密度与低置信边数量。

    只统计BROADER_THAN正式边；uncertain判断单独计数。指标用于发现
    关系图失控（过度生成、宽泛父节点过多、大量低置信边），进入验收
    报告，不直接构成硬失败条件。
    """
    edges: list[tuple[str, str, float]] = [
        (
            relation.source_requirement_id,
            relation.target_requirement_id,
            relation.confidence,
        )
        for relation in result.relations
        if relation.relation_type is RequirementRelationType.BROADER_THAN
    ]
    canonical_ids = {
        item.canonical_requirement_id for item in result.canonical_requirements
    }
    nodes: set[str] = set()
    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    for source, target, _ in edges:
        nodes.add(source)
        nodes.add(target)
        out_degree[source] = out_degree.get(source, 0) + 1
        in_degree[target] = in_degree.get(target, 0) + 1
    roots = {node for node in nodes if out_degree.get(node, 0) and not in_degree.get(node, 0)}
    isolated = canonical_ids - nodes
    return RelationGraphStats(
        edge_count=len(edges),
        node_count=len(nodes),
        edge_node_ratio=(len(edges) / len(nodes)) if nodes else 0.0,
        max_out_degree=max(out_degree.values(), default=0),
        max_in_degree=max(in_degree.values(), default=0),
        root_node_count=len(roots),
        isolated_node_count=len(isolated),
        low_confidence_edge_count=sum(
            1 for _, _, confidence in edges if confidence < low_confidence_threshold
        ),
        uncertain_count=len(result.uncertain_relations),
    )


def relation_edges_by_name(
    result: RequirementConsolidationResult,
) -> set[tuple[str, str]]:
    """把broader_than边转换为规范化名称有序对，供跨批次Jaccard比较。

    canonical ID 每次运行可能不同，因此用规范化名称对齐节点。
    """
    names = {
        item.canonical_requirement_id: normalize_requirement_name(
            item.canonical_name
        )
        for item in result.canonical_requirements
    }
    edges: set[tuple[str, str]] = set()
    for relation in result.relations:
        if relation.relation_type is not RequirementRelationType.BROADER_THAN:
            continue
        source = names.get(relation.source_requirement_id)
        target = names.get(relation.target_requirement_id)
        if source is not None and target is not None:
            edges.add((source, target))
    return edges


def edge_jaccard(
    first: set[tuple[str, str]],
    second: set[tuple[str, str]],
) -> float:
    """计算两次运行broader_than边集合的Jaccard相似度。"""
    union = first | second
    if not union:
        return 1.0
    return len(first & second) / len(union)


def direction_consistency(
    first: set[tuple[str, str]],
    second: set[tuple[str, str]],
) -> float:
    """计算共同出现边对（名称对相同、方向可能相反）的方向一致率。

    100% 表示所有共同边方向完全一致；反向边（A->B vs B->A）计为不一致。
    """
    first_pairs = {frozenset((source, target)) for source, target in first}
    second_pairs = {frozenset((source, target)) for source, target in second}
    common = first_pairs & second_pairs
    if not common:
        return 1.0
    consistent = sum(
        1
        for pair in common
        if (tuple(sorted(pair)) in first and tuple(sorted(pair)) in second)
        or (tuple(reversed(tuple(sorted(pair)))) in first
            and tuple(reversed(tuple(sorted(pair)))) in second)
    )
    return consistent / len(common)


@dataclass(frozen=True)
class FactProjection:
    """P0-4A 最小事实统计投影：P0-6 所依赖的数据合同（与层级关系无关）。"""

    instance_counts: dict[str, int]
    distinct_job_counts: dict[str, int]
    source_job_sets: dict[str, set[int]]
    importance_counts: dict[str, dict[str, int]]
    evidence_counts: dict[str, int]


def fact_projection(
    result: RequirementConsolidationResult,
    occurrences: RequirementConsolidationInput,
) -> FactProjection:
    """根据P0-4A映射计算每个canonical requirement的事实统计投影。

    只依赖（实例 -> 标准项）映射与实例来源，不读取任何关系边，因此
    层级关系（P0-4B）不可能改变投影结果。
    """
    job_by_requirement = {
        occurrence.requirement_id: occurrence.job_id
        for occurrence in occurrences.occurrences
    }
    importance_by_requirement = {
        occurrence.requirement_id: occurrence.requirement.importance.value
        for occurrence in occurrences.occurrences
    }
    instance_counts: dict[str, int] = {}
    distinct_job_counts: dict[str, int] = {}
    source_job_sets: dict[str, set[int]] = {}
    importance_counts: dict[str, dict[str, int]] = {}
    evidence_counts: dict[str, int] = {}
    for mapping in result.mappings:
        if (
            mapping.status is not RequirementMappingStatus.MAPPED
            or mapping.canonical_requirement_id is None
        ):
            continue
        canonical_id = mapping.canonical_requirement_id
        instance_counts[canonical_id] = instance_counts.get(canonical_id, 0) + 1
        evidence_counts[canonical_id] = evidence_counts.get(canonical_id, 0) + 1
        job_id = job_by_requirement.get(mapping.requirement_id)
        if job_id is not None:
            source_job_sets.setdefault(canonical_id, set()).add(job_id)
        importance = importance_by_requirement.get(mapping.requirement_id, "unknown")
        importance_counts.setdefault(canonical_id, {})
        importance_counts[canonical_id][importance] = (
            importance_counts[canonical_id].get(importance, 0) + 1
        )
    distinct_job_counts = {
        canonical_id: len(jobs)
        for canonical_id, jobs in source_job_sets.items()
    }
    return FactProjection(
        instance_counts=instance_counts,
        distinct_job_counts=distinct_job_counts,
        source_job_sets=source_job_sets,
        importance_counts=importance_counts,
        evidence_counts=evidence_counts,
    )


def projections_equal(first: FactProjection, second: FactProjection) -> bool:
    """判断两个事实投影完全一致（下游统计不变性的Hard gate）。"""
    return (
        first.instance_counts == second.instance_counts
        and first.distinct_job_counts == second.distinct_job_counts
        and first.source_job_sets == second.source_job_sets
        and first.importance_counts == second.importance_counts
        and first.evidence_counts == second.evidence_counts
    )


def build_acceptance_report(
    *,
    input_identity: dict[str, Any],
    contract: ContractViolations,
    stability: dict[str, Any] | None = None,
    metamorphic: dict[str, Any] | None = None,
    hierarchy: dict[str, Any] | None = None,
    downstream: dict[str, Any] | None = None,
    hard_gate_failures: list[str] | None = None,
    warnings: list[str] | None = None,
    diagnostics: list[str] | None = None,
) -> dict[str, Any]:
    """生成机器可读验收报告，区分 Hard gate / Warning / Diagnostic。"""
    hard_gate_failures = hard_gate_failures or []
    warnings = warnings or []
    diagnostics = diagnostics or []
    return {
        "input_identity": input_identity,
        "p0_4a_contract": {
            "coverage": contract.coverage,
            "duplicate_mapping_count": contract.duplicate_mapping_count,
            "unknown_reference_count": contract.unknown_reference_count,
            "empty_cluster_count": contract.empty_cluster_count,
            "structural_violation_count": contract.structural_violation_count,
        },
        "p0_4a_stability": stability or {},
        "metamorphic": metamorphic or {},
        "p0_4b_hierarchy": hierarchy or {},
        "downstream_invariance": downstream or {},
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
