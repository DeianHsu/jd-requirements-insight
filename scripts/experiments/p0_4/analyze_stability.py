"""离线业务影响稳定性分析（不调用模型）。

从 P0-4 私有验收运行结果生成两份输出：

- 公共报告（--report）：只保存 requirement ID、数量与脱敏结论，
  用于定稿决策与市场统计影响判断；
- 私有分析（--analysis-output）：额外保存 canonical 名称、实例
  evidence 与逐 run 判断依据，只写入 data/private/。

输出内容：

1. 每次运行的同簇实例对；
2. 所有运行均稳定出现的核心实例对（≥3/4 次同簇）；
3. 发生翻转的不稳定实例对（1~2/4 次同簇）；
4. 每个跨 JD canonical 在不同运行中的 distinct job count 范围；
5. 可能改变市场排名或报告结论的 canonical（job count 跨整数漂移）；
6. 仅影响边缘软技能表述的 canonical（job count 稳定但成员漂移）。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.consolidation import load_consolidation_selection
from app.consolidation_validation import mapping_clusters
from app.requirement_consolidation import RequirementConsolidationResult


def _result_of(run: dict) -> RequirementConsolidationResult:
    return RequirementConsolidationResult.model_validate(run["result"])


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
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.raw_output.exists():
        print(f"私有原始结果不存在：{args.raw_output}")
        return 1

    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    runs = raw.get("runs") or []
    if len(runs) < 2:
        print(f"独立运行数量不足（{len(runs)}），无法评估稳定性。")
        return 1

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job_ids = set(raw.get("selected_job_ids") or [])
            selection = load_consolidation_selection(session, job_ids=job_ids or None)
            if selection.input_fingerprint != raw.get("input_fingerprint"):
                print("输入指纹与数据库当前输入不一致，拒绝分析。")
                print(f"  raw 指纹：{raw.get('input_fingerprint')}")
                print(f"  当前指纹：{selection.input_fingerprint}")
                return 1
            occurrence_by_id = {
                occ.requirement_id: occ for occ in selection.consolidation_input.occurrences
            }
    finally:
        engine.dispose()

    requirement_ids = sorted(occurrence_by_id)
    job_id_by_requirement = {
        rid: occurrence_by_id[rid].job_id for rid in requirement_ids
    }

    # 1. 每次运行的同簇实例对（canonical 内两两）。
    run_cluster_pairs: list[set[frozenset[int]]] = []
    for run in runs:
        members = mapping_clusters(_result_of(run))
        canonical_members: dict[str, list[int]] = defaultdict(list)
        for requirement_id, (canonical_id, _) in members.items():
            canonical_members[canonical_id].append(requirement_id)
        pairs: set[frozenset[int]] = set()
        for ids in canonical_members.values():
            ids = sorted(ids)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pairs.add(frozenset({ids[i], ids[j]}))
        run_cluster_pairs.append(pairs)

    pair_count: dict[frozenset[int], int] = defaultdict(int)
    for pairs in run_cluster_pairs:
        for pair in pairs:
            pair_count[pair] += 1

    # 2. 稳定核心对（≥3/4 次同簇）。
    stable_pairs = sorted(
        (pair for pair, count in pair_count.items() if count >= 3),
        key=lambda p: (min(p), max(p)),
    )
    # 3. 翻转不稳定对（1~2/4 次同簇）。
    unstable_pairs = sorted(
        (pair for pair, count in pair_count.items() if 1 <= count <= 2),
        key=lambda p: (min(p), max(p)),
    )

    # 稳定集群（稳定对的传递闭包）。
    def clusters_of(pairs: list[frozenset[int]]) -> list[list[int]]:
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

    stable_clusters = clusters_of(stable_pairs)

    # 4. 每个稳定集群在各运行中的 distinct job count 范围。
    job_counts: dict[int, list[int]] = {}
    for cluster in stable_clusters:
        counts = []
        for run in runs:
            members = mapping_clusters(_result_of(run))
            cluster_ids = set(cluster)
            # 该运行中与集群任一成员同 canonical 的全部实例。
            same_canonical: set[int] = set()
            for requirement_id, (canonical_id, _) in members.items():
                if requirement_id in cluster_ids:
                    same_canonical.add(requirement_id)
            counts.append(
                len({job_id_by_requirement[rid] for rid in same_canonical})
            )
        job_counts[cluster[0]] = counts

    # 5/6. 分类：可能改变市场排名（job count 漂移）vs 仅边缘软技能。
    market_impact: list[dict[str, object]] = []
    edge_only: list[dict[str, object]] = []
    for cluster in stable_clusters:
        counts = job_counts[cluster[0]]
        job_min, job_max = min(counts), max(counts)
        entry = {
            "cluster_requirement_ids": cluster,
            "job_count_range": [job_min, job_max],
            "per_run_job_counts": counts,
        }
        if job_min != job_max:
            market_impact.append(entry)
        else:
            edge_only.append(entry)

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
                    "run_count": len(runs),
                }
            )

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
            "job_count_range": job_counts[cluster[0]],
        }

    private = {
        "source": {
            "raw_path": str(args.raw_output),
            "input_fingerprint": raw.get("input_fingerprint"),
            "extractor_version": raw.get("extractor_version"),
            "model": raw.get("model"),
            "prompt_version": raw.get("prompt_version"),
            "schema_version": raw.get("schema_version"),
            "selected_job_ids": raw.get("selected_job_ids"),
            "run_count": len(runs),
        },
        "requirement_count": len(requirement_ids),
        "run_cluster_pairs": [
            {"run": index, "pairs": sorted(
                (min(p), max(p)) for p in pairs
            )}
            for index, pairs in enumerate(run_cluster_pairs)
        ],
        "stable_pairs": [(min(p), max(p)) for p in stable_pairs],
        "unstable_pairs": [(min(p), max(p)) for p in unstable_pairs],
        "stable_clusters": [
            _private_entry(cluster) for cluster in stable_clusters
        ],
        "market_impact_clusters": [
            _private_entry(cluster) for cluster in stable_clusters
            if min(job_counts[cluster[0]]) != max(job_counts[cluster[0]])
        ],
        "edge_only_clusters": [
            _private_entry(cluster) for cluster in stable_clusters
            if min(job_counts[cluster[0]]) == max(job_counts[cluster[0]])
        ],
        "unstable_cross_jd_pairs": [
            {
                "pair": entry["pair_requirement_ids"],
                "jobs": entry["jobs"],
                "cooccurrence_count": entry["cooccurrence_count"],
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
            for entry in unstable_cross_jd
        ],
    }

    # 公共脱敏报告：只保留 ID、数量与结论。
    public = {
        "source": {
            "input_fingerprint": raw.get("input_fingerprint"),
            "extractor_version": raw.get("extractor_version"),
            "model": raw.get("model"),
            "prompt_version": raw.get("prompt_version"),
            "schema_version": raw.get("schema_version"),
            "selected_job_ids": raw.get("selected_job_ids"),
            "run_count": len(runs),
        },
        "requirement_count": len(requirement_ids),
        "stable_pair_count": len(stable_pairs),
        "stable_pair_ids": [(min(p), max(p)) for p in stable_pairs],
        "unstable_pair_count": len(unstable_pairs),
        "unstable_pair_ids": [(min(p), max(p)) for p in unstable_pairs],
        "cross_jd_canonical_count": len(stable_clusters),
        "market_impact_canonicals": [
            {
                "cluster_requirement_ids": cluster,
                "job_count_range": [min(job_counts[cluster[0]]), max(job_counts[cluster[0]])],
            }
            for cluster in stable_clusters
            if min(job_counts[cluster[0]]) != max(job_counts[cluster[0]])
        ],
        "unstable_cross_jd_pair_count": len(unstable_cross_jd),
        "unstable_cross_jd_pairs": [
            {
                "pair_requirement_ids": entry["pair_requirement_ids"],
                "jobs": entry["jobs"],
                "cooccurrence_count": entry["cooccurrence_count"],
                "run_count": len(runs),
            }
            for entry in unstable_cross_jd
        ],
        "edge_only_canonicals": [
            {
                "cluster_requirement_ids": cluster,
                "job_count": job_counts[cluster[0]][0],
            }
            for cluster in stable_clusters
            if min(job_counts[cluster[0]]) == max(job_counts[cluster[0]])
        ],
        "conclusion": (
            "存在 unstable 跨 JD 对或 job count 漂移的 canonical，"
            "需人工裁决后定稿"
            if unstable_cross_jd
            or any(
                min(job_counts[cluster[0]]) != max(job_counts[cluster[0]])
                for cluster in stable_clusters
            )
            else "全部跨 JD canonical 的 distinct job count 在多次运行中稳定"
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
        f"稳定对 {len(stable_pairs)}、不稳定对 {len(unstable_pairs)}、"
        f"跨 JD canonical {len(stable_clusters)}、"
        f"市场影响 {len(market_impact)}、边缘 {len(edge_only)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
