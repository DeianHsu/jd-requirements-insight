"""离线业务影响稳定性分析（不调用模型）。

从 P0-4 私有验收运行结果生成两份输出：

- 公共报告（--report）：只保存 requirement ID、数量与脱敏结论，
  用于定稿决策与市场统计影响判断；
- 私有分析（--analysis-output）：额外保存 canonical 名称、实例
  evidence 与逐观察判断依据，只写入 data/private/。

观察结果 = 全部独立运行 + 成功的顺序变形运行（失败时在报告中明确
记录）。所有稳定性判定基于实际观察总数，不依赖固定运行次数。

输出内容：

1. 每次观察的同簇实例对（按完整 canonical 分区）；
2. 所有观察均稳定出现的核心实例对（count == 观察总数）；
3. 发生翻转的不稳定实例对（0 < count < 观察总数）；
4. 每个跨 JD canonical 在各观察中的完整成员集合及 distinct job
   count 范围（成员包括与核心成员同 canonical 的全部实例）；
5. 可能改变市场统计或排名的 canonical（job count 跨整数漂移）；
6. 仅影响边缘软技能表述的 canonical（job count 稳定但成员漂移）。
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.consolidation import load_consolidation_selection
from app.consolidation_validation import mapping_clusters
from app.requirement_consolidation import RequirementConsolidationResult


def _result_of(payload: dict) -> RequirementConsolidationResult:
    return RequirementConsolidationResult.model_validate(payload["result"])


def collect_observations(raw: dict) -> tuple[list[dict], str]:
    """收集全部观察结果：独立运行 + 成功的顺序变形运行。

    返回 (observations, order_status)；order_status 为
    "included" / "failed: <原因>" / "absent"。
    """
    observations = [
        {"kind": "independent", "result": _result_of(run)}
        for run in raw.get("runs") or []
    ]
    order = raw.get("order_transformation") or {}
    if order.get("result") is not None:
        observations.append(
            {"kind": "order", "result": _result_of(order)}
        )
        order_status = "included"
    elif order.get("failed"):
        order_status = f"failed: {order['failed']}"
    else:
        order_status = "absent"
    return observations, order_status


def _clusters_of(
    pairs: list[frozenset[int]], requirement_ids: list[int]
) -> list[list[int]]:
    """稳定对的传递闭包（只返回成员数 > 1 的集群）。"""
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for pair in pairs:
        a, b = tuple(sorted(pair))
        union(a, b)
    groups: dict[int, list[int]] = defaultdict(list)
    for rid in requirement_ids:
        if rid in parent:
            groups[find(rid)].append(rid)
    return [sorted(ids) for ids in groups.values() if len(ids) > 1]


def analyze_observations(
    observations: list[dict],
    requirement_ids: list[int],
    job_id_by_requirement: dict[int, int],
) -> dict:
    """基于完整 canonical 分区做业务稳定性分析（纯函数，可测试）。

    观察结果中 canonical ID 只用于分区定位，跨观察比较一律基于成员
    集合，不依赖临时 canonical ID 相等。
    """
    total = len(observations)
    obs_members: list[dict[str, list[int]]] = []
    run_cluster_pairs: list[set[frozenset[int]]] = []
    for obs in observations:
        members = mapping_clusters(obs["result"])
        by_canonical: dict[str, list[int]] = defaultdict(list)
        for requirement_id, (canonical_id, _) in members.items():
            by_canonical[canonical_id].append(requirement_id)
        obs_members.append(by_canonical)
        pairs: set[frozenset[int]] = set()
        for ids in by_canonical.values():
            ids = sorted(ids)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.add(frozenset({ids[i], ids[j]}))
        run_cluster_pairs.append(pairs)

    pair_count = Counter(
        pair for pairs in run_cluster_pairs for pair in pairs
    )
    stable_pairs = sorted(
        (pair for pair, count in pair_count.items() if count == total),
        key=lambda p: (min(p), max(p)),
    )
    unstable_pairs = sorted(
        (pair for pair, count in pair_count.items() if 0 < count < total),
        key=lambda p: (min(p), max(p)),
    )
    stable_clusters = _clusters_of(stable_pairs, requirement_ids)

    # 每个稳定集群在各观察中的完整成员与 distinct job count。
    cluster_observations: list[dict[str, object]] = []
    for cluster in stable_clusters:
        cluster_ids = set(cluster)
        per_observation: list[dict[str, object]] = []
        for by_canonical in obs_members:
            full_members: set[int] = set()
            for ids in by_canonical.values():
                if any(requirement_id in cluster_ids for requirement_id in ids):
                    full_members.update(ids)
            member_ids = sorted(full_members)
            per_observation.append(
                {
                    "members": member_ids,
                    "job_count": len(
                        {job_id_by_requirement[rid] for rid in member_ids}
                    ),
                }
            )
        job_counts = [obs["job_count"] for obs in per_observation]
        cluster_observations.append(
            {
                "cluster_requirement_ids": cluster,
                "per_observation": per_observation,
                "job_count_range": [min(job_counts), max(job_counts)],
            }
        )

    market_impact = [
        entry
        for entry in cluster_observations
        if entry["job_count_range"][0] != entry["job_count_range"][1]
    ]
    edge_only = [
        entry
        for entry in cluster_observations
        if entry["job_count_range"][0] == entry["job_count_range"][1]
    ]

    # 不稳定对的市场影响：两成员跨 JD 时，合并与否直接改变各自
    # canonical 的独立 JD 计数（人工裁决对象）。
    unstable_cross_jd: list[dict[str, object]] = []
    for pair in unstable_pairs:
        a, b = tuple(sorted(pair))
        job_a = job_id_by_requirement[a]
        job_b = job_id_by_requirement[b]
        if job_a != job_b:
            unstable_cross_jd.append(
                {
                    "pair_requirement_ids": [a, b],
                    "jobs": sorted({job_a, job_b}),
                    "cooccurrence_count": pair_count[pair],
                    "observation_count": total,
                }
            )

    return {
        "observation_count": total,
        "run_cluster_pairs": [
            {"observation": index, "pairs": sorted(
                (min(p), max(p)) for p in pairs
            )}
            for index, pairs in enumerate(run_cluster_pairs)
        ],
        "stable_pairs": [(min(p), max(p)) for p in stable_pairs],
        "unstable_pairs": [(min(p), max(p)) for p in unstable_pairs],
        "stable_clusters": cluster_observations,
        "market_impact_clusters": market_impact,
        "edge_only_clusters": edge_only,
        "unstable_cross_jd_pairs": unstable_cross_jd,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-output",
        type=Path,
        required=True,
        help="私有验收原始结果路径（run_acceptance 输出）",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="公共脱敏稳定性报告输出路径",
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        required=True,
        help="私有分析详情输出路径（含名称与 evidence）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.raw_output.exists():
        print(f"私有原始结果不存在：{args.raw_output}")
        return 1

    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    observations, order_status = collect_observations(raw)
    if len(observations) < 2:
        print(f"观察结果数量不足（{len(observations)}），无法评估稳定性。")
        return 1

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job_ids = set(raw.get("selected_job_ids") or [])
            selection = load_consolidation_selection(
                session, job_ids=job_ids or None
            )
            if selection.input_fingerprint != raw.get("input_fingerprint"):
                print("输入指纹与数据库当前输入不一致，拒绝分析。")
                print(f"  raw 指纹：{raw.get('input_fingerprint')}")
                print(f"  当前指纹：{selection.input_fingerprint}")
                return 1
            occurrence_by_id = {
                occ.requirement_id: occ
                for occ in selection.consolidation_input.occurrences
            }
    finally:
        engine.dispose()

    requirement_ids = sorted(occurrence_by_id)
    job_id_by_requirement = {
        rid: occurrence_by_id[rid].job_id for rid in requirement_ids
    }
    analysis = analyze_observations(
        observations, requirement_ids, job_id_by_requirement
    )

    source_identity = {
        "input_fingerprint": raw.get("input_fingerprint"),
        "extractor_version": raw.get("extractor_version"),
        "model": raw.get("model"),
        "prompt_version": raw.get("prompt_version"),
        "schema_version": raw.get("schema_version"),
        "selected_job_ids": raw.get("selected_job_ids"),
        "independent_run_count": len(raw.get("runs") or []),
        "order_transformation_status": order_status,
        "observation_count": analysis["observation_count"],
    }

    # 私有详情：canonical 名称与 evidence。
    def _private_entry(cluster: list[int]) -> dict[str, object]:
        return {
            "cluster_requirement_ids": cluster,
            "members": [
                {
                    "requirement_id": rid,
                    "job_id": job_id_by_requirement[rid],
                    "raw_name": occurrence_by_id[rid].requirement.raw_name,
                    "evidence": occurrence_by_id[rid].requirement.evidence,
                }
                for rid in cluster
            ],
        }

    private = {
        "source": {**source_identity, "raw_path": str(args.raw_output)},
        "requirement_count": len(requirement_ids),
        "run_cluster_pairs": analysis["run_cluster_pairs"],
        "stable_pairs": analysis["stable_pairs"],
        "unstable_pairs": analysis["unstable_pairs"],
        "stable_clusters": [
            {
                "cluster_requirement_ids": entry["cluster_requirement_ids"],
                "members": [
                    {
                        "requirement_id": rid,
                        "job_id": job_id_by_requirement[rid],
                        "raw_name": occurrence_by_id[rid].requirement.raw_name,
                        "evidence": occurrence_by_id[rid].requirement.evidence,
                    }
                    for rid in entry["cluster_requirement_ids"]
                ],
                "per_observation": entry["per_observation"],
                "job_count_range": entry["job_count_range"],
            }
            for entry in analysis["stable_clusters"]
        ],
        "market_impact_clusters": [
            {
                "cluster_requirement_ids": entry["cluster_requirement_ids"],
                "members": [
                    {
                        "requirement_id": rid,
                        "job_id": job_id_by_requirement[rid],
                        "raw_name": occurrence_by_id[rid].requirement.raw_name,
                        "evidence": occurrence_by_id[rid].requirement.evidence,
                    }
                    for rid in entry["cluster_requirement_ids"]
                ],
                "per_observation": entry["per_observation"],
                "job_count_range": entry["job_count_range"],
            }
            for entry in analysis["market_impact_clusters"]
        ],
        "edge_only_clusters": [
            {
                "cluster_requirement_ids": entry["cluster_requirement_ids"],
                "per_observation": entry["per_observation"],
            }
            for entry in analysis["edge_only_clusters"]
        ],
        "unstable_cross_jd_pairs": [
            {
                "pair": entry["pair_requirement_ids"],
                "jobs": entry["jobs"],
                "cooccurrence_count": entry["cooccurrence_count"],
                "observation_count": entry["observation_count"],
                "members": [
                    {
                        "requirement_id": rid,
                        "job_id": job_id_by_requirement[rid],
                        "raw_name": occurrence_by_id[rid].requirement.raw_name,
                        "evidence": occurrence_by_id[rid].requirement.evidence,
                    }
                    for rid in entry["pair_requirement_ids"]
                ],
            }
            for entry in analysis["unstable_cross_jd_pairs"]
        ],
    }

    # 公共脱敏报告：只保留 ID、数量与结论。
    public = {
        "source": source_identity,
        "requirement_count": len(requirement_ids),
        "stable_pair_count": len(analysis["stable_pairs"]),
        "stable_pair_ids": analysis["stable_pairs"],
        "unstable_pair_count": len(analysis["unstable_pairs"]),
        "unstable_pair_ids": analysis["unstable_pairs"],
        "cross_jd_canonical_count": len(analysis["stable_clusters"]),
        "market_impact_canonicals": [
            {
                "cluster_requirement_ids": entry["cluster_requirement_ids"],
                "job_count_range": entry["job_count_range"],
                "per_observation": [
                    {
                        "members": obs["members"],
                        "job_count": obs["job_count"],
                    }
                    for obs in entry["per_observation"]
                ],
            }
            for entry in analysis["market_impact_clusters"]
        ],
        "unstable_cross_jd_pair_count": len(
            analysis["unstable_cross_jd_pairs"]
        ),
        "unstable_cross_jd_pairs": [
            {
                "pair_requirement_ids": entry["pair_requirement_ids"],
                "jobs": entry["jobs"],
                "cooccurrence_count": entry["cooccurrence_count"],
                "observation_count": entry["observation_count"],
            }
            for entry in analysis["unstable_cross_jd_pairs"]
        ],
        "edge_only_canonicals": [
            {
                "cluster_requirement_ids": entry["cluster_requirement_ids"],
                "job_count": entry["job_count_range"][0],
            }
            for entry in analysis["edge_only_clusters"]
        ],
        "conclusion": (
            "存在 unstable 跨 JD 对或 job count 漂移的 canonical，"
            "需人工裁决后定稿"
            if analysis["unstable_cross_jd_pairs"]
            or analysis["market_impact_clusters"]
            else "全部跨 JD canonical 的 distinct job count 在所有观察中稳定"
        ),
    }

    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.analysis_output.parent.mkdir(parents=True, exist_ok=True)
    args.analysis_output.write_text(
        json.dumps(private, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"公共报告：{args.report}")
    print(f"私有分析：{args.analysis_output}")
    print(
        f"观察总数 {analysis['observation_count']}"
        f"（独立 {len(raw.get('runs') or [])} + 顺序变形 "
        f"{order_status}）"
    )
    print(
        f"稳定对 {len(analysis['stable_pairs'])}、"
        f"不稳定对 {len(analysis['unstable_pairs'])}、"
        f"跨 JD canonical {len(analysis['stable_clusters'])}、"
        f"市场影响 {len(analysis['market_impact_clusters'])}、"
        f"边缘 {len(analysis['edge_only_clusters'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
