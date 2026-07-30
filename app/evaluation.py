"""该模块加载人工黄金答案，并计算JD要求抽取的Precision、Recall和分类准确率。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.extraction import validate_evidence
from app.ingestion import JobFileError, parse_job_file
from app.schemas import (
    GoldenExtractionRecord,
    JobExtractionResult,
    RequirementItem,
    ResponsibilityItem,
)


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


@dataclass(frozen=True)
class ItemMatchMetrics:
    """保存某一类原子项的预测、期望和确定性名称匹配数量。"""

    predicted: int = 0
    expected: int = 0
    matched: int = 0

    @property
    def precision(self) -> float:
        """返回预测原子项中通过确定性名称匹配的比例。"""
        return self.matched / self.predicted if self.predicted else 0.0

    @property
    def recall(self) -> float:
        """返回人工期望原子项中被确定性名称匹配找到的比例。"""
        return self.matched / self.expected if self.expected else 0.0

    @property
    def f1(self) -> float:
        """返回确定性名称匹配Precision与Recall的调和平均值。"""
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0


@dataclass
class AnnotationCaseMetrics:
    """汇总困难样例的分层指标、缺失来源和可人工复核的错误明细。"""

    discovered_cases: int = 0
    evaluated_cases: int = 0
    requirement_metrics: ItemMatchMetrics = field(default_factory=ItemMatchMetrics)
    responsibility_metrics: ItemMatchMetrics = field(default_factory=ItemMatchMetrics)
    exact_count_cases: int = 0
    importance_total: int = 0
    importance_correct: int = 0
    proficiency_total: int = 0
    proficiency_correct: int = 0
    category_total: int = 0
    category_correct: int = 0
    years_total: int = 0
    years_correct: int = 0
    any_of_groups_total: int = 0
    any_of_groups_correct: int = 0
    evidence_total: int = 0
    evidence_correct: int = 0
    missing_sources: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)

    @staticmethod
    def _accuracy(correct: int, total: int) -> float:
        """用统一零分母规则计算某一字段的准确率。"""
        return correct / total if total else 0.0

    @property
    def importance_accuracy(self) -> float:
        """返回已匹配要求的重要程度准确率。"""
        return self._accuracy(self.importance_correct, self.importance_total)

    @property
    def proficiency_accuracy(self) -> float:
        """返回已匹配要求的熟练度准确率。"""
        return self._accuracy(self.proficiency_correct, self.proficiency_total)

    @property
    def category_accuracy(self) -> float:
        """返回已匹配要求的类别准确率。"""
        return self._accuracy(self.category_correct, self.category_total)

    @property
    def years_accuracy(self) -> float:
        """返回人工样例中显式年限字段的准确率。"""
        return self._accuracy(self.years_correct, self.years_total)

    @property
    def any_of_group_accuracy(self) -> float:
        """返回人工期望任选组在预测中保持完整同组关系的比例。"""
        return self._accuracy(self.any_of_groups_correct, self.any_of_groups_total)

    @property
    def evidence_accuracy(self) -> float:
        """返回所选完整抽取结果中证据确实存在于原文的比例。"""
        return self._accuracy(self.evidence_correct, self.evidence_total)


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


def normalize_case_text(text: str) -> str:
    """统一困难样例的Unicode、空白和边界标点以支持稳定证据定位。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    compact = re.sub(r"\s+", "", normalized)
    return compact.strip("，。；;、,.!?！？：:")


def normalize_item_label(text: str) -> str:
    """移除名称中的格式符号但保留技术标识，用于可解释的相似度计算。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^0-9a-z+#.\u4e00-\u9fff]", "", normalized)


def item_label(item: RequirementItem | ResponsibilityItem) -> str:
    """统一取得要求的raw_name或职责的name，供同一匹配算法处理。"""
    return item.raw_name if isinstance(item, RequirementItem) else item.name


def item_name_similarity(expected: str, predicted: str) -> float:
    """计算保留专有技术词约束的名称相似度，避免把泛化概念误判为命中。"""
    expected_name = normalize_item_label(expected)
    predicted_name = normalize_item_label(predicted)
    if not expected_name or not predicted_name:
        return 0.0
    if expected_name == predicted_name:
        return 1.0

    # Python、LangChain等专有英文词必须仍出现在预测名称中，通用AI术语不作硬约束。
    generic_tokens = {"ai", "agent", "llm"}
    expected_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9.+#-]*", expected_name)
        if token not in generic_tokens
    }
    predicted_tokens = set(re.findall(r"[a-z][a-z0-9.+#-]*", predicted_name))
    if not expected_tokens.issubset(predicted_tokens):
        return 0.0

    similarity = SequenceMatcher(None, expected_name, predicted_name).ratio()
    if expected_name in predicted_name or predicted_name in expected_name:
        containment = min(len(expected_name), len(predicted_name)) / max(
            len(expected_name), len(predicted_name)
        )
        similarity = max(similarity, min(1.0, containment + 0.2))
    return similarity


def match_atomic_items(
    expected: list[RequirementItem] | list[ResponsibilityItem],
    predicted: list[RequirementItem] | list[ResponsibilityItem],
    threshold: float = 0.55,
) -> list[tuple[int, int]]:
    """按最高名称相似度进行一对一贪心匹配，并返回期望与预测项索引。"""
    candidates = []
    for expected_index, expected_item in enumerate(expected):
        for predicted_index, predicted_item in enumerate(predicted):
            score = item_name_similarity(
                item_label(expected_item), item_label(predicted_item)
            )
            if score >= threshold:
                candidates.append((score, expected_index, predicted_index))

    matches = []
    used_expected = set()
    used_predicted = set()
    # 分数相同时用索引固定顺序，保证同一输入每次产生相同结果。
    for _, expected_index, predicted_index in sorted(
        candidates, key=lambda item: (-item[0], item[1], item[2])
    ):
        if expected_index in used_expected or predicted_index in used_predicted:
            continue
        matches.append((expected_index, predicted_index))
        used_expected.add(expected_index)
        used_predicted.add(predicted_index)
    return matches


def evidence_belongs_to_case(evidence: str, sentence: str) -> bool:
    """判断预测证据与困难句子是否为包含关系，以隔离该句对应的输出项。"""
    normalized_evidence = normalize_case_text(evidence)
    normalized_sentence = normalize_case_text(sentence)
    return (
        normalized_evidence in normalized_sentence
        or normalized_sentence in normalized_evidence
    )


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


def load_annotation_cases_file(path: Path) -> dict[str, Any]:
    """读取困难样例JSON并确认顶层cases字段可供分层评测使用。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("困难样例文件必须包含cases数组")
    return payload


def _case_items(
    case: dict[str, Any], target: str
) -> list[RequirementItem] | list[ResponsibilityItem]:
    """把单个困难样例的期望项校验为Schema对象并按目标类型返回。"""
    expected = case.get("expected")
    if not isinstance(expected, dict) or not isinstance(expected.get(target), list):
        raise ValueError(f"{case.get('case_id', 'unknown')}缺少expected.{target}数组")
    if target == "requirements":
        return [RequirementItem.model_validate(item) for item in expected[target]]
    if target == "responsibilities":
        return [ResponsibilityItem.model_validate(item) for item in expected[target]]
    raise ValueError(f"不支持的annotation_target：{target}")


def _year_fields_equal(expected: RequirementItem, predicted: RequirementItem) -> bool:
    """比较年限上下限和去除空白后的原始年限表达是否全部一致。"""
    return (
        expected.min_years == predicted.min_years
        and expected.max_years == predicted.max_years
        and normalize_case_text(expected.years_text or "")
        == normalize_case_text(predicted.years_text or "")
    )


def _evaluate_expected_groups(
    expected: list[RequirementItem],
    predicted: list[RequirementItem],
    matches: list[tuple[int, int]],
) -> tuple[int, int]:
    """统计人工any_of组是否在预测中完整匹配且共享同一个非空组ID。"""
    expected_groups: dict[str, list[int]] = {}
    for index, item in enumerate(expected):
        if item.group_logic.value == "any_of" and item.group_id is not None:
            expected_groups.setdefault(item.group_id, []).append(index)

    predicted_by_expected = dict(matches)
    correct = 0
    for expected_indexes in expected_groups.values():
        predicted_indexes = [
            predicted_by_expected[index]
            for index in expected_indexes
            if index in predicted_by_expected
        ]
        if len(predicted_indexes) != len(expected_indexes):
            continue
        predicted_members = [predicted[index] for index in predicted_indexes]
        group_ids = {item.group_id for item in predicted_members}
        if (
            all(item.group_logic.value == "any_of" for item in predicted_members)
            and len(group_ids) == 1
            and None not in group_ids
        ):
            correct += 1
    return len(expected_groups), correct


def evaluate_annotation_cases(
    payload: dict[str, Any],
    predictions: dict[str, JobExtractionResult],
    source_texts: dict[str, str] | None = None,
    dataset_split: str | None = None,
) -> AnnotationCaseMetrics:
    """对困难句子执行可重复的名称代理匹配和字段级分层评测。"""
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("困难样例文件必须包含cases数组")
    if any(not isinstance(case, dict) for case in raw_cases):
        raise ValueError("cases中的每一项都必须是对象")
    cases = [
        case
        for case in raw_cases
        if dataset_split is None or case.get("dataset_split") == dataset_split
    ]
    if dataset_split is not None and not cases:
        raise ValueError(f"指定数据分组没有样例：{dataset_split}")

    summary = AnnotationCaseMetrics(discovered_cases=len(cases))
    requirement_predicted = requirement_expected = requirement_matched = 0
    responsibility_predicted = responsibility_expected = responsibility_matched = 0

    for raw_case in cases:
        case_id = str(raw_case.get("case_id", "unknown"))
        source_file = str(raw_case.get("source_file", ""))
        sentence = str(raw_case.get("sentence", ""))
        target = str(raw_case.get("annotation_target", ""))
        prediction = predictions.get(source_file)
        if prediction is None:
            if source_file not in summary.missing_sources:
                summary.missing_sources.append(source_file)
            continue

        expected_items = _case_items(raw_case, target)
        predicted_items = [
            item
            for item in getattr(prediction, target)
            if evidence_belongs_to_case(item.evidence, sentence)
        ]
        matches = match_atomic_items(expected_items, predicted_items)
        summary.evaluated_cases += 1
        summary.exact_count_cases += int(len(expected_items) == len(predicted_items))

        if target == "requirements":
            expected_requirements = list(expected_items)
            predicted_requirements = list(predicted_items)
            requirement_expected += len(expected_requirements)
            requirement_predicted += len(predicted_requirements)
            requirement_matched += len(matches)
            matched_expected = {expected_index for expected_index, _ in matches}
            matched_predicted = {predicted_index for _, predicted_index in matches}

            for expected_index, predicted_index in matches:
                expected_item = expected_requirements[expected_index]
                predicted_item = predicted_requirements[predicted_index]
                summary.importance_total += 1
                summary.importance_correct += int(
                    expected_item.importance == predicted_item.importance
                )
                summary.proficiency_total += 1
                summary.proficiency_correct += int(
                    expected_item.proficiency == predicted_item.proficiency
                )
                summary.category_total += 1
                summary.category_correct += int(
                    expected_item.category == predicted_item.category
                )
                if any(
                    value is not None
                    for value in (
                        expected_item.min_years,
                        expected_item.max_years,
                        expected_item.years_text,
                    )
                ):
                    summary.years_total += 1
                    summary.years_correct += int(
                        _year_fields_equal(expected_item, predicted_item)
                    )

            groups_total, groups_correct = _evaluate_expected_groups(
                expected_requirements, predicted_requirements, matches
            )
            summary.any_of_groups_total += groups_total
            summary.any_of_groups_correct += groups_correct
        else:
            responsibility_expected += len(expected_items)
            responsibility_predicted += len(predicted_items)
            responsibility_matched += len(matches)
            matched_expected = {expected_index for expected_index, _ in matches}
            matched_predicted = {predicted_index for _, predicted_index in matches}

        missing_names = [
            item_label(item)
            for index, item in enumerate(expected_items)
            if index not in matched_expected
        ]
        extra_names = [
            item_label(item)
            for index, item in enumerate(predicted_items)
            if index not in matched_predicted
        ]
        if missing_names:
            summary.issues.append(f"{case_id}漏项：{', '.join(missing_names)}")
        if extra_names:
            summary.issues.append(f"{case_id}多项：{', '.join(extra_names)}")

    summary.requirement_metrics = ItemMatchMetrics(
        predicted=requirement_predicted,
        expected=requirement_expected,
        matched=requirement_matched,
    )
    summary.responsibility_metrics = ItemMatchMetrics(
        predicted=responsibility_predicted,
        expected=responsibility_expected,
        matched=responsibility_matched,
    )

    # 证据存在率针对所选版本的完整输出计算，不局限于10个困难句子。
    for source_file, prediction in predictions.items():
        if source_texts is None or source_file not in source_texts:
            continue
        normalized_source = normalize_case_text(source_texts[source_file])
        for item in [*prediction.responsibilities, *prediction.requirements]:
            summary.evidence_total += 1
            summary.evidence_correct += int(
                normalize_case_text(item.evidence) in normalized_source
            )
    return summary
