"""该模块验证黄金数据加载、抽取指标计算和原文证据校验。"""

import json
from pathlib import Path

from app.evaluation import combine_metrics, evaluate_extraction, validate_golden_directory
from app.schemas import JobExtractionResult


def extraction_payload(names: list[tuple[str, str]]) -> dict[str, object]:
    """根据要求名称和重要程度生成用于指标测试的最小抽取结果。"""
    return {
        "role_family": "agent_application",
        "seniority": "unknown",
        "responsibilities": [],
        "requirements": [
            {
                "raw_name": name,
                "category": "agent_capability",
                "importance": importance,
                "proficiency": "unknown",
                "years_required": None,
                "evidence": name,
                "confidence": 1.0,
            }
            for name, importance in names
        ],
    }


def test_evaluate_extraction_calculates_expected_metrics() -> None:
    """验证要求命中、漏抽、多抽和重要程度错误会反映到对应指标。"""
    predicted = JobExtractionResult.model_validate(
        extraction_payload([("Python", "must"), ("RAG", "preferred"), ("Docker", "must")])
    )
    expected = JobExtractionResult.model_validate(
        extraction_payload([("Python", "must"), ("RAG", "must"), ("LangGraph", "preferred")])
    )

    metrics = evaluate_extraction(predicted, expected)
    combined = combine_metrics([metrics])

    assert combined.precision == 2 / 3
    assert combined.recall == 2 / 3
    assert combined.f1 == 2 / 3
    assert combined.importance_accuracy == 1 / 2


def test_validate_golden_directory_checks_source_evidence(tmp_path: Path) -> None:
    """验证黄金数据只有在来源文件存在且证据位于原文时才能通过。"""
    raw_directory = tmp_path / "raw"
    golden_directory = tmp_path / "golden"
    raw_directory.mkdir()
    golden_directory.mkdir()
    source_file = "sample.md"
    (raw_directory / source_file).write_text(
        """---
collected_at: 2026-07-21
company: 示例公司
title: Agent工程师
---

熟悉 Python。
""",
        encoding="utf-8",
    )
    golden_payload = {
        "source_file": source_file,
        "extraction": extraction_payload([("Python", "must")]),
    }
    (golden_directory / "sample.json").write_text(
        json.dumps(golden_payload, ensure_ascii=False), encoding="utf-8"
    )

    summary = validate_golden_directory(golden_directory, raw_directory)

    assert summary.discovered == 1
    assert summary.valid == 1
    assert summary.failed == 0

