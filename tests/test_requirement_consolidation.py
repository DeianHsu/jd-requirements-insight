"""验证跨JD原子要求归并合同及其确定性一致性规则。"""

import pytest
from pydantic import ValidationError

from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementMappingStatus,
    RequirementOccurrence,
    RequirementRelation,
    RequirementRelationType,
    normalize_requirement_name,
    validate_requirement_coverage,
)
from app.schemas import RequirementItem


def requirement(raw_name: str, evidence: str) -> RequirementItem:
    """构造保留完整抽取数据合同字段的虚构原子要求。"""
    return RequirementItem.model_validate(
        {
            "raw_name": raw_name,
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": evidence,
            "confidence": 0.9,
        }
    )


def consolidation_input() -> RequirementConsolidationInput:
    """构造来自两份JD并共同进入归并语料池的要求实例。"""
    return RequirementConsolidationInput(
        occurrences=[
            RequirementOccurrence(
                requirement_id=1,
                job_id=101,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                source_file="job-b.md",
                requirement=requirement(
                    "具备能力甲的使用经验", "具备能力甲的使用经验"
                ),
            ),
        ]
    )


def merged_result() -> RequirementConsolidationResult:
    """构造两个要求实例映射到同一标准要求项的有效归并结果。"""
    return RequirementConsolidationResult(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="requirement-a",
                canonical_name="能力甲使用经验",
                rationale="两条要求在各自证据中指向同一招聘条件",
                confidence=0.95,
            )
        ],
        mappings=[
            RequirementMapping(
                requirement_id=requirement_id,
                status=RequirementMappingStatus.MAPPED,
                canonical_requirement_id="requirement-a",
                rationale="要求实例表述不同，但招聘条件相同",
                confidence=0.95,
            )
            for requirement_id in (1, 2)
        ],
    )


def test_normalize_requirement_name_only_applies_generic_text_rules() -> None:
    assert normalize_requirement_name("  ＡＢＣ  Requirement  ") == "abc requirement"


def test_occurrence_preserves_complete_requirement_contract() -> None:
    occurrence = consolidation_input().occurrences[0]

    assert occurrence.requirement.raw_name == "能力甲使用经验"
    assert occurrence.requirement.evidence == "具备能力甲使用经验"
    assert occurrence.requirement.importance.value == "must"


def test_input_rejects_duplicate_requirement_occurrences() -> None:
    occurrence = consolidation_input().occurrences[0]

    with pytest.raises(ValidationError, match="要求实例ID不能重复"):
        RequirementConsolidationInput(occurrences=[occurrence, occurrence])


def test_synonymous_occurrences_can_map_to_one_requirement_without_being_deleted() -> None:
    source = consolidation_input()
    result = merged_result()

    validate_requirement_coverage(source, result)

    assert len(source.occurrences) == 2
    assert {
        mapping.canonical_requirement_id for mapping in result.mappings
    } == {"requirement-a"}


def test_result_rejects_normalized_duplicate_requirement_names() -> None:
    result = merged_result().model_dump(mode="json")
    result["canonical_requirements"].append(
        {
            "canonical_requirement_id": "requirement-b",
            "canonical_name": " 能力甲使用经验 ",
            "rationale": "重复标准要求项",
            "confidence": 0.8,
        }
    )
    result["mappings"].append(
        {
            "requirement_id": 3,
            "status": "mapped",
            "canonical_requirement_id": "requirement-b",
            "candidate_requirement_ids": [],
            "rationale": "测试重复",
            "confidence": 0.8,
        }
    )

    with pytest.raises(ValidationError, match="标准要求项名称不能重复"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_relation_to_unknown_requirement() -> None:
    result = merged_result().model_dump(mode="json")
    result["relations"] = [
        {
            "source_requirement_id": "requirement-a",
            "target_requirement_id": "missing",
            "relation_type": "part_of",
            "rationale": "测试关系",
            "confidence": 0.8,
        }
    ]

    with pytest.raises(ValidationError, match="关系引用未知标准要求项"):
        RequirementConsolidationResult.model_validate(result)


def test_result_treats_reverse_related_edges_as_duplicates() -> None:
    result = merged_result().model_dump(mode="json")
    result["canonical_requirements"].append(
        {
            "canonical_requirement_id": "requirement-b",
            "canonical_name": "能力乙",
            "rationale": "另一项能力",
            "confidence": 0.8,
        }
    )
    result["mappings"].append(
        {
            "requirement_id": 3,
            "status": "mapped",
            "canonical_requirement_id": "requirement-b",
            "candidate_requirement_ids": [],
            "rationale": "测试来源",
            "confidence": 0.8,
        }
    )
    result["relations"] = [
        RequirementRelation(
            source_requirement_id="requirement-a",
            target_requirement_id="requirement-b",
            relation_type=RequirementRelationType.RELATED_TO,
            rationale="测试相关",
            confidence=0.8,
        ),
        RequirementRelation(
            source_requirement_id="requirement-b",
            target_requirement_id="requirement-a",
            relation_type=RequirementRelationType.RELATED_TO,
            rationale="反向重复",
            confidence=0.8,
        ),
    ]

    with pytest.raises(ValidationError, match="要求关系不能重复"):
        RequirementConsolidationResult.model_validate(result)


def test_review_required_mapping_requires_candidates() -> None:
    with pytest.raises(ValidationError, match="必须包含候选标准要求项"):
        RequirementMapping(
            requirement_id=1,
            status=RequirementMappingStatus.REVIEW_REQUIRED,
            rationale="存在歧义",
            confidence=0.5,
        )


def test_coverage_rejects_missing_requirement_occurrence() -> None:
    source = consolidation_input()
    result = merged_result()
    result.mappings.pop()

    with pytest.raises(ValueError, match="遗漏要求实例"):
        validate_requirement_coverage(source, result)
