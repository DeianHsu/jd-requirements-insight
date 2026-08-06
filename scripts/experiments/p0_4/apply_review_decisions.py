"""确定性应用人工审核决定，生成最终归并结果（不调用模型）。

流程：

1. 读取一份合同通过、核心语义质量合格的验收运行（--run-index）；
2. 校验输入与版本身份（raw 与审核决定文件、数据库当前输入一致）；
3. 应用 must-link / cannot-link 决定，更新受影响的 canonical 分区与名称；
4. 重新确定性生成 mappings（build_mappings_from_canonical_partition）；
5. 重新执行完整唯一分区与精确 ID 覆盖校验；
6. 生成最终规范化结果，记录来源运行标识/指纹与审核决定文件指纹。

输出：

- --output：最终结果 JSON（私有，含 result 与来源/审核指纹）；
- --report：公共脱敏摘要（只含 ID、数量与指纹）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.consolidation import load_consolidation_selection
from app.consolidation_validation import (
    is_placeholder_canonical_name,
    result_fingerprint,
    validate_contract,
    validate_exact_identity,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
    validate_canonical_partition,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--raw-output",
        type=Path,
        required=True,
        help="私有验收原始结果路径（run_acceptance 输出）",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        required=True,
        help="人工审核决定文件（review-decisions.json）",
    )
    parser.add_argument(
        "--run-index",
        type=int,
        default=0,
        help="选择作为来源的独立运行索引（默认 0）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="最终结果 JSON 输出路径（私有）",
    )
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="公共脱敏摘要输出路径",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def _file_fingerprint(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _apply_decisions(
    result: RequirementConsolidationResult,
    decisions: list[dict],
    raw_name_by_id: dict[int, str],
) -> RequirementConsolidationResult:
    """把 must-link / cannot-link 决定应用到 canonical 分区。

    名称策略（不调用模型）：

    - must-link 合并后：决策显式提供 canonical_name 时使用之，
      否则保留主 canonical 名称；
    - cannot-link 拆分出的 singleton：使用对应 requirement 的
      raw_name（缺失时拒绝应用，不生成内部占位名）；
    - unresolved：证据不足暂不确认全部等价。必须显式提供目标结构：
      `groups=[[...], ...]`（组内合并、组间分开）或
      `preserve_source=true`（保留来源运行当前分区，不做多余变化）；
      两者皆缺时拒绝应用，不默认拆成全部 singleton。
    """
    canonicals: dict[str, CanonicalRequirement] = {
        item.canonical_requirement_id: item
        for item in result.canonical_requirements
    }
    member_to_canonical: dict[int, str] = {}
    for item in result.canonical_requirements:
        for requirement_id in item.source_requirement_ids:
            member_to_canonical[requirement_id] = item.canonical_requirement_id

    merged_ids: set[str] = set()

    for decision in decisions:
        ids = list(dict.fromkeys(decision["requirement_ids"]))
        if decision["decision"] == "must_link":
            if len(ids) < 2:
                continue
            # 主 canonical：覆盖成员最多者，平局取 canonical ID 最小。
            owners = {
                member_to_canonical[requirement_id] for requirement_id in ids
            }
            if len(owners) == 1:
                continue  # 已在同一 canonical
            owner_items = [
                (len(canonicals[owner].source_requirement_ids), owner)
                for owner in owners
            ]
            primary = min(owner_items, key=lambda pair: (-pair[0], pair[1]))[1]
            for owner in sorted(owners - {primary}):
                primary_item = canonicals[primary]
                moved = [
                    requirement_id
                    for requirement_id in canonicals[owner].source_requirement_ids
                    if requirement_id not in primary_item.source_requirement_ids
                ]
                primary_item.source_requirement_ids.extend(moved)
                primary_item.rationale += (
                    f"（审核修正：并入 {owner}，见 review-decisions）"
                )
                merged_ids.add(owner)
                for requirement_id in moved:
                    member_to_canonical[requirement_id] = primary
            # 合并后名称：决策显式指定优先，否则保留主名称。
            explicit_name = decision.get("canonical_name")
            if explicit_name:
                primary_item.canonical_name = explicit_name
                primary_item.rationale += (
                    f"（审核修正：名称定为“{explicit_name}”）"
                )
        elif decision["decision"] == "unresolved":
            groups = decision.get("groups")
            preserve_source = decision.get("preserve_source", False)
            if not groups and not preserve_source:
                raise ValueError(
                    "unresolved 决定必须提供 groups 或 preserve_source，"
                    "不得默认拆成全部 singleton"
                )
            if preserve_source:
                continue  # 保留来源运行当前分区，不做多余变化
            if not isinstance(groups, list) or not groups:
                raise ValueError("unresolved groups 结构不完整")
            # 组内合并：与 must-link 相同的确定性合并逻辑。
            for group in groups:
                group_ids = list(dict.fromkeys(group))
                if len(group_ids) < 2:
                    continue
                owners = {
                    member_to_canonical[requirement_id]
                    for requirement_id in group_ids
                }
                if len(owners) <= 1:
                    continue
                owner_items = [
                    (len(canonicals[owner].source_requirement_ids), owner)
                    for owner in owners
                ]
                primary = min(
                    owner_items, key=lambda pair: (-pair[0], pair[1])
                )[1]
                for owner in sorted(owners - {primary}):
                    primary_item = canonicals[primary]
                    moved = [
                        requirement_id
                        for requirement_id in canonicals[owner].source_requirement_ids
                        if requirement_id not in primary_item.source_requirement_ids
                    ]
                    primary_item.source_requirement_ids.extend(moved)
                    primary_item.rationale += (
                        f"（审核修正：unresolved 组内并入 {owner}）"
                    )
                    merged_ids.add(owner)
                    for requirement_id in moved:
                        member_to_canonical[requirement_id] = primary
            # 组间分开：不同组的成员不得处于同一 canonical（确定性拆出）。
            for owner, item in sorted(canonicals.items()):
                if owner in merged_ids:
                    continue
                owners_of_members = {
                    group_index
                    for requirement_id in item.source_requirement_ids
                    for group_index, group in enumerate(groups)
                    if requirement_id in group
                }
                if len(owners_of_members) <= 1:
                    continue
                # 保留成员最多的组（平局取组索引小者），其余组员拆出。
                kept_group = max(
                    owners_of_members,
                    key=lambda g: (
                        sum(1 for rid in item.source_requirement_ids if rid in groups[g]),
                        -g,
                    ),
                )
                for requirement_id in list(item.source_requirement_ids):
                    group_of = next(
                        (
                            g
                            for g, group in enumerate(groups)
                            if requirement_id in group
                        ),
                        None,
                    )
                    if group_of is None or group_of == kept_group:
                        continue
                    raw_name = raw_name_by_id.get(requirement_id)
                    if not raw_name or not raw_name.strip():
                        raise ValueError(
                            f"unresolved 拆分实例 {requirement_id} 缺少可用的"
                            "原始名称，拒绝应用审核决定"
                        )
                    new_id = f"cr-{requirement_id}-split"
                    item.source_requirement_ids.remove(requirement_id)
                    member_to_canonical[requirement_id] = new_id
                    canonicals[new_id] = CanonicalRequirement(
                        canonical_requirement_id=new_id,
                        canonical_name=raw_name.strip(),
                        source_requirement_ids=[requirement_id],
                        rationale=(
                            "审核修正：unresolved 组间拆分（见 review-decisions）"
                        ),
                        confidence=item.confidence,
                    )
                    item.rationale += (
                        f"（审核修正：unresolved 拆出实例{requirement_id}）"
                    )
        elif decision["decision"] == "cannot_link":
            if len(ids) < 2:
                continue
            for requirement_id in ids:
                owner = member_to_canonical[requirement_id]
                item = canonicals[owner]
                if len(item.source_requirement_ids) == 1:
                    continue
                raw_name = raw_name_by_id.get(requirement_id)
                if not raw_name or not raw_name.strip():
                    raise ValueError(
                        f"cannot-link 拆分实例 {requirement_id} 缺少可用的"
                        "原始名称，拒绝应用审核决定"
                    )
                # 拆出该实例为独立 singleton canonical，名称使用
                # 对应 requirement 的原始名称（可直接进入报告）。
                new_id = f"cr-{requirement_id}-split"
                item.source_requirement_ids.remove(requirement_id)
                member_to_canonical[requirement_id] = new_id
                canonicals[new_id] = CanonicalRequirement(
                    canonical_requirement_id=new_id,
                    canonical_name=raw_name.strip(),
                    source_requirement_ids=[requirement_id],
                    rationale=(
                        f"审核修正：{decision['decision']} 拆分"
                        "（见 review-decisions）"
                    ),
                    confidence=item.confidence,
                )
                item.rationale += (
                    f"（审核修正：拆出实例{requirement_id}，见 review-decisions）"
                )
        else:
            raise ValueError(f"未知审核决定类型：{decision['decision']}")

    remaining = [
        item
        for item in canonicals.values()
        if item.canonical_requirement_id not in merged_ids
    ]
    mappings = build_mappings_from_canonical_partition(remaining)
    return RequirementConsolidationResult(
        canonical_requirements=remaining,
        mappings=mappings,
    )


def _raw_identity(raw: dict) -> dict[str, object]:
    """raw 顶层身份字段；旧格式缺省时回退到首个运行的 metadata。"""
    runs = raw.get("runs") or []
    meta = runs[0].get("metadata") or {} if runs else {}
    return {
        "input_fingerprint": raw.get("input_fingerprint"),
        "extractor_version": raw.get("extractor_version"),
        "selected_job_ids": sorted(raw.get("selected_job_ids") or []),
        "model": raw.get("model") or meta.get("model"),
        "prompt_version": raw.get("prompt_version")
        or meta.get("prompt_version"),
        "schema_version": raw.get("schema_version")
        or meta.get("schema_version"),
    }


def main() -> int:
    args = parse_args()
    for path in (args.raw_output, args.review_decisions):
        if not path.exists():
            print(f"文件不存在：{path}")
            return 1

    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    decisions_payload = json.loads(
        args.review_decisions.read_text(encoding="utf-8")
    )
    decisions_fingerprint = _file_fingerprint(
        args.review_decisions.read_bytes()
    )

    # 审核决定文件与 raw 的身份一致。
    raw_identity = _raw_identity(raw)
    identity_checks = [
        ("input_fingerprint", decisions_payload.get("input_fingerprint"),
         raw_identity["input_fingerprint"]),
        ("extractor_version", decisions_payload.get("extractor_version"),
         raw_identity["extractor_version"]),
        ("selected_job_ids", decisions_payload.get("selected_job_ids"),
         raw_identity["selected_job_ids"]),
        ("prompt_version", decisions_payload.get("prompt_version"),
         raw_identity["prompt_version"]),
        ("schema_version", decisions_payload.get("schema_version"),
         raw_identity["schema_version"]),
    ]
    for field, decision_value, raw_value in identity_checks:
        if decision_value != raw_value:
            print(
                f"审核决定与 raw 身份不一致（{field}），拒绝应用：\n"
                f"  决定={decision_value}\n  raw={raw_value}"
            )
            return 1

    runs = raw.get("runs") or []
    if not 0 <= args.run_index < len(runs):
        print(f"run-index {args.run_index} 超出独立运行范围 0..{len(runs) - 1}")
        return 1
    selected_run = runs[args.run_index]
    source_identifier = selected_run.get("run_identifier", f"run-{args.run_index}")
    source_fingerprint = selected_run.get(
        "result_fingerprint"
    ) or result_fingerprint(
        RequirementConsolidationResult.model_validate(selected_run["result"])
    )

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job_ids = set(raw.get("selected_job_ids") or [])
            selection = load_consolidation_selection(
                session, job_ids=job_ids or None
            )
            if selection.input_fingerprint != raw_identity["input_fingerprint"]:
                print("输入指纹与数据库当前输入不一致，拒绝应用。")
                return 1
            consolidation_input = selection.consolidation_input
            expected_ids = {
                occurrence.requirement_id
                for occurrence in consolidation_input.occurrences
            }
            raw_name_by_id = {
                occurrence.requirement_id: occurrence.requirement.raw_name
                for occurrence in consolidation_input.occurrences
            }
    finally:
        engine.dispose()

    result = RequirementConsolidationResult.model_validate(
        selected_run["result"]
    )
    # 来源运行必须本身合同通过（结构、精确覆盖）。
    source_contract = validate_contract(result, expected_ids=expected_ids)
    if source_contract.coverage != 1.0 or (
        source_contract.structural_violation_count != 0
    ):
        print("来源运行合同未通过，拒绝作为审核应用基础。")
        return 1
    source_identity_failures = validate_exact_identity(result, expected_ids)
    if source_identity_failures:
        print("来源运行精确 ID 校验未通过，拒绝作为审核应用基础。")
        for failure in source_identity_failures:
            print(f"  - {failure}")
        return 1

    try:
        final_result = _apply_decisions(
            result, decisions_payload["decisions"], raw_name_by_id
        )
    except ValueError as exc:
        print(f"审核决定应用失败，拒绝输出：{exc}")
        return 1
    placeholder_names = [
        item.canonical_name
        for item in final_result.canonical_requirements
        if is_placeholder_canonical_name(item.canonical_name)
    ]
    if placeholder_names:
        print("审核应用后仍存在占位名称，拒绝输出：")
        for name in placeholder_names:
            print(f"  - {name}")
        return 1
    try:
        validate_canonical_partition(consolidation_input, final_result.canonical_requirements)
        validate_exact_identity(final_result, expected_ids)
        contract = validate_contract(final_result, expected_ids=expected_ids)
    except ValueError as exc:
        print(f"审核应用后校验失败，拒绝输出：{exc}")
        return 1
    identity_failures = validate_exact_identity(final_result, expected_ids)
    if identity_failures:
        print("审核应用后精确 ID 校验未通过，拒绝输出：")
        for failure in identity_failures:
            print(f"  - {failure}")
        return 1
    if contract.coverage != 1.0 or contract.structural_violation_count != 0:
        print("审核应用后合同未通过，拒绝输出。")
        return 1

    final_fingerprint = result_fingerprint(final_result)
    payload = {
        "input_fingerprint": raw_identity["input_fingerprint"],
        "extractor_version": raw_identity["extractor_version"],
        "selected_job_ids": raw_identity["selected_job_ids"],
        "model": raw_identity["model"],
        "prompt_version": raw_identity["prompt_version"],
        "schema_version": raw_identity["schema_version"],
        "source_run_identifier": source_identifier,
        "source_result_fingerprint": source_fingerprint,
        "review_decisions_fingerprint": decisions_fingerprint,
        "reviewed_by": decisions_payload.get("reviewed_by"),
        "reviewed_at": decisions_payload.get("reviewed_at"),
        "result_fingerprint": final_fingerprint,
        "result": final_result.model_dump(mode="json"),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    public = {
        "input_fingerprint": raw_identity["input_fingerprint"],
        "extractor_version": raw_identity["extractor_version"],
        "selected_job_ids": raw_identity["selected_job_ids"],
        "model": raw_identity["model"],
        "prompt_version": raw_identity["prompt_version"],
        "schema_version": raw_identity["schema_version"],
        "source_run_identifier": source_identifier,
        "source_result_fingerprint": source_fingerprint,
        "review_decisions_fingerprint": decisions_fingerprint,
        "result_fingerprint": final_fingerprint,
        "canonical_count": len(final_result.canonical_requirements),
        "mapping_count": len(final_result.mappings),
        "coverage": contract.coverage,
        "structural_violation_count": contract.structural_violation_count,
        "applied_decisions": [
            {
                "decision": d["decision"],
                "requirement_ids": d["requirement_ids"],
            }
            for d in decisions_payload["decisions"]
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(public, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"最终结果已写入：{args.output}")
    print(f"公共摘要已写入：{args.report}")
    print(f"来源运行：{source_identifier}（指纹 {source_fingerprint[:16]}…）")
    print(
        f"canonical={len(final_result.canonical_requirements)} "
        f"mappings={len(final_result.mappings)} "
        f"coverage={contract.coverage} 结构违规={contract.structural_violation_count}"
    )
    print(f"最终结果指纹：{final_fingerprint[:16]}…")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
