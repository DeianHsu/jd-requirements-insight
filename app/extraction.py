"""该模块调用OpenAI兼容LLM抽取JD结构，并校验证据、重试和幂等持久化。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from app.config import LLMSettings
from app.models import JobDescription, JobExtraction, JobRequirement, JobResponsibility
from app.schemas import JobExtractionResult

PROMPT_VERSION = "1.0"
SCHEMA_VERSION = "1.0"

SYSTEM_PROMPT = """你是招聘JD结构化抽取器，只能依据用户提供的JD原文输出JSON。
不得补充常识、推测候选人能力或改写证据；无法判断时使用unknown。
每条evidence必须是JD原文中连续出现的文本，requirements中的raw_name保留JD原始表达。
严格按照提供的JSON Schema输出，不要输出Markdown代码块或额外说明。"""


class ExtractionError(ValueError):
    """表示结构化抽取在模型调用、JSON校验或证据校验阶段失败。"""


class ExtractionClient(Protocol):
    """定义抽取服务依赖的最小LLM客户端接口，便于测试时注入假客户端。"""

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """接收系统提示和用户提示，并返回模型生成的JSON文本。"""
        ...


class OpenAICompatibleExtractionClient:
    """通过OpenAI兼容Chat Completions接口请求JSON格式的JD抽取结果。"""

    def __init__(self, settings: LLMSettings) -> None:
        """根据环境配置初始化OpenAI兼容客户端和模型名称。"""
        client_kwargs: dict[str, str] = {"api_key": settings.api_key}
        if settings.base_url:
            client_kwargs["base_url"] = settings.base_url
        self._client = OpenAI(**client_kwargs)
        self._model = settings.model

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """调用LLM并返回消息正文，接口异常会被包装成统一抽取错误。"""
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
            raise ExtractionError(f"LLM调用失败：{exc}") from exc

        content = response.choices[0].message.content
        if not content:
            raise ExtractionError("LLM返回了空内容")
        return content


@dataclass(frozen=True)
class ExtractorMetadata:
    """记录模型、Prompt和Schema版本，并生成用于幂等判断的抽取器版本。"""

    model_name: str
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION

    @property
    def extractor_version(self) -> str:
        """组合模型与规则版本，确保规则变化后可以保留新的抽取结果。"""
        return f"{self.model_name}|prompt:{self.prompt_version}|schema:{self.schema_version}"


@dataclass(frozen=True)
class ExtractionFailure:
    """记录一份JD在批量抽取中的失败原因。"""

    job_id: int
    source_file: str
    message: str


@dataclass
class ExtractionSummary:
    """汇总批量抽取的发现、成功、跳过和失败数量。"""

    discovered: int = 0
    extracted: int = 0
    skipped: int = 0
    errors: list[ExtractionFailure] = field(default_factory=list)

    @property
    def failed(self) -> int:
        """返回批量抽取失败的JD数量。"""
        return len(self.errors)


def build_user_prompt(job: JobDescription, correction: str | None = None) -> str:
    """把JD原文、输出Schema和上次校验错误组合成一次结构化抽取提示。"""
    schema_json = json.dumps(JobExtractionResult.model_json_schema(), ensure_ascii=False)
    correction_text = ""
    if correction:
        correction_text = f"\n\n上一次输出未通过校验，请修正以下问题：\n{correction}"
    return f"""请结构化抽取以下招聘JD。

公司：{job.company}
岗位：{job.title}

JSON Schema：
{schema_json}

JD原文：
{job.raw_text}
{correction_text}
"""


def parse_model_response(response_text: str) -> JobExtractionResult:
    """把模型JSON文本解析并校验为严格的JobExtractionResult。"""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"模型返回的内容不是合法JSON：{exc}") from exc

    try:
        return JobExtractionResult.model_validate(payload)
    except ValidationError as exc:
        raise ExtractionError(f"模型输出不符合Schema：{exc}") from exc


def normalize_evidence(text: str) -> str:
    """统一证据文本的Unicode和空白格式，使排版差异不影响原文包含检查。"""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def validate_evidence(result: JobExtractionResult, raw_text: str) -> None:
    """确认每条职责和要求的证据都连续存在于原始JD中，阻止无依据结果入库。"""
    normalized_source = normalize_evidence(raw_text)
    missing = []

    # 职责和要求统一执行证据落地检查，避免只约束技能而遗漏职责幻觉。
    for label, items in (
        ("responsibility", result.responsibilities),
        ("requirement", result.requirements),
    ):
        for index, item in enumerate(items):
            if normalize_evidence(item.evidence) not in normalized_source:
                missing.append(f"{label}[{index}]证据不在JD原文中：{item.evidence}")

    if missing:
        raise ExtractionError("；".join(missing))


def extract_job(
    job: JobDescription, client: ExtractionClient, max_attempts: int = 2
) -> tuple[JobExtractionResult, dict[str, object]]:
    """调用LLM抽取单份JD，并在Schema或证据失败时携带错误信息有限重试。"""
    correction = None
    last_error: ExtractionError | None = None

    for _ in range(max_attempts):
        prompt = build_user_prompt(job, correction)
        response_text = client.complete(SYSTEM_PROMPT, prompt)
        try:
            result = parse_model_response(response_text)
            validate_evidence(result, job.raw_text)
            return result, result.model_dump(mode="json")
        except ExtractionError as exc:
            # 将校验错误反馈给下一次请求，让模型只修正具体结构或证据问题。
            last_error = exc
            correction = str(exc)

    raise ExtractionError(f"经过{max_attempts}次尝试仍未通过校验：{last_error}")


def persist_extraction(
    session: Session,
    job: JobDescription,
    result: JobExtractionResult,
    raw_response: dict[str, object],
    metadata: ExtractorMetadata,
) -> tuple[JobExtraction, bool]:
    """按job_id和抽取器版本幂等保存结构化结果，并返回记录与是否新建。"""
    existing = session.scalar(
        select(JobExtraction).where(
            JobExtraction.job_id == job.id,
            JobExtraction.extractor_version == metadata.extractor_version,
        )
    )
    if existing is not None:
        return existing, False

    # 先创建抽取主记录并flush获得ID，再关联职责和要求子记录。
    extraction = JobExtraction(
        job_id=job.id,
        extractor_version=metadata.extractor_version,
        model_name=metadata.model_name,
        prompt_version=metadata.prompt_version,
        schema_version=metadata.schema_version,
        role_family=result.role_family.value,
        seniority=result.seniority.value,
        raw_response=raw_response,
    )
    session.add(extraction)
    session.flush()

    extraction.responsibilities.extend(
        JobResponsibility(name=item.name, evidence=item.evidence)
        for item in result.responsibilities
    )
    extraction.requirements.extend(
        JobRequirement(
            raw_name=item.raw_name,
            category=item.category.value,
            importance=item.importance.value,
            proficiency=item.proficiency.value,
            years_required=item.years_required,
            evidence=item.evidence,
            confidence=item.confidence,
        )
        for item in result.requirements
    )
    session.commit()
    return extraction, True


def extract_all_jobs(
    session_factory: sessionmaker[Session],
    client: ExtractionClient,
    metadata: ExtractorMetadata,
    max_attempts: int = 2,
) -> ExtractionSummary:
    """批量抽取数据库中的全部JD，跳过同版本结果并按JD隔离失败。"""
    with session_factory() as session:
        jobs = list(session.scalars(select(JobDescription).order_by(JobDescription.id)))

    summary = ExtractionSummary(discovered=len(jobs))
    for job in jobs:
        with session_factory() as session:
            exists = session.scalar(
                select(JobExtraction.id).where(
                    JobExtraction.job_id == job.id,
                    JobExtraction.extractor_version == metadata.extractor_version,
                )
            )
        if exists is not None:
            summary.skipped += 1
            continue

        try:
            result, raw_response = extract_job(job, client, max_attempts=max_attempts)
            # 模型调用结束后再开启写事务，避免网络等待期间长期占用数据库连接。
            with session_factory() as session:
                current_job = session.get(JobDescription, job.id)
                if current_job is None:
                    raise ExtractionError(f"JD记录不存在：{job.id}")
                _, created = persist_extraction(
                    session, current_job, result, raw_response, metadata
                )
                summary.extracted += int(created)
                summary.skipped += int(not created)
        except ExtractionError as exc:
            summary.errors.append(ExtractionFailure(job.id, job.source_file, str(exc)))

    return summary


def list_extractions(session_factory: sessionmaker[Session]) -> list[JobExtraction]:
    """返回包含职责、要求和原始JD关系的全部抽取结果。"""
    with session_factory() as session:
        statement = (
            select(JobExtraction)
            .options(
                selectinload(JobExtraction.job),
                selectinload(JobExtraction.responsibilities),
                selectinload(JobExtraction.requirements),
            )
            .order_by(JobExtraction.id)
        )
        return list(session.scalars(statement))
