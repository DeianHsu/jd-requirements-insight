"""面向使用者的一键 MVP 流水线：自动硬门通过后直接正式化。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.consolidation import (
    ConsolidationClient,
    ConsolidatorMetadata,
    consolidate_with_correction,
    load_consolidation_selection,
    scope_key_for,
)
from app.consolidation_finalization import persist_approved_consolidation
from app.consolidation_validation import result_fingerprint
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.extraction import (
    ExtractionClient,
    ExtractorMetadata,
    extraction_result_fingerprint,
)
from app.extraction_finalization import (
    ApprovedExtraction,
    persist_approved_extractions,
)
from app.extraction_two_stage import extract_job_two_stage_with_discovery
from app.ingestion import import_directory
from app.market_analysis import build_market_statistics
from app.market_report import build_market_report, validate_report_inputs
from app.models import JobConsolidation, JobDescription, JobExtraction

AUTO_POLICY_VERSION = "auto-v1"
DEFAULT_RUNS_ROOT = Path("data/private/runs")


class AutoPipelineError(RuntimeError):
    """表示一键流水线在某个阶段安全停止。"""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class AutoPipelineResult:
    """一次完成或复用的一键运行结果。"""

    workspace: Path
    database_path: Path
    report_path: Path
    consolidation_id: int
    reused: bool


def input_directory_fingerprint(directory: Path) -> str:
    """按文件名和字节内容计算输入目录身份；仅接受当前导入范围的 Markdown。"""
    if not directory.exists():
        raise FileNotFoundError(f"JD目录不存在：{directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"JD路径不是目录：{directory}")
    files = sorted(directory.glob("*.md"))
    if not files:
        raise ValueError(f"JD目录中没有 Markdown 文件：{directory}")
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def workspace_for_input(
    directory: Path, runs_root: Path = DEFAULT_RUNS_ROOT
) -> Path:
    """返回同一输入固定复用的私有运行目录。"""
    return runs_root / input_directory_fingerprint(directory)


def _json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _fingerprint(payload: object) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_completed_result(
    workspace: Path, output: Path | None
) -> AutoPipelineResult | None:
    summary_path = workspace / "run-summary.json"
    if not summary_path.exists():
        return None
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if summary.get("status") != "completed":
        return None
    internal_report = workspace / "market-report.md"
    database_path = workspace / "pipeline.db"
    if not internal_report.exists() or not database_path.exists():
        return None
    report_path = output or internal_report
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(internal_report.read_text(encoding="utf-8"), encoding="utf-8")
    return AutoPipelineResult(
        workspace=workspace,
        database_path=database_path,
        report_path=report_path,
        consolidation_id=int(summary["consolidation_id"]),
        reused=True,
    )


def run_auto_pipeline(
    input_directory: Path,
    extraction_client: ExtractionClient,
    consolidation_client: ConsolidationClient,
    extraction_metadata: ExtractorMetadata,
    consolidation_metadata: ConsolidatorMetadata,
    *,
    max_attempts: int = 2,
    output: Path | None = None,
    runs_root: Path = DEFAULT_RUNS_ROOT,
) -> AutoPipelineResult:
    """运行最小一键闭环；失败写私有摘要并抛错，不降级成人工裁决。"""
    input_fingerprint = input_directory_fingerprint(input_directory)
    workspace = runs_root / input_fingerprint
    workspace.mkdir(parents=True, exist_ok=True)
    completed = _load_completed_result(workspace, output)
    if completed is not None:
        return completed

    summary_path = workspace / "run-summary.json"
    previous: dict[str, object] = {}
    if summary_path.exists():
        try:
            previous = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = {}
    started_at = str(previous.get("started_at") or datetime.now(timezone.utc).isoformat())
    base_summary: dict[str, object] = {
        "policy_version": AUTO_POLICY_VERSION,
        "input_fingerprint": input_fingerprint,
        "started_at": started_at,
    }
    _write_json(summary_path, {**base_summary, "status": "running", "stage": "import"})

    database_path = workspace / "pipeline.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)
    stage = "import"
    try:
        initialize_database(engine)
        imported = import_directory(input_directory, session_factory)
        if imported.failed:
            details = "; ".join(
                f"{item.source_file}: {item.message}" for item in imported.errors
            )
            raise AutoPipelineError(stage, f"JD 导入失败：{details}")
        with session_factory() as session:
            jobs = list(
                session.scalars(select(JobDescription).order_by(JobDescription.id))
            )
        if not jobs:
            raise AutoPipelineError(stage, "没有可分析的 JD")

        stage = "extraction"
        with session_factory() as session:
            existing_extractions = list(
                session.scalars(
                    select(JobExtraction).where(
                        JobExtraction.extractor_version
                        == extraction_metadata.extractor_version
                    )
                )
            )
        selected_ids = {job.id for job in jobs}
        existing_ids = {item.job_id for item in existing_extractions}
        if existing_ids and existing_ids != selected_ids:
            raise AutoPipelineError(
                stage, "检测到部分正式抽取，拒绝在不完整批次上继续"
            )

        if not existing_ids:
            approved: list[ApprovedExtraction] = []
            artifact_runs: list[dict[str, object]] = []
            for job in jobs:
                try:
                    discovery, result, raw_response = (
                        extract_job_two_stage_with_discovery(
                            job, extraction_client, max_attempts=max_attempts
                        )
                    )
                except ValueError as exc:
                    raise AutoPipelineError(
                        stage, f"JD {job.id} 抽取未通过硬门：{exc}"
                    ) from exc
                result_payload = result.model_dump(mode="json")
                result_fp = extraction_result_fingerprint(result)
                run_identifier = f"job{job.id}_auto0"
                decision = {
                    "approval_mode": "automatic",
                    "policy_version": AUTO_POLICY_VERSION,
                    "job_id": job.id,
                    "input_fingerprint": input_fingerprint,
                    "result_fingerprint": result_fp,
                }
                final_raw = dict(raw_response)
                final_raw.update(
                    {
                        "approval_mode": "automatic",
                        "policy_version": AUTO_POLICY_VERSION,
                        "selection_reason": "current contracts and hard gates passed",
                        "approved_run_index": 0,
                        "approved_result_fingerprint": result_fp,
                        "reviewed_by": f"system:{AUTO_POLICY_VERSION}",
                        "reviewed_at": started_at,
                        "acceptance_run_identifier": (
                            f"auto-extraction-{input_fingerprint}"
                        ),
                        "source_run_identifier": run_identifier,
                        "report_fingerprint": _fingerprint(decision),
                        "raw_fingerprint": _fingerprint(result_payload),
                        "result_fingerprint": result_fp,
                    }
                )
                approved.append(
                    ApprovedExtraction(
                        job_id=job.id,
                        result=result,
                        metadata=extraction_metadata,
                        finalization_metadata=final_raw,
                    )
                )
                artifact_runs.append(
                    {
                        "run_identifier": run_identifier,
                        "job_id": job.id,
                        "discovery": discovery.model_dump(mode="json"),
                        "result": result_payload,
                        "result_fingerprint": result_fp,
                        "approval": decision,
                    }
                )
            _write_json(
                workspace / "extraction-artifact.json",
                {
                    "artifact_type": "automatic_extraction",
                    "policy_version": AUTO_POLICY_VERSION,
                    "input_fingerprint": input_fingerprint,
                    "runs": artifact_runs,
                },
            )
            with session_factory() as session:
                try:
                    persist_approved_extractions(session, approved)
                    session.commit()
                except ValueError as exc:
                    session.rollback()
                    raise AutoPipelineError(stage, str(exc)) from exc

        stage = "consolidation"
        with session_factory() as session:
            selection = load_consolidation_selection(
                session,
                job_ids=selected_ids,
                extractor_version=extraction_metadata.extractor_version,
            )
            existing_batch = session.scalar(
                select(JobConsolidation).where(
                    JobConsolidation.scope_key == scope_key_for(selected_ids),
                    JobConsolidation.consolidator_version
                    == consolidation_metadata.consolidator_version,
                    JobConsolidation.input_fingerprint == selection.input_fingerprint,
                )
            )
        if existing_batch is None:
            try:
                consolidation_result, consolidation_raw = consolidate_with_correction(
                    selection.consolidation_input,
                    consolidation_client,
                    max_attempts=max_attempts,
                )
            except ValueError as exc:
                raise AutoPipelineError(
                    stage, f"归并未通过硬门：{exc}"
                ) from exc
            consolidation_fp = result_fingerprint(consolidation_result)
            source_identifier = "consolidation-auto0"
            decision = {
                "approval_mode": "automatic",
                "policy_version": AUTO_POLICY_VERSION,
                "input_fingerprint": selection.input_fingerprint,
                "result_fingerprint": consolidation_fp,
            }
            final_raw = dict(consolidation_raw)
            final_raw.update(
                {
                    "approval_mode": "automatic",
                    "policy_version": AUTO_POLICY_VERSION,
                    "selection_reason": "exact coverage and structural contracts passed",
                    "review_decisions_fingerprint": _fingerprint(decision),
                    "source_run_identifier": source_identifier,
                    "final_result_fingerprint": consolidation_fp,
                    "reviewed_by": f"system:{AUTO_POLICY_VERSION}",
                    "reviewed_at": started_at,
                    "approved_run_index": 0,
                    "approved_result_fingerprint": consolidation_fp,
                }
            )
            _write_json(
                workspace / "consolidation-artifact.json",
                {
                    "artifact_type": "automatic_consolidation",
                    "policy_version": AUTO_POLICY_VERSION,
                    "input_fingerprint": selection.input_fingerprint,
                    "result": consolidation_result.model_dump(mode="json"),
                    "result_fingerprint": consolidation_fp,
                    "approval": decision,
                },
            )
            with session_factory() as session:
                try:
                    batch, _ = persist_approved_consolidation(
                        session,
                        session_factory,
                        selection,
                        consolidation_result,
                        final_raw,
                        consolidation_metadata,
                        scope_key_for(selected_ids),
                    )
                except ValueError as exc:
                    raise AutoPipelineError(stage, str(exc)) from exc
            consolidation_id = batch.id
        else:
            consolidation_id = existing_batch.id

        stage = "report"
        failures = validate_report_inputs(session_factory, consolidation_id)
        if failures:
            raise AutoPipelineError(
                stage, "报告输入门禁未通过：" + "; ".join(failures)
            )
        stats = build_market_statistics(session_factory, consolidation_id)
        report_text = build_market_report(stats)
        internal_report = workspace / "market-report.md"
        internal_report.write_text(report_text, encoding="utf-8")
        report_path = output or internal_report
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(report_text, encoding="utf-8")
        completed_at = datetime.now(timezone.utc).isoformat()
        _write_json(
            summary_path,
            {
                **base_summary,
                "status": "completed",
                "stage": "completed",
                "completed_at": completed_at,
                "job_count": len(jobs),
                "requirement_count": stats.occurrence_count,
                "canonical_count": stats.canonical_count,
                "consolidation_id": consolidation_id,
                "report": internal_report.name,
            },
        )
        return AutoPipelineResult(
            workspace=workspace,
            database_path=database_path,
            report_path=report_path,
            consolidation_id=consolidation_id,
            reused=False,
        )
    except AutoPipelineError as exc:
        _write_json(
            summary_path,
            {
                **base_summary,
                "status": "failed",
                "stage": exc.stage,
                "error": str(exc),
            },
        )
        raise
    except Exception as exc:
        _write_json(
            summary_path,
            {
                **base_summary,
                "status": "failed",
                "stage": stage,
                "error": str(exc),
            },
        )
        raise AutoPipelineError(stage, str(exc)) from exc
    finally:
        engine.dispose()
