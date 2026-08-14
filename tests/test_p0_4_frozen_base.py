"""增量归并审核以已批准 final result 作为冻结基线的定向测试。"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
    load_consolidation_selection,
)
from app.consolidation_validation import result_fingerprint
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.models import JobDescription, JobExtraction, JobRequirement
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)


EXTRACTOR_VERSION = "test-model|prompt:0.10|schema:3.0"


def _seed_database(path: Path) -> None:
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            requirement_names = (
                ("编程语言", "数据分析经验"),
                ("后端开发",),
            )
            for job_index, names in enumerate(requirement_names, 1):
                job = JobDescription(
                    source_hash=str(job_index) * 64,
                    source_file=f"job-{job_index}.md",
                    source_type="test",
                    collected_at=date(2026, 8, 14),
                    company=f"示例公司{job_index}",
                    title=f"示例岗位{job_index}",
                    company_type="medium_company",
                    tags=[],
                    extra_metadata={},
                    raw_text="\n".join(names),
                )
                session.add(job)
                session.flush()
                extraction = JobExtraction(
                    job_id=job.id,
                    extractor_version=EXTRACTOR_VERSION,
                    model_name="test-model",
                    prompt_version="0.10",
                    schema_version="3.0",
                    role_family="other",
                    seniority="unknown",
                    raw_response={},
                )
                session.add(extraction)
                session.flush()
                for name in names:
                    session.add(
                        JobRequirement(
                            extraction_id=extraction.id,
                            raw_name=name,
                            category="other",
                            importance="must",
                            proficiency="basic",
                            group_id=None,
                            group_logic="standalone",
                            evidence=name,
                            confidence=0.9,
                        )
                    )
            session.commit()
    finally:
        engine.dispose()


def _selection_fingerprint(path: Path, job_ids: set[int]) -> str:
    engine = create_database_engine(f"sqlite:///{path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            return load_consolidation_selection(
                session,
                job_ids=job_ids,
                extractor_version=EXTRACTOR_VERSION,
            ).input_fingerprint
    finally:
        engine.dispose()


def _result(canonicals: list[CanonicalRequirement]) -> RequirementConsolidationResult:
    return RequirementConsolidationResult(
        canonical_requirements=canonicals,
        mappings=build_mappings_from_canonical_partition(canonicals),
    )


def _write_inputs(tmp_path: Path, database_path: Path) -> tuple[Path, Path, dict]:
    frozen_result = _result(
        [
            CanonicalRequirement(
                canonical_requirement_id="old-1",
                canonical_name="编程语言",
                source_requirement_ids=[1],
                rationale="已批准旧结果",
                confidence=0.9,
            ),
            CanonicalRequirement(
                canonical_requirement_id="old-2",
                canonical_name="数据分析经验",
                source_requirement_ids=[2],
                rationale="已批准旧结果",
                confidence=0.9,
            ),
        ]
    )
    frozen_fingerprint = result_fingerprint(frozen_result)
    inherited_review_fingerprint = "b" * 64
    frozen_payload = {
        "input_fingerprint": _selection_fingerprint(database_path, {1}),
        "extractor_version": EXTRACTOR_VERSION,
        "selected_job_ids": [1],
        "model": "test-model",
        "prompt_version": CONSOLIDATION_PROMPT_VERSION,
        "schema_version": CONSOLIDATION_SCHEMA_VERSION,
        "source_run_identifier": "old-run",
        "source_result_fingerprint": "a" * 64,
        "review_decisions_fingerprint": inherited_review_fingerprint,
        "reviewed_by": "tester",
        "reviewed_at": "2026-08-14T00:00:00+00:00",
        "result_fingerprint": frozen_fingerprint,
        "result": frozen_result.model_dump(mode="json"),
    }
    frozen_path = tmp_path / "frozen-final.json"
    frozen_path.write_text(
        json.dumps(frozen_payload, ensure_ascii=False), encoding="utf-8"
    )

    source_result = _result(
        [
            CanonicalRequirement(
                canonical_requirement_id="run-old-wrong",
                canonical_name="旧范围错误合并",
                source_requirement_ids=[1, 2],
                rationale="来源运行",
                confidence=0.8,
            ),
            CanonicalRequirement(
                canonical_requirement_id="run-new",
                canonical_name="后端开发",
                source_requirement_ids=[3],
                rationale="来源运行",
                confidence=0.9,
            ),
        ]
    )
    source_fingerprint = result_fingerprint(source_result)
    full_input_fingerprint = _selection_fingerprint(database_path, {1, 2})
    raw = {
        "input_fingerprint": full_input_fingerprint,
        "extractor_version": EXTRACTOR_VERSION,
        "selected_job_ids": [1, 2],
        "model": "test-model",
        "prompt_version": CONSOLIDATION_PROMPT_VERSION,
        "schema_version": CONSOLIDATION_SCHEMA_VERSION,
        "runs": [
            {
                "run_identifier": "run-0",
                "result_fingerprint": source_fingerprint,
                "result": source_result.model_dump(mode="json"),
            }
        ],
    }
    raw_path = tmp_path / "raw.json"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    frozen_contract = {
        "input_fingerprint": frozen_payload["input_fingerprint"],
        "result_fingerprint": frozen_fingerprint,
        "review_decisions_fingerprint": inherited_review_fingerprint,
        "selected_job_ids": [1],
        "requirement_ids": [1, 2],
        "canonical_count": 2,
        "mapping_count": 2,
    }
    return raw_path, frozen_path, frozen_contract


def _run_apply(
    monkeypatch,
    tmp_path: Path,
    database_path: Path,
    raw_path: Path,
    frozen_path: Path,
    frozen_contract: dict,
    decisions: list[dict],
) -> int:
    import scripts.experiments.p0_4.apply_review_decisions as apply_script

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    decisions_payload = {
        "input_fingerprint": raw["input_fingerprint"],
        "extractor_version": raw["extractor_version"],
        "selected_job_ids": raw["selected_job_ids"],
        "model": raw["model"],
        "prompt_version": raw["prompt_version"],
        "schema_version": raw["schema_version"],
        "reviewed_by": "tester",
        "reviewed_at": "2026-08-14T00:00:00+00:00",
        "frozen_base": frozen_contract,
        "decisions": decisions,
    }
    decisions_path = tmp_path / "decisions.json"
    decisions_path.write_text(
        json.dumps(decisions_payload, ensure_ascii=False), encoding="utf-8"
    )
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
            "--frozen-base",
            str(frozen_path),
            "--output",
            str(tmp_path / "final.json"),
            "--report",
            str(tmp_path / "summary.json"),
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
        ],
    )
    return apply_script.main()


def _member_owner(result: dict) -> dict[int, str]:
    return {
        requirement_id: canonical["canonical_requirement_id"]
        for canonical in result["canonical_requirements"]
        for requirement_id in canonical["source_requirement_ids"]
    }


def test_frozen_base_applies_incremental_merge_without_replaying_old_partition(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    decisions = [
        {
            "decision": "must_link",
            "requirement_ids": [1, 3],
            "canonical_name": "编程语言",
            "rationale": "新增要求并入已批准 canonical",
        }
    ]

    assert _run_apply(
        monkeypatch,
        tmp_path,
        database_path,
        raw_path,
        frozen_path,
        contract,
        decisions,
    ) == 0
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    owners = _member_owner(final["result"])
    assert owners[1] == "old-1"
    assert owners[2] == "old-2"
    assert owners[3] == "old-1"
    assert len(final["result"]["mappings"]) == 3
    assert final["frozen_base"]["result_fingerprint"] == contract[
        "result_fingerprint"
    ]
    assert final["review_decisions_fingerprint"]
    assert final["result_fingerprint"] == result_fingerprint(
        RequirementConsolidationResult.model_validate(final["result"])
    )


def test_frozen_base_rejects_fingerprint_mismatch(monkeypatch, tmp_path) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    contract["result_fingerprint"] = "f" * 64

    assert _run_apply(
        monkeypatch, tmp_path, database_path, raw_path, frozen_path, contract, []
    ) == 1
    assert not (tmp_path / "final.json").exists()


def test_frozen_base_rejects_incomplete_final_artifact(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
    del frozen["reviewed_at"]
    frozen_path.write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")

    assert _run_apply(
        monkeypatch, tmp_path, database_path, raw_path, frozen_path, contract, []
    ) == 1
    assert not (tmp_path / "final.json").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [("requirement_ids", [1]), ("canonical_count", 3), ("mapping_count", 1)],
)
def test_frozen_base_rejects_id_or_count_mismatch(
    monkeypatch, tmp_path, field, value
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    contract[field] = value

    assert _run_apply(
        monkeypatch, tmp_path, database_path, raw_path, frozen_path, contract, []
    ) == 1
    assert not (tmp_path / "final.json").exists()


def test_frozen_base_rejects_pure_old_partition_decision(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    decisions = [
        {
            "decision": "must_link",
            "requirement_ids": [1, 2],
            "rationale": "非法修改旧分区",
        }
    ]

    assert _run_apply(
        monkeypatch,
        tmp_path,
        database_path,
        raw_path,
        frozen_path,
        contract,
        decisions,
    ) == 1
    assert not (tmp_path / "final.json").exists()


def test_frozen_base_rejects_indirect_merge_of_two_old_canonicals(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    decisions = [
        {
            "decision": "must_link",
            "requirement_ids": [1, 3],
            "canonical_name": "编程语言",
            "rationale": "先把新增项并入第一个旧 canonical",
        },
        {
            "decision": "must_link",
            "requirement_ids": [2, 3],
            "rationale": "试图经新增项间接合并第二个旧 canonical",
        },
    ]

    assert _run_apply(
        monkeypatch,
        tmp_path,
        database_path,
        raw_path,
        frozen_path,
        contract,
        decisions,
    ) == 1
    assert not (tmp_path / "final.json").exists()


def test_frozen_base_keeps_new_requirement_independent(
    monkeypatch, tmp_path
) -> None:
    database_path = tmp_path / "test.db"
    _seed_database(database_path)
    raw_path, frozen_path, contract = _write_inputs(tmp_path, database_path)
    decisions = [
        {
            "decision": "cannot_link",
            "requirement_ids": [1, 3],
            "rationale": "新增要求保持独立",
        }
    ]

    assert _run_apply(
        monkeypatch,
        tmp_path,
        database_path,
        raw_path,
        frozen_path,
        contract,
        decisions,
    ) == 0
    final = json.loads((tmp_path / "final.json").read_text(encoding="utf-8"))
    owners = _member_owner(final["result"])
    assert owners[1] == "old-1"
    assert owners[2] == "old-2"
    assert owners[3] == "incremental-3"
    assert len(final["result"]["canonical_requirements"]) == 3
