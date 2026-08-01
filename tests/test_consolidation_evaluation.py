"""验证归并评测指标：映射准确率、关系Precision/Recall/F1与未映射状态。"""

from app.consolidation_evaluation import (
    evaluate_consolidation,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementMappingStatus,
    RequirementRelation,
    RequirementRelationType,
    normalize_requirement_name,
)


def make_cases(
    with_relation: bool = True, unmapped_requirement: int | None = None
) -> dict:
    """构造使用中性虚构领域名称的人工标准答案评测文件结构。"""
    mappings = [
        {
            "requirement_id": 1,
            "canonical_requirement_id": "c1",
            "status": "mapped",
        },
        {
            "requirement_id": 2,
            "canonical_requirement_id": "c1",
            "status": "mapped",
        },
        {
            "requirement_id": 3,
            "canonical_requirement_id": "c2",
            "status": "mapped",
        },
    ]
    if unmapped_requirement is not None:
        mappings[unmapped_requirement - 1]["status"] = "unmapped"
        mappings[unmapped_requirement - 1]["canonical_requirement_id"] = None
    relations = (
        [
            {
                "source_requirement_id": "c1",
                "target_requirement_id": "c2",
                "relation_type": "related_to",
                "rationale": "统计相关",
            }
        ]
        if with_relation
        else []
    )
    return {
        "expected": {
            "canonical_requirements": [
                {
                    "canonical_requirement_id": "c1",
                    "canonical_name": "能力甲使用经验",
                    "rationale": "同一招聘条件",
                },
                {
                    "canonical_requirement_id": "c2",
                    "canonical_name": "能力乙",
                    "rationale": "独立条件",
                },
            ],
            "mappings": mappings,
            "relations": relations,
        }
    }


def make_actual(
    mapping_targets: dict[int, str],
    relations: list[tuple[str, str, str]] | None = None,
    unmapped_ids: set[int] | None = None,
) -> RequirementConsolidationResult:
    """按实例→标准项名称构造实际归并结果，标准项ID自动生成。"""
    unmapped_ids = unmapped_ids or set()
    mapped_targets = {
        requirement_id: name
        for requirement_id, name in mapping_targets.items()
        if requirement_id not in unmapped_ids
    }
    canonical_names = sorted({name for name in mapped_targets.values()})
    canonicals = [
        CanonicalRequirement(
            canonical_requirement_id=f"r{index}",
            canonical_name=name,
            rationale="实际归并",
            confidence=0.9,
        )
        for index, name in enumerate(canonical_names)
    ]
    name_to_id = {
        normalize_requirement_name(item.canonical_name): item.canonical_requirement_id
        for item in canonicals
    }
    mappings = []
    for requirement_id, name in mapping_targets.items():
        if requirement_id in unmapped_ids:
            mappings.append(
                RequirementMapping(
                    requirement_id=requirement_id,
                    status=RequirementMappingStatus.UNMAPPED,
                    rationale="无法确定",
                    confidence=0.4,
                )
            )
        else:
            mappings.append(
                RequirementMapping(
                    requirement_id=requirement_id,
                    status=RequirementMappingStatus.MAPPED,
                    canonical_requirement_id=name_to_id[
                        normalize_requirement_name(name)
                    ],
                    rationale="实际归并",
                    confidence=0.9,
                )
            )
    relation_models = []
    for source, target, relation_type in relations or []:
        relation_models.append(
            RequirementRelation(
                source_requirement_id=name_to_id[source],
                target_requirement_id=name_to_id[target],
                relation_type=RequirementRelationType(relation_type),
                rationale="实际关系",
                confidence=0.8,
            )
        )
    return RequirementConsolidationResult(
        canonical_requirements=canonicals,
        mappings=mappings,
        relations=relation_models,
    )


def test_perfect_match_scores_one() -> None:
    """验证完全一致的归并结果映射与关系指标均为1.0。"""
    cases = make_cases()
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"},
        relations=[("能力甲使用经验", "能力乙", "related_to")],
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.mapping_accuracy == 1.0
    assert metrics.relation_precision == 1.0
    assert metrics.relation_recall == 1.0
    assert metrics.relation_f1 == 1.0
    assert metrics.mapping_matched == 3
    assert metrics.relation_matched == 1


def test_partial_mapping_errors_reduce_accuracy() -> None:
    """验证映射到错误标准项的实例计入未命中。"""
    cases = make_cases()
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力乙", 3: "能力乙"},
        relations=[("能力甲使用经验", "能力乙", "related_to")],
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.mapping_accuracy == 2 / 3
    assert metrics.mapping_matched == 2
    assert metrics.relation_precision == 1.0
    assert metrics.relation_recall == 1.0


def test_missing_relation_reduces_relation_recall() -> None:
    """验证期望关系未被实际覆盖时关系召回和F1降为0。"""
    cases = make_cases()
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"},
        relations=[],
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.relation_precision == 0.0
    assert metrics.relation_recall == 0.0
    assert metrics.relation_f1 == 0.0
    assert metrics.relation_matched == 0
    assert metrics.relation_total == 1


def test_extra_relation_reduces_precision_and_f1() -> None:
    """验证标注子图中的额外错误关系计为假阳性。"""
    cases = make_cases()
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力丙", 3: "能力乙"},
        relations=[
            ("能力甲使用经验", "能力乙", "related_to"),
            ("能力甲使用经验", "能力丙", "related_to"),
        ],
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.relation_matched == 1
    assert metrics.relation_predicted == 2
    assert metrics.relation_total == 1
    assert metrics.relation_precision == 0.5
    assert metrics.relation_recall == 1.0
    assert metrics.relation_f1 == 2 / 3


def test_no_expected_relations_is_not_applicable() -> None:
    """验证没有期望和实际关系时关系指标为N/A而不是0%。"""
    cases = make_cases(with_relation=False)
    actual = make_actual({1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"})

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.relation_precision is None
    assert metrics.relation_recall is None
    assert metrics.relation_f1 is None
    assert metrics.relation_total == 0


def test_unmapped_handling_scores_accuracy() -> None:
    """验证期望未映射的实例在实际中也未映射时得1.0。"""
    cases = make_cases(unmapped_requirement=3)
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"},
        unmapped_ids={3},
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.unmapped_accuracy == 1.0
    assert metrics.mapping_accuracy == 1.0


def test_unmapped_miss_scores_zero() -> None:
    """验证期望未映射但实际映射的实例计入未命中。"""
    cases = make_cases(unmapped_requirement=3)
    actual = make_actual({1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"})

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.unmapped_accuracy == 0.0


def test_unmapped_and_review_required_are_not_equivalent() -> None:
    """验证期望unmapped而实际待审核时状态准确率为0。"""
    cases = make_cases(unmapped_requirement=3)
    actual = make_actual(
        {1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"},
        unmapped_ids={3},
    )
    actual.mappings[2] = RequirementMapping(
        requirement_id=3,
        status=RequirementMappingStatus.REVIEW_REQUIRED,
        candidate_requirement_ids=[
            actual.canonical_requirements[0].canonical_requirement_id
        ],
        rationale="需要人工判断",
        confidence=0.4,
    )

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.unmapped_accuracy == 0.0


def test_no_unmapped_expected_is_not_applicable() -> None:
    """验证没有期望未映射实例时未映射指标为N/A。"""
    cases = make_cases()
    actual = make_actual({1: "能力甲使用经验", 2: "能力甲使用经验", 3: "能力乙"})

    metrics = evaluate_consolidation(actual, cases)

    assert metrics.unmapped_accuracy is None
