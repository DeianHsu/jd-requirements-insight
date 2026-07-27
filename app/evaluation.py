"""该模块加载人工黄金答案，并计算JD要求抽取的Precision、Recall和分类准确率。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.extraction import validate_evidence
from app.ingestion import JobFileError, parse_job_file
from app.schemas import GoldenExtractionRecord, JobExtractionResult, RequirementItem


@dataclass(frozen=True)
class ExtractionMetrics:
    """保存要求项匹配数量以及由此计算出的抽取评测指标。"""

    predicted: int
    expected: int
    matched: int
    importance_correct: int

    @property
    def precision(self) -> float:
        """返回预测要求中命中人工标准答案的比例。"""
        return self.matched / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        """返回人工标准要求中被模型成功找到的比例。"""
        return self.matched / self.expected if self.expected else 0.0

    @property
    def f1(self) -> float:
        """返回Precision与Recall的调和平均值。"""
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    @property
    def importance_accuracy(self) -> float:
        """返回已匹配要求中重要程度分类正确的比例。"""
        return self.importance_correct / self.matched if self.matched else 0.0


@dataclass
class GoldenValidationSummary:
    """汇总黄金数据文件数量、通过数量和带文件名的校验错误。"""

    discovered: int = 0
    valid: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """返回未通过Schema、来源或证据校验的黄金文件数量。"""
        return len(self.errors)


def combine_metrics(metrics: list[ExtractionMetrics]) -> ExtractionMetrics:
    """汇总多份JD的计数并生成可用于整体评测的微平均指标。"""
    return ExtractionMetrics(
        predicted=sum(item.predicted for item in metrics),
        expected=sum(item.expected for item in metrics),
        matched=sum(item.matched for item in metrics),
        importance_correct=sum(item.importance_correct for item in metrics),
    )


def normalize_requirement_name(name: str) -> str:
    """对要求名称进行最小格式归一，暂不合并不同表达的技能同义词。"""
    normalized = unicodedata.normalize("NFKC", name).lower()
    return re.sub(r"\s+", " ", normalized).strip()


def requirement_map(items: list[RequirementItem]) -> dict[str, RequirementItem]:
    """按最小归一后的原始名称建立要求映射，便于集合匹配。"""
    return {normalize_requirement_name(item.raw_name): item for item in items}


def evaluate_extraction(
    predicted: JobExtractionResult, expected: JobExtractionResult
) -> ExtractionMetrics:
    """按raw_name匹配预测与黄金要求，并计算抽取和重要程度指标。"""
    predicted_map = requirement_map(predicted.requirements)
    expected_map = requirement_map(expected.requirements)
    matched_keys = predicted_map.keys() & expected_map.keys()
    importance_correct = sum(
        predicted_map[key].importance == expected_map[key].importance for key in matched_keys
    )
    return ExtractionMetrics(
        predicted=len(predicted_map),
        expected=len(expected_map),
        matched=len(matched_keys),
        importance_correct=importance_correct,
    )


def load_golden_file(path: Path) -> GoldenExtractionRecord:
    """读取单个人工标注JSON文件并使用Pydantic校验其结构。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    return GoldenExtractionRecord.model_validate(payload)


def validate_golden_directory(
    golden_directory: Path, raw_jd_directory: Path
) -> GoldenValidationSummary:
    """批量验证黄金文件Schema、来源文件对应关系和所有原文证据。"""
    files = sorted(golden_directory.glob("*.json"))
    summary = GoldenValidationSummary(discovered=len(files))

    for path in files:
        try:
            record = load_golden_file(path)
            raw_path = raw_jd_directory / record.source_file
            if not raw_path.exists():
                raise FileNotFoundError(f"原始JD不存在：{record.source_file}")
            document = parse_job_file(raw_path)
            validate_evidence(record.extraction, document.raw_text)
            summary.valid += 1
        except (ValueError, json.JSONDecodeError, OSError, JobFileError) as exc:
            # 每个黄金文件单独记录错误，避免一处人工标注问题遮蔽其他文件状态。
            summary.errors.append(f"{path.name}: {exc}")

    return summary
