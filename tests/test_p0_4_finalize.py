"""finalize_consolidation 定稿机制核心测试（不调用模型）。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

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
    from app.consolidation_validation import result_fingerprint

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            selection = load_consolidation_selection(session, job_ids={1})
            fingerprint = selection.input_fingerprint
            extractor_version = selection.extractor_version
            instance_count = len(selection.consolidation_input.occurrences)
    finally:
        engine.dispose()

    valid_result = _valid_result().model_dump(mode="json")
    run_fingerprint = result_fingerprint(_valid_result())

    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(
            {
                "input_identity": {
                    "model": "test-model",
                    "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                    "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                    "extractor_version": extractor_version,
                    "input_fingerprint": fingerprint,
                    "instance_count": instance_count,
                    "job_count": 1,
                    "selected_job_ids": [1],
                },
                "p0_4_stability": {"run_count": 1},
                "hard_gate_failures": [],
                "manual_cluster_review": {
                    "clusters": [],
                    "reviewed_by": "tester",
                    "reviewed_at": "2026-08-04T00:00:00+00:00",
                    "approved_run_index": 0,
                    "approved_result_fingerprint": run_fingerprint,
                    "conclusion": "ok",
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
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "run_count": 1,
                "runs": [
                    {
                        "run_identifier": "run-0",
                        "result_fingerprint": run_fingerprint,
                        "metadata": {
                            "model": "test-model",
                            "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                            "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                        },
                        "result": valid_result,
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
                "test-model|prompt:4.3|schema:3.0"
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


def _run_apply_review_decisions(monkeypatch, tmp_path, raw_path, decisions_path, db_path) -> int:
    import scripts.experiments.p0_4.apply_review_decisions as apply_script

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "apply_review_decisions",
            "--raw-output",
            str(raw_path),
            "--review-decisions",
            str(decisions_path),
            "--run-index",
            "0",
            "--output",
            str(tmp_path / "final.json"),
            "--report",
            str(tmp_path / "final-summary.json"),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    return apply_script.main()


def test_apply_review_decisions_must_link_merges_canonicals(
    monkeypatch, tmp_path
) -> None:
    """must-link 把两个 singleton 合并为同一 canonical 并重建 mappings。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": json.loads(
                    raw_path.read_text(encoding="utf-8")
                )["input_fingerprint"],
                "extractor_version": "test-model|prompt:0.10|schema:3.0",
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "must_link",
                        "requirement_ids": [2, 3],
                        "rationale": "测试合并",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )

    final = json.loads(
        (tmp_path / "final.json").read_text(encoding="utf-8")
    )
    result = final["result"]
    member_to_cid = {
        rid: c["canonical_requirement_id"]
        for c in result["canonical_requirements"]
        for rid in c["source_requirement_ids"]
    }
    assert member_to_cid[2] == member_to_cid[3]  # 合并
    assert member_to_cid[1] != member_to_cid[2]  # 其余不受影响
    assert len(result["mappings"]) == 3
    assert final["source_run_identifier"] == "run-0"
    assert final["review_decisions_fingerprint"]


def test_apply_review_decisions_cannot_link_splits_canonical(
    monkeypatch, tmp_path
) -> None:
    """cannot-link 把同 canonical 的成员拆成独立 singleton。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    # 先把 2、3 归入同一 canonical（cr-2），再验证 cannot-link 拆开。
    raw["runs"][0]["result"]["canonical_requirements"] = [
        raw["runs"][0]["result"]["canonical_requirements"][0],
        {
            "canonical_requirement_id": "cr-2",
            "canonical_name": "数据分析经验与学历",
            "source_requirement_ids": [2, 3],
            "rationale": "测试",
            "confidence": 0.9,
        },
    ]
    raw["runs"][0]["result"]["mappings"] = [
        {
            "requirement_id": 1,
            "canonical_requirement_id": "cr-1",
            "rationale": "测试",
            "confidence": 0.9,
        },
        {
            "requirement_id": 2,
            "canonical_requirement_id": "cr-2",
            "rationale": "测试",
            "confidence": 0.9,
        },
        {
            "requirement_id": 3,
            "canonical_requirement_id": "cr-2",
            "rationale": "测试",
            "confidence": 0.9,
        },
    ]
    from app.consolidation_validation import result_fingerprint
    from app.requirement_consolidation import RequirementConsolidationResult

    raw["runs"][0]["result_fingerprint"] = result_fingerprint(
        RequirementConsolidationResult.model_validate(raw["runs"][0]["result"])
    )
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "cannot_link",
                        "requirement_ids": [2, 3],
                        "rationale": "测试拆分",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )

    final = json.loads(
        (tmp_path / "final.json").read_text(encoding="utf-8")
    )
    result = final["result"]
    member_to_cid = {
        rid: c["canonical_requirement_id"]
        for c in result["canonical_requirements"]
        for rid in c["source_requirement_ids"]
    }
    assert member_to_cid[2] != member_to_cid[3]  # 已拆开
    assert len(result["mappings"]) == 3
    # 拆出的实例 2 的 canonical 名称必须是原始名称，无内部占位痕迹。
    split = next(
        c for c in result["canonical_requirements"]
        if c["canonical_requirement_id"] == member_to_cid[2]
    )
    assert split["canonical_name"] == "数据分析经验"
    assert "拆分" not in split["canonical_name"]
    assert "实例" not in split["canonical_name"]


def test_apply_review_decisions_must_link_explicit_name(
    monkeypatch, tmp_path
) -> None:
    """must-link 决策显式提供 canonical_name 时用于合并后名称。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": json.loads(
                    raw_path.read_text(encoding="utf-8")
                )["input_fingerprint"],
                "extractor_version": "test-model|prompt:0.10|schema:3.0",
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "must_link",
                        "requirement_ids": [2, 3],
                        "canonical_name": "数据分析经验与学历",
                        "rationale": "测试",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )
    final = json.loads(
        (tmp_path / "final.json").read_text(encoding="utf-8")
    )
    result = final["result"]
    member_to_cid = {
        rid: c["canonical_requirement_id"]
        for c in result["canonical_requirements"]
        for rid in c["source_requirement_ids"]
    }
    merged = next(
        c for c in result["canonical_requirements"]
        if c["canonical_requirement_id"] == member_to_cid[2]
    )
    assert merged["canonical_name"] == "数据分析经验与学历"


def test_apply_name_override_changes_only_final_canonical_name(
    monkeypatch, tmp_path
) -> None:
    """名称 override 精确定位最终成员集合，只改名称、不改分区。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    source_partition = sorted(
        sorted(item["source_requirement_ids"])
        for item in raw["runs"][0]["result"]["canonical_requirements"]
    )
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
                "decisions": [],
                "canonical_name_overrides": [
                    {
                        "requirement_ids": [2],
                        "canonical_name": "数据分析实践经验",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    result = final["result"]
    final_partition = sorted(
        sorted(item["source_requirement_ids"])
        for item in result["canonical_requirements"]
    )
    renamed = next(
        item
        for item in result["canonical_requirements"]
        if item["source_requirement_ids"] == [2]
    )
    assert final_partition == source_partition
    assert renamed["canonical_name"] == "数据分析实践经验"
    summary = json.loads(
        (tmp_path / "final-summary.json").read_text(encoding="utf-8")
    )
    assert summary["applied_canonical_name_overrides"] == [
        {
            "requirement_ids": [2],
            "canonical_name": "数据分析实践经验",
        }
    ]


def test_apply_without_name_override_keeps_existing_name(
    monkeypatch, tmp_path
) -> None:
    """缺少 name override 时保持旧名称策略。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    source_names = {
        tuple(item["source_requirement_ids"]): item["canonical_name"]
        for item in raw["runs"][0]["result"]["canonical_requirements"]
    }
    final_names = {
        tuple(item["source_requirement_ids"]): item["canonical_name"]
        for item in final["result"]["canonical_requirements"]
    }
    assert final_names == source_names


def test_apply_name_override_still_enforces_unique_names(
    monkeypatch, tmp_path
) -> None:
    """override 后仍由最终结果合同拒绝重复 canonical_name。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    duplicate_name = raw["runs"][0]["result"]["canonical_requirements"][0][
        "canonical_name"
    ]
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
                "decisions": [],
                "canonical_name_overrides": [
                    {
                        "requirement_ids": [2],
                        "canonical_name": duplicate_name,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 1
    )
    assert not (tmp_path / "final.json").exists()


@pytest.mark.parametrize(
    "overrides",
    [
        [{"requirement_ids": [], "canonical_name": "非法"}],
        [{"requirement_ids": [999], "canonical_name": "无法定位"}],
        [{"requirement_ids": [2], "canonical_name": "  "}],
        [
            {"requirement_ids": [2], "canonical_name": "名称一"},
            {"requirement_ids": [2], "canonical_name": "名称二"},
        ],
    ],
)
def test_apply_name_override_rejects_invalid_or_unlocatable_target(
    monkeypatch, tmp_path, overrides
) -> None:
    """非法结构或无法按完整成员集合定位时明确拒绝。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
                "decisions": [],
                "canonical_name_overrides": overrides,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 1
    )
    assert not (tmp_path / "final.json").exists()


def test_apply_name_override_rejects_non_list_contract(
    monkeypatch, tmp_path
) -> None:
    """顶层 canonical_name_overrides 非列表时不得静默忽略。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-12T00:00:00+00:00",
                "decisions": [],
                "canonical_name_overrides": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 1
    )
    assert not (tmp_path / "final.json").exists()


def test_apply_unresolved_preserve_source_keeps_partition(
    monkeypatch, tmp_path
) -> None:
    """unresolved preserve_source=true：保留来源运行当前分区，不拆全部。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    # 来源运行把 2、3 并入同一 canonical。
    result = raw["runs"][0]["result"]
    result["canonical_requirements"] = [
        result["canonical_requirements"][0],
        {
            "canonical_requirement_id": "cr-2",
            "canonical_name": "数据分析经验与学历",
            "source_requirement_ids": [2, 3],
            "rationale": "测试",
            "confidence": 0.9,
        },
    ]
    result["mappings"][2]["canonical_requirement_id"] = "cr-2"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "unresolved",
                        "requirement_ids": [2, 3],
                        "preserve_source": True,
                        "rationale": "测试保留",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    result = final["result"]
    member_to_cid = {
        rid: c["canonical_requirement_id"]
        for c in result["canonical_requirements"]
        for rid in c["source_requirement_ids"]
    }
    assert member_to_cid[2] == member_to_cid[3]  # 保留来源合并态


def test_apply_unresolved_groups_splits_between_groups(
    monkeypatch, tmp_path
) -> None:
    """unresolved 显式分组 [2,3] 与 [1]：组内合并、组间拆开。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    # 来源运行 1/2/3 全在同一 canonical。
    result = raw["runs"][0]["result"]
    result["canonical_requirements"] = [
        {
            "canonical_requirement_id": "cr-0",
            "canonical_name": "合并条件",
            "source_requirement_ids": [1, 2, 3],
            "rationale": "测试",
            "confidence": 0.9,
        }
    ]
    for mapping in result["mappings"]:
        mapping["canonical_requirement_id"] = "cr-0"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "unresolved",
                        "requirement_ids": [1, 2, 3],
                        "groups": [[2, 3], [1]],
                        "rationale": "测试分组",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 0
    )
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    result = final["result"]
    member_to_cid = {
        rid: c["canonical_requirement_id"]
        for c in result["canonical_requirements"]
        for rid in c["source_requirement_ids"]
    }
    assert member_to_cid[2] == member_to_cid[3]  # 组内合并
    assert member_to_cid[1] != member_to_cid[2]  # 组间拆开
    names = [c["canonical_name"] for c in result["canonical_requirements"]]
    assert len(names) == len(set(names))  # 无重复名称
    assert len(result["mappings"]) == 3  # 完整唯一覆盖


def test_apply_unresolved_without_structure_rejected(
    monkeypatch, tmp_path
) -> None:
    """unresolved 缺少 groups/preserve_source 时拒绝应用。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": raw["input_fingerprint"],
                "extractor_version": raw["extractor_version"],
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [
                    {
                        "decision": "unresolved",
                        "requirement_ids": [1, 2, 3],
                        "rationale": "缺结构",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 1
    )


def test_apply_review_decisions_rejects_identity_mismatch(
    monkeypatch, tmp_path
) -> None:
    """审核决定文件与 raw 输入指纹不一致时拒绝应用。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    _, raw_path = _write_inputs(tmp_path, db_path)
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": "f" * 64,
                "extractor_version": "test-model|prompt:0.10|schema:3.0",
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert (
        _run_apply_review_decisions(
            monkeypatch, tmp_path, raw_path, decisions_path, db_path
        )
        == 1
    )


def test_finalize_rejects_incomplete_coverage(monkeypatch, tmp_path) -> None:
    """来源分区不完整（coverage < 100%）时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["runs"][0]["result"]["mappings"] = raw["runs"][0]["result"]["mappings"][:2]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_swapped_ids_same_count(monkeypatch, tmp_path) -> None:
    """数量相同但 mapping ID 被替换（把 2 换成 999）时拒绝定稿。"""
    from app.consolidation_validation import result_fingerprint

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    for mapping in raw["runs"][0]["result"]["mappings"]:
        if mapping["requirement_id"] == 2:
            mapping["requirement_id"] = 999
    raw["runs"][0]["result_fingerprint"] = result_fingerprint(
        __import__(
            "app.requirement_consolidation",
            fromlist=["RequirementConsolidationResult"],
        ).RequirementConsolidationResult.model_validate(
            raw["runs"][0]["result"]
        )
    )
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_report_with_foreign_raw(monkeypatch, tmp_path) -> None:
    """已审核报告与另一份 raw 组合（身份字段不一致）时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["model"] = "other-model"  # 报告身份仍为 test-model
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def test_finalize_rejects_unapproved_run_index(monkeypatch, tmp_path) -> None:
    """审核批准 run0 而 --run-index 选择 run1 时拒绝定稿。"""
    from app.consolidation_validation import result_fingerprint

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    second = json.loads(json.dumps(raw["runs"][0]))
    second["run_identifier"] = "run-1"
    second["result_fingerprint"] = result_fingerprint(
        __import__(
            "app.requirement_consolidation",
            fromlist=["RequirementConsolidationResult"],
        ).RequirementConsolidationResult.model_validate(second["result"])
    )
    raw["runs"].append(second)
    raw["run_count"] = 2
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1


def _write_final_result(
    tmp_path: Path,
    raw_path: Path,
    decisions_path: Path,
    mutate=None,
) -> Path:
    """从 raw 的 run-0 生成审核应用后的最终结果 JSON。

    mutate 回调可修改 result dict（例如改变 canonical 分区或名称）。
    """
    import hashlib

    from app.consolidation_validation import result_fingerprint
    from app.requirement_consolidation import RequirementConsolidationResult

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    run = raw["runs"][0]
    result_dict = json.loads(json.dumps(run["result"]))
    if mutate is not None:
        mutate(result_dict)
    result = RequirementConsolidationResult.model_validate(result_dict)
    final = {
        "input_fingerprint": raw["input_fingerprint"],
        "extractor_version": raw["extractor_version"],
        "selected_job_ids": [1],
        "model": raw["model"],
        "prompt_version": raw["prompt_version"],
        "schema_version": raw["schema_version"],
        "source_run_identifier": "run-0",
        "source_result_fingerprint": run["result_fingerprint"],
        "review_decisions_fingerprint": hashlib.sha256(
            decisions_path.read_bytes()
        ).hexdigest(),
        "reviewed_by": "tester",
        "reviewed_at": "2026-08-04T00:00:00+00:00",
        "result_fingerprint": result_fingerprint(result),
        "result": result.model_dump(mode="json"),
    }
    final_path = tmp_path / "final.json"
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(
        json.dumps(final, ensure_ascii=False), encoding="utf-8"
    )
    return final_path


def _run_finalize_with_review(
    monkeypatch, tmp_path, report_path, raw_path, db_path, final_path,
    decisions_path,
) -> int:
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
            "--final-result",
            str(final_path),
            "--review-decisions",
            str(decisions_path),
            "--database-url",
            f"sqlite:///{db_path.as_posix()}",
        ],
    )
    return finalize.main()


def _decisions_file(tmp_path: Path, fingerprint: str) -> Path:
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(
            {
                "input_fingerprint": fingerprint,
                "extractor_version": "test-model|prompt:0.10|schema:3.0",
                "selected_job_ids": [1],
                "model": "test-model",
                "prompt_version": CONSOLIDATION_PROMPT_VERSION,
                "schema_version": CONSOLIDATION_SCHEMA_VERSION,
                "reviewed_by": "tester",
                "reviewed_at": "2026-08-04T00:00:00+00:00",
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return decisions_path


def test_finalize_idempotent_with_final_result(
    monkeypatch, tmp_path
) -> None:
    """相同输入、版本和最终结果：重复定稿幂等返回已有批次。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = _decisions_file(tmp_path, raw["input_fingerprint"])
    final_path = _write_final_result(tmp_path, raw_path, decisions_path)

    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            final_path, decisions_path,
        )
        == 0
    )
    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            final_path, decisions_path,
        )
        == 0
    )

    from app.database import create_database_engine, create_session_factory
    from app.models import JobConsolidation

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            assert session.query(JobConsolidation).count() == 1
    finally:
        engine.dispose()


def test_finalize_rejects_existing_batch_different_result(
    monkeypatch, tmp_path
) -> None:
    """同一输入与版本下已存在不同归并结果：拒绝且不修改已有批次。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = _decisions_file(tmp_path, raw["input_fingerprint"])
    final_path = _write_final_result(tmp_path, raw_path, decisions_path)
    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            final_path, decisions_path,
        )
        == 0
    )

    # 构造不同分区（把实例 2、3 合并）的最终结果。
    def mutate(result_dict):
        for canonical in result_dict["canonical_requirements"]:
            if canonical["canonical_requirement_id"] == "cr-2":
                canonical["source_requirement_ids"] = [2, 3]
                canonical["canonical_name"] = "数据分析经验与学历"
            elif canonical["canonical_requirement_id"] == "cr-3":
                canonical["source_requirement_ids"] = []
        result_dict["canonical_requirements"] = [
            c for c in result_dict["canonical_requirements"]
            if c["source_requirement_ids"]
        ]
        for mapping in result_dict["mappings"]:
            if mapping["requirement_id"] == 3:
                mapping["canonical_requirement_id"] = "cr-2"

    other_final = _write_final_result(
        tmp_path / "other", raw_path, decisions_path, mutate=mutate
    )
    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            other_final, decisions_path,
        )
        == 1
    )

    from app.database import create_database_engine, create_session_factory
    from app.models import JobConsolidation

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            batch = session.query(JobConsolidation).one()
            assert batch.occurrence_count == 3  # 批次未被修改
    finally:
        engine.dispose()


def test_finalize_rejects_existing_batch_without_review_metadata(
    monkeypatch, tmp_path
) -> None:
    """已有批次缺少审核元数据时不得无依据宣称一致。"""
    from app.consolidation import (
        ConsolidatorMetadata,
        load_consolidation_selection,
        persist_consolidation,
        scope_key_for,
    )

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = _decisions_file(tmp_path, raw["input_fingerprint"])
    final_path = _write_final_result(tmp_path, raw_path, decisions_path)

    # 直接以生产入口语义建一份无审核元数据的已有批次。
    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            selection = load_consolidation_selection(session, job_ids={1})
            metadata = ConsolidatorMetadata(
                model_name="test-model",
                prompt_version=CONSOLIDATION_PROMPT_VERSION,
                schema_version=CONSOLIDATION_SCHEMA_VERSION,
            )
            persist_consolidation(
                session,
                selection,
                _valid_result(),
                {"model_response": {"canonical_requirements": []}},
                metadata,
                scope_key_for({1}),
            )
    finally:
        engine.dispose()

    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            final_path, decisions_path,
        )
        == 1
    )


def test_finalize_rejects_placeholder_canonical_name(
    monkeypatch, tmp_path
) -> None:
    """最终结果包含审核占位名称时拒绝定稿。"""
    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_path = _decisions_file(tmp_path, raw["input_fingerprint"])

    def mutate(result_dict):
        result_dict["canonical_requirements"][0][
            "canonical_name"
        ] = "（拆分）实例71"

    final_path = _write_final_result(
        tmp_path, raw_path, decisions_path, mutate=mutate
    )
    assert (
        _run_finalize_with_review(
            monkeypatch, tmp_path, report_path, raw_path, db_path,
            final_path, decisions_path,
        )
        == 1
    )


def test_finalize_rejects_invalid_reviewed_at(monkeypatch, tmp_path) -> None:
    """reviewed_at 缺失或格式无效时拒绝定稿。"""
    from app.database import create_database_engine, create_session_factory
    from app.models import JobConsolidation

    db_path = tmp_path / "finalize.db"
    _seed_database(db_path)
    report_path, raw_path = _write_inputs(tmp_path, db_path)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["manual_cluster_review"]["reviewed_at"] = "not-a-date"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )

    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1

    report["manual_cluster_review"]["reviewed_at"] = ""
    report_path.write_text(
        json.dumps(report, ensure_ascii=False), encoding="utf-8"
    )
    assert _run_finalize(monkeypatch, tmp_path, report_path, raw_path, db_path) == 1

    engine = create_database_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            assert session.query(JobConsolidation).count() == 0
    finally:
        engine.dispose()


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
