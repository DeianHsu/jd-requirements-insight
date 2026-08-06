"""verify_extraction_source 离线来源复核测试（无模型）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.extraction import (
    ExtractorMetadata,
    extraction_result_fingerprint,
    persist_extraction,
    rebuild_extraction_result,
)
from app.models import JobDescription
from app.schemas import (
    JobExtractionResult,
    ProficiencyLevel,
    RequirementCategory,
    RequirementGroupLogic,
    RequirementImportance,
    RequirementItem,
    RoleFamily,
    Seniority,
)


def _make_result(evidence_prefix: str = "") -> JobExtractionResult:
    return JobExtractionResult(
        role_family=RoleFamily.OTHER,
        seniority=Seniority.UNKNOWN,
        requirements=[
            RequirementItem(
                raw_name="精通 Python",
                category=RequirementCategory.PROGRAMMING_LANGUAGE,
                importance=RequirementImportance.MUST,
                proficiency=ProficiencyLevel.ADVANCED,
                group_id=None,
                group_logic=RequirementGroupLogic.STANDALONE,
                min_years=None,
                max_years=None,
                years_text=None,
                evidence=evidence_prefix + "至少 3 年 Python 经验。",
                confidence=0.95,
            ),
            RequirementItem(
                raw_name="熟悉分布式系统",
                category=RequirementCategory.OTHER,
                importance=RequirementImportance.PREFERRED,
                proficiency=ProficiencyLevel.BASIC,
                group_id=None,
                group_logic=RequirementGroupLogic.STANDALONE,
                min_years=None,
                max_years=None,
                years_text=None,
                evidence=evidence_prefix + "有分布式系统经验优先。",
                confidence=0.9,
            ),
        ],
    )


def _seed_job(db_path: Path) -> int:
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="v" + "x" * 63,
                source_file="job-1.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="测试公司",
                title="测试岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# 测试岗位\n\n精通 Python，熟悉分布式系统。",
            )
            session.add(job)
            session.commit()
            return job.id
    finally:
        engine.dispose()


def _write_acceptance(tmp_path: Path, job_id: int) -> tuple[Path, Path]:
    result = _make_result()
    raw = {
        "identity": {
            "run_identifier": "test-acceptance",
            "model": "test-model",
            "prompt_version": "0.10",
            "schema_version": "3.0",
            "job_ids": [job_id],
            "runs": "1",
            "max_attempts": "2",
        },
        f"job{job_id}_run0": {
            "result": result.model_dump(mode="json"),
            "result_fingerprint": extraction_result_fingerprint(result),
            "raw_response": {"attempt_count": 1},
        },
    }
    report = {
        "identity": {
            "model": "test-model",
            "prompt_version": "0.10",
            "schema_version": "3.0",
            "run_identifier": "test-acceptance",
        },
        "jobs": [
            {
                "job_id": job_id,
                "input_fingerprint": __import__(
                    "app.extraction_validation", fromlist=["compute_input_fingerprint"]
                ).compute_input_fingerprint(
                    "# 测试岗位\n\n精通 Python，熟悉分布式系统。"
                ),
                "expected_runs": 1,
                "successful_runs": 1,
                "failed_runs": 0,
                "hard_gate_failures": [],
            }
        ],
        "hard_gate_failures": [],
        "passed": True,
    }
    raw_path = tmp_path / "raw.json"
    report_path = tmp_path / "report.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return report_path, raw_path


def _run_verify(
    monkeypatch, tmp_path, db_path, report_path, raw_path, job_id
) -> int:
    import scripts.experiments.p0_3.verify_extraction_source as verify

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "verify_extraction_source",
            "--job-id",
            str(job_id),
            "--report",
            str(report_path),
            "--raw-output",
            str(raw_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
            "--output",
            str(tmp_path / "check.json"),
        ],
    )
    return verify.main()


def test_verify_passes_and_backfills_source_fields(
    monkeypatch, tmp_path
) -> None:
    """通过复核并为旧格式正式抽取补齐来源证明字段。"""
    from app.models import JobExtraction

    db_path = tmp_path / "verify.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, job_id)

    # 先按旧格式定稿一条正式抽取（无来源证明字段）。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        result = _make_result()
        with session_factory() as session:
            job = session.get(JobDescription, job_id)
            metadata = ExtractorMetadata(
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
            )
            persist_extraction(
                session,
                job,
                result,
                {
                    "approved_run_index": 0,
                    "approved_result_fingerprint": extraction_result_fingerprint(
                        result
                    ),
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-04T00:00:00+00:00",
                    "source_run_identifier": f"job{job_id}_run0",
                },
                metadata,
            )
        # 旧格式正式抽取缺来源证明字段 → 复核应补齐。
        with session_factory() as session:
            extraction = session.query(JobExtraction).one()
            assert "report_fingerprint" not in (extraction.raw_response or {})
    finally:
        engine.dispose()

    assert _run_verify(
        monkeypatch, tmp_path, db_path, report_path, raw_path, job_id
    ) == 0

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            extraction = session.query(JobExtraction).one()
            raw_response = extraction.raw_response or {}
            assert raw_response["acceptance_run_identifier"] == "test-acceptance"
            assert raw_response["report_fingerprint"]
            assert raw_response["raw_fingerprint"]
            # 结果内容不变（指纹一致）。
            assert (
                extraction_result_fingerprint(
                    rebuild_extraction_result(extraction)
                )
                == extraction_result_fingerprint(_make_result())
            )
    finally:
        engine.dispose()
    check = json.loads((tmp_path / "check.json").read_text(encoding="utf-8"))
    assert check["passed"] is True
    assert check["job_id"] == job_id


def test_verify_rejects_unpassed_report(monkeypatch, tmp_path) -> None:
    """报告顶层未通过时拒绝。"""
    db_path = tmp_path / "verify.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, job_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["passed"] = False
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert _run_verify(
        monkeypatch, tmp_path, db_path, report_path, raw_path, job_id
    ) == 1


def test_verify_rejects_fingerprint_mismatch(monkeypatch, tmp_path) -> None:
    """正式抽取结果与批准运行指纹不一致时拒绝。"""
    db_path = tmp_path / "verify.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, job_id)

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = session.get(JobDescription, job_id)
            metadata = ExtractorMetadata(
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
            )
            persist_extraction(
                session,
                job,
                _make_result(evidence_prefix="1) "),
                {
                    "approved_run_index": 0,
                    "approved_result_fingerprint": "x",
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-04T00:00:00+00:00",
                    "source_run_identifier": f"job{job_id}_run0",
                },
                metadata,
            )
    finally:
        engine.dispose()

    assert _run_verify(
        monkeypatch, tmp_path, db_path, report_path, raw_path, job_id
    ) == 1
