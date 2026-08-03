"""P0-4 验收脚本端到端与门槛测试。

覆盖：默认使用 v0.8 + Schema V3 并拒绝旧输入；门槛只含合同违规与
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


def test_resolve_extractor_version_defaults_to_current_config() -> None:
    """缺省时使用当前唯一抽取配置 v0.8 + Schema V3。"""
    resolved = resolve_extractor_version(None, "test-model")
    assert resolved == "test-model|prompt:0.8|schema:3.0"


def test_resolve_extractor_version_rejects_legacy_schema() -> None:
    """显式传入非 Schema V3 版本被拒绝并提示重新抽取。"""
    for legacy in (
        "deepseek-v4-flash|prompt:2.3.1|schema:2.0",
        "test-model|prompt:0.6|schema:2.0",
    ):
        with pytest.raises(SystemExit, match="重新抽取"):
            resolve_extractor_version(legacy, "test-model")


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


def _canonical(canonical_id: str, name: str) -> dict:
    """构造一个标准要求项。"""
    return {
        "canonical_requirement_id": canonical_id,
        "canonical_name": name,
        "rationale": "测试归并",
        "confidence": 0.95,
    }


def _result_from_clusters(clusters: list[list[int]], names: list[str]):
    """按实例分组构造合法归并结果（cluster内的实例映射同一标准项）。"""
    from app.requirement_consolidation import RequirementConsolidationResult

    canonical_requirements = []
    mappings = []
    for cluster_index, cluster in enumerate(clusters):
        canonical_requirements.append(_canonical(f"cr-{cluster_index}", names[cluster[0]]))
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


def _seed_v08_extraction(database_path: Path) -> None:
    """向临时数据库写入一份 v0.8 + Schema V3 抽取结果（3 条要求实例）。"""
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
                extractor_version="test-model|prompt:0.8|schema:3.0",
                model_name="test-model",
                prompt_version="0.8",
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
    """按阶段返回合法归并响应（canonical → mappings），不调用真实模型。"""

    def __init__(self, settings) -> None:
        """保存模型名。"""
        self.model_name = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """按阶段提示返回响应。"""
        if "只输出canonical_requirements" in user_prompt:
            return json.dumps(
                {
                    "canonical_requirements": [
                        {"canonical_requirement_id": "cr-0",
                         "canonical_name": "技术甲",
                         "rationale": "测试",
                         "confidence": 0.95},
                        {"canonical_requirement_id": "cr-1",
                         "canonical_name": "能力乙",
                         "rationale": "测试",
                         "confidence": 0.95},
                    ]
                },
                ensure_ascii=False,
            )
        # 映射阶段：每个实例恰好映射一次。
        return json.dumps(
            {
                "mappings": [
                    {"requirement_id": 1, "canonical_requirement_id": "cr-0",
                     "rationale": "测试", "confidence": 0.95},
                    {"requirement_id": 2, "canonical_requirement_id": "cr-1",
                     "rationale": "测试", "confidence": 0.95},
                    {"requirement_id": 3, "canonical_requirement_id": "cr-0",
                     "rationale": "测试", "confidence": 0.95},
                ]
            },
            ensure_ascii=False,
        )


def test_p0_4_acceptance_end_to_end(monkeypatch, tmp_path) -> None:
    """P0-4 完整验收：假归并客户端 → 报告生成（端到端）。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "p0_4.db"
    _seed_v08_extraction(database_path)

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
    assert report["input_identity"]["extractor_version"] == "test-model|prompt:0.8|schema:3.0"
    stability = report["p0_4_stability"]
    assert stability["canonical_count_max"] >= stability["canonical_count_min"]
    assert report["p0_4_contract"]["coverage"] == 1.0
    assert report["manual_cluster_review"]["clusters"]
    # 多成员 cluster 出现在人工复核清单中。
    multi_member = report["manual_cluster_review"]["clusters"][0]["multi_member_clusters"]
    assert any(cluster["canonical_id"] == "cr-0" for cluster in multi_member)
    assert (tmp_path / "raw.json").exists()
