"""正式抽取定稿：从已审核验收产物原子写入正式抽取表。"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.extraction import (
    ExtractorMetadata,
    extraction_result_fingerprint,
    rebuild_extraction_result,
)
from app.extraction_validation import compute_input_fingerprint
from app.finalization import validate_extraction_finalization_metadata
from app.models import JobDescription, JobExtraction, JobRequirement
from app.schemas import JobExtractionResult


def _identity_failures(
    report: dict,
    entry: dict,
    raw: dict,
    job: JobDescription,
    run_count: int,
) -> list[str]:
    failures: list[str] = []
    if report.get("passed") is not True:
        failures.append("报告顶层未通过（passed != true）")
    if report.get("hard_gate_failures"):
        failures.append(f"报告顶层存在 hard gate：{report['hard_gate_failures']}")
    expected_runs = entry.get("expected_runs")
    if (
        expected_runs is None
        or entry.get("successful_runs") is None
        or entry.get("failed_runs") is None
    ):
        failures.append("报告条目缺少 expected/successful/failed_runs")
    else:
        if entry["successful_runs"] != expected_runs:
            failures.append("运行不完整：successful_runs != expected_runs")
        if entry["failed_runs"] != 0:
            failures.append(f"存在失败运行：failed_runs={entry['failed_runs']}")
        if run_count != expected_runs:
            failures.append("raw 成功运行数与 expected_runs 不一致")
    report_identity = report.get("identity") or {}
    raw_identity = raw.get("identity") or {}
    for field in ("run_identifier", "model", "prompt_version", "schema_version"):
        if not report_identity.get(field) or not raw_identity.get(field):
            failures.append(f"整轮身份字段缺失：{field}")
        elif report_identity[field] != raw_identity[field]:
            failures.append(f"report 与 raw 的整轮身份不一致（{field}）")
    # 整轮 JD 集合一致性（新格式产物必查；旧产物缺字段时不强制）。
    report_job_ids = report_identity.get("job_ids")
    raw_job_ids = raw_identity.get("job_ids")
    if raw_job_ids is not None and (
        not isinstance(raw_job_ids, list) or job.id not in raw_job_ids
    ):
        failures.append("raw 整轮 JD 集合不包含定稿 JD")
    if report_job_ids is not None and report_job_ids != raw_job_ids:
        failures.append("report 与 raw 的整轮 JD 集合不一致")
    report_entry_job_ids = sorted(
        entry.get("job_id")
        for entry in report.get("jobs") or []
        if isinstance(entry, dict) and entry.get("job_id") is not None
    )
    if raw_job_ids is not None and report_entry_job_ids != sorted(raw_job_ids):
        failures.append("report jobs 的 JD 集合与 raw 整轮 JD 集合不一致")
    for field in ("runs", "max_attempts"):
        report_value = report_identity.get(field)
        raw_value = raw_identity.get(field)
        if report_value is not None and raw_value is not None and report_value != raw_value:
            failures.append(f"report 与 raw 的 {field} 不一致")
    for field in ("jd_set_fingerprint",):
        report_value = report_identity.get(field)
        raw_value = raw_identity.get(field)
        if report_value is not None and raw_value is not None and report_value != raw_value:
            failures.append(f"report 与 raw 的 {field} 不一致")
    if entry.get("input_fingerprint") != compute_input_fingerprint(job.raw_text):
        failures.append("报告条目输入指纹与 JD 原文不一致")
    return failures


def finalize_extraction(
    *,
    report_path: Path,
    raw_output_path: Path,
    job_id: int,
    run_index: int,
    database_url: str,
) -> int:
    """校验审核身份并定稿一份抽取；成功返回 0，拒绝返回 1。"""
    if not report_path.exists() or not raw_output_path.exists():
        print("报告或私有原始结果不存在。")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_output_path.read_text(encoding="utf-8"))
    entries = [
        entry for entry in report.get("jobs") or [] if entry.get("job_id") == job_id
    ]
    if not entries:
        print(f"报告不包含 JD {job_id} 的验收条目，拒绝定稿。")
        return 1
    entry = entries[0]
    review = entry.get("manual_review") or {}
    reviewed_at = review.get("reviewed_at")
    if entry.get("hard_gate_failures"):
        print(f"验收未通过，拒绝定稿：{entry['hard_gate_failures']}")
        return 1
    if not review.get("reviewed_by") or not reviewed_at:
        print("人工审核未完成，拒绝定稿。")
        return 1
    try:
        datetime.datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except ValueError:
        print(f"reviewed_at 格式无效：{reviewed_at}，拒绝定稿。")
        return 1
    if review.get("approved_run_index") != run_index:
        print("审核批准的运行与 --run-index 不一致，拒绝定稿。")
        return 1
    if not review.get("approved_result_fingerprint"):
        print("人工审核未记录 approved_result_fingerprint，拒绝定稿。")
        return 1

    run_key = f"job{job_id}_run{run_index}"
    run_payload = raw.get(run_key)
    if not isinstance(run_payload, dict) or "result" not in run_payload:
        print(f"raw 中不存在运行 {run_key}，拒绝定稿。")
        return 1
    if run_payload.get("run_identifier") != run_key:
        print("raw 运行标识不一致，拒绝定稿。")
        return 1
    try:
        result = JobExtractionResult.model_validate(run_payload["result"])
    except ValueError as exc:
        print(f"被批准运行的规范化结果不合法，拒绝定稿：{exc}")
        return 1
    run_fingerprint = run_payload.get("result_fingerprint") or (
        extraction_result_fingerprint(result)
    )
    if run_fingerprint != review["approved_result_fingerprint"]:
        print("被批准运行的结果指纹与审核记录不一致，拒绝定稿。")
        return 1

    engine = create_database_engine(database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = session.get(JobDescription, job_id)
            if job is None:
                print(f"JD 不存在：{job_id}")
                return 1
            run_count = sum(
                1
                for key, payload in raw.items()
                if key.startswith(f"job{job_id}_run")
                and isinstance(payload, dict)
                and "result" in payload
            )
            failures = _identity_failures(report, entry, raw, job, run_count)
            if failures:
                for failure in failures:
                    print(f"拒绝定稿：{failure}")
                return 1
            report_identity = report["identity"]
            metadata = ExtractorMetadata(
                model_name=report_identity["model"],
                prompt_version=report_identity["prompt_version"],
                schema_version=report_identity["schema_version"],
            )
            report_fingerprint = hashlib.sha256(report_path.read_bytes()).hexdigest()
            raw_fingerprint = hashlib.sha256(raw_output_path.read_bytes()).hexdigest()
            final_raw = dict(run_payload.get("raw_response") or {})
            final_raw.update(
                {
                    "approved_run_index": run_index,
                    "approved_result_fingerprint": review[
                        "approved_result_fingerprint"
                    ],
                    "reviewed_by": review["reviewed_by"],
                    "reviewed_at": reviewed_at,
                    "source_run_identifier": run_key,
                    "acceptance_run_identifier": report_identity["run_identifier"],
                    "result_fingerprint": run_fingerprint,
                    "report_fingerprint": report_fingerprint,
                    "raw_fingerprint": raw_fingerprint,
                }
            )
            missing = validate_extraction_finalization_metadata(final_raw)
            if missing:
                print(f"定稿元数据不完整：{missing}")
                return 1

            existing = session.scalar(
                select(JobExtraction).where(
                    JobExtraction.job_id == job.id,
                    JobExtraction.extractor_version == metadata.extractor_version,
                )
            )
            if existing is not None:
                existing_raw = existing.raw_response or {}
                problems = [
                    field
                    for field in (
                        "approved_run_index",
                        "approved_result_fingerprint",
                        "source_run_identifier",
                        "acceptance_run_identifier",
                        "report_fingerprint",
                        "raw_fingerprint",
                    )
                    if existing_raw.get(field) != final_raw.get(field)
                ]
                if extraction_result_fingerprint(
                    rebuild_extraction_result(existing)
                ) != run_fingerprint:
                    problems.append("result_fingerprint")
                if problems:
                    print(f"拒绝定稿：已有正式抽取与本次不同：{problems}")
                    return 1
                print(f"已有正式抽取（ID {existing.id}）与本次完全一致，幂等跳过写入。")
                return 0

            extraction = JobExtraction(
                job_id=job.id,
                extractor_version=metadata.extractor_version,
                model_name=metadata.model_name,
                prompt_version=metadata.prompt_version,
                schema_version=metadata.schema_version,
                role_family=result.role_family.value,
                seniority=result.seniority.value,
                raw_response=final_raw,
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
            if extraction_result_fingerprint(
                rebuild_extraction_result(extraction)
            ) != run_fingerprint:
                session.rollback()
                print("回读正式结果与批准结果不一致，已回滚，拒绝定稿。")
                return 1
            session.commit()
            print(f"正式抽取记录 ID：{extraction.id}（新建）")
            return 0
    finally:
        engine.dispose()
