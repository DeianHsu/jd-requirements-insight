"""正式归并定稿：从已审核验收产物写入正式归并表。"""

from __future__ import annotations

import datetime
import hashlib
import json
from pathlib import Path

from sqlalchemy import select

from app.consolidation import (
    ConsolidatorMetadata,
    load_consolidation_selection,
    persist_consolidation,
    scope_key_for,
)
from app.consolidation_validation import (
    is_placeholder_canonical_name,
    load_persisted_consolidation_result,
    result_fingerprint,
    validate_contract,
    validate_exact_identity,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.finalization import validate_consolidation_finalization
from app.models import JobConsolidation
from app.requirement_consolidation import RequirementConsolidationResult


def _raw_identity(raw: dict) -> dict[str, object]:
    runs = raw.get("runs") or []
    metadata = runs[0].get("metadata") or {} if runs else {}
    return {
        "input_fingerprint": raw.get("input_fingerprint"),
        "extractor_version": raw.get("extractor_version"),
        "selected_job_ids": sorted(raw.get("selected_job_ids") or []),
        "model": raw.get("model") or metadata.get("model"),
        "prompt_version": raw.get("prompt_version")
        or metadata.get("prompt_version"),
        "schema_version": raw.get("schema_version")
        or metadata.get("schema_version"),
        "run_count": raw.get("run_count")
        if raw.get("run_count") is not None
        else len(runs),
    }


def finalize_consolidation(
    *,
    report_path: Path,
    raw_output_path: Path,
    database_url: str,
    run_index: int = 0,
    final_result_path: Path | None = None,
    review_decisions_path: Path | None = None,
) -> int:
    """校验审核身份并定稿一个归并批次；不调用模型。"""
    if not report_path.exists() or not raw_output_path.exists():
        print("验收报告或私有原始结果不存在。")
        return 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    raw = json.loads(raw_output_path.read_text(encoding="utf-8"))
    if report.get("hard_gate_failures"):
        print("验收存在 hard gate，拒绝定稿。")
        return 1
    review = report.get("manual_cluster_review") or {}
    reviewed_at = review.get("reviewed_at")
    if not review.get("reviewed_by") or not reviewed_at:
        print("人工 cluster 审核未完成，拒绝定稿。")
        return 1
    try:
        datetime.datetime.fromisoformat(str(reviewed_at).replace("Z", "+00:00"))
    except ValueError:
        print("reviewed_at 格式无效，拒绝定稿。")
        return 1
    if review.get("approved_run_index") != run_index:
        print("审核批准运行与 --run-index 不一致，拒绝定稿。")
        return 1

    runs = raw.get("runs") or []
    if not 0 <= run_index < len(runs):
        print("run-index 超出运行范围，拒绝定稿。")
        return 1
    selected_run = runs[run_index]
    try:
        source_result = RequirementConsolidationResult.model_validate(
            selected_run["result"]
        )
    except (KeyError, ValueError) as exc:
        print(f"来源运行结果不合法，拒绝定稿：{exc}")
        return 1
    source_fingerprint = selected_run.get("result_fingerprint") or (
        result_fingerprint(source_result)
    )
    if source_fingerprint != review.get("approved_result_fingerprint"):
        print("来源运行指纹与审核记录不一致，拒绝定稿。")
        return 1

    raw_identity = _raw_identity(raw)
    report_identity = report.get("input_identity") or {}
    for field in (
        "input_fingerprint",
        "extractor_version",
        "model",
        "prompt_version",
        "schema_version",
    ):
        if raw_identity[field] in (None, ""):
            print(f"raw 缺少定稿身份字段（{field}），拒绝定稿。")
            return 1
    if not raw_identity["selected_job_ids"]:
        print("raw selected_job_ids 为空，拒绝定稿。")
        return 1
    for field in (
        "input_fingerprint",
        "extractor_version",
        "selected_job_ids",
        "model",
        "prompt_version",
        "schema_version",
    ):
        if report_identity.get(field) != raw_identity[field]:
            print(f"报告与 raw 身份不一致（{field}），拒绝定稿。")
            return 1
    if report.get("p0_4_stability", {}).get("run_count") != raw_identity[
        "run_count"
    ]:
        print("报告与 raw 运行数不一致，拒绝定稿。")
        return 1

    result = source_result
    review_fingerprint: str | None = None
    source_identifier = selected_run.get("run_identifier", f"run-{run_index}")
    if final_result_path is not None:
        if review_decisions_path is None:
            print("提供 --final-result 时必须同时提供 --review-decisions。")
            return 1
        if not final_result_path.exists() or not review_decisions_path.exists():
            print("最终结果或审核决定文件不存在。")
            return 1
        final_payload = json.loads(final_result_path.read_text(encoding="utf-8"))
        review_fingerprint = hashlib.sha256(
            review_decisions_path.read_bytes()
        ).hexdigest()
        if final_payload.get("source_run_identifier") != source_identifier:
            print("最终结果来源运行不一致，拒绝定稿。")
            return 1
        if final_payload.get("source_result_fingerprint") != source_fingerprint:
            print("最终结果来源指纹不一致，拒绝定稿。")
            return 1
        if final_payload.get("review_decisions_fingerprint") != review_fingerprint:
            print("最终结果审核决定指纹不一致，拒绝定稿。")
            return 1
        for field in (
            "input_fingerprint",
            "extractor_version",
            "model",
            "prompt_version",
            "schema_version",
        ):
            if final_payload.get(field) != raw_identity[field]:
                print(f"最终结果与 raw 身份不一致（{field}），拒绝定稿。")
                return 1
        if final_payload.get("selected_job_ids") != raw_identity[
            "selected_job_ids"
        ]:
            print("最终结果与 raw 的 selected_job_ids 不一致，拒绝定稿。")
            return 1
        try:
            result = RequirementConsolidationResult.model_validate(
                final_payload["result"]
            )
        except (KeyError, ValueError) as exc:
            print(f"最终结果不合法，拒绝定稿：{exc}")
            return 1
        if result_fingerprint(result) != final_payload.get("result_fingerprint"):
            print("最终结果指纹不一致，拒绝定稿。")
            return 1
    else:
        # 即使不需要额外裁决，也记录一个确定性审核绑定，防止候选冒充正式批次。
        review_fingerprint = hashlib.sha256(
            json.dumps(review, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    engine = create_database_engine(database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job_ids = set(raw_identity["selected_job_ids"])
            selection = load_consolidation_selection(
                session,
                job_ids=job_ids or None,
                extractor_version=str(raw_identity["extractor_version"]),
            )
            if selection.input_fingerprint != raw_identity["input_fingerprint"]:
                print("当前数据库输入与验收输入不一致，拒绝定稿。")
                return 1
            expected_ids = {
                occurrence.requirement_id
                for occurrence in selection.consolidation_input.occurrences
            }
            contract = validate_contract(result, expected_ids=expected_ids)
            identity_failures = validate_exact_identity(result, expected_ids)
            if contract.coverage != 1.0 or contract.structural_violation_count:
                print("归并合同未通过，拒绝定稿。")
                return 1
            if identity_failures:
                print(f"精确 ID 覆盖未通过，拒绝定稿：{identity_failures}")
                return 1
            if any(
                is_placeholder_canonical_name(item.canonical_name)
                for item in result.canonical_requirements
            ):
                print("最终结果包含占位名称，拒绝定稿。")
                return 1

            metadata = ConsolidatorMetadata(
                model_name=str(raw_identity["model"]),
                prompt_version=str(raw_identity["prompt_version"]),
                schema_version=str(raw_identity["schema_version"]),
            )
            scope_key = scope_key_for(job_ids or None)
            final_raw = dict(selected_run.get("raw_response") or {})
            final_raw.update(
                {
                    "normalized_result": result.model_dump(mode="json"),
                    "review_decisions_fingerprint": review_fingerprint,
                    "source_run_identifier": source_identifier,
                    "final_result_fingerprint": result_fingerprint(result),
                    "reviewed_by": review["reviewed_by"],
                    "reviewed_at": reviewed_at,
                    "approved_run_index": run_index,
                    "approved_result_fingerprint": source_fingerprint,
                }
            )
            existing = session.scalar(
                select(JobConsolidation).where(
                    JobConsolidation.scope_key == scope_key,
                    JobConsolidation.consolidator_version
                    == metadata.consolidator_version,
                    JobConsolidation.input_fingerprint
                    == selection.input_fingerprint,
                )
            )
            if existing is not None:
                persisted = load_persisted_consolidation_result(
                    session_factory, existing.id
                )
                failures = validate_consolidation_finalization(existing, persisted)
                if result_fingerprint(persisted.result) != result_fingerprint(result):
                    failures.append("已有正式批次结果与本次不同")
                if (existing.raw_response or {}).get(
                    "review_decisions_fingerprint"
                ) != review_fingerprint:
                    failures.append("已有正式批次审核决定与本次不同")
                if (existing.raw_response or {}).get(
                    "source_run_identifier"
                ) != source_identifier:
                    failures.append("已有正式批次来源运行与本次不同")
                if failures:
                    print(f"拒绝定稿：{failures}")
                    return 1
                print(f"归并批次 ID：{existing.id}（已存在，幂等跳过）")
                return 0
            batch, _ = persist_consolidation(
                session, selection, result, final_raw, metadata, scope_key
            )
            print(f"归并批次 ID：{batch.id}（新建）")
            return 0
    finally:
        engine.dispose()
