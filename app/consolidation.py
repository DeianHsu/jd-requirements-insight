"""装配跨JD原子要求归并的输入，并调用LLM批量提出归并结果。

本模块只定义领域无关的归并执行逻辑：输入装配、LLM客户端、Prompt、
解析与有限重试；具体归并合同定义在`app/requirement_consolidation.py`。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable, Protocol, TypeVar

import httpx
from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from app.config import LLMSettings
from app.models import (
    CanonicalRequirementRecord,
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
    RequirementMappingRecord,
)
from app.requirement_consolidation import (
    CanonicalRequirement,
    RequirementConsolidationInput,
    RequirementConsolidationResult,
    RequirementMapping,
    RequirementOccurrence,
    validate_requirement_coverage,
)
from app.schemas import RequirementItem

CONSOLIDATION_PROMPT_VERSION = "4.0"
CONSOLIDATION_SCHEMA_VERSION = "2.0"
CONSOLIDATION_READ_TIMEOUT_SECONDS = 900.0
# 单次完整请求在149实例规模下曾出现约50KB响应截断；映射阶段按此上限
# 受控分块，使任何单次映射请求的实例数与输出规模都远离截断边界。
CONSOLIDATION_MAPPING_CHUNK_SIZE = 50


# Prompt v4.0：保守归并（全 mapped + singleton 回退），不出现任何具体领域技能。
CONSOLIDATION_SYSTEM_PROMPT = """你是跨JD岗位要求归并器。输入是一批来自不同JD的原子要求实例，你需要判断哪些实例指向同一招聘条件，并把每个实例映射到一个标准要求项。只能依据每个实例提供的原始名称和证据上下文判断，不得补充任何领域知识或行业常识。

【任务边界】
1. 每个输入实例必须且只能产生一条映射结果，不得遗漏任何实例，也不得为不存在的实例生成结果。
2. 每个标准要求项必须至少有一个实例作为来源；标准要求项数量不超过输入实例数量，只为有实例依据的条件创建标准项，不得提出无人引用的标准项。
3. 同义归并：只有表面名称不同但结合证据上下文后指向同一招聘条件（达到可以合并统计的程度）的实例才归并到同一标准要求项。例如实例A（raw_name="能力甲使用经验"，evidence="具备能力甲使用经验"）与实例B（raw_name="具备能力甲的使用经验"，evidence="具备能力甲的使用经验"）应归并到同一标准要求项"能力甲使用经验"。
4. 保守归并：无法确认是否等价的实例保持为不同标准要求项，每个实例至少可以形成自己的singleton标准要求项；不允许因为名称中出现相同关键词或属于同一技术领域就强制合并。
4. 同一表面词可以因证据上下文不同而映射到不同标准要求项；不能只因为名称文本相同就自动归并，必须结合证据判断。
5. 不得修改、覆盖或删除任何输入实例的原始名称、证据和属性；归并只产生标准要求项和映射，不改写输入。
6. 每条mapping都必须给出理由：说明依据了哪些证据和名称线索，不能只写"语义相同"或"相关"。

【标准项名称】
1. 标准要求项名称使用简洁、独立可读的常见表达，优先采用最贴近该条件的原文表达；改写只做最小必要修改：去掉"相关知识""概念"等冗余后缀，统一中英文或同义表达（例如"能力甲应用开发"与"甲类应用开发"归并后统一使用一个名称）。
2. 不要用"/"拼接多个实例原文，不要添加前缀修饰（如"主流开发语言""优先"），不要缩写或简化（如"能力甲基础"不能缩成"能力甲"）。
3. 名称只表达招聘条件本身，不得包含括号注释、任选组说明、重要性或熟练度标注（例如不得出现"（必备）""（任选）"等括号内容）。
4. 示例：实例"能力甲应用开发相关知识"与"甲类应用开发"归并后，标准项名称使用"能力甲应用开发"，而不是"能力甲/甲类应用开发知识"或"主流能力甲应用开发"。
5. canonical_requirement_id和去除大小写、空白差异后的canonical_name都必须全局唯一。若两个标准项会得到同一名称且确实是同一招聘条件，必须合并为一个标准项；若证据表明它们是不同条件，名称必须保留原文或证据中的最小区分信息，不能用括号元数据硬凑唯一名称。

【归并边界】
1. 任选组（any_of，例如"至少掌握一门主流能力"中的各选项）的成员与单独的硬性条件（例如"具备扎实的能力甲基础"）即使表面名称相似，也代表不同招聘门槛，不得归并到同一标准要求项。
2. 单独条件只是整体的一部分时（例如"能力甲分析能力"是"能力甲分析与解决能力"的一部分），不得与整体归并，应独立映射到各自标准要求项。
3. 年限或数值门槛不同的条件不得归并：例如"3年以上能力甲经验"与"5年以上能力甲经验"是不同招聘门槛，必须分别映射到不同标准要求项；实例带年限时，标准项名称应保留年限含义（如"3年以上能力甲经验"）。
4. 示例：实例"能力甲"（属于"至少掌握一门主流能力"任选组）与实例"能力甲基础"（硬性条件）不得归并，必须分别映射到不同标准要求项。

【输出要求】
严格按照用户提示要求的JSON结构输出，只输出用户提示指定的数组，不要输出Markdown代码块或额外说明。"""


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


@dataclass(frozen=True)
class ConsolidationSelection:
    """冻结一次归并选择的JD、抽取版本、要求实例和确定性输入指纹。"""

    selected_job_ids: tuple[int, ...]
    extraction_ids: tuple[int, ...]
    extractor_version: str
    consolidation_input: RequirementConsolidationInput
    input_fingerprint: str


def _build_input_fingerprint(
    selected_job_ids: tuple[int, ...],
    extraction_ids: tuple[int, ...],
    extractor_version: str,
    consolidation_input: RequirementConsolidationInput,
) -> str:
    """对归并范围、抽取版本和完整要求实例计算稳定SHA-256指纹。"""
    payload = {
        "selected_job_ids": selected_job_ids,
        "extraction_ids": extraction_ids,
        "extractor_version": extractor_version,
        "occurrences": consolidation_input.model_dump(mode="json")["occurrences"],
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class ConsolidationClient(Protocol):
    """定义归并服务依赖的最小LLM客户端接口，便于测试注入假客户端。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """接收系统提示和用户提示，并返回模型生成的JSON文本。"""
        ...


class OpenAICompatibleConsolidationClient:
    """通过OpenAI兼容Chat Completions接口请求JSON格式的归并结果。"""

    def __init__(self, settings: LLMSettings) -> None:
        """根据环境配置初始化OpenAI兼容客户端和模型名称。"""
        client_kwargs: dict[str, object] = {
            "api_key": settings.api_key,
            # 全量约150条实例的历史响应耗时可达8分钟；保留短连接超时，
            # 仅放宽读取阶段，并关闭SDK隐式重试以便由项目层统一计数。
            "timeout": httpx.Timeout(
                600.0,
                connect=5.0,
                read=CONSOLIDATION_READ_TIMEOUT_SECONDS,
            ),
            "max_retries": 0,
        }
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


def load_consolidation_selection(
    session: Session,
    job_ids: set[int] | None = None,
    extractor_version: str | None = None,
) -> ConsolidationSelection:
    """选择完整JD范围的共同抽取版本，并装配可复现的P0-4输入。"""
    if job_ids is not None and not job_ids:
        raise ValueError("job_ids不能为空集合")

    jobs_statement = select(JobDescription).order_by(JobDescription.id)
    if job_ids is not None:
        jobs_statement = jobs_statement.where(JobDescription.id.in_(job_ids))
    jobs = list(session.scalars(jobs_statement))
    found_job_ids = {job.id for job in jobs}
    if job_ids is not None:
        missing_job_ids = sorted(job_ids - found_job_ids)
        if missing_job_ids:
            raise ValueError(f"指定JD不存在：{missing_job_ids}")
    if not jobs:
        raise ValueError("选定范围内没有JD")

    selected_job_ids = tuple(job.id for job in jobs)
    extraction_statement = select(JobExtraction).where(
        JobExtraction.job_id.in_(selected_job_ids)
    )
    extractions = list(session.scalars(extraction_statement))
    versions_by_job = {job_id: set() for job_id in selected_job_ids}
    for extraction in extractions:
        versions_by_job[extraction.job_id].add(extraction.extractor_version)

    if extractor_version is None:
        common_versions = set.intersection(*versions_by_job.values())
        if not common_versions:
            missing = sorted(
                job_id for job_id, versions in versions_by_job.items() if not versions
            )
            if missing:
                raise ValueError(f"JD缺少抽取结果：{missing}")
            raise ValueError("选定JD不存在共同抽取器版本，请使用--extractor-version")
        if len(common_versions) > 1:
            versions = ", ".join(sorted(common_versions))
            raise ValueError(f"存在多个共同抽取器版本，请明确指定：{versions}")
        extractor_version = next(iter(common_versions))

    selected_extractions = sorted(
        (
            extraction
            for extraction in extractions
            if extraction.extractor_version == extractor_version
        ),
        key=lambda extraction: extraction.job_id,
    )
    extraction_job_ids = {extraction.job_id for extraction in selected_extractions}
    missing_extraction_ids = sorted(set(selected_job_ids) - extraction_job_ids)
    if missing_extraction_ids:
        raise ValueError(
            f"JD缺少抽取器版本{extractor_version}的结果：{missing_extraction_ids}"
        )

    extraction_ids = tuple(extraction.id for extraction in selected_extractions)
    query = (
        select(
            JobRequirement,
            JobExtraction.job_id,
            JobExtraction.id,
            JobExtraction.extractor_version,
            JobDescription.source_hash,
            JobDescription.source_file,
        )
        .join(JobExtraction, JobRequirement.extraction_id == JobExtraction.id)
        .join(JobDescription, JobExtraction.job_id == JobDescription.id)
        .where(JobExtraction.id.in_(extraction_ids))
    )

    rows = session.execute(
        query.order_by(JobDescription.id, JobRequirement.id)
    ).all()

    occurrences = [
        RequirementOccurrence(
            requirement_id=requirement.id,
            job_id=job_id,
            extraction_id=extraction_id,
            extractor_version=selected_extractor_version,
            source_hash=source_hash,
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
        for (
            requirement,
            job_id,
            extraction_id,
            selected_extractor_version,
            source_hash,
            source_file,
        ) in rows
    ]
    if not occurrences:
        raise ValueError("选定范围内没有可归并的要求实例")

    consolidation_input = RequirementConsolidationInput(occurrences=occurrences)
    input_fingerprint = _build_input_fingerprint(
        selected_job_ids,
        extraction_ids,
        extractor_version,
        consolidation_input,
    )
    return ConsolidationSelection(
        selected_job_ids=selected_job_ids,
        extraction_ids=extraction_ids,
        extractor_version=extractor_version,
        consolidation_input=consolidation_input,
        input_fingerprint=input_fingerprint,
    )


def load_requirement_occurrences(
    session: Session,
    job_ids: set[int] | None = None,
    extractor_version: str | None = None,
) -> RequirementConsolidationInput:
    """兼容既有调用，返回显式选择结果中的要求实例输入。"""
    return load_consolidation_selection(
        session,
        job_ids=job_ids,
        extractor_version=extractor_version,
    ).consolidation_input


def _serialize_occurrences(
    occurrences: list[RequirementOccurrence],
) -> list[dict[str, object]]:
    """把要求实例序列化为提示中的JSON对象列表，完整保留数据合同字段。"""
    return [
        {
            "id": occurrence.requirement_id,
            "job_id": occurrence.job_id,
            "extraction_id": occurrence.extraction_id,
            "extractor_version": occurrence.extractor_version,
            "source_hash": occurrence.source_hash,
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
        for occurrence in occurrences
    ]


def build_consolidation_user_prompt(
    consolidation_input: RequirementConsolidationInput,
) -> str:
    """把要求实例列表序列化为归并调用输入，并说明期望的输出结构。"""
    payload = {
        "task": "请对以下要求实例执行跨JD原子要求归并，输出canonical_requirements和mappings两个数组。",
        "output_schema": {
            "canonical_requirements": [
                {
                    "canonical_requirement_id": "string，唯一标识",
                    "canonical_name": "string，规范化后全局唯一",
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ],
            "mappings": [
                {
                    "requirement_id": "int，输入实例的id",
                    "canonical_requirement_id": "string，必须来自给定的标准要求项",
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ],
        },
        "requirements": _serialize_occurrences(
            consolidation_input.occurrences
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


def build_canonical_requirements_prompt(
    consolidation_input: RequirementConsolidationInput,
) -> str:
    """构建只要求模型提出标准要求项的阶段提示，输出规模与实例数解耦。"""
    payload = {
        "task": (
            "请仅根据每个实例提供的原始名称和证据判断哪些实例指向同一"
            "招聘条件，并提出标准要求项。只输出canonical_requirements，"
            "不要输出mappings。"
        ),
        "output_schema": {
            "canonical_requirements": [
                {
                    "canonical_requirement_id": "string，唯一标识",
                    "canonical_name": "string，规范化后全局唯一",
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ]
        },
        "requirements": _serialize_occurrences(
            consolidation_input.occurrences
        ),
    }
    return json.dumps(payload, ensure_ascii=False)


def build_mappings_prompt(
    chunk_input: RequirementConsolidationInput,
    canonical_requirements: list[CanonicalRequirement],
) -> str:
    """构建只要求模型输出本块实例映射的阶段提示，携带标准项清单供引用。"""
    payload = {
        "task": (
            "请把每条要求实例映射到给定的标准要求项；每个实例必须且只能"
            "产生一条映射，无法确认等价时为该实例创建独立的singleton"
            "标准要求项。只输出mappings。"
        ),
        "output_schema": {
            "mappings": [
                {
                    "requirement_id": "int，本块输入实例的id",
                    "canonical_requirement_id": (
                        "string，必须来自给定的标准要求项；无法确认等价时"
                        "新建singleton标准要求项并引用它"
                    ),
                    "rationale": "string",
                    "confidence": "0到1",
                }
            ]
        },
        "canonical_requirements": [
            {
                "canonical_requirement_id": item.canonical_requirement_id,
                "canonical_name": item.canonical_name,
            }
            for item in canonical_requirements
        ],
        "requirements": _serialize_occurrences(chunk_input.occurrences),
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


def _parse_json_object(response_text: str) -> dict[str, object]:
    """把模型JSON文本解析为顶层JSON对象，并统一包装解析错误。"""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ConsolidationError(f"模型返回的内容不是合法JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ConsolidationError("模型输出不符合归并合同：顶层必须是JSON对象")
    return payload


def parse_canonical_requirements_response(
    response_text: str,
) -> list[CanonicalRequirement]:
    """解析标准项阶段响应，校验每条标准要求项符合合同结构。"""
    payload = _parse_json_object(response_text)
    items = payload.get("canonical_requirements")
    if not isinstance(items, list) or not items:
        raise ConsolidationError(
            "模型输出不符合归并合同：缺少canonical_requirements"
        )
    try:
        return [CanonicalRequirement.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ConsolidationError(f"模型输出不符合归并合同：{exc}") from exc


def parse_mappings_response(response_text: str) -> list[RequirementMapping]:
    """解析映射阶段响应，校验每条映射符合合同结构。"""
    payload = _parse_json_object(response_text)
    items = payload.get("mappings")
    if not isinstance(items, list):
        raise ConsolidationError("模型输出不符合归并合同：缺少mappings")
    try:
        return [RequirementMapping.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ConsolidationError(f"模型输出不符合归并合同：{exc}") from exc


def _chunk_consolidation_input(
    consolidation_input: RequirementConsolidationInput,
    chunk_size: int,
) -> list[RequirementConsolidationInput]:
    """按固定上限把语料池切成互不重叠的映射输入块。"""
    occurrences = consolidation_input.occurrences
    return [
        RequirementConsolidationInput(
            occurrences=occurrences[start : start + chunk_size]
        )
        for start in range(0, len(occurrences), chunk_size)
    ]


def _validate_chunk_coverage(
    chunk_input: RequirementConsolidationInput,
    mappings: list[RequirementMapping],
) -> None:
    """确认映射块为本块每条实例且仅生成一条mapped结果，缺漏可反馈模型修正。

    新批次合同要求每个实例恰好 mapped 一次：出现 unmapped 或
    review_required 时拒绝并反馈修正，模型应改为创建 singleton
    标准要求项。
    """
    expected_ids = {item.requirement_id for item in chunk_input.occurrences}
    actual_ids = {mapping.requirement_id for mapping in mappings}
    errors = []
    missing_ids = sorted(expected_ids - actual_ids)
    if missing_ids:
        errors.append(f"遗漏要求实例：{missing_ids}")
    unexpected_ids = sorted(actual_ids - expected_ids)
    if unexpected_ids:
        errors.append(f"包含未知要求实例：{unexpected_ids}")
    if errors:
        raise ConsolidationError("；".join(errors))


T = TypeVar("T")


def _retry_stage(
    client: ConsolidationClient,
    system_prompt: str,
    stage_name: str,
    max_attempts: int,
    build_prompt: Callable[[str | None], str],
    parse: Callable[[str], T],
) -> T:
    """执行单个归并阶段，并在解析或校验失败时带修正提示有限重试。"""
    correction = None
    last_error: ConsolidationError | ValueError | None = None

    for _ in range(max_attempts):
        prompt = build_prompt(correction)
        response_text: str | None = None
        try:
            response_text = client.complete(system_prompt, prompt)
            return parse(response_text)
        except (ConsolidationError, ValueError) as exc:
            last_error = exc
            # 只有模型已经返回但内容不合同时才把错误反馈给下一次提示；
            # 调用层失败直接重试原提示，避免把网络错误误写成业务修正要求。
            correction = None if response_text is None else str(exc)

    raise ConsolidationError(
        f"经过{max_attempts}次尝试仍未通过归并校验"
        f"（{stage_name}）：{last_error}"
    )


def _with_correction_suffix(
    prompt: str, correction: str | None
) -> str:
    """为阶段提示追加上次校验错误，供模型定向修正。"""
    if correction is None:
        return prompt
    return f"{prompt}\n\n【上次校验错误，请修正后重新输出】\n{correction}"


def _request_canonical_requirements(
    consolidation_input: RequirementConsolidationInput,
    client: ConsolidationClient,
    system_prompt: str,
    max_attempts: int,
) -> list[CanonicalRequirement]:
    """阶段1：请求模型基于全部实例提出标准要求项。"""
    return _retry_stage(
        client,
        system_prompt,
        "标准项生成",
        max_attempts,
        lambda correction: _with_correction_suffix(
            build_canonical_requirements_prompt(consolidation_input), correction
        ),
        parse_canonical_requirements_response,
    )


def _validate_mapping_references(
    mappings: list[RequirementMapping],
    canonical_requirements: list[CanonicalRequirement],
) -> None:
    """确认映射引用的标准项ID全部来自给定清单，幻觉ID可反馈模型修正。"""
    known_ids = {
        item.canonical_requirement_id for item in canonical_requirements
    }
    unknown_ids = sorted(
        {
            mapping.canonical_requirement_id
            for mapping in mappings
            if mapping.canonical_requirement_id not in known_ids
        }
    )
    if unknown_ids:
        raise ConsolidationError(
            "映射引用了标准要求项清单中不存在的ID："
            f"{unknown_ids}；请只引用给定的标准要求项ID"
        )


def _request_mappings_for_chunk(
    chunk_input: RequirementConsolidationInput,
    canonical_requirements: list[CanonicalRequirement],
    client: ConsolidationClient,
    system_prompt: str,
    max_attempts: int,
) -> list[RequirementMapping]:
    """请求模型输出单个映射块的结果，并校验本块实例恰好全部覆盖且引用合法。"""
    def parse_chunk_mappings(response_text: str) -> list[RequirementMapping]:
        mappings = parse_mappings_response(response_text)
        _validate_chunk_coverage(chunk_input, mappings)
        _validate_mapping_references(mappings, canonical_requirements)
        return mappings

    return _retry_stage(
        client,
        system_prompt,
        "映射生成",
        max_attempts,
        lambda correction: _with_correction_suffix(
            build_mappings_prompt(chunk_input, canonical_requirements), correction
        ),
        parse_chunk_mappings,
    )


def consolidate_with_correction(
    consolidation_input: RequirementConsolidationInput,
    client: ConsolidationClient,
    system_prompt: str = CONSOLIDATION_SYSTEM_PROMPT,
    max_attempts: int = 2,
    mapping_chunk_size: int = CONSOLIDATION_MAPPING_CHUNK_SIZE,
) -> tuple[RequirementConsolidationResult, dict[str, object]]:
    """分阶段执行跨JD归并，并在内存合成完整结果后统一校验。

    阶段1提出标准要求项，阶段2按块完成实例映射。各阶段请求的输出
    规模都远离单次完整请求的截断边界。各阶段结果先在内存合成为完整结果，
    再统一执行合同、冲突与完整覆盖校验，成功后返回；任何阶段失败都
    不产生可持久化的部分批次。
    """
    if mapping_chunk_size <= 0:
        raise ValueError("mapping_chunk_size必须大于0")

    canonical_requirements = _request_canonical_requirements(
        consolidation_input, client, system_prompt, max_attempts
    )
    mappings: list[RequirementMapping] = []
    for chunk in _chunk_consolidation_input(
        consolidation_input, mapping_chunk_size
    ):
        mappings.extend(
            _request_mappings_for_chunk(
                chunk, canonical_requirements, client, system_prompt, max_attempts
            )
        )

    try:
        result = RequirementConsolidationResult(
            canonical_requirements=canonical_requirements,
            mappings=mappings,
        )
        validate_requirement_coverage(consolidation_input, result)
    except ValueError as exc:
        # 标准项轮与映射轮分离时，模型可能在标准项轮提出无人引用的噪声
        # 标准项（映射轮不认可）；这些标准项没有任何 mapping 引用，
        # 由确定性代码剔除并记入诊断，保证"每个标准项至少一个来源"合同成立。
        result = _drop_unreferenced_canonicals(
            canonical_requirements,
            mappings,
        )
        if result is not None:
            return result, result.model_dump(mode="json")
        raise ConsolidationError(f"模型输出不符合归并合同：{exc}") from exc
    return result, result.model_dump(mode="json")


def _drop_unreferenced_canonicals(
    canonical_requirements: list[CanonicalRequirement],
    mappings: list[RequirementMapping],
) -> RequirementConsolidationResult | None:
    """剔除没有实例来源的标准项并重建结果；无法安全剔除时返回None。

    只剔除未被任何 mapping 引用的标准项。若剔除后没有剩余标准项
    （输出完全自相矛盾）则返回None交由上层报错。
    """
    referenced_by_mapping = {
        mapping.canonical_requirement_id for mapping in mappings
    }
    dropped = [
        item.canonical_requirement_id
        for item in canonical_requirements
        if item.canonical_requirement_id not in referenced_by_mapping
    ]
    if not dropped:
        return None
    dropped_set = set(dropped)
    kept = [
        item
        for item in canonical_requirements
        if item.canonical_requirement_id not in dropped_set
    ]
    if not kept:
        return None
    try:
        return RequirementConsolidationResult(
            canonical_requirements=kept,
            mappings=mappings,
        )
    except ValueError:
        return None


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
    skipped: int = 0
    consolidation_id: int | None = None
    input_fingerprint: str | None = None
    extractor_version: str | None = None
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
    selection: ConsolidationSelection,
    result: RequirementConsolidationResult,
    raw_response: dict[str, object],
    metadata: ConsolidatorMetadata,
    scope_key: str,
) -> tuple[JobConsolidation, bool]:
    """按范围、归并器版本和输入指纹幂等保存，并返回记录与是否新建。"""
    existing = session.scalar(
        select(JobConsolidation).where(
            JobConsolidation.scope_key == scope_key,
            JobConsolidation.consolidator_version
            == metadata.consolidator_version,
            JobConsolidation.input_fingerprint == selection.input_fingerprint,
        )
    )
    if existing is not None:
        return existing, False

    # 先创建批次主记录并flush获得ID，再关联标准要求项、映射和关系子记录。
    consolidation = JobConsolidation(
        scope_key=scope_key,
        consolidator_version=metadata.consolidator_version,
        input_fingerprint=selection.input_fingerprint,
        extractor_version=selection.extractor_version,
        selected_job_ids=list(selection.selected_job_ids),
        extraction_ids=list(selection.extraction_ids),
        model_name=metadata.model_name,
        prompt_version=metadata.prompt_version,
        schema_version=metadata.schema_version,
        occurrence_count=len(selection.consolidation_input.occurrences),
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
            canonical_requirement_id=mapping.canonical_requirement_id,
            rationale=mapping.rationale,
            confidence=mapping.confidence,
        )
        for mapping in result.mappings
    )
    session.commit()
    return consolidation, True


def consolidate_requirements(
    session_factory: sessionmaker[Session],
    client: ConsolidationClient,
    metadata: ConsolidatorMetadata,
    max_attempts: int = 2,
    job_ids: set[int] | None = None,
    extractor_version: str | None = None,
    mapping_chunk_size: int = CONSOLIDATION_MAPPING_CHUNK_SIZE,
) -> ConsolidationSummary:
    """对选定JD范围执行一次跨JD归并并幂等持久化，失败隔离不中断。

    模型调用采用分阶段/受控分块输出边界：标准项生成与按块映射。各阶段
    先在内存合成完整结果并统一执行合同、冲突与完整覆盖校验，通过后
    一次性原子持久化；任一阶段失败不写入部分批次。同范围同归并器版本
    同输入指纹已有结果时跳过模型调用并计入skipped。
    """
    scope = scope_key_for(job_ids)
    summary = ConsolidationSummary()

    try:
        with session_factory() as session:
            selection = load_consolidation_selection(
                session,
                job_ids=job_ids,
                extractor_version=extractor_version,
            )
    except ValueError as exc:
        summary.errors.append(ConsolidationFailure(scope, str(exc)))
        return summary

    pool = selection.consolidation_input
    summary.discovered = len(pool.occurrences)
    summary.input_fingerprint = selection.input_fingerprint
    summary.extractor_version = selection.extractor_version

    # 幂等：范围、归并器版本和实际输入指纹全部相同时才跳过模型。
    with session_factory() as session:
        existing_consolidation = session.scalar(
            select(JobConsolidation).where(
                JobConsolidation.scope_key == scope,
                JobConsolidation.consolidator_version
                == metadata.consolidator_version,
                JobConsolidation.input_fingerprint
                == selection.input_fingerprint,
            )
        )
    if existing_consolidation is not None:
        summary.consolidation_id = existing_consolidation.id
        summary.skipped = summary.discovered
        return summary

    try:
        result, raw_response = consolidate_with_correction(
            pool,
            client,
            max_attempts=max_attempts,
            mapping_chunk_size=mapping_chunk_size,
        )
    except (ConsolidationError, ValueError) as exc:
        summary.errors.append(ConsolidationFailure(scope, str(exc)))
        return summary

    # 模型调用结束后再开启写事务，避免网络等待期间长期占用数据库连接。
    with session_factory() as session:
        consolidation, created = persist_consolidation(
            session, selection, result, raw_response, metadata, scope
        )
        summary.consolidation_id = consolidation.id
        if not created:
            summary.skipped = len(result.mappings)
            return summary

    summary.consolidated = len(result.mappings)
    summary.canonical_count = len(result.canonical_requirements)
    return summary


def list_consolidations(
    session_factory: sessionmaker[Session],
) -> list[JobConsolidation]:
    """返回按ID排序的全部归并批次记录，供CLI摘要展示。"""
    with session_factory() as session:
        statement = (
            select(JobConsolidation)
            .options(
                selectinload(JobConsolidation.canonical_requirements),
                selectinload(JobConsolidation.mappings),
            )
            .order_by(JobConsolidation.id)
        )
        return list(session.scalars(statement))
