"""P0-3 真实 JD 验证脚本（Track B；协议见 docs/VALIDATION.md）。

与 Track A（`run_acceptance.py`，合成规则场景）分离的两条独立轨道。
抽取配置为当前唯一方案 v0.10 + Schema V3（三级熟练度）。默认不调用
外部模型；完整验证必须显式`--execute`，预检使用`--dry-run`。

执行流程（真实模型）：

1. 显式选择 JD ID 或 `--all`；
2. 每份 JD 独立运行 `--runs` 次（必须 >=3）；
3. 每次运行完整确定性合同检查（Schema、discovery coverage、evidence、
   candidate type coverage、logic groups、identity、evidence attribution）；
4. 同 JD 多次运行稳定性比较（candidate block alignment、item count drift、
   field agreement、group membership agreement）；
5. 报告 proficiency distribution（basic/advanced/unknown）与 requirement 总数；
6. 输出供人工规则审计的脱敏样本索引（不含原文）；
7. 真实原文与完整模型响应只写入 `data/private/`。

返回码：参数错误非零；`--dry-run` 返回 0（预检，不是验收）；
验收 hard gate 失败返回 1，通过返回 0。
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.config import load_llm_settings
from app.database import create_database_engine, create_session_factory, initialize_database
from app.extraction import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
    extraction_result_fingerprint,
)
from app.extraction_two_stage import extract_job_two_stage_with_discovery
from app.extraction_validation import (
    RunSnapshot,
    check_contract,
    compare_runs,
    compute_input_fingerprint,
    contract_hard_gate_failures,
)
from app.models import JobDescription


def parse_args() -> argparse.Namespace:
    """解析真实 JD 范围、运行次数与付费调用确认参数。"""
    parser = argparse.ArgumentParser(description=__doc__)
    database_group = parser.add_mutually_exclusive_group(required=True)
    database_group.add_argument(
        "--use-project-database",
        action="store_true",
        help="显式使用项目默认数据库",
    )
    database_group.add_argument(
        "--database-url",
        help="显式指定实验数据库URL，建议使用临时SQLite副本",
    )
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument(
        "--all",
        action="store_true",
        help="验收全部 JD",
    )
    scope_group.add_argument(
        "--job-ids",
        type=int,
        nargs="*",
        default=None,
        help="只验收指定 JD ID（可多个）",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="每份 JD 独立运行次数（必须 >=3）",
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
        help="运行标识；缺省自动生成，每次结果独立文件不覆盖历史",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/P0-3"),
        help="脱敏验收报告目录",
    )
    parser.add_argument(
        "--raw-output-dir",
        type=Path,
        default=Path("data/private/experiments/p0_3/real_jd"),
        help="原始运行结果目录（含完整 JD 与模型响应，私有）",
    )
    parser.add_argument(
        "--audit-sample-size",
        type=int,
        default=10,
        help="供人工审计的脱敏样本索引数量（每份 JD 按固定种子抽样）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只做确定性预检（加载 JD、打印计划），不调用模型",
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


def _run_extraction(
    client: OpenAICompatibleExtractionClient,
    job: JobDescription,
    max_attempts: int,
) -> RunSnapshot:
    """使用当前唯一配置（v0.10 + Schema V3）抽取一份真实 JD。"""
    discovery, result, raw_payload = extract_job_two_stage_with_discovery(
        job, client, max_attempts=max_attempts
    )
    return RunSnapshot(
        discovery=discovery,
        result=result,
        raw_text=job.raw_text,
        raw_payload=raw_payload,
    )


def _snapshot_payload(snapshot: RunSnapshot) -> dict[str, Any]:
    """把快照序列化为私有原始结果（含完整 JD，仅写入私有目录）。"""
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
    """执行真实 JD 验收并写出脱敏报告与私有原始结果。"""
    args = parse_args()
    if not args.execute and not args.dry_run:
        print("未指定执行模式：完整验收需要 --execute（付费模型调用），"
              "确定性预检使用 --dry-run。本次未调用模型。")
        return 2

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing and not args.dry_run:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1

    database_url = None if args.use_project_database else args.database_url
    engine = create_database_engine(database_url)
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            jobs_query = select(JobDescription).order_by(JobDescription.id)
            if args.job_ids:
                jobs_query = jobs_query.where(JobDescription.id.in_(args.job_ids))
            jobs = list(session.scalars(jobs_query))
    finally:
        engine.dispose()
    if not jobs:
        print("没有选中任何 JD。")
        return 1

    job_scope = "全部" if args.all else args.job_ids
    print(f"真实 JD 验收（Track B）：{len(jobs)} 份 JD（{job_scope}）")
    print(f"当前抽取配置：prompt={PROMPT_VERSION} schema={SCHEMA_VERSION}（v0.10 + Schema V3）")
    print(f"每份 JD 独立运行：{args.runs} 次")

    if not args.execute:
        for job in jobs:
            print(
                f"  dry-run: {job.source_file} "
                f"fingerprint={compute_input_fingerprint(job.raw_text)[:12]}"
            )
        return 0

    if missing:
        print(f"缺少LLM配置：{', '.join(missing)}")
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_identifier = args.run_tag or f"real-jd-acceptance-{timestamp}"
    report_path = args.report_dir / f"{run_identifier}-report.json"
    raw_path = args.raw_output_dir / f"{run_identifier}-raw.json"

    client = OpenAICompatibleExtractionClient(settings)
    metadata = ExtractorMetadata(
        model_name=settings.model,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
    )

    job_reports: list[dict[str, Any]] = []
    hard_gate_failures: list[str] = []
    warnings: list[str] = []
    diagnostics: list[str] = []
    # 整轮验收身份：report 与 raw 共用同一对象（含 job_ids 与 JD 集合
    # 指纹），定稿时逐字段核对，防止不同实验的产物混用（局部运行名
    # job4_run0 跨实验相同）或 report/raw 指向不同 JD 集合。
    acceptance_identity = {
        "run_identifier": run_identifier,
        "model": metadata.model_name,
        "prompt_version": metadata.prompt_version,
        "schema_version": metadata.schema_version,
        "job_ids": [job.id for job in jobs],
        "jd_set_fingerprint": compute_input_fingerprint(
            "\n".join(job.raw_text for job in jobs)
        ),
        "runs": str(args.runs),
        "max_attempts": str(args.max_attempts),
    }
    raw_payload: dict[str, Any] = {"identity": acceptance_identity}
    audit_samples: list[dict[str, Any]] = []

    for job in jobs:
        job_id = job.id
        expected_runs = args.runs
        successful_runs = 0
        failed_runs = 0
        snapshots: list[RunSnapshot] = []
        identity = {
            "model": metadata.model_name,
            "prompt_version": metadata.prompt_version,
            "schema_version": metadata.schema_version,
            "input_fingerprint": compute_input_fingerprint(job.raw_text),
        }
        for index in range(expected_runs):
            try:
                snapshot = _run_extraction(client, job, args.max_attempts)
                snapshots.append(snapshot)
                successful_runs += 1
                snapshot_payload = _snapshot_payload(snapshot)
                snapshot_payload["run_identifier"] = f"job{job_id}_run{index}"
                snapshot_payload["result_fingerprint"] = (
                    extraction_result_fingerprint(snapshot.result)
                )
                raw_payload[f"job{job_id}_run{index}"] = snapshot_payload
                print(
                    f"  {job.source_file} run{index}: "
                    f"要求{len(snapshot.result.requirements)}项"
                )
            except Exception as exc:  # 实验批处理保留单次错误并继续
                failed_runs += 1
                message = f"job{job_id}: run{index} 抽取失败：{exc}"
                hard_gate_failures.append(message)
                raw_payload[f"job{job_id}_run{index}"] = {"error": str(exc)}
                print(f"  {job.source_file} run{index} 失败: {exc}")
        if successful_runs != expected_runs:
            message = (
                f"job{job_id}: 运行不完整 expected={expected_runs} "
                f"successful={successful_runs} failed={failed_runs}"
            )
            hard_gate_failures.append(message)

        job_hard: list[str] = []
        job_diagnostics: list[str] = []
        proficiency_counts: Counter[str] = Counter()
        for index, snapshot in enumerate(snapshots):
            if snapshot.discovery is None:
                job_hard.append(f"job{job_id}: run{index} discovery 缺失")
                continue
            contract = check_contract(
                snapshot.discovery,
                snapshot.result,
                snapshot.raw_text,
                identity=identity,
                raw_payload=snapshot.raw_payload,
            )
            job_hard.extend(contract_hard_gate_failures(contract))
            for requirement in snapshot.result.requirements:
                proficiency_counts[requirement.proficiency.value] += 1
            if contract.ambiguous_evidence:
                job_diagnostics.append(
                    f"job{job_id}: run{index} ambiguous_evidence="
                    f"{contract.ambiguous_evidence}"
                )

        # 同 JD 多次运行稳定性（第一版只作 warning）。
        stability: list[dict[str, Any]] = []
        for first_index in range(len(snapshots)):
            for second_index in range(first_index + 1, len(snapshots)):
                if (
                    snapshots[first_index].discovery is None
                    or snapshots[second_index].discovery is None
                ):
                    continue
                comparison = compare_runs(
                    snapshots[first_index], snapshots[second_index]
                )
                if comparison.unmatched_item_count:
                    warnings.append(
                        f"job{job_id}: 稳定性 run{first_index}-{second_index} "
                        f"unmatched_item_count={comparison.unmatched_item_count}"
                    )
                stability.append(
                    {
                        "pair": f"{first_index}-{second_index}",
                        "block_alignment_rate": round(
                            comparison.block_alignment_rate, 4
                        ),
                        "kind_agreement": round(comparison.kind_agreement, 4),
                        "item_count_drift": comparison.unmatched_item_count,
                        "category_agreement": round(comparison.category_agreement, 4),
                        "importance_agreement": round(
                            comparison.importance_agreement, 4
                        ),
                        "proficiency_agreement": round(
                            comparison.proficiency_agreement, 4
                        ),
                        "group_membership_agreement": round(
                            comparison.group_membership_agreement, 4
                        ),
                        "evidence_attribution_unmatched": (
                            comparison.evidence_span_agreement
                        ),
                    }
                )

        # 脱敏审计样本索引：固定种子抽样，不含原文。
        if snapshots:
            rng = random.Random(job_id)
            candidates = list(enumerate(snapshots[0].result.requirements))
            sampled = rng.sample(candidates, min(args.audit_sample_size, len(candidates)))
            for position, _ in sampled:
                audit_samples.append(
                    {
                        "job_id": job_id,
                        "source_file": job.source_file,
                        "item_position": position,
                        "rule_ids": ["FIELD-03", "EVID-02"],
                        "reason": "人工规则审计样本（evidence semantic support 无法自动证明）",
                    }
                )

        job_reports.append(
            {
                "job_id": job_id,
                "source_file": job.source_file,
                "input_fingerprint": compute_input_fingerprint(job.raw_text),
                "expected_runs": expected_runs,
                "successful_runs": successful_runs,
                "failed_runs": failed_runs,
                "hard_gate_failures": sorted(set(job_hard)),
                "diagnostics": job_diagnostics,
                "stability": stability,
                "requirement_count": len(snapshots[0].result.requirements)
                if snapshots
                else 0,
                "proficiency_counts": dict(proficiency_counts),
                # 人工复核记录：抽取定稿时核对被批准运行，不得只凭
                # reviewed_by 放行任意运行。
                "manual_review": {
                    "reviewed_by": None,
                    "reviewed_at": None,
                    "approved_run_index": None,
                    "approved_result_fingerprint": None,
                    "conclusion": "",
                },
            }
        )
        hard_gate_failures.extend(job_hard)

    candidate_total = sum(
        report["requirement_count"] for report in job_reports
    )
    diagnostics.append(f"candidate_requirement_total={candidate_total}")
    diagnostics.append("p0_4_input_instances: 以 v0.10 抽取结果重新验收为准（只读统计，未执行归并）")

    hard_gate_failures = sorted(set(hard_gate_failures))
    warnings = sorted(set(warnings))
    diagnostics = sorted(set(diagnostics))
    payload = {
        "identity": dict(acceptance_identity),
        "track": "B",
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "jobs": job_reports,
        "audit_samples": audit_samples,
        "hard_gate_failures": hard_gate_failures,
        "warnings": warnings,
        "diagnostics": diagnostics,
        "passed": not hard_gate_failures,
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
