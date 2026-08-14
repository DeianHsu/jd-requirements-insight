"""P0-4 事实归并真实验收：requirement instance → canonical requirement → unique mapping。

默认不调用付费模型；必须显式 `--execute`。抽取输入默认使用当前唯一配置
v0.10 + Schema V3；显式 `--extractor-version` 必须包含 schema:3.0，拒绝
非 Schema V3 数据。

执行流程（真实模型）：

1. 加载选定范围的完整输入（输入指纹固定，抽取器版本 = v0.10 + Schema V3）；
2. `--runs` 次独立非缓存运行（consolidate_with_correction，不写入正式批次）；
3. 变形测试：输入顺序打乱运行一次；
4. 指标：合同违规（coverage、重复映射、未知引用、空 cluster）、
   positive-pair Jaccard、canonical 数量漂移、singleton 比例漂移、
   顺序变形结果；
5. 输出脱敏验收报告；原始运行结果（含证据）写入 data/private/。
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from app.config import load_llm_settings
from app.consolidation import (
    ConsolidationError,
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    consolidate_with_correction,
    load_consolidation_selection,
)
from app.consolidation_validation import (
    build_acceptance_report,
    mapping_clusters,
    positive_pair_jaccard,
    result_fingerprint,
    singleton_and_canonical_drift,
    validate_contract,
    validate_exact_identity,
    write_acceptance_report,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.extraction import assert_current_extractor_version
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementConsolidationResult,
)


ORDER_TRANSFORMATION_SEED = 20260803
ORDER_EXECUTION_FAILURE_PREFIX = "order_transformation: 聚类失败："


def build_input(selection, job_ids: set[int] | None = None):
    """按requirement_id升序返回选定范围内的完整归并输入。"""
    occurrences = sorted(
        selection.consolidation_input.occurrences,
        key=lambda occurrence: occurrence.requirement_id,
    )
    return RequirementConsolidationInput(occurrences=occurrences)


def resolve_extractor_version(args_extractor_version: str | None) -> str | None:
    """解析抽取版本参数；缺省返回 None 交由生产选择逻辑。

    缺省时（None）由 `load_consolidation_selection` 自动选择所选 JD 的
    唯一共同抽取版本并校验为 v0.10 + Schema V3（归并模型名不得参与拼接，
    抽取模型可能与归并模型不同）。只有用户显式提供版本时才提前严格校验。
    """
    if args_extractor_version is None:
        return None
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


def _same_path(first: Path, second: Path) -> bool:
    """比较尚未创建也可能存在的两个路径是否指向同一位置。"""
    return first.resolve(strict=False) == second.resolve(strict=False)


def _validate_order_resume_sources(
    *,
    report: dict,
    raw: dict,
    selection,
    settings,
    consolidation_input: RequirementConsolidationInput,
) -> list[RequirementConsolidationResult]:
    """强校验可复用验收产物，只接受单一 order 执行失败。"""
    hard_gate_failures = report.get("hard_gate_failures")
    if (
        not isinstance(hard_gate_failures, list)
        or len(hard_gate_failures) != 1
        or not isinstance(hard_gate_failures[0], str)
        or not hard_gate_failures[0].startswith(ORDER_EXECUTION_FAILURE_PREFIX)
    ):
        raise ValueError("原验收报告必须且只能包含一个 order execution hard gate")
    original_failure = hard_gate_failures[0]
    report_order = (report.get("metamorphic") or {}).get(
        "order_transformation"
    ) or {}
    raw_order = raw.get("order_transformation") or {}
    if report_order.get("failed") != original_failure:
        raise ValueError("report 的 order failure 与顶层 hard gate 不一致")
    if raw_order.get("failed") != original_failure:
        raise ValueError("raw 的 order failure 与 report 不一致")
    if raw_order.get("seed") != ORDER_TRANSFORMATION_SEED:
        raise ValueError("raw 的 order transformation seed 不符合当前合同")
    if "result" in raw_order or "raw_response" in raw_order:
        raise ValueError("原 raw 已包含成功 order result，不得按失败产物重试")

    expected_identity = {
        "input_fingerprint": selection.input_fingerprint,
        "extractor_version": selection.extractor_version,
        "selected_job_ids": sorted(selection.selected_job_ids),
        "model": settings.model,
        "prompt_version": ConsolidatorMetadata(
            model_name=settings.model
        ).prompt_version,
        "schema_version": ConsolidatorMetadata(
            model_name=settings.model
        ).schema_version,
    }
    report_identity = report.get("input_identity") or {}
    for field, expected in expected_identity.items():
        if raw.get(field) != expected:
            raise ValueError(f"raw 与当前输入身份不一致：{field}")
        if report_identity.get(field) != expected:
            raise ValueError(f"report 与当前输入身份不一致：{field}")
    if report_identity.get("instance_count") != len(
        consolidation_input.occurrences
    ):
        raise ValueError("report instance_count 与当前输入不一致")
    if report_identity.get("job_count") != len(selection.selected_job_ids):
        raise ValueError("report job_count 与当前输入不一致")

    runs = raw.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("raw 缺少可复用的 independent runs")
    if raw.get("run_count") != len(runs):
        raise ValueError("raw run_count 与 independent runs 数量不一致")
    if (report.get("p0_4_stability") or {}).get("run_count") != len(runs):
        raise ValueError("report run_count 与 raw 不一致")

    expected_ids = {
        occurrence.requirement_id
        for occurrence in consolidation_input.occurrences
    }
    results: list[RequirementConsolidationResult] = []
    run_identifiers: set[str] = set()
    for index, run in enumerate(runs):
        if not isinstance(run, dict):
            raise ValueError(f"raw run{index} 不是对象")
        identifier = run.get("run_identifier")
        if not isinstance(identifier, str) or not identifier:
            raise ValueError(f"raw run{index} 缺少 run_identifier")
        if identifier in run_identifiers:
            raise ValueError("raw independent run_identifier 重复")
        run_identifiers.add(identifier)
        metadata = run.get("metadata") or {}
        for field in ("model", "prompt_version", "schema_version"):
            if metadata.get(field) != expected_identity[field]:
                raise ValueError(f"raw run{index} metadata 不一致：{field}")
        try:
            result = RequirementConsolidationResult.model_validate(run["result"])
        except (KeyError, ValueError) as exc:
            raise ValueError(f"raw run{index} result 不合法：{exc}") from exc
        if run.get("result_fingerprint") != result_fingerprint(result):
            raise ValueError(f"raw run{index} result_fingerprint 不一致")
        contract = validate_contract(result, expected_ids=expected_ids)
        identity_failures = validate_exact_identity(result, expected_ids)
        if (
            contract.coverage != 1.0
            or contract.structural_violation_count != 0
            or identity_failures
        ):
            raise ValueError(f"raw run{index} 未通过完整归并合同")
        results.append(result)
    return results


def _resume_order_transformation(
    *,
    args,
    settings,
    selection,
    consolidation_input: RequirementConsolidationInput,
) -> int:
    """只重试失败的顺序变形，复用并保留既有 independent runs。"""
    source_report_path = args.retry_order_from_report
    source_raw_path = args.retry_order_from_raw
    if not source_report_path.exists() or not source_raw_path.exists():
        print("order-only resume 的原 report/raw 不存在。")
        return 1
    if args.raw_output is None:
        print("order-only resume 必须显式指定新的 --raw-output。")
        return 2
    output_paths = (args.report, args.raw_output)
    source_paths = (source_report_path, source_raw_path)
    if _same_path(args.report, args.raw_output):
        print("order-only resume 的 report/raw 输出路径不得相同。")
        return 2
    if any(_same_path(output, source) for output in output_paths for source in source_paths):
        print("order-only resume 不得覆盖原 report/raw。")
        return 2
    if args.report.exists() or args.raw_output.exists():
        print("order-only resume 输出文件已存在，拒绝覆盖。")
        return 2

    try:
        report = json.loads(source_report_path.read_text(encoding="utf-8"))
        raw = json.loads(source_raw_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"order-only resume 无法读取原验收产物：{exc}")
        return 1
    try:
        source_results = _validate_order_resume_sources(
            report=report,
            raw=raw,
            selection=selection,
            settings=settings,
            consolidation_input=consolidation_input,
        )
    except ValueError as exc:
        print(f"order-only resume 身份校验失败：{exc}")
        return 1

    shuffled_occurrences = list(consolidation_input.occurrences)
    random.Random(ORDER_TRANSFORMATION_SEED).shuffle(shuffled_occurrences)
    shuffled_input = RequirementConsolidationInput(
        occurrences=shuffled_occurrences
    )

    def make_client() -> NamedClient:
        return NamedClient(settings)

    warnings = [
        item
        for item in report.get("warnings") or []
        if not str(item).startswith("order_transformation:")
    ]
    try:
        order_result, order_metadata, order_raw = run_once(
            make_client, shuffled_input, args.max_attempts
        )
    except (ConsolidationError, ValueError) as exc:
        order_run_failed = f"{ORDER_EXECUTION_FAILURE_PREFIX}{exc}"
        hard_gate_failures = [order_run_failed]
        report.setdefault("metamorphic", {})["order_transformation"] = {
            "failed": order_run_failed
        }
        raw["order_transformation"] = {
            "seed": ORDER_TRANSFORMATION_SEED,
            "failed": order_run_failed,
        }
    else:
        expected_ids = {
            occurrence.requirement_id
            for occurrence in consolidation_input.occurrences
        }
        order_contract = validate_contract(order_result, expected_ids=expected_ids)
        order_jaccard = positive_pair_jaccard(
            mapping_clusters(source_results[0]), mapping_clusters(order_result)
        )
        hard_gate_failures = []
        if order_contract.coverage != 1.0:
            hard_gate_failures.append(
                f"order_transformation: coverage={order_contract.coverage}"
            )
        if order_contract.structural_violation_count != 0:
            hard_gate_failures.append(
                "order_transformation: structural_violations="
                f"{order_contract.structural_violation_count}"
            )
        if order_jaccard < 0.85:
            warnings.append(
                "order_transformation: positive_pair_jaccard="
                f"{order_jaccard:.2%}"
            )
        report.setdefault("metamorphic", {})["order_transformation"] = {
            "positive_pair_jaccard": round(order_jaccard, 4),
            "coverage": order_contract.coverage,
            "structural_violations": order_contract.structural_violation_count,
        }
        raw["order_transformation"] = {
            "seed": ORDER_TRANSFORMATION_SEED,
            "requirement_id_order": [
                occurrence.requirement_id for occurrence in shuffled_occurrences
            ],
            "metadata": {
                "model": order_metadata.model_name,
                "prompt_version": order_metadata.prompt_version,
                "schema_version": order_metadata.schema_version,
            },
            "result": order_result.model_dump(mode="json"),
            "raw_response": order_raw,
        }

    report["hard_gate_failures"] = hard_gate_failures
    report["warnings"] = warnings
    write_acceptance_report(report, args.report)
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"order-only resume 报告已写入：{args.report}")
    print(f"order-only resume raw 已写入：{args.raw_output}")
    print(f"hard_gate_failures={len(hard_gate_failures)}")
    return 0 if not hard_gate_failures else 1


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
        help="抽取器版本；缺省使用当前唯一配置 v0.10 + Schema V3；"
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
        help="单次聚类任务的有限重试次数",
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
        required=True,
        help="显式选择的数据库 URL（实验建议使用临时 SQLite 副本）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=None,
        help="显式指定原始运行结果输出路径（含证据，私有）",
    )
    parser.add_argument(
        "--retry-order-from-report",
        type=Path,
        default=None,
        help="只重试顺序变形时使用的原验收报告；必须与原 raw 同时提供",
    )
    parser.add_argument(
        "--retry-order-from-raw",
        type=Path,
        default=None,
        help="只重试顺序变形时使用的原始验收 raw；必须与原 report 同时提供",
    )
    args = parser.parse_args()

    retry_order_only = bool(args.retry_order_from_report) or bool(
        args.retry_order_from_raw
    )
    if bool(args.retry_order_from_report) != bool(args.retry_order_from_raw):
        print("--retry-order-from-report 与 --retry-order-from-raw 必须同时提供。")
        return 2

    if not args.execute:
        print("必须显式--execute确认付费模型调用；本次未执行。")
        return 2

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1

    extractor_version = resolve_extractor_version(args.extractor_version)

    engine = create_database_engine(args.database_url)
    try:
        try:
            # 查询前验证数据库属于当前结构；旧库/残缺库明确拒绝。
            assert_current_database_schema(engine)
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                selection = load_consolidation_selection(
                    session,
                    job_ids=set(args.job_ids) if args.job_ids else None,
                    extractor_version=extractor_version,
                )
        except RuntimeError as exc:
            # 数据库结构门禁错误：旧库/残缺库才提示重建。
            print(f"验收无法开始：{exc}")
            print("请先备份 data/raw_jds/，删除非当前派生数据库并重新生成。")
            return 1
        except ValueError as exc:
            # 输入范围或抽取版本选择错误：不附加删除数据库建议。
            print(f"验收无法开始：{exc}")
            return 1
    finally:
        engine.dispose()

    if args.raw_output is None:
        print("执行付费归并验收必须显式指定 --raw-output。")
        return 2

    consolidation_input = build_input(selection)
    job_ids = sorted(selection.selected_job_ids)
    if retry_order_only:
        return _resume_order_transformation(
            args=args,
            settings=settings,
            selection=selection,
            consolidation_input=consolidation_input,
        )

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
    random.Random(ORDER_TRANSFORMATION_SEED).shuffle(shuffled_occurrences)
    shuffled_input = RequirementConsolidationInput(occurrences=shuffled_occurrences)
    order_result = None
    order_metadata = None
    order_raw = None
    order_seed = ORDER_TRANSFORMATION_SEED
    try:
        order_result, order_metadata, order_raw = run_once(
            make_client, shuffled_input, args.max_attempts
        )
    except (ConsolidationError, ValueError) as exc:
        # 顺序变形运行自身失败也构成 hard gate（顺序敏感幻觉）。
        order_run_failed = f"order_transformation: 聚类失败：{exc}"
    else:
        order_run_failed = None

    contract_violations = [
        validate_contract(
            run["result"],
            expected_requirement_count=len(consolidation_input.occurrences),
        )
        for run in runs
    ]

    hard_gate_failures, warnings = evaluate_gates(runs, contract_violations)
    if order_run_failed is not None:
        # 顺序变形聚类失败直接进入 hard gate。
        hard_gate_failures.append(order_run_failed)
    else:
        order_jaccard = positive_pair_jaccard(
            mapping_clusters(runs[0]["result"]), mapping_clusters(order_result)
        )
        order_contract = validate_contract(
            order_result,
            expected_requirement_count=len(consolidation_input.occurrences),
        )
        # 顺序变形合同违规直接进入 hard gate（jaccard 低于阈值仍只作 warning）。
        if order_contract.coverage != 1.0:
            hard_gate_failures.append(
                f"order_transformation: coverage={order_contract.coverage}"
            )
        if order_contract.structural_violation_count != 0:
            hard_gate_failures.append(
                "order_transformation: structural_violations="
                f"{order_contract.structural_violation_count}"
            )
        if order_jaccard < 0.85:
            warnings.append(
                f"order_transformation: positive_pair_jaccard={order_jaccard:.2%}"
            )

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
            "selected_job_ids": sorted(job_ids),
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
            "order_transformation": (
                {
                    "positive_pair_jaccard": round(order_jaccard, 4),
                    "coverage": order_contract.coverage,
                    "structural_violations": order_contract.structural_violation_count,
                }
                if order_result is not None
                else {"failed": order_run_failed}
            ),
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
    # approved_run_index / approved_result_fingerprint 由人工审核时填写，
    # 定稿时核对被选运行，不得只凭 reviewed_by 放行任意 run。
    report["manual_cluster_review"] = {
        "clusters": cluster_members,
        "reviewed_by": None,
        "reviewed_at": None,
        "approved_run_index": None,
        "approved_result_fingerprint": None,
        "conclusion": "",
        "notes": "",
    }
    write_acceptance_report(report, args.report)
    print(f"验收报告已写入：{args.report}")
    print(f"hard_gate_failures={len(hard_gate_failures)} warnings={len(warnings)}")
    for failure in hard_gate_failures:
        print(f"  [GATE] {failure}")
    for warning in warnings:
        print(f"  [WARN] {warning}")

    # 原始运行结果（含证据）只写私有位置：独立运行 + 顺序变形运行
    # （模型身份、规范化结果、成功响应、输入顺序种子，供离线分析）。
    # 顶层身份字段与每 run 的 run_identifier / result_fingerprint 供
    # 定稿时与验收报告精确核对，拒绝报告与 raw 错配。
    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    raw_payload: dict[str, object] = {
        "extractor_version": selection.extractor_version,
        "input_fingerprint": selection.input_fingerprint,
        "selected_job_ids": sorted(job_ids),
        "model": runs[0]["metadata"].model_name,
        "prompt_version": runs[0]["metadata"].prompt_version,
        "schema_version": runs[0]["metadata"].schema_version,
        "run_count": len(runs),
        "runs": [
            {
                "run_identifier": f"run-{index}",
                "result_fingerprint": result_fingerprint(run["result"]),
                "metadata": {
                    "model": run["metadata"].model_name,
                    "prompt_version": run["metadata"].prompt_version,
                    "schema_version": run["metadata"].schema_version,
                },
                "result": run["result"].model_dump(mode="json"),
                "raw_response": run["raw"],
            }
            for index, run in enumerate(runs)
        ],
    }
    if order_result is not None and order_metadata is not None:
        raw_payload["order_transformation"] = {
            "seed": order_seed,
            "requirement_id_order": [
                occurrence.requirement_id for occurrence in shuffled_occurrences
            ],
            "metadata": {
                "model": order_metadata.model_name,
                "prompt_version": order_metadata.prompt_version,
                "schema_version": order_metadata.schema_version,
            },
            "result": order_result.model_dump(mode="json"),
            "raw_response": order_raw,
        }
    else:
        raw_payload["order_transformation"] = {
            "seed": order_seed,
            "failed": order_run_failed,
        }
    args.raw_output.write_text(
        json.dumps(raw_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if not hard_gate_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
