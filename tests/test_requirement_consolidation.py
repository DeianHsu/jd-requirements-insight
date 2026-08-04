"""验证跨JD原子要求归并合同及其确定性一致性规则。"""

import pytest
from pydantic import ValidationError

from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementOccurrence,
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
                extractor_version="test-model|prompt:0.10|schema:3.0",
                source_hash="a" * 64,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                extraction_id=1002,
                extractor_version="test-model|prompt:0.10|schema:3.0",
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
                source_requirement_ids=[1, 2],
                rationale="两条要求在各自证据中指向同一招聘条件",
                confidence=0.95,
            )
        ],
        mappings=[
            RequirementMapping(
                requirement_id=requirement_id,
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
            "source_requirement_ids": [3],
            "rationale": "重复标准要求项",
            "confidence": 0.8,
        }
    )
    result["mappings"].append(
        {
            "requirement_id": 3,
            "canonical_requirement_id": "requirement-b",
            "rationale": "测试重复",
            "confidence": 0.8,
        }
    )

    with pytest.raises(
        ValidationError,
        match="标准要求项名称不能重复.*能力甲使用经验=requirement-a,requirement-b",
    ):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_mapping_to_unknown_requirement() -> None:
    result = merged_result().model_dump(mode="json")
    result["mappings"].append(
        {
            "requirement_id": 3,
            "canonical_requirement_id": "missing",
            "rationale": "测试映射",
            "confidence": 0.8,
        }
    )

    with pytest.raises(ValidationError, match="映射引用未知标准要求项"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_unreferenced_canonical_requirement() -> None:
    result = merged_result().model_dump(mode="json")
    result["canonical_requirements"].append(
        {
            "canonical_requirement_id": "requirement-b",
            "canonical_name": "能力乙",
            "source_requirement_ids": [3],
            "rationale": "无人引用的标准项",
            "confidence": 0.8,
        }
    )

    with pytest.raises(ValidationError, match="标准要求项没有来源要求"):
        RequirementConsolidationResult.model_validate(result)


def test_result_rejects_duplicate_mapping_for_same_instance() -> None:
    result = merged_result().model_dump(mode="json")
    result["mappings"].append(
        {
            "requirement_id": 1,
            "canonical_requirement_id": "requirement-a",
            "rationale": "同一实例重复映射",
            "confidence": 0.8,
        }
    )

    with pytest.raises(ValidationError, match="同一要求实例不能产生多条映射结果"):
        RequirementConsolidationResult.model_validate(result)


def test_mapping_rejects_blank_canonical_id() -> None:
    with pytest.raises(ValidationError, match="不能为空"):
        RequirementMapping(
            requirement_id=1,
            canonical_requirement_id="  ",
            rationale="空白标准项ID",
            confidence=0.8,
        )


def test_mapping_rejects_blank_rationale() -> None:
    with pytest.raises(ValidationError, match="不能为空"):
        RequirementMapping(
            requirement_id=1,
            canonical_requirement_id="requirement-a",
            rationale="  ",
            confidence=0.8,
        )


def test_partition_rejects_too_many_canonicals() -> None:
    """canonical 数量大于输入实例数量时被拒绝。"""
    from app.requirement_consolidation import validate_canonical_partition

    source = consolidation_input()
    canonicals = [
        CanonicalRequirement(
            canonical_requirement_id=f"cr-{index}",
            canonical_name=f"能力{index}",
            source_requirement_ids=[index + 1],
            rationale="测试",
            confidence=0.8,
        )
        for index in range(3)
    ]

    with pytest.raises(ValueError, match="不能大于输入实例数量"):
        validate_canonical_partition(source, canonicals)


def test_partition_rejects_duplicate_canonical_names() -> None:
    """canonical name（规范化后）重复时被拒绝。"""
    from app.requirement_consolidation import validate_canonical_partition

    source = consolidation_input()
    canonicals = [
        CanonicalRequirement(
            canonical_requirement_id="cr-0",
            canonical_name="能力甲",
            source_requirement_ids=[1],
            rationale="测试",
            confidence=0.8,
        ),
        CanonicalRequirement(
            canonical_requirement_id="cr-1",
            canonical_name=" 能力甲 ",
            source_requirement_ids=[2],
            rationale="测试",
            confidence=0.8,
        ),
    ]

    with pytest.raises(ValueError, match="canonical name 重复"):
        validate_canonical_partition(source, canonicals)


def test_partition_valid_singleton_partition_passes() -> None:
    """合法分区（含 singleton）通过校验。"""
    from app.requirement_consolidation import validate_canonical_partition

    source = consolidation_input()
    canonicals = [
        CanonicalRequirement(
            canonical_requirement_id="cr-0",
            canonical_name="能力甲使用经验",
            source_requirement_ids=[1, 2],
            rationale="同义归并",
            confidence=0.95,
        )
    ]

    validate_canonical_partition(source, canonicals)  # 不抛异常


def test_coverage_rejects_missing_requirement_occurrence() -> None:
    source = consolidation_input()
    result = merged_result()
    result.mappings.pop()

    with pytest.raises(ValueError, match="遗漏要求实例"):
        validate_requirement_coverage(source, result)


def test_coverage_rejects_unexpected_requirement_occurrence() -> None:
    source = consolidation_input()
    result = merged_result()
    result.mappings.append(
        RequirementMapping(
            requirement_id=99,
            canonical_requirement_id="requirement-a",
            rationale="不属于语料池的实例",
            confidence=0.8,
        )
    )

    with pytest.raises(ValueError, match="包含未知要求实例"):
        validate_requirement_coverage(source, result)


def test_stage1_missing_source_instance_is_rejected() -> None:
    """来源分区声明遗漏某个输入实例时被拒绝（须由模型创建singleton）。"""
    source = consolidation_input()
    result = merged_result()
    result.canonical_requirements[0].source_requirement_ids = [1]

    with pytest.raises(ValueError, match="来源声明遗漏实例"):
        validate_requirement_coverage(source, result)


def test_instance_declared_in_two_canonicals_is_rejected() -> None:
    """同一个实例被声明属于两个 canonical 时被拒绝。"""
    source = consolidation_input()
    result = merged_result()
    result.canonical_requirements.append(
        CanonicalRequirement(
            canonical_requirement_id="requirement-b",
            canonical_name="能力乙",
            source_requirement_ids=[2],
            rationale="另一个标准项",
            confidence=0.8,
        )
    )
    # 绕过 Result validator（b 无 mapping 引用会被 validator 拒绝），
    # 直接验证来源归属检查：实例 2 同时声明在 requirement-a 与 requirement-b。
    result = RequirementConsolidationResult.model_construct(
        canonical_requirements=result.canonical_requirements,
        mappings=result.mappings,
    )

    with pytest.raises(ValueError, match="不能属于多个标准要求项"):
        validate_requirement_coverage(source, result)


def test_canonical_without_source_instance_is_rejected() -> None:
    """声明了来源为空的标准项被拒绝（每个 canonical 至少一个来源）。"""
    source = consolidation_input()
    result = merged_result().model_dump(mode="json")
    result["canonical_requirements"].append(
        {
            "canonical_requirement_id": "requirement-b",
            "canonical_name": "能力乙",
            "source_requirement_ids": [],
            "rationale": "无来源标准项",
            "confidence": 0.8,
        }
    )
    result["mappings"][1]["canonical_requirement_id"] = "requirement-b"

    with pytest.raises(ValueError, match="没有来源实例"):
        validate_requirement_coverage(
            source, RequirementConsolidationResult.model_validate(result)
        )


def test_mapping_conflicts_with_source_partition() -> None:
    """映射与来源分区归属冲突时被拒绝。"""
    source = consolidation_input()
    # 绕过 Result validator（b 无 mapping 引用会被 validator 拒绝），
    # 直接验证映射与来源归属的一致性检查。
    result = RequirementConsolidationResult.model_construct(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="requirement-a",
                canonical_name="能力甲使用经验",
                source_requirement_ids=[1],
                rationale="阶段1归属",
                confidence=0.95,
            ),
            CanonicalRequirement(
                canonical_requirement_id="requirement-b",
                canonical_name="能力乙",
                source_requirement_ids=[2],
                rationale="阶段1归属",
                confidence=0.8,
            ),
        ],
        mappings=[
            RequirementMapping(
                requirement_id=1,
                canonical_requirement_id="requirement-a",
                rationale="一致",
                confidence=0.95,
            ),
            # 来源分区声明实例2归属 requirement-b，映射却指向 requirement-a。
            RequirementMapping(
                requirement_id=2,
                canonical_requirement_id="requirement-a",
                rationale="冲突映射",
                confidence=0.8,
            ),
        ],
    )

    with pytest.raises(ValueError, match="映射与来源分区归属冲突"):
        validate_requirement_coverage(source, result)


def test_stage1_singleton_for_unmergeable_instance_passes() -> None:
    """模型为无法合并实例创建 singleton，映射由来源分区确定性生成。"""
    source = consolidation_input()
    result = RequirementConsolidationResult(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="requirement-a",
                canonical_name="能力甲使用经验",
                source_requirement_ids=[1],
                rationale="同义归并",
                confidence=0.95,
            ),
            CanonicalRequirement(
                canonical_requirement_id="requirement-b",
                canonical_name="具备能力甲的使用经验",
                source_requirement_ids=[2],
                rationale="无法确认等价，保持singleton",
                confidence=0.8,
            ),
        ],
        mappings=[
            RequirementMapping(
                requirement_id=1,
                canonical_requirement_id="requirement-a",
                rationale="同条件",
                confidence=0.95,
            ),
            RequirementMapping(
                requirement_id=2,
                canonical_requirement_id="requirement-b",
                rationale="独立",
                confidence=0.8,
            ),
        ],
    )

    validate_requirement_coverage(source, result)  # 不抛异常

    assert len(result.canonical_requirements) == 2
    assert {m.canonical_requirement_id for m in result.mappings} == {
        "requirement-a",
        "requirement-b",
    }
