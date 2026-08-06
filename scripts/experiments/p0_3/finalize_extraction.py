"""从已完成的真实 JD 验收运行定稿正式抽取结果（不调用模型）。

解决“验收一份、生产再生成一份”的错位：验收脚本多次运行用于检查
稳定性，人工审核批准其中一份运行，本脚本把被批准运行的结构化结果
原样持久化为正式抽取（与验收结果完全一致，不重新调用模型）。

保证“被审核和验收的结果 = 最终持久化的结果”：

1. 读取脱敏验收报告：对应 JD 条目的 hard_gate_failures 必须为空，
   manual_review.reviewed_by / reviewed_at / approved_run_index /
   approved_result_fingerprint 必须有效；
2. 读取私有原始结果，选择 --run-index 指定的运行；
3. 核对报告与 raw 的身份（job_id、input_fingerprint、抽取器版本、
   model / prompt / schema、运行数量）；
4. 核对被批准运行的结果指纹（规范化抽取结果 sha256）；
5. 幂等安全门：已有正式抽取（同 JD + 同抽取器版本）只有在结果指纹
   与审核元数据完全一致时才允许复用；旧格式抽取缺审核元数据时拒绝
   无依据宣称一致；不同结果明确拒绝；
6. 使用现有持久化逻辑写入正式数据库（raw_response 记录审核元数据）；
7. 回读正式结果并逐项对比（数量、全部字段、evidence、逻辑组）。

本脚本不初始化 LLM 客户端、不接受 --execute、不调用任何模型。
"""
from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

from sqlalchemy import select

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.extraction import (
    extraction_result_fingerprint,
    rebuild_extraction_result,
)
from app.models import JobDescription, JobExtraction, JobRequirement
from app.schemas import JobExtractionResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="脱敏验收报告路径（run_real_jd_acceptance 输出）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        required=True,
        help="私有原始结果路径（含证据，不提交）",
    )
    parser.add_argument(
        "--job-id",
        type=int,
        required=True,
        help="定稿的 JD ID",
    )
    parser.add_argument(
        "--run-index",
        type=int,
        default=0,
        help="被批准运行索引（默认 0）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL",
    )
    return parser.parse_args()


def _validate_identity(
    report: dict,
    report_entry: dict,
    raw: dict,
    job: JobDescription,
    run_count: int,
) -> list[str]:
    """报告顶层、JD 条目与 raw 的整轮身份核对。

    覆盖：report 顶层通过、运行数严格一致、整轮 run identity 一致、
    JD 集合/版本/输入指纹一致、身份字段缺失拒绝。
    """
    failures: list[str] = []
    if report.get("passed") is not True:
        failures.append("报告顶层未通过（passed != true）")
    if report.get("hard_gate_failures"):
        failures.append(f"报告顶层存在 hard gate：{report['hard_gate_failures']}")
    if report_entry.get("job_id") != job.id:
        failures.append("报告条目 job_id 与 --job-id 不一致")

    expected_runs = report_entry.get("expected_runs")
    successful_runs = report_entry.get("successful_runs")
    failed_runs = report_entry.get("failed_runs")
    if expected_runs is None or successful_runs is None or failed_runs is None:
        failures.append("报告条目缺少 expected/successful/failed_runs")
    else:
        if successful_runs != expected_runs:
            failures.append(
                f"运行不完整：expected={expected_runs} "
                f"successful={successful_runs}"
            )
        if failed_runs != 0:
            failures.append(f"存在失败运行：failed_runs={failed_runs}")
        if run_count != expected_runs:
            failures.append(
                f"raw 成功运行数（{run_count}）与 expected_runs "
                f"（{expected_runs}）不一致"
            )
    raw_run_keys = [
        key
        for key in raw
        if key.startswith(f"job{job.id}_run")
    ]
    if len(raw_run_keys) != (expected_runs or 0):
        failures.append(
            f"raw 中该 JD 的运行记录数（{len(raw_run_keys)}）"
            f"与 expected_runs（{expected_runs}）不一致"
        )

    # 整轮验收身份：report 与 raw 必须共享同一个 acceptance run。
    report_identity = report.get("identity") or {}
    raw_identity = raw.get("identity") or {}
    if not raw_identity:
        failures.append("raw 顶层缺少整轮验收身份（identity）")
    else:
        for field in (
            "run_identifier",
            "model",
            "prompt_version",
            "schema_version",
        ):
            if not report_identity.get(field) or not raw_identity.get(field):
                failures.append(f"整轮身份字段缺失：{field}")
            elif report_identity.get(field) != raw_identity.get(field):
                failures.append(
                    f"report 与 raw 的整轮身份不一致（{field}）"
                )
        raw_job_ids = raw_identity.get("job_ids")
        if raw_job_ids != [job.id]:
            failures.append(
                f"raw 整轮 JD 集合（{raw_job_ids}）与定稿 JD 不一致"
            )

    # 输入指纹与 JD 原文一致（身份字段缺失拒绝，不填充默认值）。
    from app.extraction_validation import compute_input_fingerprint

    expected_fingerprint = compute_input_fingerprint(job.raw_text)
    if report_entry.get("input_fingerprint") != expected_fingerprint:
        failures.append("报告条目输入指纹与 JD 原文不一致")
    if not report_identity.get("model") or not report_identity.get(
        "prompt_version"
    ) or not report_identity.get("schema_version"):
        failures.append("报告顶层缺少 model/prompt/schema 身份字段")
    return failures


def main() -> int:
    args = parse_args()
    if not args.report.exists() or not args.raw_output.exists():
        print("报告或私有原始结果不存在。")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))

    entries = [
        entry for entry in report.get("jobs") or [] if entry.get("job_id") == args.job_id
    ]
    if not entries:
        print(f"报告不包含 JD {args.job_id} 的验收条目，拒绝定稿。")
        return 1
    entry = entries[0]
    if entry.get("hard_gate_failures"):
        print(f"验收未通过，拒绝定稿：{entry['hard_gate_failures']}")
        return 1
    review = entry.get("manual_review") or {}
    if not review.get("reviewed_by"):
        print("人工审核未完成（reviewed_by 为空），拒绝定稿。")
        return 1
    reviewed_at = review.get("reviewed_at")
    if not reviewed_at:
        print("人工审核未记录 reviewed_at，拒绝定稿。")
        return 1
    try:
        datetime.datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except ValueError:
        print(f"reviewed_at 格式无效：{reviewed_at}，拒绝定稿。")
        return 1
    if review.get("approved_run_index") is None:
        print("人工审核未指定 approved_run_index，拒绝定稿。")
        return 1
    if review.get("approved_run_index") != args.run_index:
        print(
            f"审核批准的运行 {review.get('approved_run_index')} 与 "
            f"--run-index {args.run_index} 不一致，拒绝定稿。"
        )
        return 1
    if review.get("approved_result_fingerprint") is None:
        print("人工审核未记录 approved_result_fingerprint，拒绝定稿。")
        return 1

    run_key = f"job{args.job_id}_run{args.run_index}"
    run_payload = raw.get(run_key)
    if not isinstance(run_payload, dict) or "result" not in run_payload:
        print(f"raw 中不存在运行 {run_key}，拒绝定稿。")
        return 1
    if run_payload.get("run_identifier") != run_key:
        print("raw 运行的 run_identifier 与预期不一致，拒绝定稿。")
        return 1

    try:
        result = JobExtractionResult.model_validate(run_payload["result"])
    except ValueError as exc:
        print(f"被批准运行的规范化结果不合法，拒绝定稿：{exc}")
        return 1
    run_fingerprint = run_payload.get("result_fingerprint")
    if run_fingerprint is None:
        run_fingerprint = extraction_result_fingerprint(result)
    if run_fingerprint != review.get("approved_result_fingerprint"):
        print("被批准运行的结果指纹与审核记录不一致，拒绝定稿。")
        return 1

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = session.scalar(
                select(JobDescription).where(JobDescription.id == args.job_id)
            )
            if job is None:
                print(f"JD 不存在：{args.job_id}")
                return 1
            run_count = sum(
                1
                for key, payload in raw.items()
                if key.startswith(f"job{args.job_id}_run")
                and isinstance(payload, dict)
                and "result" in payload
            )
            identity_failures = _validate_identity(
                report, entry, raw, job, run_count
            )
            if identity_failures:
                for failure in identity_failures:
                    print(f"拒绝定稿：{failure}")
                return 1

            from app.extraction import ExtractorMetadata

            report_identity = report.get("identity") or {}
            metadata = ExtractorMetadata(
                model_name=report_identity["model"],
                prompt_version=report_identity["prompt_version"],
                schema_version=report_identity["schema_version"],
            )

            # 来源文件指纹：正式抽取记录与验收产物完整绑定。
            import hashlib

            report_fingerprint = hashlib.sha256(
                args.report.read_bytes()
            ).hexdigest()
            raw_fingerprint = hashlib.sha256(
                args.raw_output.read_bytes()
            ).hexdigest()

            # 幂等安全门：已有正式抽取只有在结果、批准运行、来源实验、
            # 审核身份与文件指纹全部一致时才允许复用；否则明确拒绝。
            existing = session.scalar(
                select(JobExtraction).where(
                    JobExtraction.job_id == job.id,
                    JobExtraction.extractor_version == metadata.extractor_version,
                )
            )
            if existing is not None:
                problems: list[str] = []
                existing_raw = existing.raw_response or {}
                if existing_raw.get("approved_run_index") is None:
                    problems.append(
                        "已有正式抽取缺少审核元数据，无法验证一致性"
                    )
                else:
                    if existing_raw.get("approved_run_index") != args.run_index:
                        problems.append("已有正式抽取的批准运行与本次不同")
                    if existing_raw.get(
                        "approved_result_fingerprint"
                    ) != review.get("approved_result_fingerprint"):
                        problems.append(
                            "已有正式抽取的结果指纹与本次不同"
                            "（同一 JD 同一版本已存在不同结果）"
                        )
                    if existing_raw.get("source_run_identifier") != run_key:
                        problems.append(
                            "已有正式抽取的来源运行标识与本次不同"
                        )
                    if existing_raw.get(
                        "acceptance_run_identifier"
                    ) != report_identity.get("run_identifier"):
                        problems.append(
                            "已有正式抽取的来源验收实验与本次不同"
                        )
                    if existing_raw.get("report_fingerprint") != (
                        report_fingerprint
                    ):
                        problems.append("已有正式抽取的报告指纹与本次不同")
                    if existing_raw.get("raw_fingerprint") != raw_fingerprint:
                        problems.append("已有正式抽取的 raw 指纹与本次不同")
                if extraction_result_fingerprint(
                    rebuild_extraction_result(existing)
                ) != run_fingerprint:
                    problems.append(
                        "已有正式抽取回读结果与本次不同"
                        "（同一 JD 同一版本已存在不同结果）"
                    )
                if problems:
                    print("拒绝定稿（已有正式抽取未被修改）：")
                    for problem in problems:
                        print(f"  - {problem}")
                    return 1
                # 完全一致：幂等复用已有正式抽取。
                print(
                    f"已有正式抽取（ID {existing.id}）与本次完全一致，"
                    "幂等跳过写入。"
                )
                session.commit()
                extraction = existing
                created = False
                result_requirements = len(result.requirements)
                engine.dispose()
                print(f"正式抽取记录 ID：{extraction.id}（{'新建' if created else '已存在，幂等跳过'}）")
                print(f"JD {job.id}｜{job.source_file}")
                print(f"抽取器版本：{metadata.extractor_version}")
                print(f"来源运行：{run_key}（{review.get('reviewed_by')} 审核）")
                print(
                    f"要求数 {result_requirements}；回读对比一致；"
                    f"结果指纹 {run_fingerprint[:16]}…"
                )
                return 0

            # 事务原子性：写入并 flush → 回读重建 → 比较完整结果指纹 →
            # 校验审核元数据 → commit；任何失败 rollback（session 退出
            # 时未 commit 自动回滚，数据库保持定稿前状态）。
            raw_response = dict(run_payload.get("raw_response") or {})
            raw_response.update(
                {
                    "approved_run_index": args.run_index,
                    "approved_result_fingerprint": review.get(
                        "approved_result_fingerprint"
                    ),
                    "reviewed_by": review.get("reviewed_by"),
                    "reviewed_at": reviewed_at,
                    "source_run_identifier": run_key,
                    "acceptance_run_identifier": report_identity.get(
                        "run_identifier"
                    ),
                    "result_fingerprint": run_fingerprint,
                    "report_fingerprint": report_fingerprint,
                    "raw_fingerprint": raw_fingerprint,
                }
            )
            extraction = JobExtraction(
                job_id=job.id,
                extractor_version=metadata.extractor_version,
                model_name=metadata.model_name,
                prompt_version=metadata.prompt_version,
                schema_version=metadata.schema_version,
                role_family=result.role_family.value,
                seniority=result.seniority.value,
                raw_response=raw_response,
            )
            session.add(extraction)
            session.flush()
            extraction.requirements.extend(
                JobRequirement(
                    raw_name=item.raw_name,
                    category=item.category.value,
                    importance=item.importance.value,
                    proficiency=item.proficiency.value,
                    group_id=item.group_id,
                    group_logic=item.group_logic.value,
                    min_years=item.min_years,
                    max_years=item.max_years,
                    years_text=item.years_text,
                    evidence=item.evidence,
                    confidence=item.confidence,
                )
                for item in result.requirements
            )
            session.flush()
            # 回读重建并比较完整结果指纹；不一致则回滚（不 commit）。
            rebuilt = rebuild_extraction_result(extraction)
            rebuilt_fingerprint = extraction_result_fingerprint(rebuilt)
            if rebuilt_fingerprint != run_fingerprint:
                session.rollback()
                print("回读正式结果与批准结果不一致，已回滚，拒绝定稿。")
                return 1
            session.commit()
            created = True
    finally:
        engine.dispose()

    print(f"正式抽取记录 ID：{extraction.id}（{'新建' if created else '已存在，幂等跳过'}）")
    print(f"JD {job.id}｜{job.source_file}")
    print(f"抽取器版本：{metadata.extractor_version}")
    print(f"来源运行：{run_key}（{review.get('reviewed_by')} 审核）")
    print(
        f"要求数 {len(result.requirements)}；回读对比一致；"
        f"结果指纹 {run_fingerprint[:16]}…"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
