"""P0-3 抽取验收脚本（DEC-015 新协议，Track A：合成规则场景）。

默认不调用外部模型；完整验收必须显式`--execute`，预检使用`--dry-run`。
验收显式使用 candidate profile：v0.8 + Schema V3（三级熟练度），不依赖
active 全局常量间接选择。执行流程（真实模型）：

1. 加载规则场景（`data/rule_scenarios/extraction_metamorphic_cases.json`），
   对每个场景把 base_input 应用确定性变换（返回 TransformationResult：
   新文本 + base→transformed 锚点映射 + 预期变化区域）；
2. 对 base_input 独立运行 `--runs` 次（必须 >=3），每次结果独立保存；
3. 对每个变形输入运行 1 次；
4. 每次运行（base 与 transformed）都执行完整确定性合同检查
   （Schema、discovery coverage、duplicate coverage、evidence、
   candidate type coverage、logic groups、identity、evidence attribution）；
5. base 多次运行两两比较（稳定性，第一版只作 warning）；
6. base 与变形输入用 TransformationResult 锚点比较并检查场景期望属性；
7. 输出机器可读报告（hard gate / warning / diagnostic 分级），记录
   model、prompt version、schema version、scenario_set_fingerprint、
   runs、max_attempts、run identifier、timestamp；报告不输出完整 JD 文本；
8. 运行完整性（expected/successful/failed）任何缺失都属于 hard gate。

本脚本不读取人工完整答案决定通过或失败。返回码：参数错误非零；
`--dry-run` 返回 0（预检，不是验收）；验收 hard gate 失败返回 1，通过返回 0。
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import load_llm_settings
from app.extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
)
from app.extraction_two_stage import extract_job_two_stage_with_discovery, split_sentences
from app.extraction_validation import (
    RunSnapshot,
    TransformationResult,
    anchor_ids,
    check_contract,
    check_scenario_properties,
    compare_runs,
    compute_input_fingerprint,
    contract_hard_gate_failures,
    resolve_anchor,
    sentence_anchor,
)
from app.ingestion import content_hash
from app.models import JobDescription

DEFAULT_SCENARIOS_PATH = Path(
    "data/rule_scenarios/extraction_metamorphic_cases.json"
)


def _apply_transform(text: str, transformation: dict[str, Any]) -> str:
    """应用确定性文本变换；未知类型直接拒绝，保证实验可复现。"""
    kind = transformation.get("type")
    if kind == "format_bullets":
        lines = []
        for line in text.splitlines():
            stripped = line.rstrip()
            if re.match(r"^\d+\. ", stripped):
                stripped = re.sub(r"^(\d+)\. ", r"(\1) ", stripped)
            elif stripped.startswith("- "):
                stripped = "• " + stripped[2:]
            lines.append(stripped)
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines))
    if kind == "reorder_paragraphs":
        paragraphs = [part for part in re.split(r"\n\s*\n", text) if part.strip()]
        if len(paragraphs) < 2:
            raise ValueError("reorder_paragraphs 需要至少两个段落")
        rng = random.Random(transformation.get("seed", 20260803))
        head, rest = paragraphs[0], paragraphs[1:]
        rng.shuffle(rest)
        return "\n\n".join([head, *rest])
    if kind == "append_text":
        return text + transformation["text"]
    if kind == "text_replace":
        result = text
        for replacement in transformation["replacements"]:
            find = replacement["find"]
            if find not in result:
                raise ValueError(f"text_replace 未找到目标：{find}")
            result = result.replace(find, replacement["replace"])
        return result
    if kind == "duplicate_sentence":
        target = transformation["target"]
        if target not in text:
            raise ValueError(f"duplicate_sentence 未找到目标：{target}")
        return text.replace(target, target + target, 1)
    raise ValueError(f"未知变换类型：{kind}")


def _predict_transformed_sentences(
    sentence: str, transformation: dict[str, Any]
) -> list[str]:
    """预测单个 base 句经变换后的句子序列（支持一句拆两句）。"""
    kind = transformation.get("type")
    if kind == "text_replace":
        result = sentence
        for replacement in transformation["replacements"]:
            find = replacement["find"]
            replace = replacement["replace"]
            if find in result:
                result = result.replace(find, replace)
            elif find.endswith(("。", "；", ";")) and find[:-1] in result:
                # find 带句尾标点而分句不含标点：用 replace 全文替换后拆句。
                result = result.replace(find[:-1], replace)
        pieces = split_sentences(result)
        return pieces or [result]
    if kind == "duplicate_sentence":
        target = transformation["target"].rstrip("。；;")
        if sentence == target:
            return [sentence, sentence]
        return [sentence]
    # 其余变换不改变单句句界。
    return [sentence]


def _changed_regions(
    base_text: str, transformation: dict[str, Any]
) -> frozenset[str]:
    """返回字段/结构允许发生预期变化的 base 锚点集合。"""
    kind = transformation.get("type")
    sentences = split_sentences(base_text)
    anchors = anchor_ids(base_text)
    if kind == "text_replace":
        changed: set[str] = set()
        for index, sentence in enumerate(sentences):
            for replacement in transformation["replacements"]:
                find = replacement["find"]
                if find in sentence or (
                    find.endswith(("。", "；", ";")) and find[:-1] in sentence
                ):
                    changed.add(anchors[index])
        return frozenset(changed)
    if kind == "duplicate_sentence":
        target = transformation["target"].rstrip("。；;")
        return frozenset(
            anchors[index]
            for index, sentence in enumerate(sentences)
            if sentence == target
        )
    return frozenset()


def _build_anchor_map(
    base_text: str,
    transformed_text: str,
    transformation: dict[str, Any],
) -> dict[str, list[str]]:
    """构建 base 锚点 → transformed 锚点的一对多映射（不依赖完整文本相等）。"""
    base_anchors = anchor_ids(base_text)
    transformed_anchors = anchor_ids(transformed_text)
    base_sentences = split_sentences(base_text)
    anchor_map: dict[str, list[str]] = {}
    used_transformed: set[int] = set()

    # 第 1 步：内容未变句子按锚点恒等匹配（含重复句 occurrence 顺序）。
    base_by_key: dict[str, list[int]] = {}
    for index, anchor in enumerate(base_anchors):
        base_by_key.setdefault(anchor, []).append(index)
    transformed_by_key: dict[str, list[int]] = {}
    for index, anchor in enumerate(transformed_anchors):
        transformed_by_key.setdefault(anchor, []).append(index)
    for key in sorted(set(base_by_key) & set(transformed_by_key)):
        for base_index, transformed_index in zip(
            base_by_key[key], transformed_by_key[key]
        ):
            anchor_map.setdefault(base_anchors[base_index], []).append(
                transformed_anchors[transformed_index]
            )
            used_transformed.add(transformed_index)

    # 第 2 步：剩余 base 句按变换预测匹配（支持一句拆两句）。
    remaining = [
        index for index in range(len(transformed_anchors)) if index not in used_transformed
    ]
    for base_index, sentence in enumerate(base_sentences):
        base_anchor = base_anchors[base_index]
        if base_anchor in anchor_map:
            continue
        predicted = _predict_transformed_sentences(sentence, transformation)
        predicted_anchors = [sentence_anchor(piece) for piece in predicted]
        matched: list[int] = []
        for predicted_anchor in predicted_anchors:
            for position, transformed_index in enumerate(remaining):
                candidate = transformed_anchors[transformed_index]
                if candidate == predicted_anchor or candidate.startswith(
                    predicted_anchor + "#"
                ):
                    matched.append(transformed_index)
                    remaining.pop(position)
                    break
        if len(matched) == len(predicted_anchors):
            anchor_map[base_anchor] = [
                transformed_anchors[index] for index in matched
            ]
    return anchor_map


def apply_transformation(
    text: str, transformation: dict[str, Any]
) -> TransformationResult:
    """应用确定性变换并返回带锚点映射与变化区域的结果。"""
    transformed_text = _apply_transform(text, transformation)
    anchor_map = _build_anchor_map(text, transformed_text, transformation)
    changed_regions = _changed_regions(text, transformation)
    return TransformationResult(
        text=transformed_text,
        transformation_type=transformation.get("type", "unknown"),
        anchor_map=anchor_map,
        changed_regions=changed_regions,
    )


def make_job(raw_text: str, source_file: str) -> JobDescription:
    """把合成输入包装成可抽取的 JobDescription（不持久化）。"""
    return JobDescription(
        id=0,
        source_hash=content_hash(raw_text),
        source_file=source_file,
        source_type="scenario",
        collected_at=date(2026, 8, 3),
        company="示例公司",
        title="示例岗位",
        company_type="test",
        tags=[],
        extra_metadata={},
        raw_text=raw_text,
    )


def scenario_set_fingerprint(scenarios: dict[str, Any]) -> str:
    """对规范化后的完整场景文件计算 SHA-256（含全部场景字段与 protocol_version）。"""
    canonical = json.dumps(
        scenarios, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def resolve_property_anchors(
    properties: dict[str, Any], base_text: str
) -> dict[str, Any]:
    """把场景属性中声明的锚点文本解析为稳定锚点 ID（找不到时保留原文）。"""
    resolved = copy.deepcopy(properties)
    for change in resolved.get("proficiency_expected_changes", []):
        if change.get("anchor"):
            change["anchor"] = (
                resolve_anchor(base_text, change["anchor"]) or change["anchor"]
            )
    experience = resolved.get("experience_to_unknown_expected_change")
    if isinstance(experience, dict) and experience.get("anchor"):
        experience["anchor"] = (
            resolve_anchor(base_text, experience["anchor"]) or experience["anchor"]
        )
    if resolved.get("group_change_anchor"):
        resolved["group_change_anchor"] = (
            resolve_anchor(base_text, resolved["group_change_anchor"])
            or resolved["group_change_anchor"]
        )
    return resolved


def parse_args() -> argparse.Namespace:
    """解析验收范围、运行次数、输出位置与付费调用确认参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=DEFAULT_SCENARIOS_PATH,
        help=f"规则场景文件，默认{DEFAULT_SCENARIOS_PATH.as_posix()}",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="base_input 独立运行次数（必须 >=3，稳定性按3次设计）",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="两段式每段有限重试次数（必须 >=1）",
    )
    parser.add_argument(
        "--run-tag",
        type=str,
        default=None,
        help="运行标识；缺省自动生成，保证每次结果独立文件不覆盖历史",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/P0-3"),
        help="脱敏验收报告目录（默认 reports/P0-3）",
    )
    parser.add_argument(
        "--raw-output-dir",
        type=Path,
        default=Path("data/private/experiments/p0_3"),
        help="原始运行结果目录（含完整输入与模型响应，私有）",
    )
    parser.add_argument(
        "--phase",
        type=str,
        choices=("pilot", "acceptance"),
        default="pilot",
        help="pilot：检查流程、收集指标，不产生批准结论；acceptance：使用已冻结的规则/范围/阈值，可用于批准当前版本",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做确定性预检（加载场景、应用变换、打印计划），不调用模型",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认发起付费模型调用（完整验收；默认拒绝）",
    )
    args = parser.parse_args()
    if args.runs < 3:
        parser.error("--runs 必须 >= 3")
    if args.max_attempts < 1:
        parser.error("--max-attempts 必须 >= 1")
    if args.execute and args.dry_run:
        parser.error("--execute 与 --dry-run 不能同时使用")
    return args


def _snapshot_payload(snapshot: RunSnapshot) -> dict[str, Any]:
    """把快照序列化为私有原始结果（含完整输入，仅写入私有目录）。"""
    return {
        "discovery": (
            snapshot.discovery.model_dump(mode="json")
            if snapshot.discovery is not None
            else None
        ),
        "result": snapshot.result.model_dump(mode="json"),
        "raw_text": snapshot.raw_text,
    }


def _run_extraction(
    client: OpenAICompatibleExtractionClient,
    raw_text: str,
    source_file: str,
    max_attempts: int,
) -> RunSnapshot:
    """使用当前唯一配置（v0.8 + Schema V3）执行一次两段式抽取。"""
    job = make_job(raw_text, source_file)
    discovery, result, raw_payload = extract_job_two_stage_with_discovery(
        job, client, max_attempts=max_attempts
    )
    return RunSnapshot(
        discovery=discovery,
        result=result,
        raw_text=raw_text,
        raw_payload=raw_payload,
    )


def _contract_of(
    snapshot: RunSnapshot,
    identity: dict[str, str],
    scenario_id: str,
) -> Any:
    """对一次运行执行完整合同检查（base 与 transformed 共用）。"""
    if snapshot.discovery is None:
        return None
    return check_contract(
        snapshot.discovery,
        snapshot.result,
        snapshot.raw_text,
        identity=identity,
        raw_payload=snapshot.raw_payload,
    )


def main() -> int:
    """加载场景、执行验收并写出脱敏报告与私有原始结果。"""
    args = parse_args()
    if not args.scenarios.exists():
        print(f"规则场景文件不存在：{args.scenarios}")
        return 1

    scenarios = json.loads(args.scenarios.read_text(encoding="utf-8"))
    scenario_list = scenarios.get("scenarios", [])
    if not scenario_list:
        print("规则场景文件没有场景。")
        return 1

    protocol_version = scenarios.get("protocol_version", "unknown")
    set_fingerprint = scenario_set_fingerprint(scenarios)

    if not args.execute:
        if not args.dry_run:
            print("未指定执行模式：完整验收需要 --execute（付费模型调用），"
                  "确定性预检使用 --dry-run。本次未调用模型。")
            return 2
        print("dry-run 预检（不调用模型）：")
        print(f"scenario_protocol_version={protocol_version}")
        print(f"scenario_set_fingerprint={set_fingerprint[:16]}")
        for scenario in scenario_list:
            result = apply_transformation(scenario["base_input"], scenario["transformation"])
            print(
                f"  {scenario['scenario_id']}: "
                f"base={len(scenario['base_input'])}字符 "
                f"transformed={len(result.text)}字符 "
                f"anchored_blocks={len(result.anchor_map)} "
                f"changed_regions={len(result.changed_regions)}"
            )
        return 0

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_identifier = args.run_tag or f"acceptance-{timestamp}"
    report_path = args.report_dir / f"{run_identifier}-report.json"
    raw_path = args.raw_output_dir / f"{run_identifier}-raw.json"

    client = OpenAICompatibleExtractionClient(settings)
    metadata = ExtractorMetadata(
        model_name=settings.model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )

    print(f"模型：{settings.model}")
    print(f"当前抽取配置：prompt={PROMPT_VERSION} schema={SCHEMA_VERSION}（v0.8 + Schema V3）")
    print(f"验收阶段：{args.phase}（acceptance 且 hard gates 全过时才 decision_eligible）")
    print(f"运行标识：{run_identifier}")
    print(f"场景数：{len(scenario_list)}；base 独立运行：{args.runs} 次")

    scenario_reports: list[dict[str, Any]] = []
    hard_gate_failures: list[str] = []
    warnings: list[str] = []
    diagnostics: list[str] = []
    raw_payload: dict[str, Any] = {}

    for scenario in scenario_list:
        scenario_id = scenario["scenario_id"]
        base_text = scenario["base_input"]
        transformation = apply_transformation(base_text, scenario["transformation"])
        transformed_text = transformation.text
        print(f"--- {scenario_id} ---")
        scenario_hard: list[str] = []
        scenario_warnings: list[str] = []
        scenario_diagnostics: list[str] = []

        # 运行完整性计数：任何缺失都属于 hard gate。
        expected_base_runs = args.runs
        expected_transformed_runs = 1
        successful_base_runs = 0
        failed_base_runs = 0
        successful_transformed_runs = 0
        failed_transformed_runs = 0

        base_identity = {
            "model": metadata.model_name,
            "prompt_version": metadata.prompt_version,
            "schema_version": metadata.schema_version,
            "input_fingerprint": compute_input_fingerprint(base_text),
        }
        transformed_identity = {
            "model": metadata.model_name,
            "prompt_version": metadata.prompt_version,
            "schema_version": metadata.schema_version,
            "input_fingerprint": compute_input_fingerprint(transformed_text),
        }

        # base 独立运行多次，每次快照独立保存（单次失败不中断其他场景）。
        base_snapshots: list[RunSnapshot] = []
        for index in range(expected_base_runs):
            try:
                snapshot = _run_extraction(
                    client, base_text, f"{scenario_id}_base.md", args.max_attempts
                )
                base_snapshots.append(snapshot)
                successful_base_runs += 1
                raw_payload[f"{scenario_id}_base_run{index}"] = _snapshot_payload(snapshot)
                print(
                    f"  base run{index}: 职责{len(snapshot.result.responsibilities)}项 "
                    f"要求{len(snapshot.result.requirements)}项"
                )
            except Exception as exc:  # 实验批处理保留单次错误并继续
                failed_base_runs += 1
                message = f"{scenario_id}: base run{index} 抽取失败：{exc}"
                scenario_hard.append(message)
                hard_gate_failures.append(message)
                raw_payload[f"{scenario_id}_base_run{index}"] = {"error": str(exc)}
                print(f"  base run{index} 失败: {exc}")

        # 变形输入运行一次。
        transformed_snapshot: RunSnapshot | None = None
        try:
            transformed_snapshot = _run_extraction(
                client,
                transformed_text,
                f"{scenario_id}_transformed.md",
                args.max_attempts,
            )
            successful_transformed_runs += 1
            raw_payload[f"{scenario_id}_transformed"] = _snapshot_payload(
                transformed_snapshot
            )
            print(
                f"  transformed: 职责{len(transformed_snapshot.result.responsibilities)}项 "
                f"要求{len(transformed_snapshot.result.requirements)}项"
            )
        except Exception as exc:
            failed_transformed_runs += 1
            message = f"{scenario_id}: transformed 抽取失败：{exc}"
            scenario_hard.append(message)
            hard_gate_failures.append(message)
            raw_payload[f"{scenario_id}_transformed"] = {"error": str(exc)}
            print(f"  transformed 失败: {exc}")

        # 运行完整性 hard gate：预期运行缺失。
        if successful_base_runs != expected_base_runs:
            message = (
                f"{scenario_id}: base 运行不完整 "
                f"expected={expected_base_runs} successful={successful_base_runs} "
                f"failed={failed_base_runs}"
            )
            scenario_hard.append(message)
            hard_gate_failures.append(message)
        if successful_transformed_runs != expected_transformed_runs:
            message = (
                f"{scenario_id}: transformed 运行不完整 "
                f"expected={expected_transformed_runs} "
                f"successful={successful_transformed_runs} "
                f"failed={failed_transformed_runs}"
            )
            scenario_hard.append(message)
            hard_gate_failures.append(message)

        # 每次 base 运行的完整合同检查（hard gate）。
        for index, snapshot in enumerate(base_snapshots):
            contract = _contract_of(snapshot, base_identity, scenario_id)
            if contract is None:
                scenario_hard.append(f"{scenario_id}: base run{index} discovery 缺失")
                continue
            scenario_hard.extend(contract_hard_gate_failures(contract))
            if contract.ambiguous_evidence:
                scenario_diagnostics.append(
                    f"{scenario_id}: base run{index} ambiguous_evidence="
                    f"{contract.ambiguous_evidence}"
                )

        # transformed 运行同样执行完整合同检查。
        if transformed_snapshot is not None:
            transformed_contract = _contract_of(
                transformed_snapshot, transformed_identity, scenario_id
            )
            if transformed_contract is None:
                scenario_hard.append(f"{scenario_id}: transformed discovery 缺失")
            else:
                scenario_hard.extend(
                    contract_hard_gate_failures(transformed_contract)
                )
                if transformed_contract.ambiguous_evidence:
                    scenario_diagnostics.append(
                        f"{scenario_id}: transformed ambiguous_evidence="
                        f"{transformed_contract.ambiguous_evidence}"
                    )

        # base 多次运行稳定性：第一版只作 warning，不预设阈值。
        stability_comparisons: list[Any] = []
        for first_index in range(len(base_snapshots)):
            for second_index in range(first_index + 1, len(base_snapshots)):
                if (
                    base_snapshots[first_index].discovery is None
                    or base_snapshots[second_index].discovery is None
                ):
                    continue
                stability_comparisons.append(
                    compare_runs(base_snapshots[first_index], base_snapshots[second_index])
                )
        for index, comparison in enumerate(stability_comparisons):
            if comparison.unmatched_item_count:
                scenario_warnings.append(
                    f"{scenario_id}: 稳定性 run{index} unmatched_item_count="
                    f"{comparison.unmatched_item_count}"
                )
            scenario_diagnostics.append(
                f"{scenario_id}: 稳定性 run{index} block_alignment_rate="
                f"{comparison.block_alignment_rate:.2%} "
                f"kind_agreement={comparison.kind_agreement:.2%} "
                f"group_membership_agreement={comparison.group_membership_agreement:.2%} "
                f"atomic_count_agreement={comparison.atomic_item_count_agreement}"
            )

        # 变形检查：base run0 vs 变形运行（使用 TransformationResult 锚点）。
        if base_snapshots and base_snapshots[0].discovery is not None:
            if transformed_snapshot is not None and transformed_snapshot.discovery is not None:
                comparison = compare_runs(
                    base_snapshots[0],
                    transformed_snapshot,
                    transformation=transformation,
                )
                resolved_properties = resolve_property_anchors(
                    scenario.get("expected_properties", {}), base_text
                )
                property_failures, property_warnings = check_scenario_properties(
                    comparison,
                    resolved_properties,
                    changed_regions=transformation.changed_regions,
                    variant_result=transformed_snapshot.result,
                )
                scenario_hard.extend(property_failures)
                scenario_warnings.extend(property_warnings)
                scenario_diagnostics.append(
                    f"{scenario_id}: 变形 unmatched={comparison.unmatched_item_count} "
                    f"new_conditions={comparison.new_condition_items} "
                    f"name_similarity={comparison.name_similarity:.2%} "
                    f"group_membership_agreement={comparison.group_membership_agreement:.2%} "
                    f"basic_to_advanced={comparison.basic_to_advanced_upgrades}"
                )
            else:
                scenario_hard.append(f"{scenario_id}: transformed discovery 缺失")
        elif base_snapshots:
            scenario_hard.append(f"{scenario_id}: base run0 discovery 缺失")

        scenario_reports.append(
            {
                "scenario_id": scenario_id,
                "rule_ids": scenario.get("rule_ids", []),
                "severity": scenario.get("severity", "medium"),
                "expected_base_runs": expected_base_runs,
                "successful_base_runs": successful_base_runs,
                "failed_base_runs": failed_base_runs,
                "expected_transformed_runs": expected_transformed_runs,
                "successful_transformed_runs": successful_transformed_runs,
                "failed_transformed_runs": failed_transformed_runs,
                "hard_gate_failures": sorted(set(scenario_hard)),
                "warnings": sorted(set(scenario_warnings)),
                "diagnostics": scenario_diagnostics,
            }
        )
        hard_gate_failures.extend(scenario_hard)
        warnings.extend(scenario_warnings)
        diagnostics.extend(scenario_diagnostics)

    identity = {
        "model": metadata.model_name,
        "phase": args.phase,
        "prompt_version": metadata.prompt_version,
        "schema_version": metadata.schema_version,
        "scenario_protocol_version": protocol_version,
        "scenario_set_fingerprint": set_fingerprint,
        "runs": str(args.runs),
        "max_attempts": str(args.max_attempts),
        "input_fingerprint": compute_input_fingerprint(
            "\n".join(scenario["base_input"] for scenario in scenario_list)
        ),
        "run_identifier": run_identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    hard_gate_failures = sorted(set(hard_gate_failures))
    warnings = sorted(set(warnings))
    diagnostics = sorted(set(diagnostics))
    # decision_eligible 在人工审计与阈值冻结真正接入前恒为 False：
    # 脚本只计算自动 hard gate（passed），最终批准由人工汇总步骤确认
    # （见 reports/templates/final-review.md）。
    decision_eligible = False
    payload = {
        "identity": identity,
        "phase": args.phase,
        "run_count": args.runs,
        "scenarios": scenario_reports,
        "hard_gate_failures": hard_gate_failures,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "passed": not hard_gate_failures,
        "decision_eligible": decision_eligible,
    }
    args.report_dir.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.raw_output_dir.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        json.dumps(raw_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n验收报告：{report_path}")
    print(f"原始结果：{raw_path}")
    print(
        f"hard_gate_failures={len(hard_gate_failures)}、"
        f"warnings={len(warnings)}、diagnostics={len(diagnostics)}"
    )
    for failure in hard_gate_failures:
        print(f"  [FAIL] {failure}")
    for warning in warnings:
        print(f"  [WARN] {warning}")
    return 1 if hard_gate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
