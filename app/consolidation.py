"""装配跨JD原子要求归并的输入，并调用LLM批量提出归并结果。

本模块只定义领域无关的归并执行逻辑：输入装配、LLM客户端、Prompt、
解析与有限重试；具体归并合同定义在`app/requirement_consolidation.py`。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import LLMSettings
from app.models import (
    CanonicalRequirementRecord,
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
    RequirementRelationRecord,
)
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementOccurrence,
    validate_requirement_coverage,
)
from app.schemas import RequirementItem

CONSOLIDATION_PROMPT_VERSION = "1.3"
CONSOLIDATION_SCHEMA_VERSION = "1.0"

# Prompt V1只定义通用归并任务：同义归并、关系判断和未映射处理，不出现任何具体领域技能。
CONSOLIDATION_SYSTEM_PROMPT = """你是跨JD岗位要求归并器。输入是一批来自不同JD的原子要求实例，你需要判断哪些实例指向同一招聘条件，并为无法确定或需要人工判断的实例标注状态。只能依据每个实例提供的原始名称和证据上下文判断，不得补充任何领域知识或行业常识。

【任务边界】
1. 每个输入实例必须且只能产生一条mapping结果（mapped、unmapped或review_required之一），不得遗漏任何实例，也不得为不存在的实例生成结果。
2. 同义归并：只有表面名称不同但结合证据上下文后指向同一招聘条件的实例才归并到同一标准要求项。例如实例A（raw_name="能力甲使用经验"，evidence="具备能力甲使用经验"）与实例B（raw_name="具备能力甲的使用经验"，evidence="具备能力甲的使用经验"）应归并到同一标准要求项"能力甲使用经验"。
3. 同一表面词可以因证据上下文不同而映射到不同标准要求项；不能只因为名称文本相同就自动归并，必须结合证据判断。
4. 上下位、组成或仅相关关系不是同义，不能触发归并；这类实例分别映射到各自标准要求项，再建立is_a、part_of或related_to关系。
5. 关系方向固定：is_a和part_of的source指向target（下位指向上位、组成部分指向整体）；related_to无方向，反向重复视为同一关系。
6. 证据不足无法确定映射目标时使用unmapped；存在多个候选且需要人工判断时使用review_required，并把候选标准要求项ID填入candidate_requirement_ids。
7. 不得修改、覆盖或删除任何输入实例的原始名称、证据和属性；归并只产生标准要求项和映射，不改写输入。
8. 每条mapping和relation都必须给出理由：说明依据了哪些证据和名称线索，不能只写"语义相同"或"相关"。

【标准项名称】
1. 标准要求项名称使用简洁、独立可读的常见表达；优先直接采用最贴近该条件且完整的实例原文（如实例原文就是简洁完整的"能力甲"，标准项名称就用"能力甲"），改写只做最小必要修改（如去掉"相关知识"等冗余后缀、统一中英文表达）。
2. 不要用"/"拼接多个实例原文，不要添加前缀修饰（如"主流开发语言""优先"），不要缩写或简化（如"能力甲基础"不能缩成"能力甲"）。
3. 名称只表达招聘条件本身，不得包含括号注释、任选组说明、重要性或熟练度标注（例如不得出现"（必备）""（任选）"等括号内容）。
4. 示例：实例"能力甲应用开发相关知识"与"甲类应用开发"归并后，标准项名称使用"能力甲应用开发"，而不是"能力甲/甲类应用开发知识"或"主流能力甲应用开发"。

【归并边界】
1. 任选组（any_of，例如"至少掌握一门主流能力"中的各选项）的成员与单独的硬性条件（例如"具备扎实的能力甲基础"）即使表面名称相似，也代表不同招聘门槛，不得归并到同一标准要求项。
2. 单独条件只是整体的一部分时（例如"能力甲分析能力"是"能力甲分析与解决能力"的一部分），不得与整体归并，应独立映射并建立part_of关系。
3. 示例：实例"能力甲"（属于"至少掌握一门主流能力"任选组）与实例"能力甲基础"（硬性条件）不得归并，必须分别映射到不同标准要求项。

【关系生成】
1. 同一证据中并列出现的多个机制、能力或组件（例如"任务分解、工具调用、协同执行等机制"）彼此必须全部两两建立related_to关系，不得遗漏任意一对。
2. 明显属于整体组成部分的具体活动与上位活动（例如"能力甲实施经验"是"能力甲落地经验"的一部分）必须建立part_of关系，不能用related_to代替。
3. 关系不是可选项：只要符合上述情形就必须输出关系，不能只输出映射。

【输出要求】
严格按照用户提供的JSON结构输出一个JSON对象，包含canonical_requirements、mappings和relations三个数组。不要输出Markdown代码块或额外说明。"""


class ConsolidationError(ValueError):
    """表示跨JD归并在模型调用、JSON解析或合同校验阶段失败。"""


@dataclass(frozen=True)
class ConsolidatorMetadata:
    """记录模型、Prompt和归并合同版本，并生成用于幂等判断的归并器版本。"""

    model_name: str
    prompt_version: str = CONSOLIDATION_PROMPT_VERSION
    schema_version: str = CONSOLIDATION_SCHEMA_VERSION

    @property
    def consolidator_version(self) -> str:
        """组合模型与规则版本，确保规则变化后可以保留新的归并结果。"""
        return (
            f"{self.model_name}|prompt:{self.prompt_version}"
            f"|schema:{self.schema_version}"
        )


class ConsolidationClient(Protocol):
    """定义归并服务依赖的最小LLM客户端接口，便于测试注入假客户端。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """接收系统提示和用户提示，并返回模型生成的JSON文本。"""
        ...


class OpenAICompatibleConsolidationClient:
    """通过OpenAI兼容Chat Completions接口请求JSON格式的归并结果。"""

    def __init__(self, settings: LLMSettings) -> None:
        """根据环境配置初始化OpenAI兼容客户端和模型名称。"""
        client_kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            client_kwargs["base_url"] = settings.base_url
        self._client = OpenAI(**client_kwargs)
        self._model = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用LLM并返回消息正文，接口异常会被包装成统一归并错误。"""
        try:
            # JSON Object模式先约束输出形态，具体字段仍由Pydantic进行严格校验。
            response = self._client.chat.completions.create(
                model=self._model,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as exc:
            raise ConsolidationError(f"LLM调用失败：{exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise ConsolidationError("LLM返回了空内容")
        return content


def load_requirement_occurrences(
    session: Session,
    job_ids: set[int] | None = None,
) -> RequirementConsolidationInput:
    """读取选定JD范围内的全部要求实例，装配为P0-4统一输入。

    `job_ids`为None时读取全部JD；每份JD只取最新抽取结果（按抽取记录ID），
    避免同一要求实例因抽取器版本并存而重复进入归并语料池。本函数只做
    数据搬运与来源定位，不涉及任何领域技能判断。
    """
    if job_ids is not None and not job_ids:
        raise ValueError("job_ids不能为空集合")

    # 每份JD只保留最新抽取记录ID，旧版本结果不参与归并。
    latest_extraction_ids = (
        select(func.max(JobExtraction.id))
        .group_by(JobExtraction.job_id)
        .scalar_subquery()
    )
    query = (
        select(JobRequirement, JobExtraction.job_id, JobDescription.source_file)
        .join(JobExtraction, JobRequirement.extraction_id == JobExtraction.id)
        .join(JobDescription, JobExtraction.job_id == JobDescription.id)
        .where(JobExtraction.id.in_(latest_extraction_ids))
    )
    if job_ids is not None:
        query = query.where(JobDescription.id.in_(job_ids))

    # 按JD和原始要求排序，保证同一语料池的装配结果可复现。
    rows = session.execute(
        query.order_by(JobDescription.id, JobRequirement.id)
    ).all()

    occurrences = [
        RequirementOccurrence(
            requirement_id=requirement.id,
            job_id=job_id,
            source_file=source_file,
            requirement=RequirementItem(
                raw_name=requirement.raw_name,
                category=requirement.category,
                importance=requirement.importance,
                proficiency=requirement.proficiency,
                group_id=requirement.group_id,
                group_logic=requirement.group_logic,
                min_years=requirement.min_years,
                max_years=requirement.max_years,
                years_text=requirement.years_text,
                evidence=requirement.evidence,
                confidence=requirement.confidence,
            ),
        )
        for requirement, job_id, source_file in rows
    ]
    if not occurrences:
        raise ValueError("选定范围内没有可归并的要求实例")

    return RequirementConsolidationInput(occurrences=occurrences)


def build_consolidation_user_prompt(
    consolidation_input: RequirementConsolidationInput,
) -> str:
    """把要求实例列表序列化为归并调用输入，并说明期望的输出结构。"""
    instances = [
        {
            "id": occurrence.requirement_id,
            "job_id": occurrence.job_id,
            "source_file": occurrence.source_file,
            "raw_name": occurrence.requirement.raw_name,
            "evidence": occurrence.requirement.evidence,
            "category": occurrence.requirement.category.value,
            "importance": occurrence.requirement.importance.value,
            "proficiency": occurrence.requirement.proficiency.value,
            "group_id": occurrence.requirement.group_id,
            "group_logic": occurrence.requirement.group_logic.value,
            "min_years": occurrence.requirement.min_years,
            "max_years": occurrence.requirement.max_years,
            "years_text": occurrence.requirement.years_text,
        }
        for occurrence in consolidation_input.occurrences
    ]
    payload = {
        "task": "请对以下要求实例执行跨JD原子要求归并，输出canonical_requirements、mappings和relations三个数组。",
        "output_schema": {
            "canonical_requirements": [
                {
                    "canonical_requirement_id": "string，唯一标识",
                    "canonical_name": "string",
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ],
            "mappings": [
                {
                    "requirement_id": "int，输入实例的id",
                    "status": "mapped|unmapped|review_required",
                    "canonical_requirement_id": "string或null",
                    "candidate_requirement_ids": ["string，review_required时使用"],
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ],
            "relations": [
                {
                    "source_requirement_id": "string",
                    "target_requirement_id": "string",
                    "relation_type": "is_a|part_of|related_to",
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ],
        },
        "requirements": instances,
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_consolidation_response(response_text: str) -> RequirementConsolidationResult:
    """把模型JSON文本解析并校验为严格的RequirementConsolidationResult。"""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ConsolidationError(f"模型返回的内容不是合法JSON：{exc}") from exc

    try:
        return RequirementConsolidationResult.model_validate(payload)
    except ValidationError as exc:
        raise ConsolidationError(f"模型输出不符合归并合同：{exc}") from exc


def consolidate_with_correction(
    consolidation_input: RequirementConsolidationInput,
    client: ConsolidationClient,
    system_prompt: str = CONSOLIDATION_SYSTEM_PROMPT,
    max_attempts: int = 2,
) -> tuple[RequirementConsolidationResult, dict[str, object]]:
    """执行跨JD归并调用，并在JSON解析、合同校验或覆盖检查失败时有限重试。"""
    correction = None
    last_error: ConsolidationError | ValueError | None = None

    for _ in range(max_attempts):
        prompt = build_consolidation_user_prompt(consolidation_input)
        if correction is not None:
            # 将校验错误反馈给下一次请求，让模型只修正具体结构或覆盖问题。
            prompt = f"{prompt}\n\n【上次校验错误，请修正后重新输出】\n{correction}"
        response_text = client.complete(system_prompt, prompt)
        try:
            result = parse_consolidation_response(response_text)
            validate_requirement_coverage(consolidation_input, result)
            return result, result.model_dump(mode="json")
        except (ConsolidationError, ValueError) as exc:
            last_error = exc
            correction = str(exc)

    raise ConsolidationError(
        f"经过{max_attempts}次尝试仍未通过归并校验：{last_error}"
    )


@dataclass
class ConsolidationFailure:
    """记录一次跨JD归并在装配或模型调用阶段的失败原因。"""

    scope: str
    message: str


@dataclass
class ConsolidationSummary:
    """汇总一次跨JD归并的发现、成功、跳过和失败数量。"""

    discovered: int = 0
    consolidated: int = 0
    canonical_count: int = 0
    relation_count: int = 0
    skipped: int = 0
    errors: list[ConsolidationFailure] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """返回失败的归并次数。"""
        return len(self.errors)


def scope_key_for(job_ids: set[int] | None) -> str:
    """生成幂等范围键：全部JD为all，指定JD为排序后的job_ids。"""
    if job_ids is None:
        return "all"
    return "job_ids=" + ",".join(str(job_id) for job_id in sorted(job_ids))


def persist_consolidation(
    session: Session,
    consolidation_input: RequirementConsolidationInput,
    result: RequirementConsolidationResult,
    raw_response: dict[str, object],
    metadata: ConsolidatorMetadata,
    scope_key: str,
) -> tuple[JobConsolidation, bool]:
    """按范围键和归并器版本幂等保存归并结果，并返回记录与是否新建。"""
    existing = session.scalar(
        select(JobConsolidation).where(
            JobConsolidation.scope_key == scope_key,
            JobConsolidation.consolidator_version
            == metadata.consolidator_version,
        )
    )
    if existing is not None:
        return existing, False

    # 先创建批次主记录并flush获得ID，再关联标准要求项、映射和关系子记录。
    consolidation = JobConsolidation(
        scope_key=scope_key,
        consolidator_version=metadata.consolidator_version,
        model_name=metadata.model_name,
        prompt_version=metadata.prompt_version,
        schema_version=metadata.schema_version,
        occurrence_count=len(consolidation_input.occurrences),
        raw_response=raw_response,
    )
    session.add(consolidation)
    session.flush()

    consolidation.canonical_requirements.extend(
        CanonicalRequirementRecord(
            canonical_requirement_id=item.canonical_requirement_id,
            canonical_name=item.canonical_name,
            rationale=item.rationale,
            confidence=item.confidence,
        )
        for item in result.canonical_requirements
    )
    consolidation.mappings.extend(
        RequirementMappingRecord(
            requirement_id=mapping.requirement_id,
            status=mapping.status.value,
            canonical_requirement_id=mapping.canonical_requirement_id,
            candidate_requirement_ids=mapping.candidate_requirement_ids,
            rationale=mapping.rationale,
            confidence=mapping.confidence,
        )
        for mapping in result.mappings
    )
    consolidation.relations.extend(
        RequirementRelationRecord(
            source_requirement_id=relation.source_requirement_id,
            target_requirement_id=relation.target_requirement_id,
            relation_type=relation.relation_type.value,
            rationale=relation.rationale,
            confidence=relation.confidence,
        )
        for relation in result.relations
    )
    session.commit()
    return consolidation, True


def consolidate_requirements(
    session_factory: sessionmaker[Session],
    client: ConsolidationClient,
    metadata: ConsolidatorMetadata,
    max_attempts: int = 2,
    job_ids: set[int] | None = None,
) -> ConsolidationSummary:
    """对选定JD范围执行一次跨JD归并并幂等持久化，失败隔离不中断。

    当前语料规模（约150条实例）已验证可单次调用完成，不做分批；
    批次边界待P0-8数据扩充后再评估。同范围同归并器版本已有结果时
    跳过模型调用并计入skipped。
    """
    scope = scope_key_for(job_ids)
    summary = ConsolidationSummary()

    try:
        with session_factory() as session:
            pool = load_requirement_occurrences(session, job_ids=job_ids)
    except ValueError as exc:
        summary.errors.append(ConsolidationFailure(scope, str(exc)))
        return summary

    summary.discovered = len(pool.occurrences)

    # 幂等：同范围同版本已有结果时不重复调用模型。
    with session_factory() as session:
        exists = session.scalar(
            select(JobConsolidation.id).where(
                JobConsolidation.scope_key == scope,
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
            )
        )
    if exists is not None:
        summary.skipped = summary.discovered
        return summary

    try:
        result, raw_response = consolidate_with_correction(
            pool, client, max_attempts=max_attempts
        )
    except (ConsolidationError, ValueError) as exc:
        summary.errors.append(ConsolidationFailure(scope, str(exc)))
        return summary

    # 模型调用结束后再开启写事务，避免网络等待期间长期占用数据库连接。
    with session_factory() as session:
        _, created = persist_consolidation(
            session, pool, result, raw_response, metadata, scope
        )
        if not created:
            summary.skipped = len(result.mappings)
            return summary

    summary.consolidated = len(result.mappings)
    summary.canonical_count = len(result.canonical_requirements)
    summary.relation_count = len(result.relations)
    return summary
