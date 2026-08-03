"""定义跨JD原子要求归并与映射的输入、输出和确定性一致性校验。"""

from __future__ import annotations

import unicodedata
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import RequirementItem


def normalize_requirement_name(value: str) -> str:
    """统一要求名称的Unicode、大小写和空白形式，供冲突检查使用。"""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


class RequirementOccurrence(BaseModel):
    """表示某份JD中的一条原子要求，并完整保留抽取数据合同。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: int = Field(gt=0)
    job_id: int = Field(gt=0)
    extraction_id: int = Field(gt=0)
    extractor_version: str = Field(min_length=1, max_length=255)
    source_hash: str = Field(min_length=64, max_length=64)
    source_file: str = Field(min_length=1, max_length=500)
    requirement: RequirementItem

    @field_validator("source_file", "extractor_version", "source_hash")
    @classmethod
    def source_file_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白来源定位字段，确保要求映射可以回到确定输入版本。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class RequirementConsolidationInput(BaseModel):
    """汇总选定范围内全部要求实例，作为P0-4的统一输入。"""

    model_config = ConfigDict(extra="forbid")

    occurrences: list[RequirementOccurrence] = Field(min_length=1)

    @model_validator(mode="after")
    def requirement_ids_must_be_unique(self) -> Self:
        """拒绝同一要求实例重复进入跨JD归并语料池。"""
        requirement_ids = [item.requirement_id for item in self.occurrences]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("要求实例ID不能重复")
        return self


class CanonicalRequirement(BaseModel):
    """表示多个同义要求实例共同指向的跨JD标准要求项。"""

    model_config = ConfigDict(extra="forbid")

    canonical_requirement_id: str = Field(min_length=1, max_length=100)
    canonical_name: str = Field(min_length=1, max_length=255)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("canonical_requirement_id", "canonical_name", "rationale")
    @classmethod
    def required_text_must_not_be_blank(cls, value: str) -> str:
        """清理标准要求项必填文本并拒绝只有空白字符的值。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class RequirementMapping(BaseModel):
    """记录一条要求实例到标准要求项的映射、理由和置信度。

    当前合同要求每个实例必须且只能映射到一个标准要求项：无法确认
    等价时由模型创建 singleton canonical requirement，不输出
    unmapped 或 review_required 状态。
    """

    model_config = ConfigDict(extra="forbid")

    requirement_id: int = Field(gt=0)
    canonical_requirement_id: str = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("canonical_requirement_id")
    @classmethod
    def canonical_id_must_be_meaningful(cls, value: str) -> str:
        """拒绝空白标准要求项ID。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        """拒绝缺少映射理由的映射结果。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class RequirementConsolidationResult(BaseModel):
    """保存跨JD归并产生的标准要求项与要求映射。"""

    model_config = ConfigDict(extra="forbid")

    canonical_requirements: list[CanonicalRequirement] = Field(default_factory=list)
    mappings: list[RequirementMapping] = Field(min_length=1)

    @model_validator(mode="after")
    def result_must_be_internally_consistent(self) -> Self:
        """校验标准名称和映射引用在同一结果中保持一致。"""
        requirement_ids = [
            item.canonical_requirement_id for item in self.canonical_requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("标准要求项ID不能重复")

        normalized_name_items: dict[str, list[CanonicalRequirement]] = {}
        for item in self.canonical_requirements:
            normalized_name_items.setdefault(
                normalize_requirement_name(item.canonical_name), []
            ).append(item)
        duplicate_names = {
            name: items
            for name, items in normalized_name_items.items()
            if len(items) > 1
        }
        if duplicate_names:
            details = "；".join(
                f"{items[0].canonical_name}="
                f"{','.join(item.canonical_requirement_id for item in items)}"
                for items in duplicate_names.values()
            )
            raise ValueError(
                "标准要求项名称不能重复；重复名称及ID："
                f"{details}。同一招聘条件请合并并重定向引用；"
                "不同条件请保留证据中的最小区分信息"
            )

        occurrence_ids = [mapping.requirement_id for mapping in self.mappings]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("同一要求实例不能产生多条映射结果")

        known_requirements = set(requirement_ids)
        referenced_requirements: set[str] = set()
        for mapping in self.mappings:
            if mapping.canonical_requirement_id not in known_requirements:
                raise ValueError(
                    f"映射引用未知标准要求项：{mapping.canonical_requirement_id}"
                )
            referenced_requirements.add(mapping.canonical_requirement_id)

        unused_requirements = sorted(known_requirements - referenced_requirements)
        if unused_requirements:
            raise ValueError(
                f"标准要求项没有来源要求：{', '.join(unused_requirements)}"
            )
        return self


def validate_requirement_coverage(
    consolidation_input: RequirementConsolidationInput,
    result: RequirementConsolidationResult,
) -> None:
    """确认P0-4为语料池中的每条要求且仅生成一条处理结果。"""
    expected_ids = {item.requirement_id for item in consolidation_input.occurrences}
    actual_ids = {mapping.requirement_id for mapping in result.mappings}
    missing_ids = sorted(expected_ids - actual_ids)
    unexpected_ids = sorted(actual_ids - expected_ids)
    errors = []
    if missing_ids:
        errors.append(f"遗漏要求实例：{missing_ids}")
    if unexpected_ids:
        errors.append(f"包含未知要求实例：{unexpected_ids}")
    if errors:
        raise ValueError("；".join(errors))
