"""模型候选产物生成：只写显式 JSON 文件，不写正式业务表。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.consolidation import (
    ConsolidationClient,
    ConsolidationSelection,
    ConsolidatorMetadata,
    consolidate_with_correction,
)
from app.consolidation_validation import result_fingerprint
from app.extraction import (
    ExtractionClient,
    ExtractorMetadata,
    extract_job,
    extraction_result_fingerprint,
)
from app.extraction_validation import compute_input_fingerprint
from app.models import JobDescription


def _write_new_json(path: Path, payload: dict[str, object]) -> None:
    """将私有候选写入新文件；拒绝静默覆盖已有运行产物。"""
    if path.exists():
        raise FileExistsError(f"候选输出已存在，拒绝覆盖：{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def write_extraction_candidates(
    jobs: list[JobDescription],
    client: ExtractionClient,
    metadata: ExtractorMetadata,
    output: Path,
    *,
    max_attempts: int = 2,
) -> dict[str, object]:
    """运行选定 JD 的抽取并写候选文件，不持久化正式抽取记录。"""
    if output.exists():
        raise FileExistsError(f"候选输出已存在，拒绝覆盖：{output}")
    run_identifier = datetime.now(timezone.utc).strftime(
        "extraction-candidate-%Y%m%dT%H%M%SZ"
    )
    runs: list[dict[str, object]] = []
    failures: list[dict[str, object]] = []
    for job in jobs:
        try:
            result, raw_response = extract_job(job, client, max_attempts=max_attempts)
        except ValueError as exc:
            failures.append(
                {
                    "job_id": job.id,
                    "source_file": job.source_file,
                    "message": str(exc),
                }
            )
            continue
        runs.append(
            {
                "run_identifier": f"job{job.id}_candidate0",
                "job_id": job.id,
                "source_file": job.source_file,
                "input_fingerprint": compute_input_fingerprint(job.raw_text),
                "result_fingerprint": extraction_result_fingerprint(result),
                "result": result.model_dump(mode="json"),
                "raw_response": raw_response,
            }
        )

    payload: dict[str, object] = {
        "artifact_type": "extraction_candidates",
        "run_identifier": run_identifier,
        "model": metadata.model_name,
        "prompt_version": metadata.prompt_version,
        "schema_version": metadata.schema_version,
        "extractor_version": metadata.extractor_version,
        "selected_job_ids": [job.id for job in jobs],
        "runs": runs,
        "failures": failures,
    }
    _write_new_json(output, payload)
    return payload


def write_consolidation_candidate(
    selection: ConsolidationSelection,
    client: ConsolidationClient,
    metadata: ConsolidatorMetadata,
    output: Path,
    *,
    max_attempts: int = 2,
) -> dict[str, object]:
    """运行一次归并并写候选文件，不持久化正式归并批次。"""
    if output.exists():
        raise FileExistsError(f"候选输出已存在，拒绝覆盖：{output}")
    result, raw_response = consolidate_with_correction(
        selection.consolidation_input, client, max_attempts=max_attempts
    )
    run_identifier = datetime.now(timezone.utc).strftime(
        "consolidation-candidate-%Y%m%dT%H%M%SZ"
    )
    payload: dict[str, object] = {
        "artifact_type": "consolidation_candidate",
        "run_identifier": run_identifier,
        "input_fingerprint": selection.input_fingerprint,
        "extractor_version": selection.extractor_version,
        "selected_job_ids": list(selection.selected_job_ids),
        "extraction_ids": list(selection.extraction_ids),
        "model": metadata.model_name,
        "prompt_version": metadata.prompt_version,
        "schema_version": metadata.schema_version,
        "consolidator_version": metadata.consolidator_version,
        "result_fingerprint": result_fingerprint(result),
        "result": result.model_dump(mode="json"),
        "raw_response": raw_response,
    }
    _write_new_json(output, payload)
    return payload
