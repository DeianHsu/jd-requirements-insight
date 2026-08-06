"""backfill_consolidation_metadata 回归测试（临时数据库，不调用模型）。

覆盖：成功补齐、幂等、验收报告身份不一致拒绝、decisions 指纹不一致
拒绝、已有字段冲突拒绝、缺锚点拒绝、raw 批准运行指纹不一致拒绝、
历史最终结果来源指纹不一致拒绝。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
    ConsolidatorMetadata,
    load_consolidation_selection,
    persist_consolidation,
    scope_key_for,
)
from app.consolidation_validation import result_fingerprint
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import JobConsolidation, JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)


def _seed_batch(
    database_path: Path,
    decisions_fp: str = "anchor-decisions-fp",
    requirement_names: tuple[str, ...] = ("技术甲",),
) -> dict:
    """构造旧格式归并批次（仅两个锚点字段），返回各产物引用。"""
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="b" + "a" * 63,
                source_file="backfill-jd.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="示例公司",
                title="岗位",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# 岗位\n\n要求。",
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
            reqs = []
            for index, name in enumerate(requirement_names):
                req = JobRequirement(
                    extraction_id=extraction.id,
                    raw_name=name,
                    category="programming_language",
                    importance="must",
                    proficiency="basic",
                    group_id=None,
                    group_logic="standalone",
                    min_years=None,
                    max_years=None,
                    years_text=None,
                    evidence=f"熟悉{name}",
                    confidence=0.9,
                )
                session.add(req)
                session.flush()
                reqs.append(req)
            session.commit()
            job_ids = {job.id}

            selection = load_consolidation_selection(session, job_ids=job_ids)
            canonical_items = [
                CanonicalRequirement(
                    canonical_requirement_id=f"cr-{index}",
                    canonical_name=name,
                    source_requirement_ids=[req.id],
                    rationale="测试",
                    confidence=0.9,
                )
                for index, (name, req) in enumerate(zip(requirement_names, reqs))
            ]
            result = RequirementConsolidationResult(
                canonical_requirements=canonical_items,
                mappings=build_mappings_from_canonical_partition(canonical_items),
            )
            metadata = ConsolidatorMetadata(
                model_name="test-model",
                prompt_version=CONSOLIDATION_PROMPT_VERSION,
                schema_version=CONSOLIDATION_SCHEMA_VERSION,
            )
            persist_consolidation(
                session,
                selection,
                result,
                {
                    "review_decisions_fingerprint": decisions_fp,
                    "source_run_identifier": "run-0",
                },
                metadata,
                scope_key_for(job_ids or None),
            )
            session.commit()
            consolidation_id = session.query(JobConsolidation).one().id
    finally:
        engine.dispose()

    fingerprint = result_fingerprint(result)
    return {
        "consolidation_id": consolidation_id,
        "selection": selection,
        "result": result,
        "result_fingerprint": fingerprint,
        "batch_identity": {
            "input_fingerprint": selection.input_fingerprint,
            "extractor_version": selection.extractor_version,
            "selected_job_ids": sorted(selection.selected_job_ids),
            "model": "test-model",
            "prompt_version": CONSOLIDATION_PROMPT_VERSION,
            "schema_version": CONSOLIDATION_SCHEMA_VERSION,
        },
    }


def _write_inputs(
    tmp_path: Path, ctx: dict, *, decisions_fp: str = "anchor-decisions-fp"
) -> tuple[Path, Path, Path, Path]:
    """写验收报告 / raw / decisions / 历史 final-result 四份输入。"""
    result = ctx["result"]
    fp = ctx["result_fingerprint"]
    identity = ctx["batch_identity"]

    report_path = tmp_path / "acceptance-report.json"
    report_path.write_text(
        json.dumps(
            {
                "input_identity": dict(identity),
                "hard_gate_failures": [],
                "manual_cluster_review": {
                    "clusters": [],
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-05T00:00:00+00:00",
                    "approved_run_index": 0,
                    "approved_result_fingerprint": fp,
                    "conclusion": "ok",
                    "notes": "",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    raw_path = tmp_path / "acceptance-raw.json"
    raw_path.write_text(
        json.dumps(
            {
                **identity,
                "run_count": 1,
                "runs": [
                    {
                        "run_identifier": "run-0",
                        "result_fingerprint": fp,
                        "result": result.model_dump(mode="json"),
                        "metadata": {
                            "model": identity["model"],
                            "prompt_version": identity["prompt_version"],
                            "schema_version": identity["schema_version"],
                        },
                        "raw_response": {},
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    decisions_path = tmp_path / "review-decisions.json"
    decisions_path.write_text(
        json.dumps({"decisions": []}, ensure_ascii=False), encoding="utf-8"
    )
    import hashlib

    actual_decisions_fp = hashlib.sha256(
        decisions_path.read_bytes()
    ).hexdigest()

    final_path = tmp_path / "final-consolidation.json"
    final_path.write_text(
        json.dumps(
            {
                **identity,
                "source_run_identifier": "run-0",
                "source_result_fingerprint": fp,
                "review_decisions_fingerprint": actual_decisions_fp,
                "result_fingerprint": fp,
                "result": result.model_dump(mode="json"),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return report_path, raw_path, decisions_path, final_path


def _run_backfill(
    monkeypatch, tmp_path, report_path, raw_path, decisions_path, final_path, db_path
) -> int:
    import scripts.experiments.p0_4.backfill_consolidation_metadata as backfill

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "backfill_consolidation_metadata",
            "--consolidation-id",
            "1",
            "--acceptance-report",
            str(report_path),
            "--review-decisions",
            str(decisions_path),
            "--raw-output",
            str(raw_path),
            "--final-result",
            str(final_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    return backfill.main()


def _read_batch(db_path: Path) -> dict:
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).one()
            return dict(record.raw_response or {})
    finally:
        engine.dispose()


def _real_decisions_fp() -> str:
    import hashlib

    return hashlib.sha256(b'{"decisions": []}').hexdigest()


def test_backfill_succeeds_and_is_idempotent(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 0
    )
    batch = _read_batch(db_path)
    assert batch["reviewed_by"] == "tester"
    assert batch["approved_run_index"] == 0
    assert batch["approved_result_fingerprint"] == ctx["result_fingerprint"]
    assert batch["final_result_fingerprint"] == ctx["result_fingerprint"]

    # 幂等：再次执行不改动。
    before = json.dumps(_read_batch(db_path), sort_keys=True)
    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 0
    )
    assert json.dumps(_read_batch(db_path), sort_keys=True) == before


def test_backfill_rejects_identity_mismatch(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["input_identity"]["input_fingerprint"] = "wrong"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_decisions_fingerprint_mismatch(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    # decisions 文件与批次锚点指纹不一致。
    decisions_path.write_text(
        json.dumps({"decisions": [{"decision": "must_link"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    import hashlib

    real_fp = hashlib.sha256(decisions_path.read_bytes()).hexdigest()
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["review_decisions_fingerprint"] = real_fp
    final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_existing_field_conflict(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).one()
            record.raw_response = {
                **record.raw_response,
                "reviewed_by": "someone-else",
            }
            session.commit()
    finally:
        engine.dispose()

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path)["reviewed_by"] == "someone-else"  # 不覆盖


def test_backfill_rejects_missing_anchor(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).one()
            record.raw_response = {"normalized_result": {}}
            session.commit()
    finally:
        engine.dispose()

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )


def test_backfill_rejects_approved_run_fingerprint_mismatch(
    monkeypatch, tmp_path
) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["runs"][0]["result_fingerprint"] = "wrong-fingerprint"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_final_result_mismatch(monkeypatch, tmp_path) -> None:
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["source_result_fingerprint"] = "wrong"
    final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_final_content_fingerprint_mismatch(
    monkeypatch, tmp_path
) -> None:
    """历史最终结果 result 内容与声明指纹不一致时拒绝。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["result_fingerprint"] = "wrong-declared-fp"
    final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_final_identity_mismatch(monkeypatch, tmp_path) -> None:
    """历史最终结果的批次身份与批次不一致时拒绝。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["input_fingerprint"] = "wrong-identity"
    final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_illegal_approved_run_index(
    monkeypatch, tmp_path
) -> None:
    """approved_run_index 非 int 时干净拒绝且不写库。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manual_cluster_review"]["approved_run_index"] = "0"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_invalid_reviewed_at(monkeypatch, tmp_path) -> None:
    """reviewed_at 格式非法时干净拒绝且不写库。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manual_cluster_review"]["reviewed_at"] = "not-a-date"
    report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_raw_run_fingerprint_content_mismatch(
    monkeypatch, tmp_path
) -> None:
    """批准运行记录指纹与其 result 内容不一致时拒绝。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["runs"][0]["result_fingerprint"] = "content-mismatch"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_raw_identity_mismatch(monkeypatch, tmp_path) -> None:
    """raw 整轮身份与批次不一致时拒绝（防止传错批次的 raw 文件）。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["selected_job_ids"] = [999]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_approved_run_identifier_mismatch(
    monkeypatch, tmp_path
) -> None:
    """批准运行标识与 run-N 不一致时拒绝。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(db_path, decisions_fp=_real_decisions_fp())
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["runs"][0]["run_identifier"] = "run-9"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None


def test_backfill_rejects_replay_mismatch(monkeypatch, tmp_path) -> None:
    """重放（批准运行 + 审核决定）结果与当前持久化结果不一致时拒绝。"""
    db_path = tmp_path / "backfill.db"
    ctx = _seed_batch(
        db_path,
        decisions_fp=_real_decisions_fp(),
        requirement_names=("技术甲", "框架乙"),
    )
    report_path, raw_path, decisions_path, final_path = _write_inputs(
        tmp_path, ctx
    )
    # 审核决定包含 must_link：重放会合并两个 singleton canonical，
    # 与当前库结果（未合并）不一致 → 拒绝（走"重放成功但结果不同"分支）。
    decisions_path.write_text(
        json.dumps(
            {
                "decisions": [
                    {
                        "decision": "must_link",
                        "requirement_ids": [
                            mapping.requirement_id
                            for mapping in ctx["result"].mappings
                        ],
                        "rationale": "测试合并",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import hashlib

    real_fp = hashlib.sha256(decisions_path.read_bytes()).hexdigest()
    final = json.loads(final_path.read_text(encoding="utf-8"))
    final["review_decisions_fingerprint"] = real_fp
    final_path.write_text(json.dumps(final, ensure_ascii=False), encoding="utf-8")
    # 批次锚点指纹同步为新的 decisions 指纹（否则锚点冲突先触发）。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).one()
            record.raw_response = {
                **record.raw_response,
                "review_decisions_fingerprint": real_fp,
            }
            session.commit()
    finally:
        engine.dispose()

    assert (
        _run_backfill(
            monkeypatch,
            tmp_path,
            report_path,
            raw_path,
            decisions_path,
            final_path,
            db_path,
        )
        == 1
    )
    assert _read_batch(db_path).get("reviewed_by") is None
