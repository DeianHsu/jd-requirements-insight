"""P0-4 验收脚本端到端与门槛测试。

覆盖：默认使用 v0.10 + Schema V3 并拒绝旧输入；门槛只含合同违规与
positive-pair Jaccard（warning）；一次完整的 P0-4 验收（假归并客户端）
能生成报告且含人工 cluster 复核清单。
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.database import create_database_engine, create_session_factory, initialize_database
from app.models import JobDescription, JobExtraction, JobRequirement
from scripts.experiments.p0_4.run_acceptance import (
    evaluate_gates,
    resolve_extractor_version,
)


def test_resolve_extractor_version_defaults_to_none() -> None:
    """缺省返回 None，由生产选择逻辑自动选择唯一共同当前抽取版本。"""
    resolved = resolve_extractor_version(None)
    assert resolved is None


def test_resolve_extractor_version_rejects_legacy_schema() -> None:
    """显式传入非 Schema V3 版本被拒绝并提示重新抽取。"""
    for legacy in (
        "deepseek-v4-flash|prompt:2.3.1|schema:2.0",
        "test-model|prompt:0.6|schema:2.0",
    ):
        with pytest.raises(SystemExit, match="重新生成"):
            resolve_extractor_version(legacy)


def test_resolve_extractor_version_accepts_valid_version() -> None:
    """显式合法版本正常返回。"""
    resolved = resolve_extractor_version("test-model|prompt:0.10|schema:3.0")
    assert resolved == "test-model|prompt:0.10|schema:3.0"


def _mapping(
    requirement_id: int, canonical_id: str, confidence: float = 0.95
) -> dict:
    """构造一条映射（与归并合同一致）。"""
    return {
        "requirement_id": requirement_id,
        "canonical_requirement_id": canonical_id,
        "rationale": "测试映射",
        "confidence": confidence,
    }


def _canonical(canonical_id: str, name: str, source_ids: list[int]) -> dict:
    """构造一个标准要求项。"""
    return {
        "canonical_requirement_id": canonical_id,
        "canonical_name": name,
        "source_requirement_ids": source_ids,
        "rationale": "测试归并",
        "confidence": 0.95,
    }


def _result_from_clusters(clusters: list[list[int]], names: list[str]):
    """按实例分组构造合法归并结果（cluster内的实例映射同一标准项）。"""
    from app.requirement_consolidation import RequirementConsolidationResult

    canonical_requirements = []
    mappings = []
    for cluster_index, cluster in enumerate(clusters):
        canonical_requirements.append(
            _canonical(f"cr-{cluster_index}", names[cluster[0]], list(cluster))
        )
        for requirement_id in cluster:
            mappings.append(
                _mapping(requirement_id, f"cr-{cluster_index}")
            )
    return RequirementConsolidationResult(
        canonical_requirements=canonical_requirements,
        mappings=mappings,
    )


def test_gates_report_positive_pair_jaccard_warnings() -> None:
    """门槛：合同违规为 hard gate；positive-pair Jaccard 下降进 warning。"""
    names = ["甲", "乙", "丙", "丁", "戊"]
    stable = _result_from_clusters([[1, 2, 3], [4]], names)
    split = _result_from_clusters([[1, 2], [3], [4]], names)

    from app.consolidation_validation import validate_contract

    contract_violations = [
        validate_contract(run, expected_ids={1, 2, 3, 4})
        for run in (stable, split)
    ]
    hard_gate_failures, warnings = evaluate_gates(
        [{"result": stable}, {"result": split}], contract_violations
    )

    # 合同无违规；拆分导致同簇对 Jaccard 下降 → warning。
    assert hard_gate_failures == []
    assert any("positive_pair_jaccard" in item for item in warnings)

    # 完全一致时无 warning 无 hard gate。
    identical_hard, identical_warnings = evaluate_gates(
        [{"result": stable}, {"result": stable}],
        [contract_violations[0], contract_violations[0]],
    )
    assert identical_hard == []
    assert identical_warnings == []


def test_gates_contract_violations_are_hard_gate() -> None:
    """合同违规（coverage 缺失）直接构成 hard gate。"""
    from app.consolidation_validation import validate_contract

    result = _result_from_clusters([[1, 2], [3]], ["甲", "乙", "丙", "丁"])
    result.mappings.pop()  # 制造覆盖缺失
    contract = validate_contract(result, expected_ids={1, 2, 3})

    hard_gate_failures, _ = evaluate_gates(
        [{"result": result}], [contract]
    )

    assert any("coverage" in item for item in hard_gate_failures)


def _seed_current_extraction(database_path: Path) -> None:
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
                raw_text="# 示例岗位\n\n负责能力甲体系建设。\n\n"
                "熟悉技术甲。\n\n具备能力乙使用经验。",
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
                raw_response={
                    "role_family": "other",
                    "seniority": "unknown",
                    "requirements": [],
                },
            )
            session.add(extraction)
            session.flush()
            for index, (raw_name, evidence) in enumerate(
                [
                    ("技术甲", "熟悉技术甲"),
                    ("能力乙", "具备能力乙使用经验"),
                    ("技术甲", "熟悉技术甲"),
                ]
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


class FakeConsolidationClient:
    """返回单次合法 canonical 聚类响应，不调用真实模型。"""

    def __init__(self, settings) -> None:
        """保存模型名。"""
        self.model_name = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回单次合法 canonical 聚类响应（mappings 由确定性代码生成）。"""
        return json.dumps(
            {
                "canonical_requirements": [
                    {"canonical_requirement_id": "cr-0",
                     "canonical_name": "技术甲",
                     "source_requirement_ids": [1, 3],
                     "rationale": "测试",
                     "confidence": 0.95},
                    {"canonical_requirement_id": "cr-1",
                     "canonical_name": "能力乙",
                     "source_requirement_ids": [2],
                     "rationale": "测试",
                     "confidence": 0.95},
                ]
            },
            ensure_ascii=False,
        )


def test_p0_4_acceptance_end_to_end(monkeypatch, tmp_path) -> None:
    """P0-4 完整验收：假归并客户端 → 报告生成（端到端）。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "p0_4.db"
    _seed_current_extraction(database_path)

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "3",
            "--report",
            str(tmp_path / "report.json"),
            "--raw-output",
            str(tmp_path / "raw.json"),
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    assert acceptance_script.main() == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["input_identity"]["extractor_version"] == "test-model|prompt:0.10|schema:3.0"
    stability = report["p0_4_stability"]
    assert stability["canonical_count_max"] >= stability["canonical_count_min"]
    assert report["p0_4_contract"]["coverage"] == 1.0
    assert report["manual_cluster_review"]["clusters"]
    # 多成员 cluster 出现在人工复核清单中。
    multi_member = report["manual_cluster_review"]["clusters"][0]["multi_member_clusters"]
    assert any(cluster["canonical_id"] == "cr-0" for cluster in multi_member)
    assert (tmp_path / "raw.json").exists()


def test_default_extractor_version_auto_selects_unique_common(
    monkeypatch, tmp_path
) -> None:
    """缺省时自动选择数据库中的唯一共同当前版本（归并模型名不参与拼接）。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "auto.db"
    _seed_current_extraction(database_path)

    class FakeSettings:
        model = "consolidation-model"  # 归并模型名与抽取模型名不同
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
            "--report",
            str(tmp_path / "report.json"),
            "--raw-output",
            str(tmp_path / "raw.json"),
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    assert acceptance_script.main() == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    # 自动选中库中唯一 v0.10 抽取版本，而不是用归并模型名拼接。
    assert report["input_identity"]["extractor_version"] == (
        "test-model|prompt:0.10|schema:3.0"
    )
    assert report["input_identity"]["model"] == "consolidation-model"


def test_auto_selected_legacy_version_is_rejected(
    monkeypatch, tmp_path
) -> None:
    """库中唯一抽取版本是旧 v0.6：缺省自动选择后在模型调用前被版本门禁拒绝。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "legacy_version.db"
    _seed_current_extraction(database_path)

    from app.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )
    from app.models import JobExtraction

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        extraction = session.get(JobExtraction, 1)
        assert extraction is not None
        extraction.extractor_version = "test-model|prompt:0.6|schema:2.0"
        session.commit()
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        """若被初始化/调用则测试失败（版本门禁必须在模型调用前拒绝）。"""

        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", ExplodingClient
    )

    # 缺省自动选择唯一版本 v0.6 → 版本门禁拒绝（不是模型调用失败）。
    result = acceptance_script.main()
    assert result != 0


def test_multiple_common_current_versions_require_explicit_selection(
    monkeypatch, tmp_path
) -> None:
    """同一 JD 同时拥有两个合法当前抽取版本：缺省拒绝并提示显式指定。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "multi_current.db"
    _seed_current_extraction(database_path)

    from app.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )
    from app.models import JobExtraction, JobRequirement

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        # 第二条独立抽取记录：同一 JD、不同模型名、同为 v0.10 + Schema V3。
        second = JobExtraction(
            job_id=1,
            extractor_version="extractor-b|prompt:0.10|schema:3.0",
            model_name="extractor-b",
            prompt_version="0.10",
            schema_version="3.0",
            role_family="other",
            seniority="unknown",
            raw_response={},
        )
        session.add(second)
        session.flush()
        # 第二条抽取带对应要求实例，确保选中后输入完整。
        session.add(
            JobRequirement(
                extraction_id=second.id,
                raw_name="技术甲",
                category="other",
                importance="must",
                proficiency="basic",
                group_id=None,
                group_logic="standalone",
                min_years=None,
                max_years=None,
                years_text=None,
                evidence="熟悉技术甲",
                confidence=0.9,
            )
        )
        session.commit()
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        """若被初始化/调用则测试失败（多共同版本必须在模型调用前拒绝）。"""

        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", ExplodingClient
    )

    # 缺省自动选择失败：存在多个共同抽取版本，要求显式指定。
    result = acceptance_script.main()
    assert result != 0


def test_explicit_current_version_is_used_when_multiple_exist(
    monkeypatch, tmp_path
) -> None:
    """多共同版本存在时显式指定其中一个合法版本可正常选择。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "multi_explicit.db"
    _seed_current_extraction(database_path)

    from app.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )
    from app.models import JobExtraction

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        second = JobExtraction(
            job_id=1,
            extractor_version="extractor-b|prompt:0.10|schema:3.0",
            model_name="extractor-b",
            prompt_version="0.10",
            schema_version="3.0",
            role_family="other",
            seniority="unknown",
            raw_response={},
        )
        session.add(second)
        session.commit()
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
            "--extractor-version",
            "test-model|prompt:0.10|schema:3.0",
            "--report",
            str(tmp_path / "report.json"),
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", FakeConsolidationClient
    )

    assert acceptance_script.main() == 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["input_identity"]["extractor_version"] == (
        "test-model|prompt:0.10|schema:3.0"
    )


def test_legacy_database_fails_before_client_initialization(
    monkeypatch, tmp_path, capsys
) -> None:
    """P0-4 验收遇到旧数据库时，在模型客户端初始化和调用前失败并提示重建。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "legacy_gate.db"
    from app.database import create_database_engine
    from sqlalchemy import text

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE requirement_relations (id INTEGER PRIMARY KEY)"
            )
        )
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        """若被初始化/调用则测试失败。"""

        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", ExplodingClient
    )

    assert acceptance_script.main() != 0
    output = capsys.readouterr().out
    assert "备份 data/raw_jds/" in output
    assert "重新生成" in output


def test_empty_database_is_input_error_without_rebuild_hint(
    monkeypatch, tmp_path, capsys
) -> None:
    """空库（无 JD）是普通输入错误：不提示删除旧派生数据库。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "empty.db"
    from app.database import create_database_engine, initialize_database

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)  # 全新空库（无业务表）→ 门禁通过
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", ExplodingClient
    )

    assert acceptance_script.main() != 0
    output = capsys.readouterr().out
    assert "选定范围内没有JD" in output or "指定JD不存在" in output
    assert "删除旧派生数据库" not in output
    assert "备份 data/raw_jds/" not in output


def test_multiple_versions_error_has_no_rebuild_hint(
    monkeypatch, tmp_path, capsys
) -> None:
    """多共同版本错误只提示显式指定，不提示删除数据库。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "multi_hint.db"
    _seed_current_extraction(database_path)

    from app.database import (
        create_database_engine,
        create_session_factory,
        initialize_database,
    )
    from app.models import JobExtraction

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        session.add(
            JobExtraction(
                job_id=1,
                extractor_version="extractor-b|prompt:0.10|schema:3.0",
                model_name="extractor-b",
                prompt_version="0.10",
                schema_version="3.0",
                role_family="other",
                seniority="unknown",
                raw_response={},
            )
        )
        session.commit()
    engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script, "OpenAICompatibleConsolidationClient", ExplodingClient
    )

    assert acceptance_script.main() != 0
    output = capsys.readouterr().out
    assert "多个共同抽取器版本" in output or "存在多个共同抽取器版本" in output
    assert "删除旧派生数据库" not in output


def test_order_transformation_contract_violation_is_hard_gate(
    monkeypatch, tmp_path
) -> None:
    """基础 runs 合法但顺序变形合同违规时 hard gate 非空、脚本返回非零。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "order_gate.db"
    _seed_current_extraction(database_path)

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class OrderViolatingClient:
        """对顺序打乱输入返回遗漏实例的聚类（模拟顺序敏感幻觉）。"""

        def __init__(self, settings) -> None:
            self.model_name = settings.model

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            payload = json.loads(user_prompt)
            requirement_ids = [item["id"] for item in payload["requirements"]]
            if requirement_ids != [1, 2, 3]:
                # 顺序变形运行（输入顺序被打乱）：遗漏实例 2。
                source_ids = [rid for rid in requirement_ids if rid != 2]
            else:
                source_ids = requirement_ids
            return json.dumps(
                {
                    "canonical_requirements": [
                        {"canonical_requirement_id": "cr-0",
                         "canonical_name": "技术甲",
                         "source_requirement_ids": source_ids,
                         "rationale": "测试",
                         "confidence": 0.95},
                    ]
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--execute",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--runs",
            "1",
            "--report",
            str(tmp_path / "report.json"),
        ],
    )
    monkeypatch.setattr(acceptance_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        acceptance_script,
        "OpenAICompatibleConsolidationClient",
        OrderViolatingClient,
    )

    # 顺序变形 coverage 不足 → hard gate（order_transformation: coverage）。
    assert acceptance_script.main() != 0

    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert any(
        "order_transformation" in failure
        for failure in report["hard_gate_failures"]
    )


def test_precheck_legacy_database_fails_cleanly(
    monkeypatch, tmp_path, capsys
) -> None:
    """小规模预检遇到旧数据库：返回非零、无 traceback、客户端不初始化。"""
    import scripts.experiments.p0_4.run_small_scale_precheck as precheck_script

    database_path = tmp_path / "precheck_legacy.db"
    from app.database import create_database_engine
    from sqlalchemy import text

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE requirement_relations (id INTEGER PRIMARY KEY)"
            )
        )
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class ExplodingClient:
        def __init__(self, settings) -> None:
            raise AssertionError("不应初始化模型客户端")

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_small_scale_precheck", "--execute"],
    )
    monkeypatch.setattr(precheck_script, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        precheck_script,
        "OpenAICompatibleConsolidationClient",
        ExplodingClient,
    )

    assert precheck_script.main() != 0
    output = capsys.readouterr().out
    assert "预检无法开始" in output
    assert "备份 data/raw_jds/" in output
    assert "Traceback" not in output


def test_precheck_input_error_has_no_rebuild_hint(
    monkeypatch, tmp_path, capsys
) -> None:
    """小规模预检遇到普通选择错误：返回非零且不提示删除数据库。"""
    import scripts.experiments.p0_4.run_small_scale_precheck as precheck_script

    database_path = tmp_path / "precheck_empty.db"
    from app.database import create_database_engine, initialize_database

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    initialize_database(engine)  # 全新空库 → 门禁通过，但无 JD
    engine.dispose()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database_path.as_posix()}")

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    monkeypatch.setattr(
        sys,
        "argv",
        ["run_small_scale_precheck", "--execute"],
    )
    monkeypatch.setattr(precheck_script, "load_llm_settings", lambda: FakeSettings())

    assert precheck_script.main() != 0
    output = capsys.readouterr().out
    assert "预检无法开始" in output
    assert "删除旧派生数据库" not in output
    assert "备份 data/raw_jds/" not in output


def test_precheck_input_is_job_stratified(tmp_path) -> None:
    """预检输入按 JD 分层配额：全部选定 JD 进入预检，确定性可审计。"""
    from tests.test_market_report import _seed_market_db
    from app.consolidation import load_consolidation_selection
    from scripts.experiments.p0_4.run_small_scale_precheck import (
        build_precheck_input,
        precheck_selection_summary,
    )

    tmp = tmp_path / "precheck_stratified.db"
    _seed_market_db(tmp)
    engine = create_database_engine(f"sqlite:///{tmp.as_posix()}")
    try:
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            selection = load_consolidation_selection(
                session, job_ids={1, 2, 3}
            )
            total = len(selection.consolidation_input.occurrences)
            # 旧实现（按 ID 升序前 N）会只选 JD1 实例；分层必须覆盖全部 JD。
            chosen = build_precheck_input(selection, target_size=total)
            assert len(chosen.occurrences) == total

            target = 6
            summary = precheck_selection_summary(selection, target_size=target)
            assert summary["selected_total"] == target
            assert set(summary["per_job_counts"]) == {1, 2, 3}  # 全部 JD 覆盖
            assert all(count >= 1 for count in summary["per_job_counts"].values())
            # 确定性：两次构造完全一致。
            again = precheck_selection_summary(selection, target_size=target)
            assert summary == again
            # 重复 ID 校验：不重复。
            chosen_ids = [
                occurrence.requirement_id for occurrence in chosen.occurrences
            ]
            assert len(chosen_ids) == len(set(chosen_ids))
    finally:
        engine.dispose()


def test_raw_response_structure_keeps_model_and_normalized_result() -> None:
    """raw_response 保存模型响应与规范化结果，attempt_count 正确。"""
    from app.consolidation import consolidate_with_correction
    from app.requirement_consolidation import (
        RequirementConsolidationInput,
        RequirementOccurrence,
    )
    from app.schemas import RequirementItem

    class SingleClient:
        def __init__(self) -> None:
            self.calls = 0

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            self.calls += 1
            return json.dumps(
                {
                    "canonical_requirements": [
                        {"canonical_requirement_id": "cr-0",
                         "canonical_name": "技术甲",
                         "source_requirement_ids": [1, 2],
                         "rationale": "测试",
                         "confidence": 0.95},
                    ]
                },
                ensure_ascii=False,
            )

    def requirement(raw_name: str, evidence: str) -> RequirementItem:
        return RequirementItem.model_validate(
            {
                "raw_name": raw_name,
                "category": "other",
                "importance": "must",
                "proficiency": "unknown",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": evidence,
                "confidence": 0.9,
            }
        )

    source = RequirementConsolidationInput(
        occurrences=[
            RequirementOccurrence(
                requirement_id=requirement_id,
                job_id=101,
                extraction_id=1001,
                extractor_version="test-model|prompt:0.10|schema:3.0",
                source_hash="a" * 64,
                source_file="job-a.md",
                requirement=requirement("技术甲", "熟悉技术甲"),
            )
            for requirement_id in (1, 2)
        ]
    )

    result, raw = consolidate_with_correction(source, SingleClient(), max_attempts=1)

    assert len(result.mappings) == 2
    # model_response 是模型原始输出，不含确定性 mappings。
    assert "model_response" in raw
    assert "canonical_requirements" in raw["model_response"]
    assert "mappings" not in raw["model_response"]
    # normalized_result 包含确定性生成的 mappings。
    assert "normalized_result" in raw
    assert len(raw["normalized_result"]["mappings"]) == 2
    assert raw["attempt_count"] == 1
