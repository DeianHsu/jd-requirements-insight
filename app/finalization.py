"""正式结果定稿合同与来源审计。

模型候选本身不是正式结果。只有带有完整审核绑定的记录才允许被正式
消费；本模块集中定义该边界，供 finalize、报告和审计入口复用。
"""

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.consolidation_validation import (
    PersistedConsolidationResult,
    load_persisted_consolidation_result,
    result_fingerprint,
    validate_persisted_consistency,
)
from app.models import JobConsolidation, JobExtraction

EXTRACTION_FINALIZATION_FIELDS = (
    "approved_run_index",
    "approved_result_fingerprint",
    "reviewed_by",
    "reviewed_at",
    "acceptance_run_identifier",
    "source_run_identifier",
    "report_fingerprint",
    "raw_fingerprint",
)

CONSOLIDATION_FINALIZATION_FIELDS = (
    "review_decisions_fingerprint",
    "source_run_identifier",
    "reviewed_by",
    "reviewed_at",
    "approved_run_index",
    "approved_result_fingerprint",
    "final_result_fingerprint",
)

LEGACY_EXTRACTION_WAIVER_PATH = Path(
    "data/private/legacy-extraction-waiver.json"
)


def missing_finalization_fields(
    raw_response: dict[str, Any] | None,
    fields: tuple[str, ...],
) -> list[str]:
    """返回正式记录缺失的非空定稿元数据字段。"""
    payload = raw_response or {}
    return [field for field in fields if payload.get(field) in (None, "")]


def validate_extraction_finalization_metadata(
    raw_response: dict[str, Any] | None,
) -> list[str]:
    """验证正式抽取是否绑定完整验收、审核和来源身份。"""
    return missing_finalization_fields(
        raw_response, EXTRACTION_FINALIZATION_FIELDS
    )


def validate_legacy_extraction_waiver(
    path: Path,
    unbound_job_ids: set[int],
) -> dict[str, Any]:
    """校验私有历史抽取豁免是否明确覆盖待报告的未绑定 JD。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"private historical waiver 不存在：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"private historical waiver 无法读取或不是合法 JSON：{path}"
        ) from exc

    failures: list[str] = []
    if not isinstance(payload, dict):
        raise ValueError("private historical waiver 顶层必须是 JSON object")
    if payload.get("record_type") != "legacy_extraction_waiver":
        failures.append("record_type 必须为 legacy_extraction_waiver")
    if payload.get("schema_version") != 1:
        failures.append("schema_version 必须为 1")
    for field in ("approved_by", "approved_at", "reason", "risk", "status"):
        if not isinstance(payload.get(field), str) or not payload[field].strip():
            failures.append(f"{field} 必须是非空字符串")
    approved_at = payload.get("approved_at")
    if isinstance(approved_at, str) and approved_at.strip():
        try:
            datetime.date.fromisoformat(approved_at)
        except ValueError:
            failures.append("approved_at 必须是 ISO 日期")
    evidence = payload.get("existing_evidence")
    if not isinstance(evidence, list) or not evidence or not all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        failures.append("existing_evidence 必须是非空字符串列表")
    allowed_use = payload.get("allowed_use")
    if not isinstance(allowed_use, str) or "generate-report" not in allowed_use:
        failures.append("allowed_use 未明确允许 generate-report")
    if "unverified" not in str(payload.get("status", "")):
        failures.append("status 必须保留 unverified 分类")
    constraints = payload.get("constraints")
    if not isinstance(constraints, dict) or not isinstance(
        constraints.get("new_records"), str
    ) or not constraints["new_records"].strip():
        failures.append("constraints.new_records 必须明确新增记录边界")

    applicable = payload.get("applicable_records")
    raw_job_ids = applicable.get("job_ids") if isinstance(applicable, dict) else None
    covered_job_ids: set[int] = set()
    if not isinstance(raw_job_ids, list) or not raw_job_ids or not all(
        type(job_id) is int and job_id > 0 for job_id in raw_job_ids
    ):
        failures.append("applicable_records.job_ids 必须是非空正整数列表")
    else:
        covered_job_ids = set(raw_job_ids)
        if len(covered_job_ids) != len(raw_job_ids):
            failures.append("applicable_records.job_ids 不得重复")
        uncovered = sorted(unbound_job_ids - covered_job_ids)
        if uncovered:
            failures.append(f"未覆盖 non-fully-bound 来源 JD：{uncovered}")

    if failures:
        raise ValueError(
            "private historical waiver 无效：" + "；".join(failures)
        )
    return payload


def validate_consolidation_finalization(
    record: JobConsolidation,
    persisted: PersistedConsolidationResult,
) -> list[str]:
    """验证正式归并的完整审核绑定及持久化结果指纹。

    报告门禁与审计共用：批次必须带完整人工审核元数据（审核人/时间/
    批准运行/批准结果指纹/审核决定指纹）与最终结果指纹，缺任一字段
    或指纹与当前持久化结果不一致都失败；reviewed_at 必须可解析。
    """
    raw_response = record.raw_response or {}
    missing = missing_finalization_fields(
        raw_response, CONSOLIDATION_FINALIZATION_FIELDS
    )
    failures = [f"归并批次缺少定稿元数据：{field}" for field in missing]
    recorded_fingerprint = raw_response.get("final_result_fingerprint")
    if recorded_fingerprint != result_fingerprint(persisted.result):
        failures.append("定稿结果指纹与当前持久化归并结果不一致")
    reviewed_at = raw_response.get("reviewed_at")
    if reviewed_at:
        try:
            datetime.datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
        except ValueError:
            failures.append(f"reviewed_at 格式无效：{reviewed_at}")
    return failures


@dataclass(frozen=True)
class ExtractionAuditItem:
    """一份正式抽取的离线来源审计结果。"""

    extraction_id: int
    job_id: int
    extractor_version: str
    status: str
    missing_fields: tuple[str, ...]


def audit_extraction_sources(
    session_factory: sessionmaker,
) -> list[ExtractionAuditItem]:
    """只读分类正式抽取的来源绑定状态，不回填或修改数据。"""
    with session_factory() as session:
        records = list(
            session.scalars(select(JobExtraction).order_by(JobExtraction.id))
        )
    items: list[ExtractionAuditItem] = []
    for record in records:
        missing = tuple(
            validate_extraction_finalization_metadata(record.raw_response)
        )
        present_count = len(EXTRACTION_FINALIZATION_FIELDS) - len(missing)
        status = (
            "fully_bound"
            if not missing
            else "reviewed_unbound"
            if present_count
            else "unverified"
        )
        items.append(
            ExtractionAuditItem(
                extraction_id=record.id,
                job_id=record.job_id,
                extractor_version=record.extractor_version,
                status=status,
                missing_fields=missing,
            )
        )
    return items


def classify_batch_extraction_sources(
    session_factory: sessionmaker,
    extraction_ids: list[int],
) -> dict[int, str]:
    """按批次 extraction_ids 返回正式抽取的来源绑定状态（job_id -> status）。

    供报告门禁与审计复用：status 为 `unverified` / `reviewed_unbound`
    的上游表示该批次缺少可机器验证的来源绑定，消费方必须显式报告风险
    或提供结构化豁免，不能默认视为已绑定。
    """
    items = audit_extraction_sources(session_factory)
    by_id = {item.extraction_id: item for item in items}
    return {
        item.job_id: item.status
        for extraction_id in extraction_ids
        if (item := by_id.get(extraction_id)) is not None
    }


def audit_consolidation_identity(
    session_factory: sessionmaker,
    consolidation_id: int,
) -> dict[str, Any]:
    """只读返回归并批次的脱敏正式身份与门禁结论。"""
    persisted = load_persisted_consolidation_result(
        session_factory, consolidation_id
    )
    with session_factory() as session:
        record = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.id == consolidation_id
            )
        )
        if record is None:
            raise ValueError(f"归并批次不存在：{consolidation_id}")
        finalization_failures = validate_consolidation_finalization(
            record, persisted
        )
        consistency_failures = validate_persisted_consistency(persisted)
        raw_response = record.raw_response or {}
        return {
            "consolidation_id": record.id,
            "scope_key": record.scope_key,
            "selected_job_ids": list(record.selected_job_ids),
            "extraction_ids": list(record.extraction_ids),
            "extractor_version": record.extractor_version,
            "consolidator_version": record.consolidator_version,
            "input_fingerprint": record.input_fingerprint,
            "result_fingerprint": result_fingerprint(persisted.result),
            "review_decisions_fingerprint": raw_response.get(
                "review_decisions_fingerprint"
            ),
            "source_run_identifier": raw_response.get(
                "source_run_identifier"
            ),
            "occurrence_count": record.occurrence_count,
            "canonical_count": len(persisted.result.canonical_requirements),
            "mapping_count": len(persisted.result.mappings),
            "extraction_source_status": classify_batch_extraction_sources(
                session_factory, list(record.extraction_ids)
            ),
            "reportable": not finalization_failures
            and not consistency_failures,
            "failures": finalization_failures + consistency_failures,
        }
