"""验证P0-4合同校验、变形指标、下游统计投影与验收报告（无人工Gold）。"""

from __future__ import annotations

import json

from app.consolidation_validation import (
    co_clustering_agreement,
    direction_consistency,
    edge_jaccard,
    fact_projection,
    mapping_clusters,
    projections_equal,
    relation_edges_by_name,
    relation_graph_stats,
    validate_contract,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementMappingStatus,
    RequirementOccurrence,
    RequirementRelation,
    RequirementRelationType,
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
                extractor_version="test-model|prompt:1.0|schema:2.0",
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
    relations: list[dict] | None = None,
    uncertain_relations: list[dict] | None = None,
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
                rationale="测试归并",
                confidence=0.95,
            )
        )
        for requirement_id in cluster:
            mappings.append(
                RequirementMapping(
                    requirement_id=requirement_id,
                    status=RequirementMappingStatus.MAPPED,
                    canonical_requirement_id=f"cr-{cluster_index}",
                    rationale="测试映射",
                    confidence=0.95,
                )
            )
    return RequirementConsolidationResult(
        canonical_requirements=canonical_requirements,
        mappings=mappings,
        relations=[
            RequirementRelation(
                source_requirement_id=item["source"],
                target_requirement_id=item["target"],
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale=item.get("rationale", "测试包含"),
                confidence=item.get("confidence", 0.8),
            )
            for item in (relations or [])
        ],
        uncertain_relations=[
            RequirementRelation(
                source_requirement_id=item["source"],
                target_requirement_id=item["target"],
                relation_type=RequirementRelationType.UNCERTAIN,
                rationale=item.get("rationale", "无法判断方向"),
                confidence=item.get("confidence", 0.5),
            )
            for item in (uncertain_relations or [])
        ],
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
    assert contract.unknown_reference_count == 0
    # 重复映射：把第二条也指向另一个实例ID
    result.mappings.append(
        RequirementMapping(
            requirement_id=2,
            status=RequirementMappingStatus.MAPPED,
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


def test_contract_detects_graph_violations() -> None:
    # model_construct 跳过合同校验，用于验证独立合同检测器能发现违规。
    result = RequirementConsolidationResult.model_construct(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="cr-0",
                canonical_name="能力甲",
                rationale="独立要求",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-1",
                canonical_name="能力乙",
                rationale="独立要求",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-empty",
                canonical_name="空cluster",
                rationale="无来源",
                confidence=0.9,
            ),
        ],
        mappings=[
            RequirementMapping(
                requirement_id=1,
                status=RequirementMappingStatus.MAPPED,
                canonical_requirement_id="cr-0",
                rationale="测试映射",
                confidence=0.9,
            ),
            RequirementMapping(
                requirement_id=2,
                status=RequirementMappingStatus.MAPPED,
                canonical_requirement_id="cr-1",
                rationale="测试映射",
                confidence=0.9,
            ),
        ],
        relations=[
            # model_construct 跳过自环/重复等合同校验，供独立合同检测器测试。
            RequirementRelation.model_construct(
                source_requirement_id="cr-0",
                target_requirement_id="cr-0",
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale="自环",
                confidence=0.8,
            ),
            RequirementRelation(
                source_requirement_id="cr-0",
                target_requirement_id="cr-1",
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale="测试包含",
                confidence=0.8,
            ),
            RequirementRelation(
                source_requirement_id="cr-0",
                target_requirement_id="cr-1",
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale="同向重复",
                confidence=0.8,
            ),
            RequirementRelation(
                source_requirement_id="cr-1",
                target_requirement_id="cr-0",
                relation_type=RequirementRelationType.BROADER_THAN,
                rationale="反向冲突",
                confidence=0.8,
            ),
        ],
    )

    contract = validate_contract(result, expected_ids={1, 2})

    assert contract.self_loop_count == 1
    assert contract.duplicate_edge_count == 1
    assert contract.direction_conflict_count == 1
    assert contract.empty_cluster_count == 1


def test_co_clustering_agreement_counts_instance_pairs() -> None:
    first = mapping_clusters(result_payload([[1, 2], [3]]))
    second = mapping_clusters(result_payload([[1], [2, 3]]))

    high_conf, overall = co_clustering_agreement(first, second)

    # 实例对 (1,2): 同簇→不同簇 不一致；(1,3): 不同→不同 一致；
    # (2,3): 不同→同簇 不一致。overall = 1/3。
    assert overall == 1 / 3
    assert high_conf == 1 / 3


def test_co_clustering_agreement_ignores_canonical_id_renaming() -> None:
    first = mapping_clusters(result_payload([[1, 2], [3]]))
    second = mapping_clusters(result_payload([[1, 2], [3]]))
    # 把第二次运行的 canonical ID 改名：分组不变，agreement 仍为 1.0。
    second = {
        requirement_id: (f"renamed-{cluster}", confidence)
        for requirement_id, (cluster, confidence) in second.items()
    }

    high_conf, overall = co_clustering_agreement(first, second)

    assert high_conf == 1.0
    assert overall == 1.0


def test_edge_jaccard_and_direction_consistency() -> None:
    first = relation_edges_by_name(
        result_payload(
            [[1], [2], [3]],
            relations=[
                {"source": "cr-0", "target": "cr-1"},
                {"source": "cr-0", "target": "cr-2"},
            ],
        )
    )
    second = relation_edges_by_name(
        result_payload(
            [[1], [2], [3]],
            relations=[
                {"source": "cr-0", "target": "cr-1"},
                {"source": "cr-2", "target": "cr-0"},  # 反向边
            ],
        )
    )

    assert edge_jaccard(first, second) == 1 / 3
    assert direction_consistency(first, second) == 0.5


def test_relation_graph_stats_reports_sparsity() -> None:
    result = result_payload(
        [[1], [2], [3]],
        relations=[
            {"source": "cr-0", "target": "cr-1", "confidence": 0.9},
            {"source": "cr-0", "target": "cr-2", "confidence": 0.5},
        ],
        uncertain_relations=[{"source": "cr-0", "target": "cr-1"}],
    )

    stats = relation_graph_stats(result)

    assert stats.edge_count == 2
    assert stats.node_count == 3
    assert stats.edge_node_ratio == 2 / 3
    assert stats.max_out_degree == 2
    assert stats.max_in_degree == 1
    assert stats.root_node_count == 1
    assert stats.low_confidence_edge_count == 1
    assert stats.uncertain_count == 1


def test_fact_projection_ignores_hierarchy_relations() -> None:
    source = consolidation_input(["能力甲使用经验", "具备能力甲的使用经验", "能力乙"])
    base = result_payload([[1, 2], [3]])
    with_hierarchy = result_payload(
        [[1, 2], [3]],
        relations=[{"source": "cr-0", "target": "cr-1"}],
    )

    base_projection = fact_projection(base, source)
    hierarchy_projection = fact_projection(with_hierarchy, source)

    assert projections_equal(base_projection, hierarchy_projection)
    assert base_projection.instance_counts == {"cr-0": 2, "cr-1": 1}
    assert base_projection.distinct_job_counts == {"cr-0": 2, "cr-1": 1}
    assert base_projection.source_job_sets == {
        "cr-0": {101, 102},
        "cr-1": {103},
    }
    assert base_projection.evidence_counts == {"cr-0": 2, "cr-1": 1}


def test_fact_projection_counts_importance_groups() -> None:
    source = consolidation_input(["能力甲使用经验", "能力乙", "能力丙"])
    result = result_payload([[1], [2], [3]])

    projection = fact_projection(result, source)

    assert projection.importance_counts == {
        "cr-0": {"must": 1},
        "cr-1": {"must": 1},
        "cr-2": {"must": 1},
    }


def test_metamorphic_order_invariance_framework() -> None:
    """顺序不变性框架：两次运行分组一致时 agreement 必须为 1.0。

    真实模型行为由验收脚本（--execute）多次运行测量；本测试验证
    co-clustering 指标计算与框架正确性。
    """
    from app.consolidation import (
        consolidate_with_correction,
    )

    class FixedClient:
        """按调用顺序返回固定三阶段响应的模拟客户端。"""

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
            if self.calls == 2:
                return json.dumps(
                    {"mappings": self.payload["mappings"]}, ensure_ascii=False
                )
            return json.dumps(
                {"relations": self.payload["relations"]}, ensure_ascii=False
            )

    payload = {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "cr-a",
                "canonical_name": "能力甲使用经验",
                "rationale": "同一条件",
                "confidence": 0.95,
            },
            {
                "canonical_requirement_id": "cr-b",
                "canonical_name": "能力乙",
                "rationale": "独立条件",
                "confidence": 0.95,
            },
        ],
        "mappings": [
            {
                "requirement_id": requirement_id,
                "status": "mapped",
                "canonical_requirement_id": (
                    "cr-a" if requirement_id in (1, 2) else "cr-b"
                ),
                "candidate_requirement_ids": [],
                "rationale": "测试映射",
                "confidence": 0.95,
            }
            for requirement_id in (1, 2, 3)
        ],
        "relations": [],
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

    high_conf, overall = co_clustering_agreement(
        mapping_clusters(first_result),
        mapping_clusters(second_result),
    )
    assert high_conf == 1.0
    assert overall == 1.0


def test_metamorphic_conservative_fallback_keeps_singletons() -> None:
    """保守回退：语义无法确认等价的实例保持独立 singleton，不进入失败状态。"""
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
                                "rationale": "独立要求",
                                "confidence": 0.8,
                            },
                            {
                                "canonical_requirement_id": "cr-1",
                                "canonical_name": "能力乙",
                                "rationale": "无法确认等价，保持独立",
                                "confidence": 0.8,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            if self.calls == 2:
                return json.dumps(
                    {
                        "mappings": [
                            {
                                "requirement_id": 1,
                                "status": "mapped",
                                "canonical_requirement_id": "cr-0",
                                "candidate_requirement_ids": [],
                                "rationale": "测试映射",
                                "confidence": 0.8,
                            },
                            {
                                "requirement_id": 2,
                                "status": "mapped",
                                "canonical_requirement_id": "cr-1",
                                "candidate_requirement_ids": [],
                                "rationale": "测试映射",
                                "confidence": 0.8,
                            },
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"relations": []}, ensure_ascii=False)

    source = consolidation_input(["能力甲", "能力乙"])

    result, _ = consolidate_with_correction(source, FallbackClient(), max_attempts=1)

    assert len(result.mappings) == 2
    assert len(result.canonical_requirements) == 2
    assert {m.canonical_requirement_id for m in result.mappings} == {"cr-0", "cr-1"}
    assert result.hierarchy_status == "success"


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
                                "rationale": "独立要求",
                                "confidence": 0.8,
                            },
                            {
                                "canonical_requirement_id": "cr-noise",
                                "canonical_name": "无人引用的噪声条件",
                                "rationale": "模型幻觉",
                                "confidence": 0.5,
                            },
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
                                "status": "mapped",
                                "canonical_requirement_id": "cr-0",
                                "candidate_requirement_ids": [],
                                "rationale": "测试映射",
                                "confidence": 0.8,
                            }
                            for requirement_id in (1, 2)
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"relations": []}, ensure_ascii=False)

    source = consolidation_input(["能力甲", "能力乙"])

    result, raw = consolidate_with_correction(source, NoisyClient(), max_attempts=1)

    assert len(result.canonical_requirements) == 1
    assert result.canonical_requirements[0].canonical_requirement_id == "cr-0"
    assert len(result.mappings) == 2
    assert result.hierarchy_status == "success"


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
                                "status": "mapped",
                                "canonical_requirement_id": "CR-noise",
                                "candidate_requirement_ids": [],
                                "rationale": "幻觉引用",
                                "confidence": 0.8,
                            }
                            for requirement_id in (1, 2)
                        ]
                    },
                    ensure_ascii=False,
                )
            if self.calls == 3:
                return json.dumps(
                    {
                        "mappings": [
                            {
                                "requirement_id": requirement_id,
                                "status": "mapped",
                                "canonical_requirement_id": "cr-0",
                                "candidate_requirement_ids": [],
                                "rationale": "修正后引用",
                                "confidence": 0.8,
                            }
                            for requirement_id in (1, 2)
                        ]
                    },
                    ensure_ascii=False,
                )
            return json.dumps({"relations": []}, ensure_ascii=False)

    source = consolidation_input(["能力甲", "能力乙"])

    client = HallucinatingClient()
    result, _ = consolidate_with_correction(source, client, max_attempts=3)

    assert all(
        mapping.canonical_requirement_id == "cr-0" for mapping in result.mappings
    )
    assert len(result.mappings) == 2
    # 映射轮第二次请求（修正轮）应携带上次校验错误，包含幻觉ID。
    assert "CR-noise" in client.prompts[2]
