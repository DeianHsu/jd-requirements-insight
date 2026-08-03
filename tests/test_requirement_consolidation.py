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
                extraction_id=1001,
                extractor_version="test-model|prompt:1.0|schema:2.0",
                source_hash="a" * 64,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                extraction_id=1002,
                extractor_version="test-model|prompt:1.0|schema:2.0",
                source_hash="b" * 64,
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


def result_with_three_canonicals() -> dict:
    """构造三个标准要求项及各自来源，供关系图约束测试使用。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": f"requirement-{index}",
                "canonical_name": f"能力{index}",
                "rationale": "独立要求",
                "confidence": 0.9,
            }
            for index in range(3)
        ],
        "mappings": [
            {
                "requirement_id": index + 1,
                "status": "mapped",
                "canonical_requirement_id": f"requirement-{index}",
                "candidate_requirement_ids": [],
                "rationale": "来源映射",
                "confidence": 0.9,
            }
            for index in range(3)
        ],
        "relations": [],
    }


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

    with pytest.raises(
        ValidationError,
        match="标准要求项名称不能重复.*能力甲使用经验=requirement-a,requirement-b",
    ):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_relation_to_unknown_requirement() -> None:
    result = merged_result().model_dump(mode="json")
    result["relations"] = [
        {
            "source_requirement_id": "requirement-a",
            "target_requirement_id": "missing",
            "relation_type": "broader_than",
            "rationale": "测试关系",
            "confidence": 0.8,
        }
    ]

    with pytest.raises(ValidationError, match="关系引用未知标准要求项"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_duplicate_broader_edges() -> None:
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
            relation_type=RequirementRelationType.BROADER_THAN,
            rationale="测试包含",
            confidence=0.8,
        ),
        RequirementRelation(
            source_requirement_id="requirement-a",
            target_requirement_id="requirement-b",
            relation_type=RequirementRelationType.BROADER_THAN,
            rationale="同向重复",
            confidence=0.8,
        ),
    ]

    with pytest.raises(ValidationError, match="要求关系不能重复"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_broader_than_direction_conflict() -> None:
    """验证同一对标准要求项互为broader_than时被方向冲突检查拒绝。"""
    result = result_with_three_canonicals()
    result["relations"] = [
        {
            "source_requirement_id": "requirement-0",
            "target_requirement_id": "requirement-1",
            "relation_type": "broader_than",
            "rationale": "甲包含乙",
            "confidence": 0.8,
        },
        {
            "source_requirement_id": "requirement-1",
            "target_requirement_id": "requirement-0",
            "relation_type": "broader_than",
            "rationale": "乙包含甲",
            "confidence": 0.8,
        },
    ]

    with pytest.raises(ValidationError, match="broader_than 方向冲突"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_broader_than_cycle() -> None:
    """验证broader_than包含图不能形成环。"""
    result = result_with_three_canonicals()
    result["relations"] = [
        {
            "source_requirement_id": f"requirement-{source}",
            "target_requirement_id": f"requirement-{target}",
            "relation_type": "broader_than",
            "rationale": "包含关系",
            "confidence": 0.8,
        }
        for source, target in ((0, 1), (1, 2), (2, 0))
    ]

    with pytest.raises(ValidationError, match="broader_than关系不能形成环"):
        RequirementConsolidationResult.model_validate(result)


def test_result_accepts_directed_relation_chain() -> None:
    """验证无环的broader_than关系链仍可通过合同。"""
    result = result_with_three_canonicals()
    result["relations"] = [
        {
            "source_requirement_id": f"requirement-{source}",
            "target_requirement_id": f"requirement-{target}",
            "relation_type": "broader_than",
            "rationale": "合法关系链",
            "confidence": 0.8,
        }
        for source, target in ((0, 1), (1, 2))
    ]

    validated = RequirementConsolidationResult.model_validate(result)

    assert len(validated.relations) == 2


def test_review_required_mapping_requires_candidates() -> None:
    with pytest.raises(ValidationError, match="必须包含候选标准要求项"):
        RequirementMapping(
            requirement_id=1,
            status=RequirementMappingStatus.REVIEW_REQUIRED,
            rationale="存在歧义",
            confidence=0.5,
        )


def test_uncertain_in_relations_is_rejected() -> None:
    """验证uncertain不能作为正式关系进入relations。"""
    result = result_with_three_canonicals()
    result["relations"] = [
        {
            "source_requirement_id": "requirement-0",
            "target_requirement_id": "requirement-1",
            "relation_type": "uncertain",
            "rationale": "无法判断方向",
            "confidence": 0.5,
        }
    ]

    with pytest.raises(ValidationError, match="uncertain 是模型判断状态"):
        RequirementConsolidationResult.model_validate(result)


def test_uncertain_relations_require_uncertain_type_and_known_references() -> None:
    """验证uncertain_relations只接受uncertain类型且引用已知标准项。"""
    result = result_with_three_canonicals()
    result["uncertain_relations"] = [
        {
            "source_requirement_id": "requirement-0",
            "target_requirement_id": "requirement-1",
            "relation_type": "broader_than",
            "rationale": "类型错误",
            "confidence": 0.8,
        }
    ]

    with pytest.raises(ValidationError, match="只能保存 uncertain 判断记录"):
        RequirementConsolidationResult.model_validate(result)

    result["uncertain_relations"] = [
        {
            "source_requirement_id": "missing",
            "target_requirement_id": "requirement-1",
            "relation_type": "uncertain",
            "rationale": "引用未知",
            "confidence": 0.5,
        }
    ]
    with pytest.raises(ValidationError, match="不确定判断引用未知标准要求项"):
        RequirementConsolidationResult.model_validate(result)


def test_coverage_rejects_missing_requirement_occurrence() -> None:
    source = consolidation_input()
    result = merged_result()
    result.mappings.pop()

    with pytest.raises(ValueError, match="遗漏要求实例"):
        validate_requirement_coverage(source, result)
