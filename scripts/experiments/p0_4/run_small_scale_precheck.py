"""P0-4 单次聚类归并：小规模预检入口。

从选定抽取器版本的实例池中按 requirement_id 升序取前 target_size 条，
调用`consolidate_with_correction`执行单次 LLM 聚类归并，记录模型请求的
实例数、输入输出字符数与耗时，并输出 P0-4 合同违规与事实统计。
必须显式`--execute`确认付费模型调用。

输出：脱敏验收指标写入`reports/P0-4/`（仅统计数字，不含真实证据）；
完整归并结果（含原始名称与证据）写入`data/private/experiments/P0-4/`。
"""

from __future__ import annotations

import argparse
import json
import time
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
    mapping_clusters,
    validate_contract,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.requirement_consolidation import (
    RequirementConsolidationInput,
)


class RecordingClient:
    """包装真实LLM客户端，记录每次请求的实例数、请求与响应规模、耗时。"""

    def __init__(self, inner: OpenAICompatibleConsolidationClient) -> None:
        """保存被包装客户端并初始化请求记录。"""
        self.inner = inner
        self.records: list[dict[str, object]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用真实客户端，并记录实例数、字符数与耗时。"""
        started_at = time.monotonic()
        try:
            payload = json.loads(user_prompt)
            instance_count: int | None = len(payload.get("requirements", []))
        except json.JSONDecodeError:
            # 重试修正提示不是纯JSON（追加了校验错误文本），实例数记为空。
            instance_count = None
        response = self.inner.complete(system_prompt, user_prompt)
        self.records.append(
            {
                "instance_count": instance_count,
                "request_chars": len(user_prompt),
                "response_chars": len(response),
                "response_head": response[:300],
                "elapsed_seconds": round(time.monotonic() - started_at, 2),
            }
        )
        return response


def build_precheck_input(
    selection, target_size: int = 75
) -> RequirementConsolidationInput:
    """按 JD 分层配额构造预检输入，保证全部选定 JD 进入预检。

    旧实现按 requirement_id 升序取前 N 条，新增 JD（ID 更大）会被
    截掉。分层配额：每份 JD 至少 1 条，其余按各 JD 实例数比例分配
    （余数按 JD 升序补齐），JD 内按 requirement_id 升序——确定性、
    可审计，且覆盖已有跨 JD 核心条件与新增 JD 的代表性要求。
    """
    occurrences = selection.consolidation_input.occurrences
    total = len(occurrences)
    if target_size > total:
        raise RuntimeError(
            f"预检输入无法构造{target_size}条（可选{total}条）"
        )
    by_job: dict[int, list] = {}
    for occurrence in occurrences:
        by_job.setdefault(occurrence.job_id, []).append(occurrence)
    job_ids = sorted(by_job)
    if target_size < len(job_ids):
        raise RuntimeError(
            f"target_size={target_size} 小于 JD 数 {len(job_ids)}，"
            "无法保证全部 JD 进入预检"
        )
    # 每 JD 配额 = max(1, floor(target * n_job / total))，余数按 JD
    # 升序逐份补 1，直至补满。
    quotas: dict[int, int] = {}
    remaining = target_size
    for job_id in job_ids:
        quota = max(1, target_size * len(by_job[job_id]) // total)
        quotas[job_id] = quota
        remaining -= quota
    for job_id in job_ids:
        if remaining <= 0:
            break
        quotas[job_id] += 1
        remaining -= 1
    if remaining != 0:
        raise RuntimeError(f"预检配额分配失败（余量 {remaining}）")

    chosen: list = []
    for job_id in job_ids:
        chosen.extend(
            sorted(by_job[job_id], key=lambda occurrence: occurrence.requirement_id)[
                : quotas[job_id]
            ]
        )
    return RequirementConsolidationInput(occurrences=chosen)


def precheck_selection_summary(
    selection, target_size: int = 75
) -> dict[str, object]:
    """预检选样摘要（可审计）：每 JD 配额与实际选中 ID 数。"""
    chosen = build_precheck_input(selection, target_size)
    by_job: dict[int, int] = {}
    for occurrence in chosen.occurrences:
        by_job[occurrence.job_id] = by_job.get(occurrence.job_id, 0) + 1
    return {
        "target_size": target_size,
        "selected_total": len(chosen.occurrences),
        "per_job_counts": dict(sorted(by_job.items())),
        "requirement_id_counts": len(
            {occurrence.requirement_id for occurrence in chosen.occurrences}
        ),
    }


def main() -> int:
    """解析参数、加载输入并执行小规模预检与合同验证。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认发起付费模型调用",
    )
    parser.add_argument(
        "--extractor-version",
        type=str,
        default=None,
        help="抽取器版本；缺省使用当前唯一配置 v0.10 + Schema V3",
    )
    parser.add_argument(
        "--job-ids",
        type=int,
        nargs="*",
        default=None,
        help="只预检指定JD；缺省为全部",
    )
    parser.add_argument(
        "--target-size",
        type=int,
        default=75,
        help="预检输入实例数",
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
        default=Path("reports/P0-4/precheck.json"),
        help="脱敏验收指标报告输出路径",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=Path("data/private/experiments/P0-4/precheck-result.json"),
        help="完整归并结果（含证据）输出路径",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
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

    engine = create_database_engine(args.database_url)
    try:
        try:
            # 只读入口：查询前验证数据库属于当前结构，旧库明确拒绝。
            assert_current_database_schema(engine)
            session_factory = create_session_factory(engine)
            with session_factory() as session:
                selection = load_consolidation_selection(
                    session,
                    job_ids=set(args.job_ids) if args.job_ids else None,
                    extractor_version=args.extractor_version,
                )
        except RuntimeError as exc:
            # 数据库结构门禁错误：旧库/残缺库才提示重建。
            print(f"预检无法开始：{exc}")
            print("请先备份 data/raw_jds/，删除非当前派生数据库并重新生成。")
            return 1
        except ValueError as exc:
            # 输入范围或抽取版本选择错误：不附加删除数据库建议。
            print(f"预检无法开始：{exc}")
            return 1
    finally:
        engine.dispose()

    precheck_input = build_precheck_input(selection, args.target_size)
    print(f"模型：{settings.model}")
    print(f"抽取器版本：{selection.extractor_version}")
    print(f"输入范围：{len(precheck_input.occurrences)}条实例")
    print("预计模型调用：1（canonical 聚类）")
    print("输出目标：仅统计指标（脱敏）；完整结果写入私有目录。")

    client = RecordingClient(
        OpenAICompatibleConsolidationClient(settings)
    )
    metadata = ConsolidatorMetadata(model_name=settings.model)
    print(f"归并器版本：{metadata.consolidator_version}")
    try:
        result, raw = consolidate_with_correction(
            precheck_input,
            client,
            max_attempts=args.max_attempts,
        )
    except (ConsolidationError, ValueError) as exc:
        print(f"预检失败：{exc}")
        diagnose_path = Path("data/private/experiments/P0-4/precheck-diagnose.json")
        diagnose_path.parent.mkdir(parents=True, exist_ok=True)
        diagnose_path.write_text(
            json.dumps(client.records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"请求诊断记录（含响应开头）：{diagnose_path}")
        return 1

    contract = validate_contract(
        result,
        expected_requirement_count=len(precheck_input.occurrences),
    )
    clusters = mapping_clusters(result)
    cluster_members: dict[str, list[int]] = {}
    for requirement_id, (canonical_id, _) in clusters.items():
        cluster_members.setdefault(canonical_id, []).append(requirement_id)
    member_counts = [len(ids) for ids in cluster_members.values()]
    report = {
        "consolidator_version": metadata.consolidator_version,
        "extractor_version": selection.extractor_version,
        "input_fingerprint": selection.input_fingerprint,
        "input_size": len(precheck_input.occurrences),
        "request_records": [
            {key: value for key, value in record.items() if key != "response_head"}
            for record in client.records
        ],
        "p0_4_contract": {
            "coverage": contract.coverage,
            "duplicate_mapping_count": contract.duplicate_mapping_count,
            "unknown_reference_count": contract.unknown_reference_count,
            "empty_cluster_count": contract.empty_cluster_count,
            "structural_violation_count": contract.structural_violation_count,
        },
        "p0_4_facts": {
            "canonical_count": len(cluster_members),
            "singleton_count": sum(1 for count in member_counts if count == 1),
            "max_cluster_size": max(member_counts, default=0),
        },
    }

    args.raw_output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.raw_output.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"P0-4 覆盖：{contract.coverage:.2%}，"
          f"结构违规：{contract.structural_violation_count}")
    print(f"P0-4 事实：标准项{len(cluster_members)}个、"
          f"singleton {sum(1 for count in member_counts if count == 1)}个、"
          f"最大cluster {max(member_counts, default=0)}")
    print(f"模型请求记录：{json.dumps(client.records, ensure_ascii=False)}")
    print(f"脱敏报告：{args.report}")
    print(f"原始结果：{args.raw_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
