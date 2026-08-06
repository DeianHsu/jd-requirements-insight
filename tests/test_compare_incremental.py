"""compare_incremental 增量稳定性比较核心测试（离线、不调用模型）。

覆盖验收：

1. 旧 singleton ID 小于同簇成员时仍正确计入吸收；
2. 旧 singleton ID 大于同簇成员时结果相同；
3. 外部旧 ID 集合缺一条时拒绝；
4. 外部旧 ID 集合多一条时拒绝；
5. 新 raw selected_job_ids 不符时拒绝；
6. 新 raw input fingerprint 不符时拒绝；
7. 某观察缺失一个 requirement ID 时拒绝；
8. 某观察结构违规时拒绝；
9. canonical ID 改名但成员相同，不产生漂移；
10. 私有输出包含完整成员、名称、来源 JD 和 evidence；
11. 新增实例扩员和旧实例错误合并能够区分。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.database import (
    create_database_engine,
    create_session_factory,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationResult,
    build_mappings_from_canonical_partition,
)


def _result(*clusters: list[int], rename: dict | None = None) -> dict:
    """构造规范化结果 dict；rename 用于 canonical ID 改名测试。"""
    canonicals = []
    for index, ids in enumerate(clusters):
        cid = f"cr-{index}"
        if rename and cid in rename:
            cid = rename[cid]
        canonicals.append(
            CanonicalRequirement(
                canonical_requirement_id=cid,
                canonical_name=f"条件{index}",
                source_requirement_ids=list(ids),
                rationale="测试",
                confidence=0.9,
            )
        )
    result = RequirementConsolidationResult(
        canonical_requirements=canonicals,
        mappings=build_mappings_from_canonical_partition(canonicals),
    )
    return result.model_dump(mode="json")


def _seed_five_job_db(database_path: Path) -> tuple[dict, dict]:
    """合成库：3 JD 旧批次（8 实例）+ 追加 JD4（实例 9）。"""
    from tests.test_market_report import _seed_market_db
    from app.models import JobDescription, JobExtraction, JobRequirement
    from datetime import date

    _seed_market_db(database_path)
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = JobDescription(
                source_hash="m4" + "a" * 62,
                source_file="sample-4.md",
                source_type="test",
                collected_at=date(2026, 8, 1),
                company="示例新科技",
                title="AI 平台工程师",
                city="杭州",
                company_type="medium_company",
                tags=[],
                extra_metadata={},
                raw_text="# AI 平台工程师\n\n职责与要求。",
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
            requirement = JobRequirement(
                extraction_id=extraction.id,
                raw_name="新增平台条件",
                category="other",
                importance="preferred",
                proficiency="basic",
                group_id=None,
                group_logic="standalone",
                min_years=None,
                max_years=None,
                years_text=None,
                evidence="有 AI 平台建设经验者优先。",
                confidence=0.9,
            )
            session.add(requirement)
            session.flush()
            session.commit()
    finally:
        engine.dispose()

    # 旧批次身份（3 JD）与新选择身份（4 JD）。
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            from app.consolidation import load_consolidation_selection
            from app.consolidation_validation import (
                load_persisted_consolidation_result,
            )

            old = load_persisted_consolidation_result(session_factory, 1)
            old_ids = sorted(old.expected_requirement_ids)
            new_selection = load_consolidation_selection(
                session, job_ids={1, 2, 3, 4}
            )
    finally:
        engine.dispose()
    return {
        "old_ids": old_ids,
        "new_fingerprint": new_selection.input_fingerprint,
        "new_extractor": new_selection.extractor_version,
    }, {
        "old_ids": old_ids,
        "new_fingerprint": new_selection.input_fingerprint,
    }


def _write_raw(tmp_path: Path, runs: list[dict], identity: dict) -> Path:
    payload = {
        "extractor_version": "test-model|prompt:0.10|schema:3.0",
        "input_fingerprint": identity["new_fingerprint"],
        "selected_job_ids": [1, 2, 3, 4],
        "runs": runs,
    }
    path = tmp_path / "new-raw.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _run_compare(
    monkeypatch, tmp_path, database_path, raw_path, old_ids_path=None
) -> int:
    import scripts.experiments.p0_4.compare_incremental as compare

    argv = [
        "compare_incremental",
        "--consolidation-id",
        "1",
        "--new-job-ids",
        "1",
        "2",
        "3",
        "4",
        "--new-raw-output",
        str(raw_path),
        "--review-decisions",
        str(tmp_path / "decisions.json"),
        "--report",
        str(tmp_path / "summary.json"),
        "--analysis-output",
        str(tmp_path / "analysis.json"),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
    ]
    if old_ids_path is not None:
        argv += ["--old-requirement-ids", str(old_ids_path)]
    monkeypatch.setattr(sys, "argv", argv)
    return compare.main()


def _decisions_file(tmp_path: Path) -> Path:
    path = tmp_path / "decisions.json"
    path.write_text(
        json.dumps(
            {
                "input_fingerprint": "x",
                "decisions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _baseline_raw(tmp_path: Path, identity: dict) -> Path:
    # 观察1：数据(3)与学历(8)合并（3 为 pair 中较小 singleton）、
    #        协作(2,5)保持、编程(1,4,7)保持、新增(9)并入协作（扩员）。
    runs = [
        {
            "result": _result(
                [1, 4, 7], [2, 5, 9], [3, 8], [6]
            )
        },
        {
            "result": _result([1, 4, 7], [2, 5], [3], [6], [8], [9])
        },
    ]
    return _write_raw(tmp_path, runs, identity)


def test_singleton_absorption_independent_of_id_size(
    monkeypatch, tmp_path
) -> None:
    """旧 singleton 无论 ID 大小都计入吸收（3 与 8 同簇均被识别）。"""
    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    assert _run_compare(monkeypatch, tmp_path, database_path, raw_path) == 0
    analysis = json.loads(
        (tmp_path / "analysis.json").read_text(encoding="utf-8")
    )
    absorbed = {
        entry["singleton"] for entry in analysis["absorbed_singletons"]
    }
    assert 3 in absorbed  # ID 小于同簇成员（8）
    assert 8 in absorbed  # ID 大于同簇成员（3）——结果一致
    assert 6 not in absorbed  # 未与任何旧实例同簇


def test_external_old_ids_must_equal_formal_batch(tmp_path) -> None:
    """外部旧 ID 集合与正式批次不一致（缺/多）时拒绝。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    # 缺一条。
    missing = tmp_path / "missing.txt"
    missing.write_text(
        "\n".join(str(i) for i in identity["old_ids"][1:]), encoding="utf-8"
    )
    _sys = sys
    _sys.argv = [
        "compare_incremental",
        "--consolidation-id",
        "1",
        "--new-job-ids",
        "1", "2", "3", "4",
        "--new-raw-output",
        str(raw_path),
        "--old-requirement-ids",
        str(missing),
        "--review-decisions",
        str(tmp_path / "decisions.json"),
        "--report",
        str(tmp_path / "summary.json"),
        "--analysis-output",
        str(tmp_path / "analysis.json"),
        "--database-url",
        f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 1

    # 多一条。
    extra = tmp_path / "extra.txt"
    extra.write_text(
        "\n".join(str(i) for i in identity["old_ids"] + [999]),
        encoding="utf-8",
    )
    _sys.argv[-11] = str(extra)
    assert compare_main() == 1


def test_raw_identity_gates(tmp_path) -> None:
    """新 raw selected_job_ids 或 input fingerprint 不符时拒绝。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["selected_job_ids"] = [1, 2, 3]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    sys.argv = [
        "compare_incremental",
        "--consolidation-id", "1",
        "--new-job-ids", "1", "2", "3", "4",
        "--new-raw-output", str(raw_path),
        "--review-decisions", str(tmp_path / "decisions.json"),
        "--report", str(tmp_path / "summary.json"),
        "--analysis-output", str(tmp_path / "analysis.json"),
        "--database-url", f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 1

    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    raw["selected_job_ids"] = [1, 2, 3, 4]
    raw["input_fingerprint"] = "f" * 64
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert compare_main() == 1


def test_observation_contract_gates(tmp_path) -> None:
    """某观察缺 requirement 或结构违规时拒绝（不静默跳过）。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    # 观察 2 缺一个 mapping（8 被删）。
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    result2 = raw["runs"][1]["result"]
    result2["mappings"] = [
        m for m in result2["mappings"] if m["requirement_id"] != 8
    ]
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    sys.argv = [
        "compare_incremental",
        "--consolidation-id", "1",
        "--new-job-ids", "1", "2", "3", "4",
        "--new-raw-output", str(raw_path),
        "--review-decisions", str(tmp_path / "decisions.json"),
        "--report", str(tmp_path / "summary.json"),
        "--analysis-output", str(tmp_path / "analysis.json"),
        "--database-url", f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 1

    # 观察 1 结构违规（mapping 引用未知 canonical）。
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    result1 = raw["runs"][0]["result"]
    result1["mappings"][0]["canonical_requirement_id"] = "cr-unknown"
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert compare_main() == 1


def test_canonical_id_rename_no_drift(tmp_path) -> None:
    """canonical ID 改名但成员相同，不产生漂移。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    _decisions_file(tmp_path)

    runs = [
        {"result": _result([1, 4, 7], [2, 5], [3], [6], [8], [9])},
        {
            "result": _result(
                [1, 4, 7], [2, 5], [3], [6], [8], [9],
                rename={"cr-0": "cr-x", "cr-1": "cr-y"},
            )
        },
    ]
    raw_path = _write_raw(tmp_path, runs, identity)
    sys.argv = [
        "compare_incremental",
        "--consolidation-id", "1",
        "--new-job-ids", "1", "2", "3", "4",
        "--new-raw-output", str(raw_path),
        "--review-decisions", str(tmp_path / "decisions.json"),
        "--report", str(tmp_path / "summary.json"),
        "--analysis-output", str(tmp_path / "analysis.json"),
        "--database-url", f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 0
    summary = json.loads(
        (tmp_path / "summary.json").read_text(encoding="utf-8")
    )
    assert summary["split_pair_count"] == 0
    assert summary["new_merge_pair_count"] == 0
    assert summary["must_link_broken_count"] == 0


def test_private_output_has_full_membership_and_evidence(tmp_path) -> None:
    """私有输出包含完整成员、名称、来源 JD 和 evidence。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    sys.argv = [
        "compare_incremental",
        "--consolidation-id", "1",
        "--new-job-ids", "1", "2", "3", "4",
        "--new-raw-output", str(raw_path),
        "--review-decisions", str(tmp_path / "decisions.json"),
        "--report", str(tmp_path / "summary.json"),
        "--analysis-output", str(tmp_path / "analysis.json"),
        "--database-url", f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 0
    analysis = json.loads(
        (tmp_path / "analysis.json").read_text(encoding="utf-8")
    )
    absorbed = next(
        e for e in analysis["absorbed_singletons"] if e["singleton"] == 3
    )
    details = absorbed["per_observation_full_members"][0]["member_details"]
    assert details  # 完整成员
    assert any(
        entry["requirement_id"] == 8 and entry["raw_name"] == "学历"
        for entry in details
    )
    assert all("job_id" in entry and "evidence" in entry for entry in details)


def test_expansion_distinguished_from_old_merge(tmp_path) -> None:
    """新增实例扩员与旧实例错误合并能够区分。"""
    from scripts.experiments.p0_4.compare_incremental import main as compare_main

    database_path = tmp_path / "cmp.db"
    identity, _ = _seed_five_job_db(database_path)
    raw_path = _baseline_raw(tmp_path, identity)
    _decisions_file(tmp_path)

    sys.argv = [
        "compare_incremental",
        "--consolidation-id", "1",
        "--new-job-ids", "1", "2", "3", "4",
        "--new-raw-output", str(raw_path),
        "--review-decisions", str(tmp_path / "decisions.json"),
        "--report", str(tmp_path / "summary.json"),
        "--analysis-output", str(tmp_path / "analysis.json"),
        "--database-url", f"sqlite:///{database_path.as_posix()}",
    ]
    assert compare_main() == 0
    summary = json.loads(
        (tmp_path / "summary.json").read_text(encoding="utf-8")
    )
    analysis = json.loads(
        (tmp_path / "analysis.json").read_text(encoding="utf-8")
    )
    # 扩员：协作 [2,5] 观察 0 加入新增实例 9。
    assert summary["expansion_count"] >= 1
    assert any(
        entry["old_members"] == [2, 5] and entry["new_members_added"] == [9]
        for entry in analysis["expansions"]
    )
    # 旧合并：3 与 8 同簇（旧批次无此对）→ new_merge。
    assert (3, 8) in [
        tuple(entry["pair"]) for entry in analysis["new_merges"]
    ]
