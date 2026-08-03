"""装配跨JD原子要求归并的输入，并调用LLM单次输出canonical聚类结果。

本模块只定义领域无关的归并执行逻辑：输入装配、LLM客户端、Prompt、
解析与有限重试；具体归并合同定义在`app/requirement_consolidation.py`。

归并流程（单次 LLM 任务）：

    RequirementConsolidationInput
    → 单次 LLM 输出 canonical_requirements（含来源分区 source_requirement_ids）
    → validate_canonical_partition 立即验证完整唯一分区
    → build_mappings_from_canonical_partition 确定性生成 mappings
    → validate_requirement_coverage 最终合同
    → 持久化

模型只负责决定 cluster；mappings 由确定性代码展开并持久化。
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
from app.extraction import assert_current_extractor_version
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
    RequirementOccurrence,
    build_mappings_from_canonical_partition,
    validate_canonical_partition,
    validate_requirement_coverage,
)
from app.schemas import RequirementItem

# 归并合同 v4.1 / Schema 3.0：单次聚类输出 canonical + 来源分区，
# mappings 由确定性代码生成。旧归并版本（prompt:4.0/schema:2.0 等）
# 不再兼容，新旧批次按 consolidator_version 区分。
CONSOLIDATION_PROMPT_VERSION = "4.1"
CONSOLIDATION_SCHEMA_VERSION = "3.0"
CONSOLIDATION_READ_TIMEOUT_SECONDS = 900.0


# Prompt v4.1：单次聚类，只输出 canonical requirements 与来源分区；
# 不输出 mappings、不输出任何关系或层级结构。
CONSOLIDATION_SYSTEM_PROMPT = """你是跨JD岗位要求归并器。输入是一批来自不同JD的原子要求实例，你需要判断哪些实例指向同一招聘条件，输出标准要求项（canonical requirements）。只能依据每个实例提供的原始名称和证据上下文判断，不得补充任何领域知识或行业常识。

【任务】
1. 把指向同一招聘条件（达到可以合并统计的程度）的实例归入同一个标准要求项；每个标准要求项必须声明其来源实例ID（source_requirement_ids）。
2. 每个输入实例必须且只能归属一个标准要求项：全部实例的归属构成完整唯一分区，不得遗漏任何实例，也不得让同一实例属于多个标准要求项。
3. 无法确认是否与任何其他实例等价的实例，创建只包含该实例的singleton标准要求项；不允许因为名称中出现相同关键词或属于同一技术领域就强制合并。
4. 同一表面词可以因证据上下文不同而映射到不同标准要求项；不能只因为名称文本相同就自动归并，必须结合证据判断。
5. 标准要求项数量不得超过输入实例数量，只为有实例依据的条件创建标准项，不得提出无来源的标准项。

【归并边界】
1. 只有真正等价、可以合并统计的招聘条件才能同簇；上下位、组成关系、仅相关的技术不得合并，分别归属各自标准要求项。
2. 任选组（any_of）成员与单独的硬性条件即使表面名称相似，也代表不同招聘门槛，不得归并。
3. 单独条件只是整体的一部分时（例如"能力甲分析能力"是"能力甲分析与解决能力"的一部分），不得与整体归并。
4. 年限或数值门槛不同的条件不得归并：例如"3年以上能力甲经验"与"5年以上能力甲经验"是不同招聘门槛。
5. 不得修改、覆盖或删除任何输入实例的原始名称、证据和属性；归并只产生标准要求项，不改写输入。

【标准项名称】
1. 标准要求项名称使用简洁、独立可读的常见表达，优先采用最贴近该条件的原文表达；改写只做最小必要修改：去掉"相关知识""概念"等冗余后缀，统一中英文或同义表达。
2. 不要用"/"拼接多个实例原文，不要添加前缀修饰（如"主流开发语言""优先"），不要缩写或简化（如"能力甲基础"不能缩成"能力甲"）。
3. 名称只表达招聘条件本身，不得包含括号注释、任选组说明、重要性或熟练度标注（例如不得出现"（必备）""（任选）"等括号内容）。
4. canonical_requirement_id和去除大小写、空白差异后的canonical_name都必须全局唯一。若两个标准项会得到同一名称且确实是同一招聘条件，必须合并为一个标准项；若证据表明它们是不同条件，名称必须保留原文或证据中的最小区分信息，不能用括号元数据硬凑唯一名称。

【输出要求】
严格按照用户提示要求的JSON结构输出，只输出canonical_requirements数组，不要输出映射、关系或层级结构，不要输出Markdown代码块或额外说明。"""


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

    # 当前主线只消费 v0.8 + Schema V3：显式指定或自动选中的旧版本都拒绝。
    assert_current_extractor_version(extractor_version)

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


def build_canonical_requirements_prompt(
    consolidation_input: RequirementConsolidationInput,
) -> str:
    """构建只要求模型输出标准要求项与来源分区的提示。"""
    payload = {
        "task": (
            "请根据每个实例提供的原始名称和证据判断哪些实例指向同一"
            "招聘条件，并提出标准要求项；每个实例必须且只能归属一个"
            "标准要求项（在其source_requirement_ids中声明），无法与其他"
            "实例合并的实例必须创建包含它的singleton标准项。"
            "只输出canonical_requirements数组，不要输出映射。"
        ),
        "output_schema": {
            "canonical_requirements": [
                {
                    "canonical_requirement_id": "string，唯一标识",
                    "canonical_name": "string，规范化后全局唯一",
                    "source_requirement_ids": ["int，本标准项来源实例的id；覆盖全部输入实例且不重复"],
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


def parse_canonical_requirements_response(
    response_text: str,
) -> list[CanonicalRequirement]:
    """解析模型响应，校验每条标准要求项符合合同结构。"""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ConsolidationError(f"模型返回的内容不是合法JSON：{exc}") from exc
    if not isinstance(payload, dict):
        raise ConsolidationError("模型输出不符合归并合同：顶层必须是JSON对象")
    items = payload.get("canonical_requirements")
    if not isinstance(items, list) or not items:
        raise ConsolidationError(
            "模型输出不符合归并合同：缺少canonical_requirements"
        )
    try:
        return [CanonicalRequirement.model_validate(item) for item in items]
    except ValidationError as exc:
        raise ConsolidationError(f"模型输出不符合归并合同：{exc}") from exc


T = TypeVar("T")


def _retry_stage(
    client: ConsolidationClient,
    system_prompt: str,
    stage_name: str,
    max_attempts: int,
    build_prompt: Callable[[str | None], str],
    parse: Callable[[str], T],
) -> tuple[T, int, str]:
    """执行归并任务，并在解析或分区校验失败时带修正提示有限重试。

    返回（解析结果, 成功尝试次数, 成功那次模型的原始响应文本）。
    """
    correction = None
    last_error: ConsolidationError | ValueError | None = None

    for attempt in range(1, max_attempts + 1):
        prompt = build_prompt(correction)
        response_text: str | None = None
        try:
            response_text = client.complete(system_prompt, prompt)
            return parse(response_text), attempt, response_text
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
    """为提示追加上次校验错误，供模型定向修正。"""
    if correction is None:
        return prompt
    return f"{prompt}\n\n【上次校验错误，请修正后重新输出】\n{correction}"


def _request_canonical_partition(
    consolidation_input: RequirementConsolidationInput,
    client: ConsolidationClient,
    system_prompt: str,
    max_attempts: int,
) -> tuple[list[CanonicalRequirement], int, dict[str, object]]:
    """请求模型输出 canonical 聚类，并在模型输出后立即校验来源分区。

    分区校验（validate_canonical_partition）在重试循环内执行：任何
    遗漏/重复归属/未知ID/空来源/名称重复都会作为具体错误反馈给下一次
    提示，不会进入后续处理或持久化。

    返回（canonical 列表, 成功尝试次数, 成功那次模型响应的 JSON 对象）。
    """
    def parse_partition(response_text: str) -> list[CanonicalRequirement]:
        canonical_requirements = parse_canonical_requirements_response(response_text)
        validate_canonical_partition(consolidation_input, canonical_requirements)
        return canonical_requirements

    canonical_requirements, attempt_count, response_text = _retry_stage(
        client,
        system_prompt,
        "canonical 聚类",
        max_attempts,
        lambda correction: _with_correction_suffix(
            build_canonical_requirements_prompt(consolidation_input), correction
        ),
        parse_partition,
    )
    try:
        model_response = json.loads(response_text)
    except json.JSONDecodeError:
        model_response = {}
    return canonical_requirements, attempt_count, model_response


def consolidate_with_correction(
    consolidation_input: RequirementConsolidationInput,
    client: ConsolidationClient,
    system_prompt: str = CONSOLIDATION_SYSTEM_PROMPT,
    max_attempts: int = 2,
) -> tuple[RequirementConsolidationResult, dict[str, object]]:
    """执行单次 LLM 聚类归并并确定性展开 mappings。

    流程：单次 LLM 输出 canonical_requirements（含来源分区）→ 立即
    验证分区完整唯一 → 确定性生成 mappings → 最终合同校验 → 返回。
    任何合同失败都通过有限重试反馈模型修正，不产生部分结果。
    """
    canonical_requirements, attempt_count, model_response = (
        _request_canonical_partition(
            consolidation_input, client, system_prompt, max_attempts
        )
    )
    mappings = build_mappings_from_canonical_partition(canonical_requirements)
    result = RequirementConsolidationResult(
        canonical_requirements=canonical_requirements,
        mappings=mappings,
    )
    validate_requirement_coverage(consolidation_input, result)

    # raw_response 同时保存成功那次的模型响应与规范化结果：
    # model_response 是模型原始输出（不含确定性 mappings）；
    # normalized_result 是通过校验并确定性生成 mappings 后的结果。
    normalized_result = result.model_dump(mode="json")
    raw_response: dict[str, object] = {
        "model_response": model_response,
        "normalized_result": normalized_result,
        "attempt_count": attempt_count,
    }
    return result, raw_response


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

    # 先创建批次主记录并flush获得ID，再关联标准要求项与映射子记录。
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
            source_requirement_ids=list(item.source_requirement_ids),
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
) -> ConsolidationSummary:
    """对选定JD范围执行一次跨JD归并并幂等持久化，失败隔离不中断。

    模型调用为单次 canonical 聚类；mappings 由来源分区确定性生成。
    合同通过后一次性原子持久化；失败不写入部分批次。同范围同归并器
    版本同输入指纹已有结果时跳过模型调用并计入skipped。
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
