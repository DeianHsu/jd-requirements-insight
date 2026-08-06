"""增量稳定性比较：新 5 JD 归并结果限制到旧 83 条实例，与已定稿
3 JD 正式批次比较（离线、无模型）。

比较基于成员关系（实例对），不依赖临时 canonical ID：

- 旧 canonical 的内部同簇对在新观察中是否保持（拆分检测）；
- 旧 singleton 是否进入新 canonical（合理扩员检测）；
- 旧人工裁决 must-link 对是否仍同簇（回归约束）；
- 旧人工裁决 cannot-link 对是否仍分开（回归约束）；
- 旧批次中不同 canonical 的实例是否被新输入错误合并。

输出：

- --report：公共脱敏摘要（requirement ID、数量与结论）；
- --analysis-output：私有分析详情（canonical 名称与 evidence）。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.consolidation_validation import (
    load_persisted_consolidation_result,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.requirement_consolidation import RequirementConsolidationResult


def _result_of(payload: dict) -> RequirementConsolidationResult:
    return RequirementConsolidationResult.model_validate(payload["result"])


def _cluster_pairs(members: dict[str, list[int]]) -> set[frozenset[int]]:
    pairs: set[frozenset[int]] = set()
    for ids in members.values():
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pairs.add(frozenset({ids[i], ids[j]}))
    return pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consolidation-id",
        type=int,
        required=True,
        help="旧 3 JD 正式批次ID（增量基线）",
    )
    parser.add_argument(
        "--new-raw-output",
        type=Path,
        required=True,
        help="新 5 JD 验收原始结果路径",
    )
    parser.add_argument(
        "--old-requirement-ids",
        type=Path,
        required=True,
        help="旧 83 条 requirement ID 集合（私有文件，每行一个 ID）",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        required=True,
        help="旧人工裁决文件（回归约束来源）",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="公共脱敏摘要输出路径",
    )
    parser.add_argument(
        "--analysis-output",
        type=Path,
        required=True,
        help="私有分析详情输出路径",
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
    old_ids = sorted(
        int(line.strip())
        for line in args.old_requirement_ids.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    old_id_set = set(old_ids)

    raw = json.loads(args.new_raw_output.read_text(encoding="utf-8"))
    observations = [_result_of(run) for run in raw.get("runs") or []]
    order = raw.get("order_transformation") or {}
    if order.get("result") is not None:
        observations.append(_result_of(order))
    if len(observations) < 2:
        print(f"观察结果不足（{len(observations)}）")
        return 1

    # 旧批次基线：成员关系（限制到旧 ID）。
    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        persisted = load_persisted_consolidation_result(
            session_factory, args.consolidation_id
        )
        old_result = persisted.result
    finally:
        engine.dispose()

    old_members: dict[str, list[int]] = defaultdict(list)
    for mapping in old_result.mappings:
        if mapping.requirement_id in old_id_set:
            old_members[mapping.canonical_requirement_id].append(
                mapping.requirement_id
            )
    old_pairs = _cluster_pairs(old_members)
    old_singletons = {
        ids[0]
        for ids in old_members.values()
        if len(ids) == 1
    }

    # 旧裁决回归约束（must-link / cannot-link 对）。
    decisions = json.loads(args.review_decisions.read_text(encoding="utf-8"))
    must_link_pairs: set[frozenset[int]] = set()
    cannot_link_pairs: set[frozenset[int]] = set()
    for decision in decisions.get("decisions") or []:
        ids = sorted(set(decision["requirement_ids"]) & old_id_set)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                pair = frozenset({ids[i], ids[j]})
                if decision["decision"] == "must_link":
                    must_link_pairs.add(pair)
                elif decision["decision"] == "cannot_link":
                    cannot_link_pairs.add(pair)

    # 各观察（限制到旧 ID）的成员对。
    observation_pairs: list[set[frozenset[int]]] = []
    observation_members: list[dict[str, list[int]]] = []
    for result in observations:
        members: dict[str, list[int]] = defaultdict(list)
        for mapping in result.mappings:
            if mapping.requirement_id in old_id_set:
                members[mapping.canonical_requirement_id].append(
                    mapping.requirement_id
                )
        observation_members.append(members)
        observation_pairs.append(_cluster_pairs(members))

    total = len(observations)

    def keep_rate(pair: frozenset[int]) -> float:
        return sum(1 for pairs in observation_pairs if pair in pairs) / total

    # 旧同簇对保持率（拆分检测：旧 canonical 内部对被拆开）。
    split_pairs = [
        (min(pair), max(pair))
        for pair in sorted(old_pairs, key=lambda p: (min(p), max(p)))
        if keep_rate(pair) < 1.0
    ]
    # 旧 singleton 进入新 canonical（任一观察中与旧实例同簇）。
    absorbed_singletons: list[int] = []
    for singleton in sorted(old_singletons):
        absorbed = False
        for pairs in observation_pairs:
            if any(singleton in pair and min(pair) != singleton for pair in pairs):
                absorbed = True
                break
        if absorbed:
            absorbed_singletons.append(singleton)
    # 旧不同 canonical 实例被错误合并（旧批次无此对但新观察中出现）。
    new_merges = sorted(
        (min(pair), max(pair))
        for pair in set().union(*observation_pairs) - old_pairs
        if keep_rate(pair) > 0
    )
    # 裁决回归：must-link 必须保持、cannot-link 必须保持分开。
    must_link_broken = sorted(
        (min(pair), max(pair))
        for pair in must_link_pairs
        if keep_rate(pair) < 1.0
    )
    cannot_link_broken = sorted(
        (min(pair), max(pair))
        for pair in cannot_link_pairs
        if keep_rate(pair) > 0.0
    )

    private = {
        "old_consolidation_id": args.consolidation_id,
        "old_requirement_count": len(old_ids),
        "observation_count": total,
        "split_pairs": [
            {"pair": pair, "keep_rate": keep_rate(frozenset(pair))}
            for pair in split_pairs
        ],
        "absorbed_singletons": absorbed_singletons,
        "new_merges": new_merges,
        "must_link_broken": must_link_broken,
        "cannot_link_broken": cannot_link_broken,
        "old_pairs_count": len(old_pairs),
        "old_singleton_count": len(old_singletons),
    }
    public = {
        "old_consolidation_id": args.consolidation_id,
        "old_requirement_count": len(old_ids),
        "observation_count": total,
        "old_pairs_count": len(old_pairs),
        "split_pair_count": len(split_pairs),
        "absorbed_singleton_count": len(absorbed_singletons),
        "new_merge_pair_count": len(new_merges),
        "must_link_broken_count": len(must_link_broken),
        "cannot_link_broken_count": len(cannot_link_broken),
        "conclusion": (
            "旧批次成员关系保持稳定、旧裁决全部成立"
            if not split_pairs and not must_link_broken and not cannot_link_broken
            else "存在需要人工裁决的变化（详见私有分析）"
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
    print(f"公共摘要：{args.report}")
    print(f"私有分析：{args.analysis_output}")
    print(
        f"旧对 {len(old_pairs)}、拆分 {len(split_pairs)}、"
        f"吸收 singleton {len(absorbed_singletons)}、新合并 {len(new_merges)}、"
        f"must-link 破坏 {len(must_link_broken)}、"
        f"cannot-link 破坏 {len(cannot_link_broken)}"
    )
    return 0


def _member_lookup(session_factory, old_id_set: set[int]) -> dict[int, dict]:
    from app.models import JobRequirement

    with session_factory() as session:
        rows = session.query(JobRequirement).filter(
            JobRequirement.id.in_(sorted(old_id_set))
        ).all()
        return {
            row.id: {
                "job_id": row.extraction.job_id if row.extraction else None,
                "raw_name": row.raw_name,
                "evidence": row.evidence,
            }
            for row in rows
        }


if __name__ == "__main__":
    raise SystemExit(main())
