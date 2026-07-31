"""该模块验证人工标准答案加载、抽取指标计算和原文证据校验。"""

import json
from pathlib import Path

import pytest

from app.evaluation import (
    combine_metrics,
    evaluate_annotation_cases,
    evaluate_extraction,
    item_name_similarity,
    validate_golden_directory,
)
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
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
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
    """验证人工标准答案只有在来源文件存在且证据位于原文时才能通过。"""
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


def test_item_name_similarity_keeps_specific_technology_boundary() -> None:
    """验证名称代理匹配允许显式修饰语，但拒绝把具体框架泛化为通用概念。"""
    assert item_name_similarity("Python", "精通Python") >= 0.55
    assert item_name_similarity("LangChain框架使用经验", "Agent框架使用经验") == 0.0


def test_evaluate_annotation_cases_reports_layered_metrics() -> None:
    """验证困难样例评测能分别计算原子项、字段、任选组、年限和证据指标。"""
    source_file = "sample.md"
    requirement_sentence = "熟悉 Python / Node.js 中至少一种；具备3-5年开发经验。"
    responsibility_sentence = "构建智能体工作流，并实现报告生成自动化。"
    payload = {
        "cases": [
            {
                "case_id": "case_requirement",
                "dataset_split": "development",
                "source_file": source_file,
                "sentence": requirement_sentence,
                "annotation_target": "requirements",
                "expected": {
                    "requirements": [
                        {
                            "raw_name": name,
                            "category": category,
                            "importance": "must",
                            "proficiency": proficiency,
                            "group_id": group_id,
                            "group_logic": group_logic,
                            "min_years": min_years,
                            "max_years": max_years,
                            "years_text": years_text,
                            "evidence": requirement_sentence,
                            "confidence": 1.0,
                        }
                        for (
                            name,
                            category,
                            proficiency,
                            group_id,
                            group_logic,
                            min_years,
                            max_years,
                            years_text,
                        ) in [
                            (
                                "Python",
                                "programming_language",
                                "familiar",
                                "language_group",
                                "any_of",
                                None,
                                None,
                                None,
                            ),
                            (
                                "Node.js",
                                "programming_language",
                                "familiar",
                                "language_group",
                                "any_of",
                                None,
                                None,
                                None,
                            ),
                            (
                                "开发经验",
                                "experience",
                                "unknown",
                                None,
                                "standalone",
                                3,
                                5,
                                "3-5年",
                            ),
                        ]
                    ]
                },
            },
            {
                "case_id": "case_responsibility",
                "dataset_split": "validation",
                "source_file": source_file,
                "sentence": responsibility_sentence,
                "annotation_target": "responsibilities",
                "expected": {
                    "responsibilities": [
                        {
                            "name": "构建智能体工作流",
                            "evidence": responsibility_sentence,
                        },
                        {
                            "name": "实现报告生成自动化",
                            "evidence": responsibility_sentence,
                        },
                    ]
                },
            },
        ]
    }
    predicted_payload = {
        "role_family": "agent_application",
        "seniority": "unknown",
        "responsibilities": [
            {"name": "负责构建智能体工作流", "evidence": responsibility_sentence},
            {"name": "实现报告生成自动化", "evidence": responsibility_sentence},
        ],
        "requirements": [
            {
                "raw_name": "Python开发经验",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "familiar",
                "group_id": "group_1",
                "group_logic": "any_of",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": requirement_sentence,
                "confidence": 0.9,
            },
            {
                "raw_name": "Node.js开发经验",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "familiar",
                "group_id": "group_1",
                "group_logic": "any_of",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": requirement_sentence,
                "confidence": 0.9,
            },
            {
                "raw_name": "3-5年开发经验",
                "category": "experience",
                "importance": "must",
                "proficiency": "unknown",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": 3,
                "max_years": 5,
                "years_text": "3-5 年",
                "evidence": requirement_sentence,
                "confidence": 0.9,
            },
        ],
    }
    prediction = JobExtractionResult.model_validate(predicted_payload)

    summary = evaluate_annotation_cases(
        payload,
        {source_file: prediction},
        {source_file: f"{requirement_sentence}\n{responsibility_sentence}"},
    )

    assert summary.evaluated_cases == 2
    assert summary.requirement_metrics.matched == 3
    assert summary.responsibility_metrics.matched == 2
    assert summary.exact_count_cases == 2
    assert summary.importance_accuracy == 1.0
    assert summary.proficiency_accuracy == 1.0
    assert summary.category_accuracy == 1.0
    assert summary.years_accuracy == 1.0
    assert summary.any_of_group_accuracy == 1.0
    assert summary.evidence_accuracy == 1.0
    assert summary.issues == []

    development = evaluate_annotation_cases(
        payload,
        {source_file: prediction},
        {source_file: f"{requirement_sentence}\n{responsibility_sentence}"},
        dataset_split="development",
    )
    assert development.discovered_cases == 1
    assert development.evaluated_cases == 1
    assert development.requirement_metrics.matched == 3
    assert development.responsibility_metrics.expected == 0

    with pytest.raises(ValueError, match="指定数据分组没有样例"):
        evaluate_annotation_cases(
            payload,
            {source_file: prediction},
            dataset_split="unknown",
        )
