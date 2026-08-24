"""一键 MVP 流水线测试；所有模型响应均由假客户端提供。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auto_pipeline import AutoPipelineError, run_auto_pipeline
from app.consolidation import ConsolidatorMetadata
from app.database import create_database_engine, create_session_factory
from app.extraction import ExtractorMetadata
from app.models import JobConsolidation, JobExtraction
from typer.testing import CliRunner

runner = CliRunner()


def _write_jd(directory: Path, name: str = "jd.md", skill: str = "Python") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(
        f"""---
collected_at: 2026-08-24
company: 虚构公司
title: 虚构工程师
company_type: unknown
---

# 虚构工程师

熟悉 {skill}。
""",
        encoding="utf-8",
    )


class FakeExtractionClient:
    def __init__(self, *, fail_skill: str | None = None) -> None:
        self.fail_skill = fail_skill
        self.call_count = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        payload = json.loads(user_prompt.split("\n\n【上次校验错误", 1)[0])
        if "全局发现" in system_prompt:
            blocks = []
            for item in payload["sentences"]:
                kind = "requirement" if "熟悉" in item["text"] else "excluded"
                blocks.append(
                    {
                        "block_id": f"b{item['index']}",
                        "sentence_indexes": [item["index"]],
                        "kind": kind,
                        "source_span": item["text"],
                        "note": "fake",
                    }
                )
            return json.dumps(
                {"role_family": "other", "seniority": "unknown", "blocks": blocks},
                ensure_ascii=False,
            )
        evidence = next(
            sentence["text"]
            for sentence in payload["sentences"]
            if "熟悉" in sentence["text"]
        )
        if self.fail_skill and self.fail_skill in evidence:
            return "{}"
        skill = evidence.removeprefix("熟悉 ").removesuffix("。").strip()
        return json.dumps(
            {
                "role_family": "other",
                "seniority": "unknown",
                "requirements": [
                    {
                        "raw_name": skill,
                        "category": "programming_language",
                        "importance": "must",
                        "proficiency": "basic",
                        "group_id": None,
                        "group_logic": "standalone",
                        "min_years": None,
                        "max_years": None,
                        "years_text": None,
                        "evidence": evidence,
                        "confidence": 0.9,
                    }
                ],
            },
            ensure_ascii=False,
        )


class FakeConsolidationClient:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.call_count = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        if self.fail:
            return "{}"
        payload = json.loads(user_prompt.split("\n\n【上次校验错误", 1)[0])
        canonical = []
        for item in payload["requirements"]:
            requirement_id = item["id"]
            canonical.append(
                {
                    "canonical_requirement_id": f"cr-{requirement_id}",
                    "canonical_name": item["raw_name"],
                    "source_requirement_ids": [requirement_id],
                    "rationale": "fake singleton",
                    "confidence": 0.9,
                }
            )
        return json.dumps({"canonical_requirements": canonical}, ensure_ascii=False)


def _run(
    jd_dir: Path,
    runs_root: Path,
    extraction_client=None,
    consolidation_client=None,
):
    return run_auto_pipeline(
        jd_dir,
        extraction_client or FakeExtractionClient(),
        consolidation_client or FakeConsolidationClient(),
        ExtractorMetadata(model_name="fake-model"),
        ConsolidatorMetadata(model_name="fake-model"),
        runs_root=runs_root,
    )


def test_one_click_pipeline_completes_with_automatic_provenance(tmp_path: Path) -> None:
    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir)

    result = _run(jd_dir, tmp_path / "runs")

    assert result.report_path.exists()
    assert "Python" in result.report_path.read_text(encoding="utf-8")
    summary = json.loads((result.workspace / "run-summary.json").read_text("utf-8"))
    assert summary["status"] == "completed"
    assert summary["job_count"] == 1
    engine = create_database_engine(f"sqlite:///{result.database_path.as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        extraction = session.scalar(select(JobExtraction))
        consolidation = session.scalar(select(JobConsolidation))
        assert extraction.raw_response["approval_mode"] == "automatic"
        assert extraction.raw_response["reviewed_by"] == "system:auto-v1"
        assert consolidation.raw_response["approval_mode"] == "automatic"
        assert consolidation.raw_response["reviewed_by"] == "system:auto-v1"
    engine.dispose()


def test_extraction_failure_leaves_no_formal_extraction(tmp_path: Path) -> None:
    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir, "a.md", "Python")
    _write_jd(jd_dir, "b.md", "失败技能")

    with pytest.raises(AutoPipelineError, match="抽取未通过硬门") as caught:
        _run(
            jd_dir,
            tmp_path / "runs",
            extraction_client=FakeExtractionClient(fail_skill="失败技能"),
        )

    assert caught.value.stage == "extraction"
    workspace = next((tmp_path / "runs").iterdir())
    engine = create_database_engine(f"sqlite:///{(workspace / 'pipeline.db').as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        assert session.query(JobExtraction).count() == 0
    engine.dispose()
    assert not (workspace / "market-report.md").exists()


def test_consolidation_failure_leaves_no_batch_or_report(tmp_path: Path) -> None:
    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir)

    with pytest.raises(AutoPipelineError, match="归并未通过硬门"):
        _run(
            jd_dir,
            tmp_path / "runs",
            consolidation_client=FakeConsolidationClient(fail=True),
        )

    workspace = next((tmp_path / "runs").iterdir())
    engine = create_database_engine(f"sqlite:///{(workspace / 'pipeline.db').as_posix()}")
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        assert session.query(JobExtraction).count() == 1
        assert session.query(JobConsolidation).count() == 0
    engine.dispose()
    assert not (workspace / "market-report.md").exists()


def test_completed_input_is_reused_without_model_calls(tmp_path: Path) -> None:
    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir)
    runs_root = tmp_path / "runs"
    first = _run(jd_dir, runs_root)
    extraction_client = FakeExtractionClient(fail_skill="Python")
    consolidation_client = FakeConsolidationClient(fail=True)

    second = _run(
        jd_dir,
        runs_root,
        extraction_client=extraction_client,
        consolidation_client=consolidation_client,
    )

    assert second.reused is True
    assert second.consolidation_id == first.consolidation_id
    assert extraction_client.call_count == 0
    assert consolidation_client.call_count == 0


def test_cli_requires_execute_before_loading_model_settings(
    tmp_path: Path, monkeypatch
) -> None:
    from app.cli import cli
    import app.cli as cli_module

    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir)
    monkeypatch.setattr(
        cli_module,
        "load_llm_settings",
        lambda: (_ for _ in ()).throw(AssertionError("不应读取模型配置")),
    )

    result = runner.invoke(cli, ["analyze-jds", str(jd_dir)])

    assert result.exit_code == 2
    assert "--execute" in result.output


def test_cli_execute_runs_the_real_orchestrator_with_fake_clients(
    tmp_path: Path, monkeypatch
) -> None:
    from app.cli import cli
    import app.cli as cli_module

    class FakeSettings:
        model = "fake-model"

        def missing_fields(self) -> list[str]:
            return []

    jd_dir = tmp_path / "jds"
    _write_jd(jd_dir)
    runs_root = tmp_path / "runs"
    real_runner = run_auto_pipeline

    monkeypatch.setattr(cli_module, "load_llm_settings", lambda: FakeSettings())
    monkeypatch.setattr(
        cli_module,
        "OpenAICompatibleExtractionClient",
        lambda settings: FakeExtractionClient(),
    )
    monkeypatch.setattr(
        cli_module,
        "OpenAICompatibleConsolidationClient",
        lambda settings: FakeConsolidationClient(),
    )
    monkeypatch.setattr(
        cli_module,
        "run_auto_pipeline",
        lambda *args, **kwargs: real_runner(*args, **kwargs, runs_root=runs_root),
    )

    result = runner.invoke(cli, ["analyze-jds", str(jd_dir), "--execute"])

    assert result.exit_code == 0, result.output
    assert "分析完成" in result.output
    workspace = next(runs_root.iterdir())
    assert (workspace / "market-report.md").exists()
