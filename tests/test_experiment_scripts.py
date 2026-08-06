"""该模块验证实验脚本导入安全性、私有输出边界和真实调用显式确认。"""

import json
import sys
from datetime import date
from pathlib import Path

import pytest

from app.database import create_database_engine, create_session_factory, initialize_database
from app.models import JobDescription
from scripts.experiments.p0_3 import run_acceptance
from scripts.experiments.p0_3 import run_real_jd_acceptance

def _write_scenario_file(tmp_path: Path, scenario_id: str = "SCN-TEST") -> Path:
    """写入最小合法场景文件。"""
    path = tmp_path / "scenarios.json"
    path.write_text(
        json.dumps(
            {
                "protocol_version": "1.1",
                "description": "test",
                "scenarios": [
                    {
                        "scenario_id": scenario_id,
                        "rule_ids": ["REQ-02"],
                        "base_input": "熟悉技术甲。",
                        "transformation": {
                            "type": "text_replace",
                            "replacements": [{"find": "熟悉", "replace": "精通"}],
                        },
                        "expected_properties": {"fact_set_preserved": True},
                        "forbidden_violations": [],
                        "severity": "medium",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def test_acceptance_script_requires_explicit_mode(
    monkeypatch, tmp_path, capsys
) -> None:
    """验证验收脚本默认不调用外部模型：无--execute且无--dry-run返回2。"""
    scenarios = _write_scenario_file(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_acceptance", "--scenarios", str(scenarios)],
    )
    assert run_acceptance.main() == 2
    output = capsys.readouterr().out
    assert "--execute" in output
    assert "--dry-run" in output
    assert "未调用模型" in output


def test_acceptance_script_dry_run_does_not_call_model(
    monkeypatch, tmp_path, capsys
) -> None:
    """dry-run 预检不调用模型并返回 0（测试 27）。"""
    scenarios = _write_scenario_file(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_acceptance", "--scenarios", str(scenarios), "--dry-run"],
    )
    assert run_acceptance.main() == 0
    output = capsys.readouterr().out
    assert "dry-run" in output
    assert "未调用模型" not in output or "不调用模型" in output


def test_acceptance_script_rejects_invalid_runs(monkeypatch, tmp_path) -> None:
    """--runs 0 与 --max-attempts 0 必须在模型调用前失败。"""
    scenarios = _write_scenario_file(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_acceptance", "--scenarios", str(scenarios), "--runs", "0"],
    )
    with pytest.raises(SystemExit):
        run_acceptance.parse_args()
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_acceptance", "--scenarios", str(scenarios), "--max-attempts", "0"],
    )
    with pytest.raises(SystemExit):
        run_acceptance.parse_args()


def test_acceptance_script_fingerprint_changes_with_protocol(
    monkeypatch, tmp_path
) -> None:
    """完整场景协议变化会改变 fingerprint（测试 26）。"""
    first = _write_scenario_file(tmp_path, "SCN-TEST")
    payload = json.loads(first.read_text(encoding="utf-8"))
    payload["protocol_version"] = "1.2"
    second = tmp_path / "scenarios-v2.json"
    second.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    assert run_acceptance.scenario_set_fingerprint(
        json.loads(first.read_text(encoding="utf-8"))
    ) != run_acceptance.scenario_set_fingerprint(
        json.loads(second.read_text(encoding="utf-8"))
    )


def test_acceptance_complete_run_reports_incompleteness(
    monkeypatch, tmp_path, capsys
) -> None:
    """单次失败写入场景与全局 hard gate；预期运行缺失为 hard gate（测试 24/25）。"""
    scenarios = _write_scenario_file(tmp_path)

    class FailingSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class FailingClient:
        def __init__(self, settings) -> None:
            pass

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            raise RuntimeError("模拟模型调用失败")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--scenarios",
            str(scenarios),
            "--execute",
            "--runs",
            "3",
            "--report-dir",
            str(tmp_path / "reports"),
            "--raw-output-dir",
            str(tmp_path / "raw"),
        ],
    )
    monkeypatch.setattr(
        run_acceptance, "load_llm_settings", lambda: FailingSettings()
    )
    monkeypatch.setattr(
        run_acceptance, "OpenAICompatibleExtractionClient", FailingClient
    )
    assert run_acceptance.main() == 1
    output = capsys.readouterr().out
    assert "[FAIL]" in output
    assert "运行不完整" in output

    report_path = next((tmp_path / "reports").glob("*-report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is False
    scenario = report["scenarios"][0]
    assert scenario["expected_base_runs"] == 3
    assert scenario["successful_base_runs"] == 0
    assert scenario["failed_base_runs"] == 3
    assert any("运行不完整" in item for item in report["hard_gate_failures"])
    assert any("抽取失败" in item for item in scenario["hard_gate_failures"])
    # raw 私有输出边界：原始结果只写私有目录。
    raw_files = list((tmp_path / "raw").glob("*-raw.json"))
    assert raw_files
    raw = json.loads(raw_files[0].read_text(encoding="utf-8"))
    assert "SCN-TEST_base_run0" in raw
    assert "error" in raw["SCN-TEST_base_run0"]


def test_acceptance_script_uses_single_current_config(monkeypatch, tmp_path) -> None:
    """验收脚本使用当前唯一配置（v0.10 + Schema V3），无双 Profile。"""
    from app.extraction import PROMPT_VERSION, SCHEMA_VERSION
    from app.extraction_two_stage import TWO_STAGE_PROMPT_VERSION

    scenarios = _write_scenario_file(tmp_path)
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_acceptance", "--scenarios", str(scenarios), "--dry-run"],
    )
    assert run_acceptance.main() == 0
    assert PROMPT_VERSION == TWO_STAGE_PROMPT_VERSION == "0.10"
    assert SCHEMA_VERSION == "3.0"


def test_acceptance_script_defaults_keep_raw_results_private(
    monkeypatch,
) -> None:
    """验证验收脚本默认原始结果留在私有目录，报告进入实验报告目录。"""
    assert run_acceptance.DEFAULT_SCENARIOS_PATH == Path(
        "data/rule_scenarios/extraction_metamorphic_cases.json"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_acceptance",
            "--report-dir",
            "reports/P0-3",
            "--raw-output-dir",
            "data/private/experiments/p0_3",
        ],
    )
    args = run_acceptance.parse_args()
    assert args.raw_output_dir.is_relative_to(Path("data/private/experiments"))
    assert args.report_dir.is_relative_to(Path("reports"))


# ---------------------------------------------------------------------------
# run_real_jd_acceptance（Track B）
# ---------------------------------------------------------------------------


def test_real_jd_acceptance_requires_explicit_mode(monkeypatch, capsys) -> None:
    """Track B 无--execute且无--dry-run返回2且不调用模型。"""
    monkeypatch.setattr(
        sys,
        "argv",
        ["run_real_jd_acceptance", "--use-project-database", "--all"],
    )
    assert run_real_jd_acceptance.main() == 2
    output = capsys.readouterr().out
    assert "--execute" in output
    assert "未调用模型" in output


def test_real_jd_acceptance_rejects_invalid_runs(monkeypatch) -> None:
    """Track B --runs<3 与 --max-attempts<1 在模型调用前失败。"""
    for runs in (0, 1, 2):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "run_real_jd_acceptance",
                "--use-project-database",
                "--all",
                "--runs",
                str(runs),
            ],
        )
        with pytest.raises(SystemExit):
            run_real_jd_acceptance.parse_args()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_jd_acceptance",
            "--use-project-database",
            "--all",
            "--max-attempts",
            "0",
        ],
    )
    with pytest.raises(SystemExit):
        run_real_jd_acceptance.parse_args()


def test_real_jd_acceptance_scope_mutually_exclusive(monkeypatch) -> None:
    """Track B --all 与 --job-ids 互斥。"""
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_jd_acceptance",
            "--use-project-database",
            "--all",
            "--job-ids",
            "1",
        ],
    )
    with pytest.raises(SystemExit):
        run_real_jd_acceptance.parse_args()


def test_real_jd_acceptance_dry_run_uses_temp_database(
    monkeypatch, tmp_path
) -> None:
    """Track B dry-run 不调用模型（空库返回1：没有选中JD；不触发模型）。"""
    database = tmp_path / "empty.db"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_jd_acceptance",
            "--database-url",
            f"sqlite:///{database.as_posix()}",
            "--all",
            "--dry-run",
        ],
    )
    assert run_real_jd_acceptance.main() == 1


def test_track_b_success_path_generates_full_report(monkeypatch, tmp_path) -> None:
    """Track B 成功路径端到端：假 LLM → 完整报告。

    覆盖 requirement_count 类型正确（int，可被 sum 汇总）。
    """
    database_path = tmp_path / "track_b.db"
    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            session.add(
                JobDescription(
                    source_hash="a" * 64,
                    source_file="job-a.md",
                    source_type="test",
                    collected_at=date(2026, 8, 3),
                    company="示例公司",
                    title="示例岗位",
                    company_type="medium_company",
                    tags=[],
                    extra_metadata={},
                    raw_text="# 示例岗位\n\n负责能力甲体系建设。\n\n熟悉技术甲。",
                )
            )
            session.commit()
    finally:
        engine.dispose()

    class FakeSettings:
        model = "test-model"
        api_key = "test-key"
        base_url = None

        def missing_fields(self) -> list[str]:
            return []

    class FakeExtractionClient:
        def __init__(self, settings) -> None:
            pass

        def complete(self, system_prompt: str, user_prompt: str) -> str:
            if "全局发现" in system_prompt:
                return json.dumps(
                    {
                        "role_family": "other",
                        "seniority": "unknown",
                        "blocks": [
                            {"block_id": "b0", "sentence_indexes": [0],
                             "kind": "excluded",
                             "source_span": "# 示例岗位",
                             "note": "标题"},
                            {"block_id": "b1", "sentence_indexes": [1],
                             "kind": "responsibility",
                             "source_span": "负责能力甲体系建设",
                             "note": "工作内容"},
                            {"block_id": "b2", "sentence_indexes": [2],
                             "kind": "requirement",
                             "source_span": "熟悉技术甲",
                             "note": "候选人条件"},
                        ],
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "role_family": "other",
                    "seniority": "unknown",
                    "requirements": [
                        {
                            "raw_name": "技术甲",
                            "category": "programming_language",
                            "importance": "must",
                            "proficiency": "basic",
                            "group_id": None,
                            "group_logic": "standalone",
                            "min_years": None,
                            "max_years": None,
                            "years_text": None,
                            "evidence": "熟悉技术甲",
                            "confidence": 0.9,
                        }
                    ],
                },
                ensure_ascii=False,
            )

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_real_jd_acceptance",
            "--database-url",
            f"sqlite:///{database_path.as_posix()}",
            "--job-ids",
            "1",
            "--execute",
            "--runs",
            "3",
            "--report-dir",
            str(tmp_path / "reports"),
            "--raw-output-dir",
            str(tmp_path / "raw"),
        ],
    )
    monkeypatch.setattr(
        run_real_jd_acceptance, "load_llm_settings", lambda: FakeSettings()
    )
    monkeypatch.setattr(
        run_real_jd_acceptance,
        "OpenAICompatibleExtractionClient",
        FakeExtractionClient,
    )

    assert run_real_jd_acceptance.main() == 0

    report_path = next((tmp_path / "reports").glob("*-report.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["jobs"][0]["requirement_count"] == 1
    assert isinstance(report["jobs"][0]["requirement_count"], int)
    assert "jd_set_fingerprint" in report["identity"]
    raw_files = list((tmp_path / "raw").glob("*-raw.json"))
    assert raw_files
