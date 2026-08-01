"""评测跨JD归并结果：要求映射准确率、关系准确率与未映射处理。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.requirement_consolidation import (
    RequirementConsolidationResult,
    RequirementMappingStatus,
    normalize_requirement_name,
)


@dataclass
class ConsolidationMetrics:
    """汇总归并评测的映射、关系和未映射处理指标。"""

    mapping_accuracy: float | None
    relation_accuracy: float | None
    unmapped_accuracy: float | None
    mapping_matched: int
    mapping_total: int
    relation_matched: int
    relation_total: int


def load_consolidation_cases(path: Path) -> dict[str, Any]:
    """读取人工标准答案评测文件并返回原始字典。"""
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
) -> set[tuple[str, str, str]]:
    """把关系转换为规范化名称三元组，related_to反向视为同一关系。"""
    result: set[tuple[str, str, str]] = set()
    for rel in relations:
        source = name_map.get(_field(rel, "source_requirement_id"))
        target = name_map.get(_field(rel, "target_requirement_id"))
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
    """把实际归并结果与人工标准答案对比，返回映射/关系/未映射指标。

    匹配按规范化标准要求项名称进行，不依赖模型生成的标准项ID。
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

    # 关系准确率：期望关系三元组被实际结果覆盖的比例。
    expected_relations = _relation_set(
        expected_names, expected["relations"]
    )
    actual_relations = _relation_set(actual_names, actual.relations)
    relation_matched = len(expected_relations & actual_relations)
    relation_total = len(expected_relations)

    # 未映射处理：期望非mapped实例实际是否也非mapped；无适用样本时为N/A。
    expected_unmapped = [
        item["requirement_id"]
        for item in expected["mappings"]
        if item["status"] != RequirementMappingStatus.MAPPED.value
    ]
    unmapped_total = len(expected_unmapped)
    unmapped_matched: int | None = None
    if unmapped_total:
        actual_unmapped = {
            item.requirement_id
            for item in actual.mappings
            if item.status is not RequirementMappingStatus.MAPPED
        }
        unmapped_matched = sum(
            1 for requirement_id in expected_unmapped if requirement_id in actual_unmapped
        )

    return ConsolidationMetrics(
        mapping_accuracy=(
            mapping_matched / mapping_total if mapping_total else None
        ),
        relation_accuracy=(
            relation_matched / relation_total if relation_total else None
        ),
        unmapped_accuracy=(
            unmapped_matched / unmapped_total if unmapped_total else None
        ),
        mapping_matched=mapping_matched,
        mapping_total=mapping_total,
        relation_matched=relation_matched,
        relation_total=relation_total,
    )
