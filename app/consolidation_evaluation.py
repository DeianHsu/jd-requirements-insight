"""评测跨JD归并结果：要求映射准确率、关系Precision/Recall/F1与状态处理。

参考标注草案（data/consolidation_cases.json）已按用户裁决降级为参考材料，
本模块指标只用于离线诊断与报告，不构成 P0-4 验收门槛；验收以规则合同、
同输入多次运行一致性与人工抽样复核为准（docs/work/P0-4.md）。
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
    RequirementMappingStatus,
    RequirementRelation,
    RequirementRelationType,
    normalize_requirement_name,
)


@dataclass
class ConsolidationMetrics:
    """汇总归并评测的映射、关系和未映射处理指标。"""

    mapping_accuracy: float | None
    relation_precision: float | None
    relation_recall: float | None
    relation_f1: float | None
    unmapped_accuracy: float | None
    mapping_matched: int
    mapping_total: int
    relation_matched: int
    relation_predicted: int
    relation_total: int


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
    """按显式批次ID加载映射和关系，重建可供离线评测的归并结果。"""
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
        )
        return PersistedConsolidationResult(
            consolidation_id=record.id,
            scope_key=record.scope_key,
            consolidator_version=record.consolidator_version,
            extractor_version=record.extractor_version,
            input_fingerprint=record.input_fingerprint,
            result=result,
        )


def load_consolidation_cases(path: Path) -> dict[str, Any]:
    """读取参考标注评测文件并返回原始字典（草案已降级，仅作参考）。"""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _name_map(requirements: list[Any], name_field: str) -> dict[str, str]:
    """把标准要求项ID映射为规范化名称，供跨ID名称匹配。"""
    return {
        item[name_field]: normalize_requirement_name(item["canonical_name"])
        for item in requirements
    }


def _field(item: Any, name: str) -> Any:
    """兼容字典和Pydantic对象的字段读取。"""
    return item[name] if isinstance(item, dict) else getattr(item, name)


def _relation_set(
    name_map: dict[str, str],
    relations: list[Any],
    relation_type_field: str = "relation_type",
    allowed_requirement_ids: set[str] | None = None,
) -> set[tuple[str, str, str]]:
    """把关系转换为规范化名称三元组，related_to反向视为同一关系。"""
    result: set[tuple[str, str, str]] = set()
    for rel in relations:
        source_id = _field(rel, "source_requirement_id")
        target_id = _field(rel, "target_requirement_id")
        if allowed_requirement_ids is not None and (
            source_id not in allowed_requirement_ids
            or target_id not in allowed_requirement_ids
        ):
            continue
        source = name_map.get(source_id)
        target = name_map.get(target_id)
        if not source or not target:
            continue
        relation_type = _field(rel, relation_type_field)
        if relation_type == "related_to":
            source, target = sorted((source, target))
        result.add((source, target, relation_type))
    return result


def evaluate_consolidation(
    actual: RequirementConsolidationResult,
    cases: dict[str, Any],
) -> ConsolidationMetrics:
    """把实际归并结果与参考标注对比，返回映射/关系/未映射参考指标。

    匹配按规范化标准要求项名称进行，不依赖模型生成的标准项ID。
    草案标注已降级为参考材料，本函数指标仅作诊断，不构成验收门槛。
    """
    expected = cases["expected"]
    expected_names = _name_map(
        expected["canonical_requirements"], "canonical_requirement_id"
    )
    actual_names = {
        item.canonical_requirement_id: normalize_requirement_name(
            item.canonical_name
        )
        for item in actual.canonical_requirements
    }

    # 映射准确率：期望(实例→标准项名称)与实际一致的比例；期望非mapped
    # 的实例不进入本指标分母，由未映射处理指标单独衡量。
    mapping_matched = 0
    mapping_total = 0
    for expected_mapping in expected["mappings"]:
        if expected_mapping["status"] != RequirementMappingStatus.MAPPED.value:
            continue
        mapping_total += 1
        expected_name = expected_names.get(
            expected_mapping["canonical_requirement_id"]
        )
        actual_mapping = next(
            (
                item
                for item in actual.mappings
                if item.requirement_id == expected_mapping["requirement_id"]
            ),
            None,
        )
        if actual_mapping is None or actual_mapping.canonical_requirement_id is None:
            continue
        actual_name = actual_names.get(actual_mapping.canonical_requirement_id)
        if expected_name is not None and actual_name == expected_name:
            mapping_matched += 1

    # 只在人工标准答案覆盖的实例所映射出的标准项子图内评测关系；子图中的
    # 额外关系计为假阳性，子图外未标注关系不作判断。
    annotated_requirement_ids = {
        item["requirement_id"]
        for item in expected["mappings"]
        if item["status"] == RequirementMappingStatus.MAPPED.value
    }
    annotated_actual_canonical_ids = {
        item.canonical_requirement_id
        for item in actual.mappings
        if item.requirement_id in annotated_requirement_ids
        and item.canonical_requirement_id is not None
    }
    expected_relations = _relation_set(
        expected_names, expected["relations"]
    )
    actual_relations = _relation_set(
        actual_names,
        actual.relations,
        allowed_requirement_ids=annotated_actual_canonical_ids,
    )
    relation_matched = len(expected_relations & actual_relations)
    relation_predicted = len(actual_relations)
    relation_total = len(expected_relations)
    if relation_predicted or relation_total:
        relation_precision = (
            relation_matched / relation_predicted if relation_predicted else 0.0
        )
        relation_recall = (
            relation_matched / relation_total if relation_total else None
        )
        if relation_precision == 0 or relation_recall in (None, 0):
            relation_f1 = 0.0
        else:
            relation_f1 = (
                2
                * relation_precision
                * relation_recall
                / (relation_precision + relation_recall)
            )
    else:
        relation_precision = None
        relation_recall = None
        relation_f1 = None

    # 未映射处理要求状态精确相同，不能把unmapped和review_required视为等价。
    expected_unmapped = {
        item["requirement_id"]: item["status"]
        for item in expected["mappings"]
        if item["status"] != RequirementMappingStatus.MAPPED.value
    }
    unmapped_total = len(expected_unmapped)
    unmapped_matched: int | None = None
    if unmapped_total:
        actual_statuses = {
            item.requirement_id: item.status.value
            for item in actual.mappings
        }
        unmapped_matched = sum(
            1
            for requirement_id, expected_status in expected_unmapped.items()
            if actual_statuses.get(requirement_id) == expected_status
        )

    return ConsolidationMetrics(
        mapping_accuracy=(
            mapping_matched / mapping_total if mapping_total else None
        ),
        relation_precision=relation_precision,
        relation_recall=relation_recall,
        relation_f1=relation_f1,
        unmapped_accuracy=(
            unmapped_matched / unmapped_total if unmapped_total else None
        ),
        mapping_matched=mapping_matched,
        mapping_total=mapping_total,
        relation_matched=relation_matched,
        relation_predicted=relation_predicted,
        relation_total=relation_total,
    )
