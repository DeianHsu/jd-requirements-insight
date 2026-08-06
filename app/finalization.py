"""正式结果定稿合同与来源审计。

模型候选本身不是正式结果。只有带有完整审核绑定的记录才允许被正式
消费；本模块集中定义该边界，供 finalize、报告和审计入口复用。
"""

from __future__ import annotations

from dataclasses import dataclass
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


def validate_consolidation_finalization(
    record: JobConsolidation,
    persisted: PersistedConsolidationResult,
) -> list[str]:
    """验证正式归并的审核绑定及持久化结果指纹。"""
    raw_response = record.raw_response or {}
    missing = missing_finalization_fields(
        raw_response, CONSOLIDATION_FINALIZATION_FIELDS
    )
    failures = [f"归并批次缺少定稿元数据：{field}" for field in missing]
    recorded_fingerprint = raw_response.get("final_result_fingerprint")
    if recorded_fingerprint and recorded_fingerprint != result_fingerprint(
        persisted.result
    ):
        failures.append("定稿结果指纹与当前持久化归并结果不一致")
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
            "reportable": not finalization_failures
            and not consistency_failures,
            "failures": finalization_failures + consistency_failures,
        }
