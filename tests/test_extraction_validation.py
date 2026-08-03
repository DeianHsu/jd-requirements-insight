"""验证抽取合同检查、运行间比较与场景属性检查（P0-3 新协议，DEC-015）。

覆盖：候选块类型覆盖、excluded 违规、evidence 歧义与归属；相同输入/变形
两套块对齐；组内最佳配对；逻辑组结构比较（去临时 ID）；场景属性检查。
"""

import json

from app.extraction_two_stage import parse_discovery_response
from app.extraction_validation import (
    RunSnapshot,
    TransformationResult,
    anchor_ids,
    build_acceptance_report,
    check_contract,
    check_discovery_coverage,
    check_payload_schema,
    check_scenario_properties,
    compare_runs,
    contract_hard_gate_failures,
    compute_input_fingerprint,
)
from app.schemas import JobExtractionResult

RAW_TEXT = "# 示例岗位\n\n负责能力甲体系建设。\n\n熟悉技术甲和框架乙。"


def discovery_payload() -> dict:
    """返回覆盖三句中性 JD 的合法发现段响应。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0],
                "kind": "excluded",
                "source_span": "# 示例岗位",
                "note": "标题，非条件内容",
            },
            {
                "block_id": "b1",
                "sentence_indexes": [1],
                "kind": "responsibility",
                "source_span": "负责能力甲体系建设",
                "note": "工作内容",
            },
            {
                "block_id": "b2",
                "sentence_indexes": [2],
                "kind": "requirement",
                "source_span": "熟悉技术甲和框架乙",
                "note": "候选人条件",
            },
        ],
    }


def result_payload() -> dict:
    """返回与候选块一致的合法抽取数据合同响应（三级熟练度）。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "requirements": [
            {
                "raw_name": "技术甲",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "熟悉技术甲和框架乙",
                "confidence": 0.9,
            },
            {
                "raw_name": "框架乙",
                "category": "agent_framework",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "熟悉技术甲和框架乙",
                "confidence": 0.9,
            },
        ],
    }


def make_snapshot(
    discovery: dict | None = None,
    result: dict | None = None,
    raw_text: str = RAW_TEXT,
) -> RunSnapshot:
    """构造一次运行快照，discovery/result 缺省使用合法值。"""
    parsed_discovery = parse_discovery_response(
        json.dumps(discovery or discovery_payload(), ensure_ascii=False)
    )
    parsed_result = JobExtractionResult.model_validate(result or result_payload())
    return RunSnapshot(
        discovery=parsed_discovery,
        result=parsed_result,
        raw_text=raw_text,
        raw_payload=result or result_payload(),
    )


def full_identity() -> dict[str, str]:
    """返回身份完整的验收身份字典。"""
    return {
        "model": "test-model",
        "prompt_version": "0.8",
        "schema_version": "3.0",
        "input_fingerprint": compute_input_fingerprint(RAW_TEXT),
    }


# ---------------------------------------------------------------------------
# 合同检查
# ---------------------------------------------------------------------------


def test_coverage_passes_for_complete_discovery() -> None:
    """覆盖检查通过合法发现结果。"""
    snapshot = make_snapshot()
    report = check_discovery_coverage(snapshot.discovery, RAW_TEXT)

    assert report.coverage == 1.0
    assert report.passed


def test_coverage_reports_missing_sentence() -> None:
    """sentence 覆盖缺失被报告为 hard gate 失败。"""
    payload = discovery_payload()
    payload["blocks"].pop()
    snapshot = make_snapshot(discovery=payload)

    report = check_discovery_coverage(snapshot.discovery, RAW_TEXT)
    assert report.missing_sentences == [2]
    assert report.coverage < 1.0

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert "discovery_coverage" in " ".join(contract_hard_gate_failures(contract))


def test_coverage_reports_duplicate_sentence() -> None:
    """sentence 重复覆盖被报告。"""
    payload = discovery_payload()
    payload["blocks"][2]["sentence_indexes"] = [1, 2]
    payload["blocks"][2]["source_span"] = "负责能力甲体系建设熟悉技术甲和框架乙"
    snapshot = make_snapshot(discovery=payload)

    report = check_discovery_coverage(snapshot.discovery, RAW_TEXT)
    assert report.duplicate_covered_sentences == [1]

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert "duplicate_sentence_coverage" in " ".join(contract_hard_gate_failures(contract))


def test_coverage_reports_invalid_span() -> None:
    """候选块 source_span 与分句索引不对应时被报告。"""
    payload = discovery_payload()
    payload["blocks"][2]["source_span"] = "不存在的原文"
    snapshot = make_snapshot(discovery=payload)

    report = check_discovery_coverage(snapshot.discovery, RAW_TEXT)
    assert report.invalid_span_blocks == ["b2"]


def test_contract_reports_evidence_not_in_source() -> None:
    """evidence 不存在于原文时证据违规被报告。"""
    payload = result_payload()
    payload["requirements"][0]["evidence"] = "熟悉框架丙"
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.evidence_violations
    assert "evidence_violations" in " ".join(contract_hard_gate_failures(contract))


def test_contract_reports_unprocessed_block() -> None:
    """非 excluded 候选块没有任何输出项命中时被报告。"""
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["evidence"] = "负责能力甲体系建设"
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.unprocessed_blocks == ["b2"]
    assert "unprocessed_blocks" in " ".join(contract_hard_gate_failures(contract))


def test_contract_reports_type_violation_responsibility_block_produces_requirement() -> None:
    """responsibility 块产出 requirement 时类型覆盖失败（职责不得误抽为要求）。"""
    payload = result_payload()
    payload["requirements"] = [
        {
            "raw_name": "能力甲体系建设",
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "负责能力甲体系建设",
            "confidence": 0.9,
        }
    ]
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.type_violations
    assert "b1" in contract.type_violations[0]
    assert "candidate_type_violations" in " ".join(contract_hard_gate_failures(contract))


def test_contract_mixed_block_records_produced_kinds() -> None:
    """mixed 块允许产出职责或要求，并记录实际产出（测试 20）。"""
    payload = discovery_payload()
    payload["blocks"][2]["kind"] = "mixed"
    payload["blocks"][2]["note"] = "工作与条件混合"
    snapshot = make_snapshot(discovery=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.passed
    assert contract.produced_kinds["b2"] == ["requirement", "requirement"]


def test_contract_reports_excluded_block_must_preferred() -> None:
    """excluded 块产出 must/preferred requirement 被拒绝（测试 21）。"""
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["evidence"] = "# 示例岗位"
    payload["requirements"].append(
        {
            "raw_name": "能力丁",
            "category": "other",
            "importance": "preferred",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "# 示例岗位",
            "confidence": 0.8,
        }
    )
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.excluded_violations
    assert "excluded_block_violations" in " ".join(contract_hard_gate_failures(contract))


def test_contract_reports_evidence_unattributed_items() -> None:
    """输出项只命中 excluded 块时作为无依据明确事实被报告（重命名后字段）。"""
    payload = result_payload()
    payload["requirements"][0]["evidence"] = "# 示例岗位"
    payload["requirements"][1]["evidence"] = "# 示例岗位"
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert len(contract.evidence_unattributed_items) == 2
    assert "evidence_unattributed_items" in " ".join(contract_hard_gate_failures(contract))
    assert not hasattr(contract, "unattributed_items")


def test_contract_reports_ambiguous_evidence() -> None:
    """输出项 evidence 命中多个候选块时报告歧义。"""
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["evidence"] = "负责能力甲体系建设熟悉技术甲和框架乙"
    # span 必须与分句对应：把两块的 span 合并验证歧义需要改写原文结构。
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    # evidence 同时命中 b1（responsibility）与 b2（requirement）之外的 excluded 块？
    # "负责能力甲体系建设熟悉技术甲和框架乙" 命中 b1 与 b2 两个块。
    assert contract.ambiguous_evidence


def test_contract_reports_identity_missing() -> None:
    """结果版本和输入身份缺失时被报告且 hard gate 失败。"""
    snapshot = make_snapshot()

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity={}
    )
    assert not contract.identity_complete
    assert contract.identity_missing
    assert "identity_incomplete" in " ".join(contract_hard_gate_failures(contract))


def test_payload_schema_rejects_invalid_any_of() -> None:
    """非法 any_of（单成员组）被合同检查拒绝并记录为逻辑组错误。"""
    payload = result_payload()
    payload["requirements"][0]["group_id"] = "group_1"
    payload["requirements"][0]["group_logic"] = "any_of"
    payload["requirements"][1]["group_id"] = None
    payload["requirements"][1]["group_logic"] = "standalone"

    valid, errors, group_errors = check_payload_schema(payload)
    assert not valid
    assert errors
    assert group_errors
    assert "any_of" in group_errors[0]


# ---------------------------------------------------------------------------
# 块对齐与原子项配对
# ---------------------------------------------------------------------------


def test_compare_runs_aligns_candidate_blocks() -> None:
    """两次运行以候选块为锚点正确对齐（相同输入）。"""
    base = make_snapshot()
    variant = make_snapshot()

    comparison = compare_runs(base, variant)

    assert comparison.aligned_block_count == 3
    assert comparison.block_alignment_rate == 1.0
    assert comparison.kind_agreement == 1.0
    assert comparison.atomic_item_count_agreement
    assert comparison.unmatched_item_count == 0
    assert comparison.category_agreement == 1.0
    assert comparison.group_membership_agreement == 1.0


def test_compare_same_span_blocks_do_not_collide() -> None:
    """相同 source span 重复出现不会因字典覆盖丢失（测试 13）。"""
    raw_text = "熟悉技术甲。\n熟悉技术甲。"
    payload = {
        "role_family": "other",
        "seniority": "unknown",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0],
                "kind": "requirement",
                "source_span": "熟悉技术甲",
                "note": "条件一",
            },
            {
                "block_id": "b1",
                "sentence_indexes": [1],
                "kind": "requirement",
                "source_span": "熟悉技术甲",
                "note": "条件二（重复句）",
            },
        ],
    }
    base = make_snapshot(discovery=payload, raw_text=raw_text)
    variant = make_snapshot(discovery=payload, raw_text=raw_text)

    comparison = compare_runs(base, variant)

    assert comparison.aligned_block_count == 2
    assert comparison.unaligned_base_blocks == []
    assert comparison.unaligned_variant_blocks == []


def test_compare_item_order_swap_no_false_drift() -> None:
    """同证据两个要求只交换输出顺序，应完全一致（测试 14）。"""
    base_payload = result_payload()
    variant_payload = result_payload()
    variant_payload["requirements"] = list(reversed(variant_payload["requirements"]))
    base = make_snapshot(result=base_payload)
    variant = make_snapshot(result=variant_payload)

    comparison = compare_runs(base, variant)

    assert comparison.unmatched_item_count == 0
    assert comparison.category_agreement == 1.0
    assert comparison.importance_agreement == 1.0
    assert comparison.proficiency_agreement == 1.0
    assert comparison.evidence_span_agreement == 1.0


def test_compare_reports_item_count_drift() -> None:
    """原子项数量漂移被报告为 unmatched。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"].append(
        {
            "raw_name": "技术丙",
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "熟悉技术甲和框架乙",
            "confidence": 0.8,
        }
    )
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)

    assert not comparison.atomic_item_count_agreement
    assert comparison.unmatched_variant_count == 1
    assert comparison.unmatched_item_count == 1


def test_compare_field_drift_only_reports_target_field() -> None:
    """同证据一个字段变化，只报告目标字段（测试 15 的字段面）。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"][0]["category"] = "agent_framework"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)

    assert comparison.category_agreement < 1.0
    assert comparison.importance_agreement == 1.0
    assert comparison.proficiency_agreement == 1.0


def test_compare_reports_drifted_block_identifiers() -> None:
    """span 相同但 block_id 不同的候选块被报告为漂移标识。"""
    base = make_snapshot()
    payload = discovery_payload()
    payload["blocks"][2]["block_id"] = "b2x"
    variant = make_snapshot(discovery=payload)

    comparison = compare_runs(base, variant)

    assert ("b2", "b2x") in comparison.drifted_block_identifiers
    assert comparison.block_alignment_rate == 1.0


def test_compare_accepts_name_change_with_same_evidence() -> None:
    """名称变化但证据和字段一致时不误判为整体失败。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"][0]["raw_name"] = "使用技术甲的经验"
    payload["requirements"][1]["raw_name"] = "框架乙使用经验"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)

    assert comparison.unmatched_item_count == 0
    assert comparison.evidence_span_agreement == 1.0
    assert comparison.category_agreement == 1.0
    assert comparison.atomic_item_count_agreement


def test_compare_requirement_removal_reports_unmatched() -> None:
    """要求项被删除时报告 unmatched（避免静默接受事实丢失）。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"] = payload["requirements"][:1]
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)

    assert comparison.unmatched_base_count == 1


def test_compare_anchored_text_replace_alignment() -> None:
    """文本替换通过 anchor map 对齐，不依赖完整 span 相等（测试 15）。"""
    base = make_snapshot()
    variant_text = RAW_TEXT.replace("熟悉技术甲和框架乙", "精通技术甲和框架乙")
    variant_payload = discovery_payload()
    variant_payload["blocks"][2]["source_span"] = "精通技术甲和框架乙"
    variant_payload["blocks"][2]["sentence_indexes"] = [2]
    variant_result = result_payload()
    for requirement in variant_result["requirements"]:
        requirement["proficiency"] = "advanced"
        requirement["evidence"] = "精通技术甲和框架乙"
    variant = make_snapshot(
        discovery=variant_payload, result=variant_result, raw_text=variant_text
    )

    transformation = TransformationResult(
        text=variant_text,
        transformation_type="text_replace",
        anchor_map={anchor_ids(RAW_TEXT)[2]: [anchor_ids(variant_text)[2]]},
        changed_regions=frozenset({anchor_ids(RAW_TEXT)[2]}),
    )
    comparison = compare_runs(base, variant, transformation=transformation)

    assert comparison.aligned_block_count == 3
    assert comparison.unmatched_item_count == 0
    # 目标块 basic->advanced 属预期变化，全局 proficiency 一致性下降属预期；
    # 非目标块由 proficiency_invariance 保证不变。
    assert comparison.proficiency_agreement < 1.0
    failures, _ = check_scenario_properties(
        comparison,
        {
            "proficiency_expected_changes": [
                {"anchor": "熟悉技术甲和框架乙", "from": "basic", "to": "advanced"}
            ],
            "proficiency_invariance": True,
        },
        changed_regions=frozenset({anchor_ids(RAW_TEXT)[2]}),
    )
    assert failures == []


def test_compare_anchored_one_to_many_alignment() -> None:
    """一句拆两句的一对多锚点可对齐（测试 16）。"""
    base = make_snapshot()
    variant_text = RAW_TEXT.replace(
        "熟悉技术甲和框架乙", "熟悉技术甲。掌握框架乙"
    )
    variant_payload = discovery_payload()
    # 新句界：0 标题 / 1 职责 / 2 熟悉技术甲 / 3 掌握框架乙
    variant_payload["blocks"][2]["sentence_indexes"] = [2, 3]
    variant_payload["blocks"][2]["source_span"] = "熟悉技术甲。掌握框架乙"
    variant_result = result_payload()
    variant_result["requirements"][0]["evidence"] = "熟悉技术甲"
    variant_result["requirements"][1]["evidence"] = "掌握框架乙"
    variant_result["requirements"][1]["proficiency"] = "advanced"
    variant = make_snapshot(
        discovery=variant_payload, result=variant_result, raw_text=variant_text
    )

    base_anchor = anchor_ids(RAW_TEXT)[2]
    variant_anchors = anchor_ids(variant_text)
    transformation = TransformationResult(
        text=variant_text,
        transformation_type="text_replace",
        anchor_map={base_anchor: [variant_anchors[2], variant_anchors[3]]},
        changed_regions=frozenset({base_anchor}),
    )
    comparison = compare_runs(base, variant, transformation=transformation)

    assert comparison.aligned_block_count == 3
    assert comparison.unmatched_item_count == 0


# ---------------------------------------------------------------------------
# 逻辑组结构比较（去临时 ID）
# ---------------------------------------------------------------------------


def _any_of_payload(group_a: str, group_b: str) -> dict:
    """构造两个 any_of 组（各两个成员）的抽取结果。"""
    payload = result_payload()
    payload["requirements"] = [
        {
            "raw_name": "技术甲",
            "category": "programming_language",
            "importance": "must",
            "proficiency": "basic",
            "group_id": group_a,
            "group_logic": "any_of",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "熟悉技术甲",
            "confidence": 0.9,
        },
        {
            "raw_name": "框架乙",
            "category": "agent_framework",
            "importance": "must",
            "proficiency": "basic",
            "group_id": group_a,
            "group_logic": "any_of",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "熟悉技术甲",
            "confidence": 0.9,
        },
        {
            "raw_name": "技术丙",
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": group_b,
            "group_logic": "any_of",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "熟悉框架丁",
            "confidence": 0.9,
        },
        {
            "raw_name": "框架丁",
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": group_b,
            "group_logic": "any_of",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "熟悉框架丁",
            "confidence": 0.9,
        },
    ]
    return payload


def _any_of_discovery() -> dict:
    """构造包含两句要求的发现段（每句一个 requirement 块）。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0],
                "kind": "excluded",
                "source_span": "# 示例岗位",
                "note": "标题，非条件内容",
            },
            {
                "block_id": "b1",
                "sentence_indexes": [1],
                "kind": "responsibility",
                "source_span": "负责能力甲体系建设",
                "note": "工作内容",
            },
            {
                "block_id": "b2",
                "sentence_indexes": [2],
                "kind": "requirement",
                "source_span": "熟悉技术甲和框架乙",
                "note": "候选人条件一",
            },
            {
                "block_id": "b3",
                "sentence_indexes": [3],
                "kind": "requirement",
                "source_span": "熟悉框架丁",
                "note": "候选人条件二",
            },
        ],
    }


ANY_OF_RAW = (
    "# 示例岗位\n\n负责能力甲体系建设。\n\n熟悉技术甲和框架乙。\n\n熟悉框架丁。"
)


def _any_of_snapshot(group_a: str, group_b: str) -> RunSnapshot:
    """构造带两个 any_of 组的完整快照。"""
    return make_snapshot(
        discovery=_any_of_discovery(),
        result=_any_of_payload(group_a, group_b),
        raw_text=ANY_OF_RAW,
    )


def test_group_id_rename_does_not_drift() -> None:
    """group ID 改名但成员相同，agreement=100%（测试 17）。"""
    base = _any_of_snapshot("group_1", "group_2")
    variant = _any_of_snapshot("group_x", "group_y")

    comparison = compare_runs(base, variant)

    assert comparison.group_membership_agreement == 1.0
    assert comparison.group_type_agreement == 1.0
    assert comparison.unmatched_item_count == 0
    assert comparison.group_id_map == {"group_1": "group_x", "group_2": "group_y"}


def test_group_member_removed_reports_drift() -> None:
    """一个成员被移出 any_of，必须报告漂移（测试 18）。"""
    base = _any_of_snapshot("group_1", "group_2")
    payload = _any_of_payload("group_1", "group_2")
    # 组完全解散：两个成员都变为 standalone（避免出现单成员 any_of 非法合同）。
    for requirement in payload["requirements"][:2]:
        requirement["group_id"] = None
        requirement["group_logic"] = "standalone"
    variant = make_snapshot(
        discovery=_any_of_discovery(), result=payload, raw_text=ANY_OF_RAW
    )

    comparison = compare_runs(base, variant)

    assert comparison.group_membership_agreement < 1.0


def test_groups_merged_reports_drift() -> None:
    """两个组被错误合并，必须报告漂移。"""
    base = _any_of_snapshot("group_1", "group_2")
    payload = _any_of_payload("group_1", "group_2")
    for requirement in payload["requirements"]:
        requirement["group_id"] = "merged"
    variant = make_snapshot(
        discovery=_any_of_discovery(), result=payload, raw_text=ANY_OF_RAW
    )

    comparison = compare_runs(base, variant)

    assert comparison.group_membership_agreement < 1.0
    assert comparison.cross_group_merges > 0


def test_group_member_order_swap_no_drift() -> None:
    """成员顺序变化不影响结果。"""
    payload = _any_of_payload("group_1", "group_2")
    base = make_snapshot(
        discovery=_any_of_discovery(), result=payload, raw_text=ANY_OF_RAW
    )
    variant_payload = _any_of_payload("group_1", "group_2")
    variant_payload["requirements"] = list(reversed(variant_payload["requirements"]))
    variant = make_snapshot(
        discovery=_any_of_discovery(), result=variant_payload, raw_text=ANY_OF_RAW
    )

    comparison = compare_runs(base, variant)

    assert comparison.group_membership_agreement == 1.0
    assert comparison.unmatched_item_count == 0


# ---------------------------------------------------------------------------
# 场景属性检查
# ---------------------------------------------------------------------------


def test_scenario_properties_fact_set_preserved_failure() -> None:
    """base 事实在 variant 中丢失时 fact_set_preserved 检查失败。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"].pop()
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(comparison, {"fact_set_preserved": True})

    assert failures
    assert "fact_set_preserved" in failures[0]


def test_scenario_properties_fact_set_unchanged_passes() -> None:
    """事实集双向一致时 fact_set_unchanged 检查通过。"""
    comparison = compare_runs(make_snapshot(), make_snapshot())

    failures, warnings = check_scenario_properties(
        comparison,
        {
            "fact_set_unchanged": True,
            "block_set_preserved": True,
            "field_invariance": ["category", "importance", "proficiency"],
        },
    )

    assert failures == []
    assert warnings == []


def test_scenario_properties_no_new_conditions_failure() -> None:
    """variant 新增 must/preferred 条件时 no_new_conditions 检查失败。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"].append(
        {
            "raw_name": "框架戊",
            "category": "agent_framework",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": "负责能力甲体系建设",
            "confidence": 0.8,
        }
    )
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(comparison, {"no_new_conditions": True})

    assert failures
    assert "no_new_conditions" in failures[0]
    assert comparison.new_condition_items


def test_scenario_properties_basic_to_advanced_expected_change() -> None:
    """basic→advanced 的目标锚点变化被正确定位（SCN-005 检查器）。"""
    base = make_snapshot()
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["proficiency"] = "advanced"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(
        comparison,
        {
            "proficiency_expected_changes": [
                {"anchor": "熟悉技术甲和框架乙", "from": "basic", "to": "advanced"}
            ],
            "proficiency_invariance": True,
        },
    )
    assert failures == []

    # 未变化时检查失败。
    failures, _ = check_scenario_properties(
        compare_runs(base, base),
        {
            "proficiency_expected_changes": [
                {"anchor": "熟悉技术甲和框架乙", "from": "basic", "to": "advanced"}
            ]
        },
    )
    assert failures


def test_scenario_properties_same_band_proficiency_invariance() -> None:
    """同级词变化（basic→basic）不改变 proficiency（SCN-011/012 检查器）。"""
    comparison = compare_runs(make_snapshot(), make_snapshot())

    failures, _ = check_scenario_properties(
        comparison,
        {
            "fact_set_preserved": True,
            "field_invariance": [
                "category",
                "importance",
                "proficiency",
                "group_type",
                "group_membership",
            ],
        },
    )
    assert failures == []


def test_scenario_properties_experience_to_unknown() -> None:
    """经验表达变化（basic→unknown）被定位（SCN-013 检查器）。"""
    base = make_snapshot()
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["proficiency"] = "unknown"
        requirement["evidence"] = "具备技术甲和框架乙项目经验"
    raw_text = RAW_TEXT.replace(
        "熟悉技术甲和框架乙", "具备技术甲和框架乙项目经验"
    )
    variant_payload = discovery_payload()
    variant_payload["blocks"][2]["source_span"] = "具备技术甲和框架乙项目经验"
    variant = make_snapshot(
        discovery=variant_payload, result=payload, raw_text=raw_text
    )

    transformation = TransformationResult(
        text=raw_text,
        transformation_type="text_replace",
        anchor_map={anchor_ids(RAW_TEXT)[2]: [anchor_ids(raw_text)[2]]},
        changed_regions=frozenset({anchor_ids(RAW_TEXT)[2]}),
    )
    comparison = compare_runs(base, variant, transformation=transformation)
    failures, _ = check_scenario_properties(
        comparison,
        {
            "experience_to_unknown_expected_change": {
                "anchor": "熟悉技术甲和框架乙"
            }
        },
        changed_regions=frozenset({anchor_ids(RAW_TEXT)[2]}),
    )
    assert failures == []


def test_scenario_properties_group_change_and_member_count() -> None:
    """standalone→any_of 与组成员数自动检查（SCN-007 检查器）。"""
    base = make_snapshot()
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["group_id"] = "group_1"
        requirement["group_logic"] = "any_of"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(
        comparison,
        {
            "group_logic_changed_to": "any_of",
            "expected_group_member_count": 2,
            "group_members_preserved": True,
        },
        changed_regions=frozenset({anchor_ids(RAW_TEXT)[2]}),
    )
    assert failures == []


def test_scenario_properties_raw_name_follows_replacements() -> None:
    """raw_name 跟随技术替换且不输出旧名（SCN-008 检查器）。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"][0]["raw_name"] = "技术丙"
    payload["requirements"][1]["raw_name"] = "框架丁"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(
        comparison,
        {
            "raw_name_follows_replacements": [
                {"find": "技术甲", "replace": "技术丙"},
                {"find": "框架乙", "replace": "框架丁"},
            ]
        },
        variant_result=variant.result,
    )
    assert failures == []

    # variant 仍输出旧名时失败。
    old_variant = make_snapshot()
    failures, _ = check_scenario_properties(
        compare_runs(base, old_variant),
        {
            "raw_name_follows_replacements": [
                {"find": "技术甲", "replace": "技术丙"}
            ]
        },
        variant_result=old_variant.result,
    )
    assert failures


def test_build_acceptance_report_without_contract() -> None:
    """缺少合同时验收报告标记为不可用并整体失败（独立工具语义）。"""
    report = build_acceptance_report(identity=full_identity(), contract=None)

    assert "contract_check_unavailable" in report.hard_gate_failures
    assert not report.passed
    assert "input_fingerprint" in report.identity
