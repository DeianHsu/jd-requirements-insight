"""定义跨JD原子要求归并与映射的输入、输出和确定性一致性校验。"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas import RequirementItem


def normalize_requirement_name(value: str) -> str:
    """统一要求名称的Unicode、大小写和空白形式，供冲突检查使用。"""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(normalized.split())


class RequirementRelationType(StrEnum):
    """限定标准要求项之间允许保存的非同义语义关系。"""

    IS_A = "is_a"
    PART_OF = "part_of"
    RELATED_TO = "related_to"


class RequirementMappingStatus(StrEnum):
    """表示要求实例在一次跨JD归并中的处理状态。"""

    MAPPED = "mapped"
    UNMAPPED = "unmapped"
    REVIEW_REQUIRED = "review_required"


class RequirementOccurrence(BaseModel):
    """表示某份JD中的一条原子要求，并完整保留抽取数据合同。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: int = Field(gt=0)
    job_id: int = Field(gt=0)
    source_file: str = Field(min_length=1, max_length=500)
    requirement: RequirementItem

    @field_validator("source_file")
    @classmethod
    def source_file_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白来源文件名，确保要求映射可以回到所属JD。"""
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
    """记录一条要求实例到标准要求项的映射、理由和置信度。"""

    model_config = ConfigDict(extra="forbid")

    requirement_id: int = Field(gt=0)
    status: RequirementMappingStatus
    canonical_requirement_id: str | None = Field(default=None, max_length=100)
    candidate_requirement_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("canonical_requirement_id")
    @classmethod
    def optional_requirement_id_must_be_meaningful(
        cls, value: str | None
    ) -> str | None:
        """拒绝已映射结果使用空白标准要求项ID。"""
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("candidate_requirement_ids")
    @classmethod
    def candidate_ids_must_be_meaningful(cls, values: list[str]) -> list[str]:
        """清理待审核候选ID并拒绝空白或重复候选。"""
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("候选标准要求项ID不能为空")
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("候选标准要求项ID不能重复")
        return cleaned

    @field_validator("rationale")
    @classmethod
    def rationale_must_not_be_blank(cls, value: str) -> str:
        """拒绝缺少映射理由的映射结果。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @model_validator(mode="after")
    def status_fields_must_match(self) -> Self:
        """确保确定映射、未映射和待审核状态只携带适用字段。"""
        if self.status is RequirementMappingStatus.MAPPED:
            if self.canonical_requirement_id is None:
                raise ValueError("mapped结果必须包含标准要求项ID")
            if self.candidate_requirement_ids:
                raise ValueError("mapped结果不能包含待审核候选")
        else:
            if self.canonical_requirement_id is not None:
                raise ValueError("非mapped结果不能保存确定标准要求项ID")
            if (
                self.status is RequirementMappingStatus.REVIEW_REQUIRED
                and not self.candidate_requirement_ids
            ):
                raise ValueError("review_required结果必须包含候选标准要求项")
            if (
                self.status is RequirementMappingStatus.UNMAPPED
                and self.candidate_requirement_ids
            ):
                raise ValueError("unmapped结果不能包含候选标准要求项")
        return self


class RequirementRelation(BaseModel):
    """表示标准要求项之间的关系；下位指向上位，组成部分指向整体。"""

    model_config = ConfigDict(extra="forbid")

    source_requirement_id: str = Field(min_length=1, max_length=100)
    target_requirement_id: str = Field(min_length=1, max_length=100)
    relation_type: RequirementRelationType
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)

    @field_validator("source_requirement_id", "target_requirement_id", "rationale")
    @classmethod
    def relation_text_must_not_be_blank(cls, value: str) -> str:
        """清理要求关系文本并拒绝空白引用或理由。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @model_validator(mode="after")
    def relation_must_not_reference_itself(self) -> Self:
        """拒绝标准要求项与自身建立关系。"""
        if self.source_requirement_id == self.target_requirement_id:
            raise ValueError("要求关系不能指向自身")
        return self


class RequirementConsolidationResult(BaseModel):
    """保存跨JD归并产生的标准要求项、要求映射和非同义关系。"""

    model_config = ConfigDict(extra="forbid")

    canonical_requirements: list[CanonicalRequirement] = Field(default_factory=list)
    mappings: list[RequirementMapping] = Field(min_length=1)
    relations: list[RequirementRelation] = Field(default_factory=list)

    @model_validator(mode="after")
    def result_must_be_internally_consistent(self) -> Self:
        """校验标准名称、要求映射和关系引用在同一结果中保持一致。"""
        requirement_ids = [
            item.canonical_requirement_id for item in self.canonical_requirements
        ]
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("标准要求项ID不能重复")

        normalized_names = [
            normalize_requirement_name(item.canonical_name)
            for item in self.canonical_requirements
        ]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("标准要求项名称不能重复")

        occurrence_ids = [mapping.requirement_id for mapping in self.mappings]
        if len(occurrence_ids) != len(set(occurrence_ids)):
            raise ValueError("同一要求实例不能产生多条映射结果")

        known_requirements = set(requirement_ids)
        referenced_requirements: set[str] = set()
        for mapping in self.mappings:
            mapping_requirements = (
                [mapping.canonical_requirement_id]
                if mapping.canonical_requirement_id is not None
                else mapping.candidate_requirement_ids
            )
            for requirement_id in mapping_requirements:
                if requirement_id not in known_requirements:
                    raise ValueError(f"映射引用未知标准要求项：{requirement_id}")
                if mapping.canonical_requirement_id is not None:
                    referenced_requirements.add(requirement_id)

        unused_requirements = sorted(known_requirements - referenced_requirements)
        if unused_requirements:
            raise ValueError(
                f"标准要求项没有来源要求：{', '.join(unused_requirements)}"
            )

        relation_keys: set[tuple[str, str, RequirementRelationType]] = set()
        for relation in self.relations:
            if relation.source_requirement_id not in known_requirements:
                raise ValueError(
                    f"关系引用未知标准要求项：{relation.source_requirement_id}"
                )
            if relation.target_requirement_id not in known_requirements:
                raise ValueError(
                    f"关系引用未知标准要求项：{relation.target_requirement_id}"
                )
            source_id = relation.source_requirement_id
            target_id = relation.target_requirement_id
            if relation.relation_type is RequirementRelationType.RELATED_TO:
                source_id, target_id = sorted((source_id, target_id))
            key = (source_id, target_id, relation.relation_type)
            if key in relation_keys:
                raise ValueError("要求关系不能重复")
            relation_keys.add(key)
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
