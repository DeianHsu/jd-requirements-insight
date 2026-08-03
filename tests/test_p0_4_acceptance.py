"""P0-4 验收脚本端到端与门槛测试（DEC-017：P0-4A/P0-4B 分开验收）。

覆盖：默认使用 v0.8 + Schema V3 并拒绝旧输入；P0-4A 门槛含新聚类指标
（positive_pair_jaccard / neighbor stability 等进报告与 warning）；
P0-4B 门槛独立（edge Jaccard / 方向一致率 / 稀疏度）；一次完整的
P0-4A 验收（假归并客户端）能生成报告且 requirement 计数为数值。
"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.database import create_database_engine, create_session_factory, initialize_database
from app.models import JobDescription, JobExtraction, JobRequirement
from scripts.experiments.p0_4.run_acceptance import (
    evaluate_p0_4a_gates,
    evaluate_p0_4b_gates,
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
    """构造一条 mapped 映射（与归并合同一致）。"""
    return {
        "requirement_id": requirement_id,
        "status": "mapped",
        "canonical_requirement_id": canonical_id,
        "candidate_requirement_ids": [],
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
        relations=[],
        uncertain_relations=[],
        hierarchy_status="not_run",
    )


def test_p0_4a_gates_report_new_metrics_and_warnings() -> None:
    """P0-4A 门槛：已冻结的 co-clustering 仍为 hard gate；新指标进 warning/诊断。"""
    names = ["甲", "乙", "丙", "丁", "戊"]
    stable = _result_from_clusters([[1, 2, 3], [4]], names)
    split = _result_from_clusters([[1, 2], [3], [4]], names)
    runs = [{"result": stable}, {"result": split}]

    hard_gate_failures, warnings, diagnostics = evaluate_p0_4a_gates(
        runs,
        contract_violations=[],
        downstream_equal=True,
    )

    # 拆分导致同簇对 Jaccard 与邻居稳定性下降 → warning（不擅自设 hard gate）；
    # co-clustering 门槛同时触发 hard gate（两个指标共同报警）。
    assert any("positive_pair_jaccard" in item for item in warnings)
    assert any("positive_pair_jaccard" in item for item in diagnostics)
    assert hard_gate_failures  # co-clustering 66.67% 低于冻结门槛

    # 完全一致时无 warning。
    identical_hard, identical_warnings, _ = evaluate_p0_4a_gates(
        [{"result": stable}, {"result": stable}],
        contract_violations=[],
        downstream_equal=True,
    )
    assert identical_hard == []
    assert identical_warnings == []


def test_p0_4a_gates_keep_frozen_coclustering_thresholds() -> None:
    """已冻结的 co-clustering 门槛（高置信>=90%、全部>=85%）仍是 hard gate。"""
    names = ["甲", "乙", "丙", "丁"]
    split = _result_from_clusters([[1], [2], [3]], names)
    runs = [{"result": split}, {"result": split}]

    hard_gate_failures, _, _ = evaluate_p0_4a_gates(
        runs, contract_violations=[], downstream_equal=True
    )
    assert hard_gate_failures == []

    divergent = _result_from_clusters([[1, 2, 3]], names)
    hard_gate_failures, _, _ = evaluate_p0_4a_gates(
        [{"result": split}, {"result": divergent}],
        contract_violations=[],
        downstream_equal=True,
    )
    assert hard_gate_failures


def test_p0_4b_gates_are_separate_from_p0_4a() -> None:
    """P0-4B 门槛独立：edge Jaccard/方向/稀疏度只在 P0-4B 模式判定。"""
    from app.requirement_consolidation import RequirementConsolidationResult

    result = RequirementConsolidationResult(
        canonical_requirements=[
            _canonical("cr-0", "甲"),
            _canonical("cr-1", "乙"),
        ],
        mappings=[_mapping(1, "cr-0"), _mapping(2, "cr-1")],
        relations=[
            {
                "source_requirement_id": "cr-0",
                "target_requirement_id": "cr-1",
                "relation_type": "broader_than",
                "rationale": "测试",
                "confidence": 0.9,
            }
        ],
        uncertain_relations=[],
        hierarchy_status="success",
    )
    runs = [{"result": result}, {"result": result}]

    hard_gate_failures, _, _ = evaluate_p0_4b_gates(
        runs, contract_violations=[], graph_stats=[]
    )
    assert hard_gate_failures == []


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
                    "responsibilities": [],
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
        # 映射阶段：每个实例恰好 mapped 一次。
        return json.dumps(
            {
                "mappings": [
                    {"requirement_id": 1, "status": "mapped",
                     "canonical_requirement_id": "cr-0",
                     "candidate_requirement_ids": [], "rationale": "测试",
                     "confidence": 0.95},
                    {"requirement_id": 2, "status": "mapped",
                     "canonical_requirement_id": "cr-1",
                     "candidate_requirement_ids": [], "rationale": "测试",
                     "confidence": 0.95},
                    {"requirement_id": 3, "status": "mapped",
                     "canonical_requirement_id": "cr-0",
                     "candidate_requirement_ids": [], "rationale": "测试",
                     "confidence": 0.95},
                ]
            },
            ensure_ascii=False,
        )


def test_p0_4a_acceptance_end_to_end(monkeypatch, tmp_path) -> None:
    """P0-4A 完整验收：假归并客户端 → 报告生成（端到端）。"""
    import scripts.experiments.p0_4.run_acceptance as acceptance_script

    database_path = tmp_path / "p0_4a.db"
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
            "--track",
            "p0-4a",
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
    stability = report["p0_4a_stability"]
    assert stability["canonical_count_max"] >= stability["canonical_count_min"]
    assert stability["merge_pair_metrics"]
    assert stability["cluster_stability"]
    assert (tmp_path / "raw.json").exists()
