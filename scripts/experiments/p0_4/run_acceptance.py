"""P0-4 事实归并真实验收：requirement instance → canonical requirement → unique mapping。

默认不调用付费模型；必须显式 `--execute`。抽取输入默认使用当前唯一配置
v0.8 + Schema V3；显式 `--extractor-version` 必须包含 schema:3.0，拒绝
非 Schema V3 数据。

执行流程（真实模型）：

1. 加载选定范围的完整输入（输入指纹固定，抽取器版本 = v0.8 + Schema V3）；
2. `--runs` 次独立非缓存运行（consolidate_with_correction，不写入正式批次）；
3. 变形测试：输入顺序打乱运行一次；
4. 指标：合同违规（coverage、重复映射、未知引用、空 cluster）、
   positive-pair Jaccard、canonical 数量漂移、singleton 比例漂移、
   顺序/分块变形结果；
5. 输出脱敏验收报告；原始运行结果（含证据）写入 data/private/。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from app.config import load_llm_settings
from app.consolidation import (
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    consolidate_with_correction,
    load_consolidation_selection,
)
from app.consolidation_validation import (
    build_acceptance_report,
    mapping_clusters,
    positive_pair_jaccard,
    singleton_and_canonical_drift,
    validate_contract,
    write_acceptance_report,
)
from app.database import create_database_engine, create_session_factory
from app.extraction import assert_current_extractor_version
from app.requirement_consolidation import RequirementConsolidationInput


def build_input(selection, job_ids: set[int] | None = None):
    """按requirement_id升序返回选定范围内的完整归并输入。"""
    occurrences = sorted(
        selection.consolidation_input.occurrences,
        key=lambda occurrence: occurrence.requirement_id,
    )
    return RequirementConsolidationInput(occurrences=occurrences)


def resolve_extractor_version(
    args_extractor_version: str | None, model_name: str
) -> str:
    """默认使用当前唯一抽取配置 v0.8 + Schema V3；显式输入严格校验版本身份。"""
    if args_extractor_version is None:
        from app.extraction import PROMPT_VERSION, SCHEMA_VERSION

        return f"{model_name}|prompt:{PROMPT_VERSION}|schema:{SCHEMA_VERSION}"
    try:
        assert_current_extractor_version(args_extractor_version)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    return args_extractor_version


def run_once(client_factory, consolidation_input, max_attempts):
    """执行一次独立归并运行，返回（result, metadata, raw）。"""
    client = client_factory()
    metadata = ConsolidatorMetadata(model_name=client.model_name)
    result, raw = consolidate_with_correction(
        consolidation_input,
        client,
        max_attempts=max_attempts,
    )
    return result, metadata, raw


class NamedClient:
    """包装真实LLM客户端并保留模型名称。"""

    def __init__(self, settings) -> None:
        """保存LLM配置并初始化内部客户端。"""
        self.model_name = settings.model
        self._inner = OpenAICompatibleConsolidationClient(settings)

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """委托给真实客户端。"""
        return self._inner.complete(system_prompt, user_prompt)


def evaluate_gates(
    runs: list[dict],
    contract_violations: list,
) -> tuple[list[str], list[str]]:
    """P0-4 门槛：合同违规与 positive-pair Jaccard 稳定性。"""
    hard_gate_failures: list[str] = []
    warnings: list[str] = []

    for index, contract in enumerate(contract_violations):
        if contract.coverage != 1.0:
            hard_gate_failures.append(f"run{index}: coverage={contract.coverage}")
        if contract.structural_violation_count != 0:
            hard_gate_failures.append(
                f"run{index}: structural_violations="
                f"{contract.structural_violation_count}"
            )

    for first_index in range(len(runs)):
        for second_index in range(first_index + 1, len(runs)):
            pair_jaccard = positive_pair_jaccard(
                mapping_clusters(runs[first_index]["result"]),
                mapping_clusters(runs[second_index]["result"]),
            )
            if pair_jaccard < 0.85:
                warnings.append(
                    f"run{first_index}-{second_index}: positive_pair_jaccard="
                    f"{pair_jaccard:.2%}"
                )
    return hard_gate_failures, warnings


def main() -> int:
    """解析参数并执行真实模型验收（P0-4 事实归并）。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认发起付费模型调用（默认拒绝）",
    )
    parser.add_argument(
        "--extractor-version",
        type=str,
        default=None,
        help="抽取器版本；缺省使用当前唯一配置 v0.8 + Schema V3；"
        "显式输入必须包含 schema:3.0，拒绝非 Schema V3 数据",
    )
    parser.add_argument(
        "--job-ids",
        type=int,
        nargs="*",
        default=None,
        help="只验收指定JD；缺省为全部",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="独立运行次数",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="每个阶段的有限重试次数",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/P0-4/acceptance-report.json"),
        help="脱敏验收报告输出路径",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL（测试与实验建议使用临时SQLite副本）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=Path("data/private/experiments/P0-4/acceptance-runs.json"),
        help="原始运行结果输出路径（含证据，私有）",
    )
    args = parser.parse_args()

    if not args.execute:
        print("必须显式--execute确认付费模型调用；本次未执行。")
        return 2

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1

    extractor_version = resolve_extractor_version(
        args.extractor_version, settings.model
    )

    engine = create_database_engine(args.database_url)
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            selection = load_consolidation_selection(
                session,
                job_ids=set(args.job_ids) if args.job_ids else None,
                extractor_version=extractor_version,
            )
    finally:
        engine.dispose()

    consolidation_input = build_input(selection)
    job_ids = sorted(selection.selected_job_ids)
    print(f"模型：{settings.model}")
    print(f"抽取器版本：{selection.extractor_version}")
    print(f"输入：{len(consolidation_input.occurrences)}条实例 / {len(job_ids)}份JD")
    print(f"计划模型调用：{args.runs}次独立运行 + 1次顺序变形")
    print("门槛：coverage=100%、结构违规=0；positive-pair Jaccard <85% 记入 warning")

    def make_client() -> NamedClient:
        return NamedClient(settings)

    runs: list[dict] = []
    for index in range(args.runs):
        print(f"--- 独立运行 {index + 1}/{args.runs} ---")
        result, metadata, raw = run_once(
            make_client,
            consolidation_input,
            args.max_attempts,
        )
        runs.append({"result": result, "metadata": metadata, "raw": raw})

    # 变形测试：输入顺序打乱（固定随机种子保证可复现）。
    print("--- 顺序变形运行 ---")
    shuffled_occurrences = list(consolidation_input.occurrences)
    random.Random(20260803).shuffle(shuffled_occurrences)
    shuffled_input = RequirementConsolidationInput(occurrences=shuffled_occurrences)
    order_result, _, _ = run_once(
        make_client, shuffled_input, args.max_attempts
    )

    contract_violations = [
        validate_contract(
            run["result"],
            expected_requirement_count=len(consolidation_input.occurrences),
        )
        for run in runs
    ]

    order_jaccard = positive_pair_jaccard(
        mapping_clusters(runs[0]["result"]), mapping_clusters(order_result)
    )
    order_contract = validate_contract(
        order_result,
        expected_requirement_count=len(consolidation_input.occurrences),
    )

    hard_gate_failures, warnings = evaluate_gates(runs, contract_violations)
    if order_jaccard < 0.85:
        warnings.append(f"order_transformation: positive_pair_jaccard={order_jaccard:.2%}")

    drift = singleton_and_canonical_drift(
        [mapping_clusters(run["result"]) for run in runs]
    )

    stability_metrics: list[dict] = []
    for first_index in range(len(runs)):
        for second_index in range(first_index + 1, len(runs)):
            stability_metrics.append(
                {
                    "pair": f"{first_index}-{second_index}",
                    "positive_pair_jaccard": round(
                        positive_pair_jaccard(
                            mapping_clusters(runs[first_index]["result"]),
                            mapping_clusters(runs[second_index]["result"]),
                        ),
                        4,
                    ),
                }
            )

    # 人工复核清单：所有多成员 cluster（不自动批准，由人检查合并是否正确）。
    cluster_members = []
    for index, run in enumerate(runs):
        members = mapping_clusters(run["result"])
        multi_member: dict[str, list[int]] = {}
        for requirement_id, (canonical_id, _) in members.items():
            if canonical_id in multi_member:
                multi_member[canonical_id].append(requirement_id)
            else:
                multi_member[canonical_id] = [requirement_id]
        cluster_members.append(
            {
                "run": index,
                "multi_member_clusters": [
                    {"canonical_id": canonical_id, "requirement_ids": ids}
                    for canonical_id, ids in sorted(multi_member.items())
                    if len(ids) > 1
                ],
            }
        )

    report = build_acceptance_report(
        input_identity={
            "model": settings.model,
            "prompt_version": runs[0]["metadata"].prompt_version,
            "schema_version": runs[0]["metadata"].schema_version,
            "extractor_version": selection.extractor_version,
            "input_fingerprint": selection.input_fingerprint,
            "instance_count": len(consolidation_input.occurrences),
            "job_count": len(job_ids),
        },
        contract=contract_violations[0],
        stability={
            "run_count": args.runs,
            "positive_pair_jaccard_pairs": stability_metrics,
            "canonical_count_min": drift["canonical_count_min"],
            "canonical_count_max": drift["canonical_count_max"],
            "singleton_ratios": drift["singleton_ratios"],
        },
        metamorphic={
            "order_transformation": {
                "positive_pair_jaccard": round(order_jaccard, 4),
                "coverage": order_contract.coverage,
                "structural_violations": order_contract.structural_violation_count,
            },
        },
        hard_gate_failures=hard_gate_failures,
        warnings=warnings,
        diagnostics=[
            f"canonical_count_range={drift['canonical_count_min']}"
            f"..{drift['canonical_count_max']}",
            f"singleton_ratios={drift['singleton_ratios']}",
        ],
    )
    # 人工复核记录：cluster 成员清单与人工检查确认字段。
    report["manual_cluster_review"] = {
        "clusters": cluster_members,
        "reviewed_by": None,
        "reviewed_at": None,
        "notes": "",
    }
    write_acceptance_report(report, args.report)
    print(f"验收报告已写入：{args.report}")
    print(f"hard_gate_failures={len(hard_gate_failures)} warnings={len(warnings)}")
    for failure in hard_gate_failures:
        print(f"  [GATE] {failure}")
    for warning in warnings:
        print(f"  [WARN] {warning}")

    # 原始运行结果（含证据）只写私有位置。
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(
            {
                "extractor_version": selection.extractor_version,
                "runs": [
                    {
                        "metadata": {
                            "model": run["metadata"].model_name,
                            "prompt_version": run["metadata"].prompt_version,
                            "schema_version": run["metadata"].schema_version,
                        },
                        "result": run["result"].model_dump(mode="json"),
                        "raw_response": run["raw"],
                    }
                    for run in runs
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0 if not hard_gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
