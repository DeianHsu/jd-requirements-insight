"""增量稳定性比较：新 5 JD 归并结果限制到旧 83 条实例，与已定稿
3 JD 正式批次比较（离线、无模型）。

真值来源：

- 旧范围 = 正式批次（consolidation id）的 expected requirement IDs，
  外部 ID 文件仅作校验输入（不等即拒绝）；
- 新 raw = 当前数据库选定 JD 的正式输入（selected_job_ids /
  input_fingerprint / 抽取器版本一致），每个观察均执行完整合同与
  精确 ID 校验，任一观察不合格即拒绝，不静默跳过。

比较基于成员关系（实例对），不依赖临时 canonical ID；singleton
吸收统计不依赖实例 ID 大小；私有详情包含完整成员、名称、来源 JD
与 evidence。
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from app.consolidation import load_consolidation_selection
from app.consolidation_validation import (
    load_persisted_consolidation_result,
    validate_contract,
    validate_exact_identity,
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
        help="旧 3 JD 正式批次ID（增量基线，唯一真值来源）",
    )
    parser.add_argument(
        "--new-job-ids",
        type=int,
        nargs="+",
        required=True,
        help="新输入选定 JD ID（如 1 2 3 4 5）",
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
        help="可选外部旧 ID 文件（每行一个 ID）；提供时必须与正式批次"
        " expected IDs 完全相等，否则拒绝",
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


def _member_lookup(session_factory, ids: set[int]) -> dict[int, dict]:
    from app.models import JobRequirement

    with session_factory() as session:
        rows = session.query(JobRequirement).filter(
            JobRequirement.id.in_(sorted(ids))
        ).all()
        return {
            row.id: {
                "job_id": row.extraction.job_id if row.extraction else None,
                "raw_name": row.raw_name,
                "evidence": row.evidence,
            }
            for row in rows
        }


def main() -> int:
    args = parse_args()
    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)

        # 旧范围唯一真值：正式批次 expected IDs。
        persisted = load_persisted_consolidation_result(
            session_factory, args.consolidation_id
        )
        old_expected = set(persisted.expected_requirement_ids)
        if args.old_requirement_ids is not None:
            external_ids = {
                int(line.strip())
                for line in args.old_requirement_ids.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            }
            if external_ids != old_expected:
                missing = sorted(old_expected - external_ids)
                extra = sorted(external_ids - old_expected)
                print(
                    f"外部旧 ID 集合与正式批次不一致，拒绝分析："
                    f"缺失 {len(missing)} 条、多余 {len(extra)} 条"
                )
                return 1
        old_id_set = old_expected

        # 新输入真值：数据库当前选定 JD 的正式输入。
        with session_factory() as session:
            selection = load_consolidation_selection(
                session, job_ids=set(args.new_job_ids)
            )
            new_expected = {
                occurrence.requirement_id
                for occurrence in selection.consolidation_input.occurrences
            }
            new_fingerprint = selection.input_fingerprint
            new_extractor = selection.extractor_version
    finally:
        engine.dispose()

    raw = json.loads(args.new_raw_output.read_text(encoding="utf-8"))
    # 新 raw 身份门禁：选定 JD 集合、输入指纹、抽取器版本一致。
    raw_job_ids = sorted(raw.get("selected_job_ids") or [])
    if raw_job_ids != sorted(args.new_job_ids):
        print(
            f"新 raw selected_job_ids（{raw_job_ids}）与目标范围"
            f"（{sorted(args.new_job_ids)}）不一致，拒绝分析。"
        )
        return 1
    if raw.get("input_fingerprint") != new_fingerprint:
        print("新 raw 输入指纹与数据库当前输入不一致，拒绝分析。")
        return 1
    if raw.get("extractor_version") != new_extractor:
        print(
            f"新 raw 抽取器版本（{raw.get('extractor_version')}）"
            f"与当前输入（{new_extractor}）不一致，拒绝分析。"
        )
        return 1

    observations: list[dict] = []
    from pydantic import ValidationError

    try:
        for run in raw.get("runs") or []:
            observations.append(
                {"kind": "independent", "result": _result_of(run)}
            )
        order = raw.get("order_transformation") or {}
        if order.get("result") is not None:
            observations.append({"kind": "order", "result": _result_of(order)})
        elif order.get("failed"):
            print(f"顺序变形运行失败（{order['failed']}），拒绝分析。")
            return 1
    except ValidationError as exc:
        print(f"观察结果不合法（结构合同），拒绝分析：{exc}")
        return 1
    if len(observations) < 2:
        print(f"观察结果不足（{len(observations)}）")
        return 1

    # 每个观察必须完整通过合同与精确 ID 校验（不静默跳过）。
    for index, observation in enumerate(observations):
        contract = validate_contract(
            observation["result"], expected_ids=new_expected
        )
        identity_failures = validate_exact_identity(
            observation["result"], new_expected
        )
        if contract.coverage != 1.0 or (
            contract.structural_violation_count != 0
        ):
            print(
                f"观察 {index}（{observation['kind']}）合同未通过，拒绝分析："
                f"coverage={contract.coverage} "
                f"结构违规={contract.structural_violation_count}"
            )
            return 1
        if identity_failures:
            print(f"观察 {index}（{observation['kind']}）精确 ID 校验失败，拒绝分析：")
            for failure in identity_failures:
                print(f"  - {failure}")
            return 1

    # 旧批次基线：成员关系（限制到旧 ID）。
    old_members: dict[str, list[int]] = defaultdict(list)
    for mapping in persisted.result.mappings:
        if mapping.requirement_id in old_id_set:
            old_members[mapping.canonical_requirement_id].append(
                mapping.requirement_id
            )
    old_pairs = _cluster_pairs(old_members)
    old_singletons = {
        ids[0] for ids in old_members.values() if len(ids) == 1
    }

    # 旧裁决回归约束。
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

    # 各观察（限制到旧 ID）的完整 canonical 成员。
    observation_members: list[dict[str, list[int]]] = []
    observation_pairs: list[set[frozenset[int]]] = []
    for observation in observations:
        members: dict[str, list[int]] = defaultdict(list)
        for mapping in observation["result"].mappings:
            if mapping.requirement_id in old_id_set:
                members[mapping.canonical_requirement_id].append(
                    mapping.requirement_id
                )
        observation_members.append(members)
        observation_pairs.append(_cluster_pairs(members))

    total = len(observations)

    def keep_rate(pair: frozenset[int]) -> float:
        return sum(1 for pairs in observation_pairs if pair in pairs) / total

    # 1. 旧同簇对被拆开（keep_rate < 1）。
    split_pairs = [
        (min(pair), max(pair))
        for pair in sorted(old_pairs, key=lambda p: (min(p), max(p)))
        if keep_rate(pair) < 1.0
    ]
    # 2. 旧不同 canonical 被合并（新观察出现旧批次不存在的对）。
    new_merges = sorted(
        (min(pair), max(pair))
        for pair in set().union(*observation_pairs) - old_pairs
        if keep_rate(pair) > 0
    )
    # 3. 旧 singleton 吸收：与任意其他旧实例同簇即计（与 ID 大小无关），
    #    并记录其完整成员集合（含新增 JD 实例的扩员）。
    absorbed_singletons: list[dict[str, object]] = []
    for singleton in sorted(old_singletons):
        per_observation_members: list[list[int]] = []
        for members in observation_members:
            full: set[int] = set()
            for ids in members.values():
                if singleton in ids and len(ids) > 1:
                    full.update(ids)
            per_observation_members.append(sorted(full))
        if any(per_observation_members):
            absorbed_singletons.append(
                {
                    "singleton": singleton,
                    "per_observation_full_members": per_observation_members,
                    "joined_old_ids": sorted(
                        {
                            rid
                            for members in per_observation_members
                            for rid in members
                            if rid != singleton and rid in old_id_set
                        }
                    ),
                    "joined_new_ids": sorted(
                        {
                            rid
                            for members in per_observation_members
                            for rid in members
                            if rid != singleton and rid not in old_id_set
                        }
                    ),
                }
            )

    # 4. 新增实例扩员 vs 旧实例错误合并：旧 canonical（成员集合）在
    #    观察中的完整集合含新增实例（扩员）——旧对保持但 canonical
    #    加入新实例。
    old_canonical_sets = {
        frozenset(ids) for ids in old_members.values() if len(ids) > 1
    }
    expansion: list[dict[str, object]] = []
    # 扩员分析必须使用全量观察（含新增 JD 实例）。
    full_observation_members: list[dict[str, list[int]]] = []
    for observation in observations:
        members: dict[str, list[int]] = defaultdict(list)
        for mapping in observation["result"].mappings:
            members[mapping.canonical_requirement_id].append(
                mapping.requirement_id
            )
        full_observation_members.append(members)
    for index, members in enumerate(full_observation_members):
        for canonical_ids in members.values():
            old_part = set(canonical_ids) & old_id_set
            if len(old_part) > 1 and frozenset(old_part) in old_canonical_sets:
                new_part = sorted(set(canonical_ids) - old_id_set)
                if new_part:
                    expansion.append(
                        {
                            "observation": index,
                            "kind": observations[index]["kind"],
                            "old_members": sorted(old_part),
                            "new_members_added": new_part,
                        }
                    )
    # 5/6. 裁决回归。
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

    # 私有详情：完整成员、名称、来源 JD、evidence、保持率、job count。
    lookup = _member_lookup(session_factory, old_id_set)
    old_job_counts = {
        frozenset(ids): len({lookup[rid]["job_id"] for rid in ids})
        for ids in old_members.values()
    }

    def _member_entries(ids: list[int]) -> list[dict[str, object]]:
        return [
            {
                "requirement_id": rid,
                "job_id": lookup[rid]["job_id"],
                "raw_name": lookup[rid]["raw_name"],
                "evidence": lookup[rid]["evidence"],
            }
            for rid in ids
        ]

    def _pair_entry(pair: tuple[int, int]) -> dict[str, object]:
        a, b = pair
        return {
            "pair": [a, b],
            "keep_rate": keep_rate(frozenset(pair)),
            "jobs": sorted({lookup[a]["job_id"], lookup[b]["job_id"]}),
            "members": _member_entries([a, b]),
            "hit_must_link": frozenset(pair) in must_link_pairs,
            "hit_cannot_link": frozenset(pair) in cannot_link_pairs,
        }

    private = {
        "old_consolidation_id": args.consolidation_id,
        "old_requirement_ids": sorted(old_id_set),
        "old_expected_ids_source": "formal_batch",
        "new_fingerprint": new_fingerprint,
        "new_selected_job_ids": sorted(args.new_job_ids),
        "observation_count": total,
        "split_pairs": [_pair_entry(pair) for pair in split_pairs],
        "new_merges": [_pair_entry(pair) for pair in new_merges],
        "absorbed_singletons": [
            {
                "singleton": entry["singleton"],
                "per_observation_full_members": [
                    {
                        "members": members,
                        "member_details": _member_entries(members),
                    }
                    for members in entry["per_observation_full_members"]
                ],
                "joined_old_ids": entry["joined_old_ids"],
                "joined_new_ids": entry["joined_new_ids"],
            }
            for entry in absorbed_singletons
        ],
        "expansions": expansion,
        "must_link_broken": [_pair_entry(pair) for pair in must_link_broken],
        "cannot_link_broken": [
            _pair_entry(pair) for pair in cannot_link_broken
        ],
        "old_pairs_count": len(old_pairs),
        "old_singleton_count": len(old_singletons),
        "old_canonical_members": [
            {
                "members": sorted(ids),
                "member_details": _member_entries(sorted(ids)),
                "distinct_job_count": old_job_counts[frozenset(ids)],
            }
            for ids in old_members.values()
        ],
    }
    public = {
        "old_consolidation_id": args.consolidation_id,
        "old_requirement_count": len(old_id_set),
        "new_fingerprint": new_fingerprint,
        "new_selected_job_ids": sorted(args.new_job_ids),
        "observation_count": total,
        "old_pairs_count": len(old_pairs),
        "split_pair_count": len(split_pairs),
        "absorbed_singleton_count": len(absorbed_singletons),
        "new_merge_pair_count": len(new_merges),
        "expansion_count": len(expansion),
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
        f"扩员 {len(expansion)}、must-link 破坏 {len(must_link_broken)}、"
        f"cannot-link 破坏 {len(cannot_link_broken)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
