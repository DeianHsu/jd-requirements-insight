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
        "--frozen-base",
        type=Path,
        help=(
            "已批准的较小范围 final consolidation；提供后，旧 partition "
            "直接继承且只允许本轮增量裁决"
        ),
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
    canonical_name_overrides: list[dict] | None = None,
    protected_requirement_ids: set[int] | None = None,
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
    - canonical_name_overrides：全部分区决定应用后，以最终 canonical 的
      完整 requirement_ids 集合精确定位并改名；只改名称，不改变分区。
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
    protected_ids = set(protected_requirement_ids or set())

    def select_primary(owners: set[str]) -> str:
        """冻结模式优先保留旧 canonical owner；普通模式保持旧策略。"""
        protected_owners = {
            member_to_canonical[requirement_id]
            for requirement_id in protected_ids
            if member_to_canonical.get(requirement_id) in owners
        }
        if len(protected_owners) > 1:
            raise ValueError(
                "审核决定试图合并多个 frozen canonical，拒绝应用："
                f"{sorted(protected_owners)}"
            )
        if protected_owners:
            return next(iter(protected_owners))
        owner_items = [
            (len(canonicals[owner].source_requirement_ids), owner)
            for owner in owners
        ]
        return min(owner_items, key=lambda pair: (-pair[0], pair[1]))[1]

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
            primary = select_primary(owners)
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
                primary = select_primary(owners)
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
            ids_to_split = ids
            if protected_ids:
                members_by_owner: dict[str, list[int]] = {}
                for requirement_id in ids:
                    owner = member_to_canonical[requirement_id]
                    members_by_owner.setdefault(owner, []).append(requirement_id)
                ids_to_split = [
                    requirement_id
                    for owner_ids in members_by_owner.values()
                    if len(owner_ids) > 1
                    for requirement_id in owner_ids
                    if requirement_id not in protected_ids
                ]
            for requirement_id in ids_to_split:
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
    member_set_to_canonical = {
        frozenset(item.source_requirement_ids): item for item in remaining
    }
    overridden_member_sets: set[frozenset[int]] = set()
    overrides = (
        [] if canonical_name_overrides is None else canonical_name_overrides
    )
    if not isinstance(overrides, list):
        raise ValueError("canonical_name_overrides 必须是列表")
    for index, override in enumerate(overrides):
        if not isinstance(override, dict):
            raise ValueError(
                f"canonical_name_overrides[{index}] 必须是对象"
            )
        requirement_ids = override.get("requirement_ids")
        canonical_name = override.get("canonical_name")
        if (
            not isinstance(requirement_ids, list)
            or not requirement_ids
            or any(
                not isinstance(requirement_id, int)
                for requirement_id in requirement_ids
            )
            or len(requirement_ids) != len(set(requirement_ids))
        ):
            raise ValueError(
                f"canonical_name_overrides[{index}].requirement_ids "
                "必须是非空且不重复的整数列表"
            )
        if not isinstance(canonical_name, str) or not canonical_name.strip():
            raise ValueError(
                f"canonical_name_overrides[{index}].canonical_name "
                "必须是非空字符串"
            )
        member_set = frozenset(requirement_ids)
        if member_set in overridden_member_sets:
            raise ValueError(
                "canonical_name_overrides 重复定位最终 canonical："
                f"{sorted(member_set)}"
            )
        target = member_set_to_canonical.get(member_set)
        if target is None:
            raise ValueError(
                "canonical_name_overrides 无法按完整成员集合定位最终 "
                f"canonical：{sorted(member_set)}"
            )
        target.canonical_name = canonical_name.strip()
        target.rationale += (
            f"（审核修正：最终名称定为“{canonical_name.strip()}”）"
        )
        overridden_member_sets.add(member_set)
    mappings = build_mappings_from_canonical_partition(remaining)
    return RequirementConsolidationResult(
        canonical_requirements=remaining,
        mappings=mappings,
    )


def _raw_identity(raw: dict) -> dict[str, object]:
    """读取当前验收 raw 的顶层身份合同，不推断或回退。"""
    return {
        "input_fingerprint": raw.get("input_fingerprint"),
        "extractor_version": raw.get("extractor_version"),
        "selected_job_ids": sorted(raw.get("selected_job_ids") or []),
        "model": raw.get("model"),
        "prompt_version": raw.get("prompt_version"),
        "schema_version": raw.get("schema_version"),
    }


def main() -> int:
    args = parse_args()
    input_paths = [args.raw_output, args.review_decisions]
    if args.frozen_base is not None:
        input_paths.append(args.frozen_base)
    for path in input_paths:
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
    frozen_contract = decisions_payload.get("frozen_base")
    if bool(args.frozen_base) != bool(frozen_contract):
        print("--frozen-base 与 review-decisions.frozen_base 必须同时提供。")
        return 1

    # 审核决定文件与 raw 的身份一致。
    raw_identity = _raw_identity(raw)
    for field in (
        "input_fingerprint",
        "extractor_version",
        "model",
        "prompt_version",
        "schema_version",
    ):
        if raw_identity[field] in (None, ""):
            print(f"raw 缺少身份字段（{field}），拒绝应用。")
            return 1
    if not raw_identity["selected_job_ids"]:
        print("raw selected_job_ids 为空，拒绝应用。")
        return 1
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

    frozen_artifact: dict | None = None
    frozen_result: RequirementConsolidationResult | None = None
    frozen_requirement_ids: set[int] = set()
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
            if args.frozen_base is not None:
                frozen_artifact, frozen_result, frozen_requirement_ids = (
                    _load_and_validate_frozen_base(
                        args.frozen_base,
                        frozen_contract,
                        expected_ids,
                        job_ids,
                        raw_identity,
                    )
                )
                frozen_selection = load_consolidation_selection(
                    session,
                    job_ids=set(frozen_artifact["selected_job_ids"]),
                    extractor_version=str(raw_identity["extractor_version"]),
                )
                if frozen_selection.input_fingerprint != frozen_artifact[
                    "input_fingerprint"
                ]:
                    raise ValueError(
                        "frozen base input_fingerprint 与当前数据库子范围不一致"
                    )
    except ValueError as exc:
        print(f"frozen-base 身份校验失败，拒绝应用：{exc}")
        return 1
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
        application_result = result
        if frozen_result is not None:
            _validate_incremental_decision_scope(
                decisions_payload["decisions"],
                decisions_payload.get("canonical_name_overrides"),
                frozen_requirement_ids,
                expected_ids,
            )
            application_result = _build_frozen_incremental_base(
                frozen_result,
                result,
                frozen_requirement_ids,
                expected_ids,
                raw_name_by_id,
            )
        final_result = _apply_decisions(
            application_result,
            decisions_payload["decisions"],
            raw_name_by_id,
            decisions_payload.get("canonical_name_overrides"),
            protected_requirement_ids=frozen_requirement_ids,
        )
        if frozen_result is not None:
            _validate_frozen_base_unchanged(
                frozen_result,
                final_result,
                frozen_requirement_ids,
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
    if frozen_artifact is not None:
        payload["frozen_base"] = {
            "input_fingerprint": frozen_artifact["input_fingerprint"],
            "result_fingerprint": frozen_artifact["result_fingerprint"],
            "review_decisions_fingerprint": frozen_artifact[
                "review_decisions_fingerprint"
            ],
            "selected_job_ids": frozen_artifact["selected_job_ids"],
            "requirement_count": len(frozen_requirement_ids),
            "canonical_count": len(frozen_result.canonical_requirements),
            "mapping_count": len(frozen_result.mappings),
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
        "applied_canonical_name_overrides": [
            {
                "requirement_ids": override["requirement_ids"],
                "canonical_name": override["canonical_name"],
            }
            for override in decisions_payload.get(
                "canonical_name_overrides", []
            )
        ],
    }
    if frozen_artifact is not None:
        public["frozen_base"] = payload["frozen_base"]
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


def _validate_incremental_decision_scope(
    decisions: list[dict],
    canonical_name_overrides: list[dict] | None,
    frozen_requirement_ids: set[int],
    expected_ids: set[int],
) -> None:
    """冻结模式只允许涉及新增成员的裁决，不重放纯旧分区语义。"""
    if not isinstance(decisions, list):
        raise ValueError("decisions 必须是列表")
    for index, decision in enumerate(decisions):
        requirement_ids = decision.get("requirement_ids")
        if not isinstance(requirement_ids, list) or not requirement_ids:
            raise ValueError(f"decisions[{index}].requirement_ids 必须是非空列表")
        ids = set(requirement_ids)
        unknown = sorted(ids - expected_ids)
        if unknown:
            raise ValueError(f"decisions[{index}] 引用未知 requirement IDs：{unknown}")
        frozen_ids = ids & frozen_requirement_ids
        if len(frozen_ids) > 1:
            raise ValueError(
                "增量裁决不得包含两个及以上 frozen requirement IDs："
                f"decisions[{index}]={sorted(frozen_ids)}"
            )
        if frozen_ids and ids <= frozen_requirement_ids:
            raise ValueError(
                f"增量裁决不得只修改 frozen requirement：decisions[{index}]"
            )
        if decision.get("decision") == "unresolved" and frozen_ids:
            raise ValueError(
                "frozen-base 模式不接受涉及 frozen requirement 的 unresolved；"
                "请使用明确 must_link / cannot_link"
            )

    overrides = [] if canonical_name_overrides is None else canonical_name_overrides
    if not isinstance(overrides, list):
        raise ValueError("canonical_name_overrides 必须是列表")
    for index, override in enumerate(overrides):
        requirement_ids = override.get("requirement_ids")
        if not isinstance(requirement_ids, list) or not requirement_ids:
            continue  # 由现有 override 合同给出统一错误
        ids = set(requirement_ids)
        unknown = sorted(ids - expected_ids)
        if unknown:
            raise ValueError(
                f"canonical_name_overrides[{index}] 引用未知 IDs：{unknown}"
            )
        if ids <= frozen_requirement_ids:
            raise ValueError(
                "增量名称 override 不得只重命名 frozen canonical："
                f"canonical_name_overrides[{index}]"
            )


def _build_frozen_incremental_base(
    frozen_result: RequirementConsolidationResult,
    source_result: RequirementConsolidationResult,
    frozen_requirement_ids: set[int],
    expected_ids: set[int],
    raw_name_by_id: dict[int, str],
) -> RequirementConsolidationResult:
    """复制冻结 canonical，并把本轮新增 requirement 初始化为 singleton。"""
    source_by_requirement = {
        requirement_id: canonical
        for canonical in source_result.canonical_requirements
        for requirement_id in canonical.source_requirement_ids
    }
    canonicals = [item.model_copy(deep=True) for item in frozen_result.canonical_requirements]
    known_canonical_ids = {
        item.canonical_requirement_id for item in canonicals
    }
    for requirement_id in sorted(expected_ids - frozen_requirement_ids):
        raw_name = raw_name_by_id.get(requirement_id)
        source = source_by_requirement.get(requirement_id)
        if not raw_name or not raw_name.strip() or source is None:
            raise ValueError(
                f"新增 requirement {requirement_id} 缺少来源或 raw_name"
            )
        canonical_id = f"incremental-{requirement_id}"
        if canonical_id in known_canonical_ids:
            raise ValueError(f"增量 canonical ID 与 frozen base 冲突：{canonical_id}")
        canonicals.append(
            CanonicalRequirement(
                canonical_requirement_id=canonical_id,
                canonical_name=raw_name.strip(),
                source_requirement_ids=[requirement_id],
                rationale=(
                    "frozen-base 增量初始化：新增 requirement 先保持 singleton，"
                    "仅由本轮 review-decisions 改变归属"
                ),
                confidence=source.confidence,
            )
        )
    # 中间态可能存在同名 singleton；最终仍由 RequirementConsolidationResult
    # 的唯一名称合同与 canonical_name_overrides 严格校验。
    return RequirementConsolidationResult.model_construct(
        canonical_requirements=canonicals,
        mappings=build_mappings_from_canonical_partition(canonicals),
    )


def _validate_frozen_base_unchanged(
    frozen_result: RequirementConsolidationResult,
    final_result: RequirementConsolidationResult,
    frozen_requirement_ids: set[int],
) -> None:
    """逐 canonical 校验 frozen ID、成员 partition 与名称均未改变。"""
    final_by_id = {
        item.canonical_requirement_id: item
        for item in final_result.canonical_requirements
    }
    final_owner = {
        requirement_id: item.canonical_requirement_id
        for item in final_result.canonical_requirements
        for requirement_id in item.source_requirement_ids
    }
    failures: list[str] = []
    for frozen in frozen_result.canonical_requirements:
        current = final_by_id.get(frozen.canonical_requirement_id)
        if current is None:
            failures.append(f"frozen canonical 缺失：{frozen.canonical_requirement_id}")
            continue
        expected_members = set(frozen.source_requirement_ids)
        actual_old_members = set(current.source_requirement_ids) & frozen_requirement_ids
        if actual_old_members != expected_members:
            failures.append(
                f"frozen canonical 成员变化：{frozen.canonical_requirement_id}"
            )
        if current.canonical_name != frozen.canonical_name:
            failures.append(
                f"frozen canonical 名称变化：{frozen.canonical_requirement_id}"
            )
        for requirement_id in expected_members:
            if final_owner.get(requirement_id) != frozen.canonical_requirement_id:
                failures.append(f"frozen requirement 归属变化：{requirement_id}")
    if failures:
        raise ValueError("；".join(failures))


def _load_and_validate_frozen_base(
    path: Path,
    contract: dict,
    current_expected_ids: set[int],
    current_selected_job_ids: set[int],
    raw_identity: dict[str, object],
) -> tuple[dict, RequirementConsolidationResult, set[int]]:
    """读取完整 final artifact，并按 decisions 中绑定的身份强校验。"""
    required_artifact_fields = {
        "input_fingerprint",
        "extractor_version",
        "selected_job_ids",
        "model",
        "prompt_version",
        "schema_version",
        "source_run_identifier",
        "source_result_fingerprint",
        "review_decisions_fingerprint",
        "reviewed_by",
        "reviewed_at",
        "result_fingerprint",
        "result",
    }
    if not path.exists():
        raise ValueError(f"frozen base 文件不存在：{path}")
    artifact = json.loads(path.read_text(encoding="utf-8"))
    missing_fields = sorted(required_artifact_fields - set(artifact))
    if missing_fields or any(
        artifact.get(field) in (None, "")
        for field in required_artifact_fields - {"result"}
    ):
        raise ValueError(
            f"frozen base 不是完整 final result，缺失/空字段：{missing_fields}"
        )

    required_contract_fields = {
        "input_fingerprint",
        "result_fingerprint",
        "review_decisions_fingerprint",
        "selected_job_ids",
        "requirement_ids",
        "canonical_count",
        "mapping_count",
    }
    if not isinstance(contract, dict):
        raise ValueError("review-decisions 缺少 frozen_base 身份合同")
    missing_contract = sorted(required_contract_fields - set(contract))
    if missing_contract:
        raise ValueError(f"frozen_base 身份合同缺少字段：{missing_contract}")
    requirement_ids = contract.get("requirement_ids")
    if (
        not isinstance(requirement_ids, list)
        or not requirement_ids
        or any(not isinstance(item, int) for item in requirement_ids)
        or len(requirement_ids) != len(set(requirement_ids))
    ):
        raise ValueError("frozen_base.requirement_ids 必须是非空、不重复整数列表")
    frozen_ids = set(requirement_ids)
    if not frozen_ids < current_expected_ids:
        raise ValueError("frozen requirement IDs 必须是当前完整输入的真子集")

    selected_job_ids = contract.get("selected_job_ids")
    if (
        not isinstance(selected_job_ids, list)
        or not selected_job_ids
        or any(not isinstance(item, int) for item in selected_job_ids)
        or len(selected_job_ids) != len(set(selected_job_ids))
    ):
        raise ValueError("frozen_base.selected_job_ids 必须是非空、不重复整数列表")
    if not set(selected_job_ids) <= current_selected_job_ids:
        raise ValueError("frozen selected_job_ids 不在当前输入范围内")

    bound_fields = (
        "input_fingerprint",
        "result_fingerprint",
        "review_decisions_fingerprint",
        "selected_job_ids",
    )
    for field in bound_fields:
        if artifact.get(field) != contract.get(field):
            raise ValueError(f"frozen base 与 review-decisions 身份不一致：{field}")
    for field in (
        "extractor_version",
        "model",
        "prompt_version",
        "schema_version",
    ):
        if artifact.get(field) != raw_identity.get(field):
            raise ValueError(f"frozen base 与当前 raw 版本身份不一致：{field}")

    try:
        result = RequirementConsolidationResult.model_validate(artifact["result"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"frozen base result 不合法：{exc}") from exc
    content_fingerprint = result_fingerprint(result)
    if content_fingerprint != artifact["result_fingerprint"]:
        raise ValueError("frozen base 内容与 result_fingerprint 不一致")
    if len(result.canonical_requirements) != contract["canonical_count"]:
        raise ValueError("frozen base canonical_count 不匹配")
    if len(result.mappings) != contract["mapping_count"]:
        raise ValueError("frozen base mapping_count 不匹配")
    identity_failures = validate_exact_identity(result, frozen_ids)
    contract_result = validate_contract(result, expected_ids=frozen_ids)
    if identity_failures or contract_result.coverage != 1.0 or (
        contract_result.structural_violation_count != 0
    ):
        raise ValueError(
            "frozen base 未精确覆盖绑定 requirement IDs："
            f"{identity_failures}"
        )
    return artifact, result, frozen_ids


if __name__ == "__main__":
    raise SystemExit(main())
