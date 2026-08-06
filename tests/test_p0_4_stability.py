"""analyze_stability 业务稳定性分析核心测试（纯函数，不调用模型）。

覆盖验收场景：

1. 稳定核心成员 A、B 某次运行与 C 同簇、另一次只有 A、B 同簇时，
   识别完整成员与 JD 覆盖差异；
2. 顺序变形结果参与总观察次数和稳定性判定；
3. 运行数量变化时"全部稳定"按实际观察总数判断；
4. 临时 canonical ID 改名但成员分区相同，不产生错误漂移；
5. 跨 JD 成员加入或离开 canonical 时，distinct job count 范围正确。
"""
from __future__ import annotations

from scripts.experiments.p0_4.analyze_stability import (
    analyze_observations,
    collect_observations,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)


def _result(*clusters: list[int]) -> RequirementConsolidationResult:
    """按分区构造规范化结果；canonical ID 为 cr-0..cr-n。"""
    canonicals = [
        CanonicalRequirement(
            canonical_requirement_id=f"cr-{index}",
            canonical_name=f"条件{index}",
            source_requirement_ids=list(ids),
            rationale="测试",
            confidence=0.9,
        )
        for index, ids in enumerate(clusters)
    ]
    return RequirementConsolidationResult(
        canonical_requirements=canonicals,
        mappings=build_mappings_from_canonical_partition(canonicals),
    )


def _observation(result: RequirementConsolidationResult) -> dict:
    return {"kind": "independent", "result": result}


def _ids(entry: dict) -> list[int]:
    return entry["cluster_requirement_ids"]


def test_full_membership_and_job_coverage_detected() -> None:
    """核心成员 A、B 稳定，某次运行 A、B、C 同簇：识别完整成员差异。"""
    requirement_ids = [1, 2, 3, 4, 5]
    job_by_id = {1: 1, 2: 1, 3: 2, 4: 2, 5: 3}
    observations = [
        _observation(_result([1, 2, 3], [4], [5])),  # 1、2、3 同簇
        _observation(_result([1, 2], [3], [4], [5])),  # 只有 1、2 同簇
    ]

    analysis = analyze_observations(observations, requirement_ids, job_by_id)

    assert analysis["stable_pairs"] == [(1, 2)]  # 全部观察同簇
    assert len(analysis["stable_clusters"]) == 1
    cluster = analysis["stable_clusters"][0]
    assert _ids(cluster) == [1, 2]
    per_observation = cluster["per_observation"]
    # 第一次观察的完整成员包含非核心成员 3（同一 canonical）。
    assert per_observation[0]["members"] == [1, 2, 3]
    assert per_observation[0]["job_count"] == 2  # JD 1 + JD 2（实例3）
    assert per_observation[1]["members"] == [1, 2]
    assert per_observation[1]["job_count"] == 1
    # 完整成员视角下存在 JD 覆盖漂移 → 市场影响。
    assert cluster["job_count_range"] == [1, 2]
    assert len(analysis["market_impact_clusters"]) == 1


def test_order_observation_participates_in_stability() -> None:
    """顺序变形结果参与总观察次数与稳定性判定。"""
    requirement_ids = [1, 2, 3, 4]
    job_by_id = {1: 1, 2: 2, 3: 1, 4: 2}
    observations = [
        _observation(_result([1, 2], [3, 4])),
        _observation(_result([1, 2], [3, 4])),
        {"kind": "order", "result": _result([1, 2], [3, 4])},
    ]

    analysis = analyze_observations(observations, requirement_ids, job_by_id)

    assert analysis["observation_count"] == 3
    assert analysis["stable_pairs"] == [(1, 2), (3, 4)]  # 3/3 全部稳定
    assert analysis["unstable_pairs"] == []


def test_order_observation_flips_stability() -> None:
    """顺序变形中某对未同簇时，该对按实际观察总数判为翻转。"""
    requirement_ids = [1, 2, 3]
    job_by_id = {1: 1, 2: 2, 3: 1}
    observations = [
        _observation(_result([1, 2], [3])),
        _observation(_result([1, 2], [3])),
        {"kind": "order", "result": _result([1], [2], [3])},  # 1、2 分开
    ]

    analysis = analyze_observations(observations, requirement_ids, job_by_id)

    assert analysis["observation_count"] == 3
    assert analysis["stable_pairs"] == []
    assert (1, 2) in analysis["unstable_pairs"]
    # 跨 JD 翻转对进入不稳定跨 JD 清单。
    assert any(
        entry["pair_requirement_ids"] == [1, 2]
        and entry["cooccurrence_count"] == 2
        and entry["observation_count"] == 3
        for entry in analysis["unstable_cross_jd_pairs"]
    )


def test_stability_uses_actual_observation_total() -> None:
    """运行数量变化时，"全部稳定"按实际观察总数判断。"""
    requirement_ids = [1, 2, 3]
    job_by_id = {1: 1, 2: 2, 3: 1}
    # 2 次观察全部同簇。
    two = analyze_observations(
        [_observation(_result([1, 2, 3])), _observation(_result([1, 2, 3]))],
        requirement_ids,
        job_by_id,
    )
    assert two["stable_pairs"] == [(1, 2), (1, 3), (2, 3)]
    # 3 次观察全部同簇。
    three = analyze_observations(
        [
            _observation(_result([1, 2, 3])),
            _observation(_result([1, 2, 3])),
            _observation(_result([1, 2, 3])),
        ],
        requirement_ids,
        job_by_id,
    )
    assert three["stable_pairs"] == [(1, 2), (1, 3), (2, 3)]
    # 3 次观察中 2 次同簇 → 翻转，不是稳定。
    mixed = analyze_observations(
        [
            _observation(_result([1, 2, 3])),
            _observation(_result([1, 2, 3])),
            _observation(_result([1], [2], [3])),
        ],
        requirement_ids,
        job_by_id,
    )
    assert mixed["stable_pairs"] == []
    assert len(mixed["unstable_pairs"]) == 3


def test_canonical_id_rename_does_not_create_drift() -> None:
    """临时 canonical ID 改名但成员分区相同：不产生错误漂移。"""
    requirement_ids = [1, 2, 3, 4]
    job_by_id = {1: 1, 2: 1, 3: 2, 4: 2}
    # 两次观察分区相同但 canonical ID 不同（cr-0 vs cr-9）。
    canonicals_a = [
        CanonicalRequirement(
            canonical_requirement_id="cr-0",
            canonical_name="条件甲",
            source_requirement_ids=[1, 2],
            rationale="测试",
            confidence=0.9,
        ),
        CanonicalRequirement(
            canonical_requirement_id="cr-1",
            canonical_name="条件乙",
            source_requirement_ids=[3, 4],
            rationale="测试",
            confidence=0.9,
        ),
    ]
    canonicals_b = [
        CanonicalRequirement(
            canonical_requirement_id="cr-9",
            canonical_name="条件甲",
            source_requirement_ids=[1, 2],
            rationale="测试",
            confidence=0.9,
        ),
        CanonicalRequirement(
            canonical_requirement_id="cr-8",
            canonical_name="条件乙",
            source_requirement_ids=[3, 4],
            rationale="测试",
            confidence=0.9,
        ),
    ]
    observations = [
        {
            "kind": "independent",
            "result": RequirementConsolidationResult(
                canonical_requirements=canonicals_a,
                mappings=build_mappings_from_canonical_partition(canonicals_a),
            ),
        },
        {
            "kind": "order",
            "result": RequirementConsolidationResult(
                canonical_requirements=canonicals_b,
                mappings=build_mappings_from_canonical_partition(canonicals_b),
            ),
        },
    ]

    analysis = analyze_observations(observations, requirement_ids, job_by_id)

    assert (1, 2) in analysis["stable_pairs"]
    assert (3, 4) in analysis["stable_pairs"]
    assert analysis["unstable_pairs"] == []
    for cluster in analysis["stable_clusters"]:
        assert cluster["job_count_range"][0] == cluster["job_count_range"][1]
    assert analysis["market_impact_clusters"] == []


def test_cross_jd_member_join_leave_changes_job_range() -> None:
    """跨 JD 成员加入/离开 canonical 时，distinct job count 范围变化。"""
    requirement_ids = [1, 2, 3, 4]
    job_by_id = {1: 1, 2: 1, 3: 2, 4: 2}
    observations = [
        _observation(_result([1, 2, 3], [4])),  # 成员 3（JD2）加入
        _observation(_result([1, 2], [3], [4])),  # 3 离开
        _observation(_result([1, 2], [3], [4])),
    ]

    analysis = analyze_observations(observations, requirement_ids, job_by_id)

    cluster = analysis["stable_clusters"][0]
    assert _ids(cluster) == [1, 2]
    # 3 次观察中只有一次完整成员含 3 → job count 1..2 漂移。
    assert cluster["per_observation"][0]["job_count"] == 2
    assert cluster["per_observation"][1]["job_count"] == 1
    assert cluster["job_count_range"] == [1, 2]
    assert len(analysis["market_impact_clusters"]) == 1


def test_collect_observations_includes_successful_order() -> None:
    """成功的顺序变形运行被收集为观察结果；失败时明确记录。"""
    raw = {
        "runs": [
            {"result": _result([1, 2]).model_dump(mode="json")},
            {"result": _result([1, 2]).model_dump(mode="json")},
        ],
        "order_transformation": {
            "seed": 1,
            "result": _result([1, 2]).model_dump(mode="json"),
        },
    }
    observations, order_status = collect_observations(raw)
    assert len(observations) == 3
    assert observations[-1]["kind"] == "order"
    assert order_status == "included"

    raw["order_transformation"] = {"seed": 1, "failed": "顺序变形失败：超时"}
    observations, order_status = collect_observations(raw)
    assert len(observations) == 2
    assert order_status.startswith("failed:")
