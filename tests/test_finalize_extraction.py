"""finalize_extraction 抽取定稿机制核心测试（不调用模型）。

覆盖模块验收：

1. 被批准结果能够无模型持久化；
2. 持久化后的 requirement 内容与批准结果逐项一致；
3. 报告与其他 raw 文件组合时拒绝；
4. 未批准运行拒绝定稿；
5. 输入指纹或版本不一致时拒绝；
6. 相同结果重复定稿幂等；
7. 已存在不同结果时拒绝静默复用；
8. 定稿期间不初始化模型客户端。
"""
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
from app.extraction_validation import compute_input_fingerprint
from app.models import JobDescription, JobExtraction, JobRequirement
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


def _seed_job(database_path: Path) -> int:
    """写入一份无抽取的 JD，返回 job_id。"""
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="e" + "f" * 63,
                source_file="jd-004.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="示例公司",
                title="AI 研发工程师",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# AI 研发工程师\n\n## 任职要求\n1. 熟悉 Python。\n2. 有 RAG 项目经验者优先。",
            )
            session.add(job)
            session.flush()
            job_id = job.id
            session.commit()
            return job_id
    finally:
        engine.dispose()


def _make_result(evidence_prefix: str = "1. ") -> JobExtractionResult:
    return JobExtractionResult(
        role_family=RoleFamily.OTHER,
        seniority=Seniority.UNKNOWN,
        requirements=[
            RequirementItem(
                raw_name="编程语言",
                category=RequirementCategory.OTHER,
                importance=RequirementImportance.MUST,
                proficiency=ProficiencyLevel.BASIC,
                group_id=None,
                group_logic=RequirementGroupLogic.STANDALONE,
                min_years=None,
                max_years=None,
                years_text=None,
                evidence=evidence_prefix + "熟悉 Python。",
                confidence=0.9,
            ),
            RequirementItem(
                raw_name="RAG 项目经验",
                category=RequirementCategory.OTHER,
                importance=RequirementImportance.PREFERRED,
                proficiency=ProficiencyLevel.UNKNOWN,
                group_id=None,
                group_logic=RequirementGroupLogic.STANDALONE,
                min_years=None,
                max_years=None,
                years_text=None,
                evidence="2. 有 RAG 项目经验者优先。",
                confidence=0.9,
            ),
        ],
    )


def _write_acceptance(
    tmp_path: Path, database_path: Path, job_id: int, job_ids=None
) -> tuple[Path, Path]:
    """写验收报告与私有原始结果（2 个运行），返回（report, raw）。"""
    from app.extraction import extraction_result_fingerprint

    job_ids = job_ids if job_ids is not None else [job_id]
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = session.query(JobDescription).filter(
                JobDescription.id == job_id
            ).one()
    finally:
        engine.dispose()

    fingerprint = compute_input_fingerprint(job.raw_text)
    results = [_make_result(), _make_result(evidence_prefix="1) ")]
    raw = {}
    for index, result in enumerate(results):
        raw[f"job{job_id}_run{index}"] = {
            "discovery": None,
            "result": result.model_dump(mode="json"),
            "raw_text": job.raw_text,
            "run_identifier": f"job{job_id}_run{index}",
            "result_fingerprint": extraction_result_fingerprint(result),
            "raw_response": {"attempt_count": 1},
        }
    report = {
        "identity": {
            "model": "test-model",
            "prompt_version": "0.10",
            "schema_version": "3.0",
            "job_count": str(len(job_ids)),
            "runs": "2",
            "run_identifier": "test-acceptance",
        },
        "jobs": [
            {
                "job_id": job_id,
                "source_file": job.source_file,
                "input_fingerprint": fingerprint,
                "expected_runs": 2,
                "successful_runs": 2,
                "failed_runs": 0,
                "hard_gate_failures": [],
                "requirement_count": 2,
                "manual_review": {
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-05T00:00:00+00:00",
                    "approved_run_index": 0,
                    "approved_result_fingerprint": (
                        extraction_result_fingerprint(results[0])
                    ),
                    "conclusion": "ok",
                },
            }
        ],
        "hard_gate_failures": [],
        "passed": True,
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return report_path, raw_path


def _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) -> int:
    import scripts.experiments.p0_3.finalize_extraction as finalize

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finalize_extraction",
            "--report",
            str(report_path),
            "--raw-output",
            str(raw_path),
            "--job-id",
            "1",
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    return finalize.main()


def test_finalize_persists_approved_run(monkeypatch, tmp_path) -> None:
    """被批准结果能够无模型持久化，且逐项一致。"""
    from app.extraction import (
        extraction_result_fingerprint,
        rebuild_extraction_result,
    )

    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            extraction = session.query(JobExtraction).one()
            assert extraction.job_id == job_id
            assert extraction.extractor_version == (
                "test-model|prompt:0.10|schema:3.0"
            )
            requirements = session.query(JobRequirement).all()
            assert len(requirements) == 2
            rebuilt = rebuild_extraction_result(extraction)
            expected = _make_result()
            assert extraction_result_fingerprint(rebuilt) == (
                extraction_result_fingerprint(expected)
            )
            # 逐项一致：evidence、importance、category。
            rebuilt_items = sorted(
                rebuilt.requirements, key=lambda item: item.evidence
            )
            expected_items = sorted(
                expected.requirements, key=lambda item: item.evidence
            )
            for actual, wanted in zip(rebuilt_items, expected_items):
                assert actual.evidence == wanted.evidence
                assert actual.importance == wanted.importance
                assert actual.category == wanted.category
                assert actual.group_logic == wanted.group_logic
            raw_response = extraction.raw_response or {}
            assert raw_response["approved_run_index"] == 0
            assert raw_response["reviewed_by"] == "tester"
    finally:
        engine.dispose()


def test_finalize_rejects_foreign_raw(monkeypatch, tmp_path) -> None:
    """报告与其他 raw 文件组合（运行指纹不一致）时拒绝。"""
    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    # 换一份 raw：run0 结果不同（指纹不同），报告仍批准原指纹。
    from app.extraction import extraction_result_fingerprint

    raw["job1_run0"]["result"] = _make_result(
        evidence_prefix="9. "
    ).model_dump(mode="json")
    raw["job1_run0"]["result_fingerprint"] = extraction_result_fingerprint(
        _make_result(evidence_prefix="9. ")
    )
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_unapproved_run(monkeypatch, tmp_path) -> None:
    """未批准运行（approved_run_index 缺失或与 --run-index 不符）拒绝。"""
    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["jobs"][0]["manual_review"]["approved_run_index"] = None
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1

    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["jobs"][0]["manual_review"]["approved_run_index"] = 1
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_identity_mismatch(monkeypatch, tmp_path) -> None:
    """输入指纹不一致时拒绝。"""
    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["jobs"][0]["input_fingerprint"] = "f" * 64
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_idempotent(monkeypatch, tmp_path) -> None:
    """相同结果重复定稿幂等（只存在一份正式抽取）。"""
    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            assert session.query(JobExtraction).count() == 1
    finally:
        engine.dispose()


def test_finalize_rejects_existing_different_result(monkeypatch, tmp_path) -> None:
    """同一 JD 同一版本已存在不同结果时拒绝静默复用。"""
    from app.extraction import extraction_result_fingerprint

    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0

    # 批准一份新结果（报告与 raw 同步更新为 run0 的新指纹）。
    new_result = _make_result(evidence_prefix="8. ")
    new_fingerprint = extraction_result_fingerprint(new_result)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["jobs"][0]["manual_review"]["approved_result_fingerprint"] = (
        new_fingerprint
    )
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["job1_run0"]["result"] = new_result.model_dump(mode="json")
    raw["job1_run0"]["result_fingerprint"] = new_fingerprint
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1
    # 已有正式抽取未被修改。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            extraction = session.query(JobExtraction).one()
            assert len(extraction.requirements) == 2
            assert (
                extraction.raw_response or {}
            ).get("approved_result_fingerprint") != new_fingerprint
    finally:
        engine.dispose()


def test_finalize_rejects_existing_without_review_metadata(
    monkeypatch, tmp_path
) -> None:
    """旧格式正式抽取缺少审核元数据时，不得无依据宣称已审核。"""
    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["jobs"][0]["manual_review"]["approved_run_index"] = None
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    # 直接以生产入口语义写入一份无审核元数据的正式抽取。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            from app.extraction import ExtractorMetadata, persist_extraction

            job = session.query(JobDescription).one()
            metadata = ExtractorMetadata(
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
            )
            persist_extraction(
                session,
                job,
                _make_result(),
                {"model_response": {"requirements": []}},
                metadata,
            )
    finally:
        engine.dispose()

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_does_not_initialize_llm(monkeypatch, tmp_path) -> None:
    """定稿机制不引用任何 LLM 客户端或配置加载。"""
    import inspect

    import scripts.experiments.p0_3.finalize_extraction as finalize

    source = inspect.getsource(finalize)
    assert "OpenAI" not in source
    assert "load_llm_settings" not in source
    assert 'add_argument("--execute"' not in source

    db_path = tmp_path / "finalize.db"
    job_id = _seed_job(db_path)
    report_path, raw_path = _write_acceptance(tmp_path, db_path, job_id)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finalize_extraction",
            "--report",
            str(report_path),
            "--raw-output",
            str(raw_path),
            "--job-id",
            str(job_id),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    assert finalize.main() == 0
