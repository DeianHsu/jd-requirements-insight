"""验证抽取合同检查、运行间比较与场景属性检查（P0-3 新协议，DEC-015）。"""

import json

from app.extraction_two_stage import parse_discovery_response
from app.extraction_validation import (
    RunSnapshot,
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
    """返回与候选块一致的合法抽取数据合同响应。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "responsibilities": [
            {"name": "建设能力甲体系", "evidence": "负责能力甲体系建设"}
        ],
        "requirements": [
            {
                "raw_name": "技术甲",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "familiar",
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
                "proficiency": "familiar",
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
        "prompt_version": "0.7",
        "schema_version": "2.0",
        "input_fingerprint": compute_input_fingerprint(RAW_TEXT),
    }


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
    """非 excluded 候选块没有被任何输出项命中时被报告（judge 候选覆盖）。"""
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["evidence"] = "负责能力甲体系建设"
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert contract.unprocessed_blocks == ["b2"]
    assert "unprocessed_blocks" in " ".join(contract_hard_gate_failures(contract))


def test_contract_reports_unattributed_item() -> None:
    """输出项只命中 excluded 块时作为无依据明确事实被报告。"""
    payload = result_payload()
    payload["requirements"][0]["evidence"] = "# 示例岗位"
    payload["requirements"][1]["evidence"] = "# 示例岗位"
    snapshot = make_snapshot(result=payload)

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity=full_identity()
    )
    assert len(contract.unattributed_items) == 2
    assert "unattributed_facts" in " ".join(contract_hard_gate_failures(contract))


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


def test_contract_reports_identity_missing() -> None:
    """结果版本和输入身份缺失时被报告且 hard gate 失败。"""
    snapshot = make_snapshot()

    contract = check_contract(
        snapshot.discovery, snapshot.result, RAW_TEXT, identity={}
    )
    assert not contract.identity_complete
    assert contract.identity_missing
    assert "identity_incomplete" in " ".join(contract_hard_gate_failures(contract))


def test_compare_runs_aligns_candidate_blocks() -> None:
    """两次运行以候选块为锚点正确对齐。"""
    base = make_snapshot()
    variant = make_snapshot()

    comparison = compare_runs(base, variant)

    assert comparison.aligned_block_count == 3
    assert comparison.block_alignment_rate == 1.0
    assert comparison.kind_agreement == 1.0
    assert comparison.atomic_item_count_agreement
    assert comparison.unmatched_item_count == 0
    assert comparison.category_agreement == 1.0
    assert comparison.group_logic_agreement == 1.0


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


def test_compare_reports_field_drift() -> None:
    """字段漂移被报告（category 一致性下降）。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"][0]["category"] = "agent_framework"
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)

    assert comparison.category_agreement < 1.0
    assert comparison.importance_agreement == 1.0


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


def test_scenario_properties_fact_set_preserved_failure() -> None:
    """base 事实在 variant 中丢失时 fact_set_preserved 检查失败。"""
    base = make_snapshot()
    payload = result_payload()
    payload["requirements"].pop()
    variant = make_snapshot(result=payload)

    comparison = compare_runs(base, variant)
    failures, _ = check_scenario_properties(
        comparison, {"fact_set_preserved": True}
    )

    assert failures
    assert "fact_set_preserved" in failures[0]


def test_scenario_properties_fact_set_unchanged_passes() -> None:
    """事实集双向一致时 fact_set_unchanged 检查通过。"""
    comparison = compare_runs(make_snapshot(), make_snapshot())

    failures, warnings = check_scenario_properties(
        comparison, {"fact_set_unchanged": True, "block_set_preserved": True}
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
    failures, _ = check_scenario_properties(
        comparison, {"no_new_conditions": True}
    )

    assert failures
    assert "no_new_conditions" in failures[0]
    assert comparison.new_condition_items


def test_scenario_properties_proficiency_upgraded() -> None:
    """熟悉→精通的变化被正确识别，未变化时检查失败。"""
    base = make_snapshot()
    payload = result_payload()
    for requirement in payload["requirements"]:
        requirement["proficiency"] = "expert"
    upgraded = make_snapshot(result=payload)

    failures, _ = check_scenario_properties(
        compare_runs(base, upgraded), {"proficiency_upgraded": True}
    )
    assert failures == []

    failures, _ = check_scenario_properties(
        compare_runs(base, base), {"proficiency_upgraded": True}
    )
    assert failures


def test_build_acceptance_report_without_contract() -> None:
    """缺少合同时验收报告标记为不可用并整体失败。"""
    report = build_acceptance_report(identity=full_identity(), contract=None)

    assert "contract_check_unavailable" in report.hard_gate_failures
    assert not report.passed
    assert "input_fingerprint" in report.identity
