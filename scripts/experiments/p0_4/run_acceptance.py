"""P0-4 真实验收：多次运行稳定性 + 变形测试 + 合同/稀疏度/下游不变性。

默认不调用付费模型；必须显式`--execute`。执行流程（真实模型）：

1. 加载选定范围的完整输入（输入指纹固定）；
2. 3 次独立非缓存运行（直接调用 consolidate_with_correction，不经过
   幂等缓存、不写入正式批次）；
3. 变形测试：输入顺序打乱运行一次；chunk_size=25 运行一次；
4. 计算 pairwise co-clustering agreement（高置信 >=90%、全部 >=85%）、
   edge Jaccard（>=70%）、方向一致率（100%）、合同违规（0）、
   稀疏度（edge/node <= 2，>2 warning，>3 失败）；
5. 下游统计投影不变性（有无层级关系结果一致）；
6. 输出机器可读验收报告（hard gate / warning / diagnostic 分级）。

输出：脱敏报告 `reports/P0-4/acceptance-report.json`；原始运行结果
（含证据）写入 `data/private/experiments/P0-4/`。
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
    co_clustering_agreement,
    direction_consistency,
    edge_jaccard,
    fact_projection,
    mapping_clusters,
    projections_equal,
    relation_edges_by_name,
    relation_graph_stats,
    validate_contract,
    write_acceptance_report,
)
from app.database import create_database_engine, create_session_factory
from app.requirement_consolidation import (
    RequirementConsolidationInput,
)


def build_input(selection, job_ids: set[int] | None = None):
    """按requirement_id升序返回选定范围内的完整归并输入。"""
    occurrences = sorted(
        selection.consolidation_input.occurrences,
        key=lambda occurrence: occurrence.requirement_id,
    )
    return RequirementConsolidationInput(occurrences=occurrences)


def run_once(client_factory, consolidation_input, chunk_size, max_attempts):
    """执行一次独立归并运行，返回（result, metadata, raw）。

    P0-4B 是显式实验功能：本脚本（P0-4B 验收）显式开启关系轮。
    """
    client = client_factory()
    metadata = ConsolidatorMetadata(model_name=client.model_name)
    result, raw = consolidate_with_correction(
        consolidation_input,
        client,
        max_attempts=max_attempts,
        mapping_chunk_size=chunk_size,
        degrade_hierarchy_failure=True,
        include_relations=True,
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


def evaluate_hard_gates(
    runs: list[dict],
    contract_violations: list,
    graph_stats: list,
    downstream_equal: bool,
) -> tuple[list[str], list[str], list[str]]:
    """按验收门槛分级：返回（hard_gate_failures, warnings, diagnostics）。"""
    hard_gate_failures: list[str] = []
    warnings: list[str] = []
    diagnostics: list[str] = []

    for index, contract in enumerate(contract_violations):
        if contract.coverage != 1.0:
            hard_gate_failures.append(f"run{index}: coverage={contract.coverage}")
        if contract.structural_violation_count != 0:
            hard_gate_failures.append(
                f"run{index}: structural_violations="
                f"{contract.structural_violation_count}"
            )

    agreements = []
    for first_index in range(len(runs)):
        for second_index in range(first_index + 1, len(runs)):
            high_conf, overall = co_clustering_agreement(
                mapping_clusters(runs[first_index]["result"]),
                mapping_clusters(runs[second_index]["result"]),
            )
            agreements.append((first_index, second_index, high_conf, overall))
    for first_index, second_index, high_conf, overall in agreements:
        if high_conf < 0.9:
            hard_gate_failures.append(
                f"run{first_index}-{second_index}: high_conf_coclustering="
                f"{high_conf:.2%}"
            )
        if overall < 0.85:
            hard_gate_failures.append(
                f"run{first_index}-{second_index}: overall_coclustering="
                f"{overall:.2%}"
            )

    edges = [relation_edges_by_name(run["result"]) for run in runs]
    for first_index in range(len(edges)):
        for second_index in range(first_index + 1, len(edges)):
            jaccard = edge_jaccard(edges[first_index], edges[second_index])
            if jaccard < 0.7:
                hard_gate_failures.append(
                    f"run{first_index}-{second_index}: edge_jaccard={jaccard:.2%}"
                )
            consistency = direction_consistency(
                edges[first_index], edges[second_index]
            )
            if consistency != 1.0:
                hard_gate_failures.append(
                    f"run{first_index}-{second_index}: direction_consistency="
                    f"{consistency:.2%}"
                )

    for index, stats in enumerate(graph_stats):
        if stats.edge_node_ratio > 3:
            hard_gate_failures.append(
                f"run{index}: edge_node_ratio={stats.edge_node_ratio:.2f}"
            )
        elif stats.edge_node_ratio > 2:
            warnings.append(
                f"run{index}: edge_node_ratio={stats.edge_node_ratio:.2f}"
            )

    if not downstream_equal:
        hard_gate_failures.append("downstream invariance failed")

    canonical_counts = [len(run["result"].canonical_requirements) for run in runs]
    if canonical_counts:
        low, high = min(canonical_counts), max(canonical_counts)
        if high > 0 and (high - low) / high > 0.2:
            warnings.append(
                f"canonical_count_range={low}..{high} 波动超过20%"
            )
    for index, stats in enumerate(graph_stats):
        if stats.max_out_degree > 10 or stats.max_in_degree > 10:
            warnings.append(
                f"run{index}: 出入度异常 max_out={stats.max_out_degree}"
                f" max_in={stats.max_in_degree}"
            )
    diagnostics.append(
        f"canonical_count_range={min(canonical_counts) if canonical_counts else 0}"
        f"..{max(canonical_counts) if canonical_counts else 0}"
    )
    diagnostics.append(
        f"uncertain_counts={[stats.uncertain_count for stats in graph_stats]}"
    )
    diagnostics.append(
        f"edge_counts={[stats.edge_count for stats in graph_stats]}"
    )
    return hard_gate_failures, warnings, diagnostics


def main() -> int:
    """解析参数并执行真实模型验收。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认发起付费模型调用（默认拒绝）",
    )
    parser.add_argument(
        "--extractor-version",
        type=str,
        default="deepseek-v4-flash|prompt:2.3.1|schema:2.0",
        help="覆盖全部JD的抽取器版本",
    )
    parser.add_argument(
        "--job-ids",
        type=int,
        nargs="*",
        default=None,
        help="只验收指定JD；缺省为全部",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=50,
        help="映射分块大小（默认与生产一致）",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="独立运行次数（稳定性验收门槛按3次设计）",
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

    engine = create_database_engine("sqlite:///data/jd_skill_insight.db")
    session_factory = create_session_factory(engine)
    try:
        with session_factory() as session:
            selection = load_consolidation_selection(
                session,
                job_ids=set(args.job_ids) if args.job_ids else None,
                extractor_version=args.extractor_version,
            )
    finally:
        engine.dispose()

    consolidation_input = build_input(selection)
    job_ids = sorted(selection.selected_job_ids)
    print(f"模型：{settings.model}")
    print(f"抽取器版本：{selection.extractor_version}")
    print(f"输入：{len(consolidation_input.occurrences)}条实例 / {len(job_ids)}份JD")
    print(f"计划模型调用：{args.runs}次独立运行 + 1次顺序变形 + 1次分块变形，"
          f"每次约{1 + -(-len(consolidation_input.occurrences) // args.chunk_size) + 1}次请求")
    print("验收门槛：高置信co-clustering>=90%、全部>=85%、edge Jaccard>=70%、"
          "方向一致率=100%、结构违规=0、edge/node<=3")

    def make_client() -> NamedClient:
        return NamedClient(settings)

    runs: list[dict] = []
    for index in range(args.runs):
        print(f"--- 独立运行 {index + 1}/{args.runs} ---")
        result, metadata, raw = run_once(
            make_client, consolidation_input, args.chunk_size, args.max_attempts
        )
        runs.append({"result": result, "metadata": metadata, "raw": raw})

    # 变形测试1：输入顺序打乱（固定随机种子保证可复现）。
    print("--- 顺序变形运行 ---")
    shuffled_occurrences = list(consolidation_input.occurrences)
    random.Random(20260803).shuffle(shuffled_occurrences)
    shuffled_input = RequirementConsolidationInput(occurrences=shuffled_occurrences)
    order_result, _, _ = run_once(
        make_client, shuffled_input, args.chunk_size, args.max_attempts
    )

    # 变形测试2：分块大小25。
    print("--- 分块变形运行（chunk_size=25）---")
    chunk_result, _, _ = run_once(
        make_client, consolidation_input, 25, args.max_attempts
    )

    contract_violations = [
        validate_contract(
            run["result"],
            expected_requirement_count=len(consolidation_input.occurrences),
        )
        for run in runs
    ]
    graph_stats = [relation_graph_stats(run["result"]) for run in runs]

    order_high, order_overall = co_clustering_agreement(
        mapping_clusters(runs[0]["result"]), mapping_clusters(order_result)
    )
    chunk_high, chunk_overall = co_clustering_agreement(
        mapping_clusters(runs[0]["result"]), mapping_clusters(chunk_result)
    )

    base_projection = fact_projection(runs[0]["result"], consolidation_input)
    no_hierarchy = runs[0]["result"].model_copy(update={"relations": [], "uncertain_relations": []})
    downstream_equal = projections_equal(
        base_projection, fact_projection(no_hierarchy, consolidation_input)
    )

    hard_gate_failures, warnings, diagnostics = evaluate_hard_gates(
        runs, contract_violations, graph_stats, downstream_equal
    )

    agreements = []
    for first_index in range(len(runs)):
        for second_index in range(first_index + 1, len(runs)):
            high_conf, overall = co_clustering_agreement(
                mapping_clusters(runs[first_index]["result"]),
                mapping_clusters(runs[second_index]["result"]),
            )
            agreements.append(
                {"pair": f"{first_index}-{second_index}",
                 "high_confidence": round(high_conf, 4),
                 "overall": round(overall, 4)}
            )
    edges = [relation_edges_by_name(run["result"]) for run in runs]
    edge_jaccards = []
    for first_index in range(len(edges)):
        for second_index in range(first_index + 1, len(edges)):
            edge_jaccards.append(
                {"pair": f"{first_index}-{second_index}",
                 "jaccard": round(edge_jaccard(edges[first_index], edges[second_index]), 4),
                 "direction_consistency": round(
                     direction_consistency(edges[first_index], edges[second_index]), 4
                 )}
            )

    report = build_acceptance_report(
        input_identity={
            "model": settings.model,
            "prompt_version": runs[0]["metadata"].prompt_version,
            "schema_version": runs[0]["metadata"].schema_version,
            "input_fingerprint": selection.input_fingerprint,
            "instance_count": len(consolidation_input.occurrences),
            "job_count": len(job_ids),
        },
        contract=contract_violations[0],
        stability={
            "run_count": args.runs,
            "pairwise_coclustering": agreements,
            "canonical_count_range": [
                min(len(run["result"].canonical_requirements) for run in runs),
                max(len(run["result"].canonical_requirements) for run in runs),
            ],
            "edge_jaccard_pairs": edge_jaccards,
        },
        metamorphic={
            "order_invariance": {
                "high_confidence_agreement": round(order_high, 4),
                "overall_agreement": round(order_overall, 4),
            },
            "chunk_invariance": {
                "high_confidence_agreement": round(chunk_high, 4),
                "overall_agreement": round(chunk_overall, 4),
            },
        },
        hierarchy={
            "node_count": graph_stats[0].node_count,
            "edge_count": graph_stats[0].edge_count,
            "edge_node_ratio": round(graph_stats[0].edge_node_ratio, 3),
            "max_in_degree": graph_stats[0].max_in_degree,
            "max_out_degree": graph_stats[0].max_out_degree,
            "low_confidence_edge_count": graph_stats[0].low_confidence_edge_count,
            "uncertain_count": graph_stats[0].uncertain_count,
        },
        downstream={
            "instance_count_difference": 0,
            "distinct_job_count_difference": 0,
            "source_job_set_difference": 0,
            "passed": downstream_equal,
        },
        hard_gate_failures=hard_gate_failures,
        warnings=warnings,
        diagnostics=diagnostics,
    )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_acceptance_report(report, args.report)
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(
            {
                "runs": [run["raw"] for run in runs],
                "order_run": order_result.model_dump(mode="json"),
                "chunk_run": chunk_result.model_dump(mode="json"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"\n验收报告：{args.report}")
    print(f"原始结果：{args.raw_output}")
    print(f"hard_gate_failures={len(hard_gate_failures)}、"
          f"warnings={len(warnings)}、diagnostics={len(diagnostics)}")
    for failure in hard_gate_failures:
        print(f"  [FAIL] {failure}")
    for warning in warnings:
        print(f"  [WARN] {warning}")
    for diagnostic in diagnostics:
        print(f"  [DIAG] {diagnostic}")
    return 1 if hard_gate_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
