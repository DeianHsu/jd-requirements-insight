"""P0-3 抽取验收脚本（DEC-015 新协议）：规则符合性 + 稳定性 + 变形测试。

默认不调用外部模型；真实调用必须显式`--execute`。执行流程（真实模型）：

1. 加载规则场景（`data/rule_scenarios/extraction_metamorphic_cases.json`），
   对每个场景把 base_input 应用确定性变换得到变形输入；
2. 对 base_input 独立运行 `--runs` 次（默认 3），每次结果独立保存；
3. 对每个变形输入运行 1 次；
4. 对每次运行执行确定性合同检查（Schema、覆盖、证据、逻辑组、归属、身份）；
5. base 多次运行两两比较（稳定性，第一版只作 warning）；
6. base 与变形输入比较并检查场景期望属性（变形 hard gate）；
7. 输出机器可读报告（hard gate / warning / diagnostic 分级），
   记录 model、prompt version、schema version、input fingerprint、
   run identifier、timestamp；报告不输出完整 JD 文本。

本脚本不读取人工完整答案决定通过或失败（legacy Gold 见
evaluate_two_stage_results.py，仅作历史比较）。
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from app.config import load_llm_settings
from app.extraction import ExtractorMetadata, OpenAICompatibleExtractionClient
from app.extraction_two_stage import extract_job_two_stage_with_discovery
from app.extraction_validation import (
    RunSnapshot,
    build_acceptance_report,
    check_contract,
    check_scenario_properties,
    compare_runs,
    compute_input_fingerprint,
    contract_hard_gate_failures,
)
from app.ingestion import content_hash
from app.models import JobDescription

DEFAULT_SCENARIOS_PATH = Path(
    "data/rule_scenarios/extraction_metamorphic_cases.json"
)


def apply_transformation(text: str, transformation: dict[str, Any]) -> str:
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
        help="base_input 独立运行次数（稳定性按3次设计）",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=2,
        help="两段式每段有限重试次数",
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
        "--execute",
        action="store_true",
        help="确认发起付费模型调用（默认拒绝）",
    )
    return parser.parse_args()


def _run_extraction(
    client: OpenAICompatibleExtractionClient,
    raw_text: str,
    source_file: str,
    max_attempts: int,
) -> RunSnapshot:
    """执行一次两段式抽取并返回包含发现段与最终结果的可比较快照。"""
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

    if not args.execute:
        print("必须显式--execute确认付费模型调用；本次未执行。")
        print(f"已加载场景：{len(scenario_list)} 个（未调用模型）")
        for scenario in scenario_list:
            base = scenario["base_input"]
            transformed = apply_transformation(base, scenario["transformation"])
            print(
                f"  {scenario['scenario_id']}: "
                f"base={len(base)}字符 fingerprint={compute_input_fingerprint(base)[:12]} "
                f"transformed={len(transformed)}字符"
            )
        return 2

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
    metadata = ExtractorMetadata(model_name=settings.model)

    print(f"模型：{settings.model}")
    print(f"Prompt 版本：{metadata.prompt_version}")
    print(f"Schema 版本：{metadata.schema_version}")
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
        transformed_text = apply_transformation(base_text, scenario["transformation"])
        print(f"--- {scenario_id} ---")

        # base 独立运行多次，每次快照独立保存（单次失败不中断其他场景）。
        base_snapshots: list[RunSnapshot] = []
        for index in range(args.runs):
            try:
                snapshot = _run_extraction(
                    client, base_text, f"{scenario_id}_base.md", args.max_attempts
                )
                base_snapshots.append(snapshot)
                raw_payload[f"{scenario_id}_base_run{index}"] = _snapshot_payload(snapshot)
                print(
                    f"  base run{index}: 职责{len(snapshot.result.responsibilities)}项 "
                    f"要求{len(snapshot.result.requirements)}项"
                )
            except Exception as exc:  # 实验批处理保留单次错误并继续
                hard_gate_failures.append(f"{scenario_id}: base run{index} 抽取失败：{exc}")
                raw_payload[f"{scenario_id}_base_run{index}"] = {"error": str(exc)}
                print(f"  base run{index} 失败: {exc}")

        # 变形输入运行一次。
        transformed_snapshot: RunSnapshot | None = None
        try:
            transformed_snapshot = _run_extraction(
                client, transformed_text, f"{scenario_id}_transformed.md", args.max_attempts
            )
            raw_payload[f"{scenario_id}_transformed"] = _snapshot_payload(
                transformed_snapshot
            )
            print(
                f"  transformed: 职责{len(transformed_snapshot.result.responsibilities)}项 "
                f"要求{len(transformed_snapshot.result.requirements)}项"
            )
        except Exception as exc:
            hard_gate_failures.append(f"{scenario_id}: transformed 抽取失败：{exc}")
            raw_payload[f"{scenario_id}_transformed"] = {"error": str(exc)}
            print(f"  transformed 失败: {exc}")

        # 每次 base 运行的合同检查（hard gate）。
        scenario_hard: list[str] = []
        scenario_warnings: list[str] = []
        for index, snapshot in enumerate(base_snapshots):
            if snapshot.discovery is None:
                scenario_hard.append(f"{scenario_id}: base run{index} discovery 缺失")
                continue
            identity = {
                "model": metadata.model_name,
                "prompt_version": metadata.prompt_version,
                "schema_version": metadata.schema_version,
                "input_fingerprint": compute_input_fingerprint(base_text),
            }
            contract = check_contract(
                snapshot.discovery,
                snapshot.result,
                snapshot.raw_text,
                identity=identity,
                raw_payload=snapshot.raw_payload,
            )
            scenario_hard.extend(contract_hard_gate_failures(contract))

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
            diagnostics.append(
                f"{scenario_id}: 稳定性 run{index} block_alignment_rate="
                f"{comparison.block_alignment_rate:.2%} "
                f"kind_agreement={comparison.kind_agreement:.2%} "
                f"atomic_count_agreement={comparison.atomic_item_count_agreement}"
            )

        # 变形检查：base run0 vs 变形运行。
        if base_snapshots and base_snapshots[0].discovery is not None:
            if transformed_snapshot is not None and transformed_snapshot.discovery is not None:
                comparison = compare_runs(base_snapshots[0], transformed_snapshot)
                property_failures, property_warnings = check_scenario_properties(
                    comparison, scenario.get("expected_properties", {})
                )
                scenario_hard.extend(property_failures)
                scenario_warnings.extend(property_warnings)
                diagnostics.append(
                    f"{scenario_id}: 变形 unmatched={comparison.unmatched_item_count} "
                    f"new_conditions={comparison.new_condition_items} "
                    f"name_similarity={comparison.name_similarity:.2%} "
                    f"evidence_agreement={comparison.evidence_span_agreement:.2%}"
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
                "hard_gate_failures": sorted(set(scenario_hard)),
                "warnings": sorted(set(scenario_warnings)),
            }
        )
        hard_gate_failures.extend(scenario_hard)
        warnings.extend(scenario_warnings)

    identity = {
        "model": metadata.model_name,
        "prompt_version": metadata.prompt_version,
        "schema_version": metadata.schema_version,
        "input_fingerprint": compute_input_fingerprint(
            "\n".join(scenario["base_input"] for scenario in scenario_list)
        ),
        "run_identifier": run_identifier,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    report = build_acceptance_report(
        identity=identity,
        contract=None,
        comparisons=None,
        scenario_failures=None,
        run_count=args.runs,
    )
    report.hard_gate_failures = sorted(set(hard_gate_failures))
    report.warnings = sorted(set(warnings))
    report.diagnostics = sorted(set(diagnostics))

    payload = {
        "identity": report.identity,
        "run_count": report.run_count,
        "scenarios": scenario_reports,
        "hard_gate_failures": report.hard_gate_failures,
        "warnings": report.warnings,
        "diagnostics": report.diagnostics,
        "passed": report.passed,
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
        f"hard_gate_failures={len(report.hard_gate_failures)}、"
        f"warnings={len(report.warnings)}、diagnostics={len(report.diagnostics)}"
    )
    for failure in report.hard_gate_failures:
        print(f"  [FAIL] {failure}")
    for warning in report.warnings:
        print(f"  [WARN] {warning}")
    return 1 if report.hard_gate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
