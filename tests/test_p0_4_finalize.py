"""finalize_consolidation 定稿机制核心测试（不调用模型）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
)
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    RequirementMapping,
)


def _seed_database(database_path: Path) -> None:
    """向临时数据库写入一份 v0.10 + Schema V3 抽取结果（3 条要求实例）。"""
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="a" * 64,
                source_file="job-a.md",
                source_type="test",
                collected_at=date(2026, 8, 3),
                company="示例公司",
                title="示例岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# 示例岗位\n\n负责能力甲体系建设。\n\n1. 熟悉编程语言。\n2. 具备数据分析经验者优先。\n3. 本科及以上学历。",
            )
            session.add(job)
            session.flush()
            extraction = JobExtraction(
                job_id=job.id,
                extractor_version="test-model|prompt:0.10|schema:3.0",
                model_name="test-model",
                prompt_version="0.10",
                schema_version="3.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
            session.add(extraction)
            session.flush()
            for raw_name, evidence in (
                ("编程语言", "熟悉编程语言"),
                ("数据分析经验", "具备数据分析经验者优先"),
                ("本科及以上学历", "本科及以上学历"),
            ):
                session.add(
                    JobRequirement(
                        extraction_id=extraction.id,
                        raw_name=raw_name,
                        category="other",
                        importance="must",
                        proficiency="basic",
                        group_id=None,
                        group_logic="standalone",
                        min_years=None,
                        max_years=None,
                        years_text=None,
                        evidence=evidence,
                        confidence=0.9,
                    )
                )
            session.commit()
    finally:
        engine.dispose()


def _valid_result() -> RequirementConsolidationResult:
    """一份合同通过的规范化结果（coverage=100%、结构违规=0）。"""
    return RequirementConsolidationResult(
        canonical_requirements=[
            CanonicalRequirement(
                canonical_requirement_id="cr-1",
                canonical_name="编程语言",
                source_requirement_ids=[1],
                rationale="测试",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-2",
                canonical_name="数据分析经验",
                source_requirement_ids=[2],
                rationale="测试",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="cr-3",
                canonical_name="本科及以上学历",
                source_requirement_ids=[3],
                rationale="测试",
                confidence=0.9,
            ),
        ],
        mappings=[
            RequirementMapping(
                requirement_id=1,
                canonical_requirement_id="cr-1",
                rationale="测试",
                confidence=0.9,
            ),
            RequirementMapping(
                requirement_id=2,
                canonical_requirement_id="cr-2",
                rationale="测试",
                confidence=0.9,
            ),
            RequirementMapping(
                requirement_id=3,
                canonical_requirement_id="cr-3",
                rationale="测试",
                confidence=0.9,
            ),
        ],
    )


def _write_inputs(tmp_path: Path, database_path: Path) -> tuple[Path, Path]:
    """写验收报告与私有原始结果，返回（report_path, raw_path）。"""
    from app.consolidation import load_consolidation_selection

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            selection = load_consolidation_selection(session, job_ids={1})
            fingerprint = selection.input_fingerprint
            extractor_version = selection.extractor_version
    finally:
        engine.dispose()

    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "hard_gate_failures": [],
                "manual_cluster_review": {
                    "clusters": [],
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-04T00:00:00+00:00",
                    "notes": "ok",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(
        json.dumps(
            {
                "extractor_version": extractor_version,
                "input_fingerprint": fingerprint,
                "selected_job_ids": [1],
                "runs": [
                    {
                        "metadata": {
                            "model": "test-model",
                            "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                            "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                        },
                        "result": _valid_result().model_dump(mode="json"),
                        "raw_response": {
                            "model_response": {"canonical_requirements": []},
                            "attempt_count": 1,
                        },
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return report_path, raw_path


def _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) -> int:
    import scripts.experiments.p0_4.finalize_consolidation as finalize

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "finalize_consolidation",
            "--report",
            str(report_path),
            "--raw-output",
            str(raw_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    return finalize.main()


def test_finalize_persists_reviewed_run(monkeypatch, tmp_path) -> None:
    """通过人工审核的验收运行可定稿为正式批次。"""
    from app.database import create_database_engine, create_session_factory
    from app.models import JobConsolidation

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            assert batch.occurrence_count == 3
            assert batch.consolidator_version == (
                "test-model|prompt:4.1|schema:3.0"
            )
    finally:
        engine.dispose()


def test_finalize_rejects_input_fingerprint_change(
    monkeypatch, tmp_path
) -> None:
    """输入指纹变化时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["input_fingerprint"] = "f" * 64
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_version_mismatch(monkeypatch, tmp_path) -> None:
    """抽取器版本不一致时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["extractor_version"] = "test-model|prompt:0.6|schema:2.0"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_unreviewed_run(monkeypatch, tmp_path) -> None:
    """未完成人工审核时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manual_cluster_review"]["reviewed_by"] = None
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_incomplete_coverage(monkeypatch, tmp_path) -> None:
    """来源分区不完整（coverage < 100%）时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["runs"][0]["result"]["mappings"] = raw["runs"][0]["result"]["mappings"][:2]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_is_idempotent(monkeypatch, tmp_path) -> None:
    """同一结果重复定稿保持幂等（只存在一份批次）。"""
    from app.database import create_database_engine, create_session_factory
    from app.models import JobConsolidation

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 0

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            count = session.query(JobConsolidation).count()
            assert count == 1
    finally:
        engine.dispose()
