"""验证P0-4合同校验、稳定性指标、变形框架与验收报告（无人工Gold）。"""

from __future__ import annotations

import json

from app.consolidation_validation import (
    mapping_clusters,
    positive_pair_jaccard,
    singleton_and_canonical_drift,
    validate_contract,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementOccurrence,
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


def consolidation_input(
    names: list[str] | None = None,
) -> RequirementConsolidationInput:
    """构造来自两份JD的要求实例（默认两条指向同一条件）。"""
    names = names or ["能力甲使用经验", "具备能力甲的使用经验"]
    return RequirementConsolidationInput(
        occurrences=[
            RequirementOccurrence(
                requirement_id=index + 1,
                job_id=101 + index,
                extraction_id=1001 + index,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                source_hash=f"{index + 1:064x}",
                source_file=f"job-{index + 1}.md",
                requirement=requirement(name, f"具备{name}。"),
            )
            for index, name in enumerate(names)
        ]
    )


def result_payload(
    clusters: list[list[int]],
    names: list[str] | None = None,
) -> RequirementConsolidationResult:
    """按实例分组构造合法归并结果（cluster内的实例映射同一标准项）。"""
    names = names or [
        "能力甲使用经验",
        "具备能力甲的使用经验",
        "能力乙",
        "能力丙",
    ]
    canonical_requirements = []
    mappings = []
    for cluster_index, cluster in enumerate(clusters):
        canonical_requirements.append(
            CanonicalRequirement(
                canonical_requirement_id=f"cr-{cluster_index}",
                canonical_name=names[cluster[0]],
                source_requirement_ids=list(cluster),
                rationale="测试归并",
                confidence=0.95,
            )
        )
        for requirement_id in cluster:
            mappings.append(
                RequirementMapping(
                    requirement_id=requirement_id,
                    canonical_requirement_id=f"cr-{cluster_index}",
                    rationale="测试映射",
                    confidence=0.95,
                )
            )
    return RequirementConsolidationResult(
        canonical_requirements=canonical_requirements,
        mappings=mappings,
    )


def test_contract_zero_violations_on_clean_result() -> None:
    result = result_payload([[1, 2]])

    contract = validate_contract(
        result, expected_ids={1, 2}, expected_requirement_count=2
    )

    assert contract.coverage == 1.0
    assert contract.structural_violation_count == 0


def test_contract_detects_coverage_gap_and_duplicates() -> None:
    result = result_payload([[1, 2]])
    result.mappings[0].requirement_id = 99  # 未知实例

    contract = validate_contract(result, expected_ids={1, 2})

    assert contract.coverage < 1.0
    # 重复映射：把第二条也指向另一个实例ID
    result.mappings.append(
        RequirementMapping(
            requirement_id=2,
            canonical_requirement_id="cr-0",
            rationale="重复",
            confidence=0.9,
        )
    )
    contract = validate_contract(result, expected_ids={1, 2})
    assert contract.duplicate_mapping_count == 1


def test_contract_detects_unknown_reference() -> None:
    result = result_payload([[1, 2]])
    result.mappings[0].canonical_requirement_id = "missing"

    contract = validate_contract(result, expected_ids={1, 2})

    assert contract.unknown_reference_count == 1


def test_contract_detects_empty_cluster() -> None:
    # model_construct 跳过合同校验，用于验证独立合同检测器能发现空 cluster。
    result = RequirementConsolidationResult.model_construct(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="cr-0",
                canonical_name="能力甲",
                source_requirement_ids=[1],
                rationale="独立要求",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-empty",
                canonical_name="空cluster",
                source_requirement_ids=[],
                rationale="无来源",
                confidence=0.9,
            ),
        ],
        mappings=[
            RequirementMapping(
                requirement_id=1,
                canonical_requirement_id="cr-0",
                rationale="测试映射",
                confidence=0.9,
            ),
        ],
    )

    contract = validate_contract(result, expected_ids={1})

    assert contract.empty_cluster_count == 1


def test_positive_pair_jaccard_identical_mapping_is_one() -> None:
    """完全相同的两次运行：同簇正实例对 Jaccard = 1.0。"""
    first = mapping_clusters(result_payload([[1, 2], [3, 4]]))
    second = mapping_clusters(result_payload([[1, 2], [3, 4]]))

    assert positive_pair_jaccard(first, second) == 1.0


def test_positive_pair_jaccard_detects_cluster_split() -> None:
    """cluster 被拆开时同簇对 Jaccard 显著下降（比总 pairwise 更敏感）。"""
    names = ["甲", "乙", "丙", "丁"]
    first = mapping_clusters(result_payload([[1, 2, 3, 4]], names=names))
    second = mapping_clusters(result_payload([[1, 2], [3, 4]], names=names))

    assert positive_pair_jaccard(first, second) == 2 / 6  # 2 对交集 / 6 对并集


def test_positive_pair_jaccard_ignores_canonical_id_renaming() -> None:
    """canonical ID 改名不影响分组比较。"""
    first = mapping_clusters(result_payload([[1, 2], [3]]))
    second = mapping_clusters(result_payload([[1, 2], [3]]))
    second = {
        requirement_id: (f"renamed-{cluster}", confidence)
        for requirement_id, (cluster, confidence) in second.items()
    }

    assert positive_pair_jaccard(first, second) == 1.0


def test_singleton_and_canonical_drift_reports_ranges() -> None:
    """singleton 比例与 canonical 数量漂移被报告。"""
    runs = [
        mapping_clusters(result_payload([[1, 2], [3]])),
        mapping_clusters(result_payload([[1], [2], [3]])),
    ]

    drift = singleton_and_canonical_drift(runs)

    assert drift["canonical_counts"] == [2, 3]
    assert drift["canonical_count_range"] == 1
    assert drift["singleton_ratios"] == [0.5, 1.0]


def test_order_invariance_framework_pairs_are_stable() -> None:
    """顺序不变性框架：两次运行分组一致时 positive-pair Jaccard = 1.0。

    真实模型行为由验收脚本（--execute）多次运行测量；本测试验证
    指标计算与框架正确性。
    """
    from app.consolidation import (
        consolidate_with_correction,
    )

    class FixedClient:
        """按调用顺序返回固定两阶段响应的模拟客户端。"""

        def __init__(self, payload: dict) -> None:
            self.payload = payload
            self.calls = 0

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {"canonical_requirements": self.payload["canonical_requirements"]},
                    ensure_ascii=False,
                )
            return json.dumps(
                {"mappings": self.payload["mappings"]}, ensure_ascii=False
            )

    payload = {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "cr-a",
                "canonical_name": "能力甲使用经验",
                "source_requirement_ids": [1, 2],
                "rationale": "同一条件",
                "confidence": 0.95,
            },
            {
                "canonical_requirement_id": "cr-b",
                "canonical_name": "能力乙",
                "source_requirement_ids": [3],
                "rationale": "独立条件",
                "confidence": 0.95,
            },
        ],
        "mappings": [
            {
                "requirement_id": requirement_id,
                "canonical_requirement_id": (
                    "cr-a" if requirement_id in (1, 2) else "cr-b"
                ),
                "rationale": "测试映射",
                "confidence": 0.95,
            }
            for requirement_id in (1, 2, 3)
        ],
    }
    source = consolidation_input(["能力甲使用经验", "具备能力甲的使用经验", "能力乙"])

    first_result, _ = consolidate_with_correction(
        source, FixedClient(payload), max_attempts=1
    )
    # 打乱输入顺序后再次运行：模型返回相同分组。
    shuffled_source = RequirementConsolidationInput(
        occurrences=list(reversed(source.occurrences))
    )
    second_result, _ = consolidate_with_correction(
        shuffled_source, FixedClient(payload), max_attempts=1
    )

    assert (
        positive_pair_jaccard(
            mapping_clusters(first_result), mapping_clusters(second_result)
        )
        == 1.0
    )


def test_metamorphic_conservative_fallback_keeps_singletons() -> None:
    """保守回退：语义无法确认等价的实例保持独立 singleton。"""
    from app.consolidation import consolidate_with_correction

    class FallbackClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {
                        "canonical_requirements": [
                            {
                                "canonical_requirement_id": "cr-0",
                                "canonical_name": "能力甲",
                                "source_requirement_ids": [1],
                                "rationale": "独立要求",
                                "confidence": 0.8,
                            },
                            {
                                "canonical_requirement_id": "cr-1",
                                "canonical_name": "能力乙",
                                "source_requirement_ids": [2],
                                "rationale": "无法确认等价，保持独立",
                                "confidence": 0.8,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mappings": [
                        {
                            "requirement_id": 1,
                            "canonical_requirement_id": "cr-0",
                            "rationale": "测试映射",
                            "confidence": 0.8,
                        },
                        {
                            "requirement_id": 2,
                            "canonical_requirement_id": "cr-1",
                            "rationale": "测试映射",
                            "confidence": 0.8,
                        },
                    ]
                },
                ensure_ascii=False,
            )

    source = consolidation_input(["能力甲", "能力乙"])

    result, _ = consolidate_with_correction(source, FallbackClient(), max_attempts=1)

    assert len(result.mappings) == 2
    assert len(result.canonical_requirements) == 2
    assert {m.canonical_requirement_id for m in result.mappings} == {"cr-0", "cr-1"}


def test_unreferenced_canonical_is_dropped_deterministically() -> None:
    """标准项轮提出的无来源标准项被确定性剔除，映射合同仍成立。"""
    from app.consolidation import consolidate_with_correction

    class NoisyClient:
        """标准项轮多提出一个无人引用的噪声标准项。"""

        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.calls += 1
            if self.calls == 1:
                return json.dumps(
                    {
                        "canonical_requirements": [
                            {
                                "canonical_requirement_id": "cr-0",
                                "canonical_name": "能力甲",
                                "source_requirement_ids": [1, 2],
                                "rationale": "独立要求",
                                "confidence": 0.8,
                            },
                            {
                                "canonical_requirement_id": "cr-noise",
                                "canonical_name": "无人引用的噪声条件",
                                "source_requirement_ids": [],
                                "rationale": "模型幻觉",
                                "confidence": 0.5,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mappings": [
                        {
                            "requirement_id": requirement_id,
                            "canonical_requirement_id": "cr-0",
                            "rationale": "测试映射",
                            "confidence": 0.8,
                        }
                        for requirement_id in (1, 2)
                    ]
                },
                ensure_ascii=False,
            )

    source = consolidation_input(["能力甲", "能力乙"])

    result, _ = consolidate_with_correction(source, NoisyClient(), max_attempts=1)

    assert len(result.canonical_requirements) == 1
    assert result.canonical_requirements[0].canonical_requirement_id == "cr-0"
    assert len(result.mappings) == 2


def test_mapping_reference_to_unknown_canonical_is_rejected_and_retried() -> None:
    """映射轮引用清单外标准项ID时被块级校验拒绝，并在重试修正后成功。"""
    from app.consolidation import consolidate_with_correction

    class HallucinatingClient:
        """映射轮首次引用不存在的CR-noise，收到修正提示后改为合法ID。"""

        def __init__(self) -> None:
            self.calls = 0
            self.prompts: list[str] = []

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.calls += 1
            self.prompts.append(user_prompt)
            if self.calls == 1:
                return json.dumps(
                    {
                        "canonical_requirements": [
                            {
                                "canonical_requirement_id": "cr-0",
                                "canonical_name": "能力甲",
                                "source_requirement_ids": [1, 2],
                                "rationale": "独立要求",
                                "confidence": 0.8,
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            if self.calls == 2:
                return json.dumps(
                    {
                        "mappings": [
                            {
                                "requirement_id": requirement_id,
                                "canonical_requirement_id": "CR-noise",
                                "rationale": "幻觉引用",
                                "confidence": 0.8,
                            }
                            for requirement_id in (1, 2)
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "mappings": [
                        {
                            "requirement_id": requirement_id,
                            "canonical_requirement_id": "cr-0",
                            "rationale": "修正后引用",
                            "confidence": 0.8,
                        }
                        for requirement_id in (1, 2)
                    ]
                },
                ensure_ascii=False,
            )

    source = consolidation_input(["能力甲", "能力乙"])

    client = HallucinatingClient()
    result, _ = consolidate_with_correction(source, client, max_attempts=3)

    assert all(
        mapping.canonical_requirement_id == "cr-0" for mapping in result.mappings
    )
    assert len(result.mappings) == 2
    # 映射轮第二次请求（修正轮）应携带上次校验错误，包含幻觉ID。
    assert "CR-noise" in client.prompts[2]
