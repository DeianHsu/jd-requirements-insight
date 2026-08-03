"""该模块使用Pydantic定义JD文件进入数据库前的输入校验规则。"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, Self

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


class JobDocument(BaseModel):
    """表示一份已解析但尚未持久化的Markdown JD，并统一校验字段类型。"""

    # 允许暂未纳入稳定字段的元数据进入系统，避免早期迭代丢失信息。
    model_config = ConfigDict(extra="allow")

    source_url: str | None = None
    source_type: str = "unknown"
    source_image: str | None = None
    collected_at: date
    company: str
    title: str
    title_truncated: bool = False
    city: str | None = None
    salary: str | None = None
    experience: str | None = None
    education: str | None = None
    company_type: str = "unknown"
    company_size: str | None = None
    industry: str | None = None
    financing_status: str | None = None
    tags: list[str] = Field(default_factory=list)
    raw_text: str
    source_file: str

    @field_validator("company", "title", "raw_text")
    @classmethod
    def must_not_be_blank(cls, value: str) -> str:
        """拒绝只包含空白字符的公司、岗位和正文，防止无效记录进入数据库。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    def unknown_metadata(self) -> dict[str, Any]:
        """返回尚未进入稳定抽取数据合同的额外元数据，以便原样保存和后续扩展。"""
        return dict(self.model_extra or {})


class RoleFamily(StrEnum):
    """限定岗位所属方向，避免模型为相同岗位生成不一致的自由文本分类。"""

    AGENT_APPLICATION = "agent_application"
    LLM_APPLICATION = "llm_application"
    RAG_APPLICATION = "rag_application"
    AI_ALGORITHM = "ai_algorithm"
    AI_PLATFORM = "ai_platform"
    OTHER = "other"
    UNKNOWN = "unknown"


class Seniority(StrEnum):
    """限定岗位级别，用于后续区分初级、转型、中级和高级市场要求。"""

    JUNIOR = "junior"
    TRANSITION = "transition"
    MID = "mid"
    SENIOR = "senior"
    UNKNOWN = "unknown"


class RequirementCategory(StrEnum):
    """限定岗位要求类别，为后续分组统计提供稳定维度。"""

    PROGRAMMING_LANGUAGE = "programming_language"
    BACKEND_ENGINEERING = "backend_engineering"
    AGENT_FRAMEWORK = "agent_framework"
    AGENT_CAPABILITY = "agent_capability"
    RAG = "rag"
    LLM_APPLICATION = "llm_application"
    MODEL_TRAINING = "model_training"
    ML_FRAMEWORK = "ml_framework"
    RETRIEVAL = "retrieval"
    DEPLOYMENT = "deployment"
    SOFTWARE_ENGINEERING = "software_engineering"
    DOMAIN_KNOWLEDGE = "domain_knowledge"
    EDUCATION = "education"
    EXPERIENCE = "experience"
    SOFT_SKILL = "soft_skill"
    OTHER = "other"


class RequirementImportance(StrEnum):
    """限定要求的重要程度，区分硬性要求、加分项和普通提及。"""

    MUST = "must"
    PREFERRED = "preferred"
    MENTIONED = "mentioned"
    UNKNOWN = "unknown"


class ProficiencyLevel(StrEnum):
    """限定JD表达粗粒度的掌握程度（Schema V3 三级）。

    - `unknown`：JD 没有明确熟练程度，或只写项目经验、使用经验、有经验、
      参与过；不得推断程度。
    - `basic`：了解、理解、熟悉、能够使用、具备基础使用能力。
    - `advanced`：掌握、熟练、扎实、精通、专家级。

    原始程度词保留在 evidence 与 raw_name，枚举只表达粗粒度岗位门槛；
    `none`（完全不会）属于未来候选人个人能力层，不属于岗位要求。
    """

    UNKNOWN = "unknown"
    BASIC = "basic"
    ADVANCED = "advanced"


# V2 五级熟练度到 V3 三级的确定性映射（旧数据读取兼容，不重写物理数据）。
LEGACY_PROFICIENCY_MAP: dict[str, ProficiencyLevel] = {
    "understand": ProficiencyLevel.BASIC,
    "familiar": ProficiencyLevel.BASIC,
    "proficient": ProficiencyLevel.ADVANCED,
    "expert": ProficiencyLevel.ADVANCED,
}


def map_legacy_proficiency(value: str) -> ProficiencyLevel:
    """把任意版本熟练度值映射为 Schema V3 三级；未知非法值明确失败。

    旧 Schema V2 五级值按确定性映射转换；未知值抛 ValueError，
    不允许静默归入 unknown。
    """
    try:
        return ProficiencyLevel(value)
    except ValueError:
        pass
    mapped = LEGACY_PROFICIENCY_MAP.get(value)
    if mapped is None:
        raise ValueError(f"未知熟练度值：{value!r}（不允许静默归入 unknown）")
    return mapped


class RequirementGroupLogic(StrEnum):
    """限定原子要求的组合逻辑，区分独立条件与满足任意一项的候选组。"""

    STANDALONE = "standalone"
    ANY_OF = "any_of"


class ResponsibilityItem(BaseModel):
    """表示一项岗位职责及其在原始JD中的连续证据文本。"""

    model_config = ConfigDict(extra="forbid")

    name: str
    evidence: str

    @field_validator("name", "evidence")
    @classmethod
    def responsibility_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白职责名称和证据，确保每项职责都可阅读并可追溯。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned


class RequirementItem(BaseModel):
    """表示一项原子岗位要求及其逻辑组、年限范围和可追溯原文证据。"""

    model_config = ConfigDict(extra="forbid")

    raw_name: str
    category: RequirementCategory
    importance: RequirementImportance
    proficiency: ProficiencyLevel = ProficiencyLevel.UNKNOWN
    group_id: str | None = Field(default=None, max_length=100)
    group_logic: RequirementGroupLogic = RequirementGroupLogic.STANDALONE
    min_years: float | None = Field(
        default=None,
        ge=0,
        le=50,
        validation_alias=AliasChoices("min_years", "years_required"),
    )
    max_years: float | None = Field(default=None, ge=0, le=50)
    years_text: str | None = Field(default=None, max_length=100)
    evidence: str
    confidence: float = Field(ge=0, le=1)

    @field_validator("proficiency", mode="before")
    @classmethod
    def coerce_proficiency(cls, value: object) -> object:
        """读取兼容：旧 Schema V2 五级值确定性映射为三级，未知值明确失败。

        数据库与历史 raw_response 只保存字符串，Schema 版本由抽取器版本
        隔离；本校验让旧结果无需重写物理数据即可按当前合同读取。
        """
        if isinstance(value, ProficiencyLevel):
            return value
        if isinstance(value, str):
            return map_legacy_proficiency(value)
        raise ValueError(f"熟练度必须是字符串或 ProficiencyLevel：{value!r}")

    @field_validator("raw_name", "evidence")
    @classmethod
    def requirement_must_not_be_blank(cls, value: str) -> str:
        """拒绝空白要求名称和证据，避免产生无法解释的统计记录。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("group_id", "years_text")
    @classmethod
    def optional_text_must_be_meaningful(cls, value: str | None) -> str | None:
        """把可选文本的空白值统一为空值，避免创建不可引用的逻辑组或年限描述。"""
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def validate_group_and_year_range(self) -> Self:
        """校验逻辑组字段必须配套使用，并拒绝上下限颠倒的经验范围。"""
        # 独立要求不应携带组ID，any_of要求则必须能通过组ID找到其他候选项。
        if self.group_logic is RequirementGroupLogic.STANDALONE and self.group_id is not None:
            raise ValueError("standalone要求不能设置group_id")
        if self.group_logic is RequirementGroupLogic.ANY_OF and self.group_id is None:
            raise ValueError("any_of要求必须设置group_id")
        if (
            self.min_years is not None
            and self.max_years is not None
            and self.max_years < self.min_years
        ):
            raise ValueError("max_years不能小于min_years")
        return self


class JobExtractionResult(BaseModel):
    """定义一份JD完成结构化抽取后必须满足的完整输出合同。"""

    model_config = ConfigDict(extra="forbid")

    role_family: RoleFamily
    seniority: Seniority
    responsibilities: list[ResponsibilityItem]
    requirements: list[RequirementItem]

    @model_validator(mode="after")
    def any_of_group_must_have_multiple_members(self) -> Self:
        """拒绝只有一个成员的any_of组，防止逻辑组失去“任选其一”的业务含义。"""
        group_sizes: dict[str, int] = {}
        for item in self.requirements:
            if item.group_logic is RequirementGroupLogic.ANY_OF and item.group_id is not None:
                group_sizes[item.group_id] = group_sizes.get(item.group_id, 0) + 1
        invalid_groups = sorted(group_id for group_id, size in group_sizes.items() if size < 2)
        if invalid_groups:
            raise ValueError(f"any_of组至少需要两个成员：{', '.join(invalid_groups)}")
        return self


class GoldenExtractionRecord(BaseModel):
    """把人工标准答案与原始JD文件名绑定，供自动评测和证据校验使用。"""

    model_config = ConfigDict(extra="forbid")

    source_file: str
    extraction: JobExtractionResult
