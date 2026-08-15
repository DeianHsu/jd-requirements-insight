"""该模块调用OpenAI兼容LLM抽取JD结构，并校验证据、重试和幂等持久化。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from app.config import LLMSettings
from app.models import JobDescription, JobExtraction
from app.schemas import JobExtractionResult

# 当前唯一抽取配置：v0.10 + Schema V3（两段式，三级熟练度）。
# 旧 Prompt（V2.3.1、v0.6、v0.7）与旧 Schema V2 不再维护，历史由 Git 与
# 已有报告保存。PROMPT_VERSION / SCHEMA_VERSION 必须与
# extraction_two_stage.py 的 TWO_STAGE_PROMPT_VERSION /
# TWO_STAGE_SCHEMA_VERSION 保持同步。
PROMPT_VERSION = "0.10"
SCHEMA_VERSION = "3.0"

class ExtractionError(ValueError):
    """表示结构化抽取在模型调用、JSON校验或证据校验阶段失败。"""


def assert_current_extractor_version(extractor_version: str) -> None:
    """严格校验抽取器版本为 v0.10 + Schema V3；其余版本明确拒绝。

    版本身份按 `|` 分段解析：`prompt:` 段与 `schema:` 段必须恰好各一个，
    且值分别严格等于当前版本；空值、重复段、缺失段、额外冲突段均拒绝。
    模型名称与其他非冲突身份段可以保留。当前主线只消费 v0.10 + Schema V3
    的抽取结果；旧版本要求用当前抽取器重新生成对应范围的数据，不做
    兼容、转换或迁移。
    """
    parts = extractor_version.split("|")
    if any(not part for part in parts):
        raise ValueError(
            f"抽取器版本身份无效：{extractor_version}；"
            "不得包含空段。当前只支持 v0.10 + Schema V3，请使用当前"
            "抽取器重新生成该范围的数据。"
        )
    prompt_parts = [
        part for part in parts if part.startswith("prompt:")
    ]
    schema_parts = [
        part for part in parts if part.startswith("schema:")
    ]
    if len(prompt_parts) != 1 or len(schema_parts) != 1:
        raise ValueError(
            f"抽取器版本身份无效：{extractor_version}；"
            "prompt: 与 schema: 段必须恰好各一个。"
            "当前只支持 v0.10 + Schema V3，请使用当前抽取器重新生成"
            "该范围的数据。"
        )
    if (
        prompt_parts[0] != f"prompt:{PROMPT_VERSION}"
        or schema_parts[0] != f"schema:{SCHEMA_VERSION}"
    ):
        raise ValueError(
            f"当前只支持 v0.10 + Schema V3：{extractor_version}。"
            "请使用当前抽取器重新生成该范围的数据。"
        )


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
    """记录模型、Prompt和抽取数据合同版本，并生成用于幂等判断的抽取器版本。"""

    model_name: str
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION

    @property
    def extractor_version(self) -> str:
        """组合模型与规则版本，确保规则变化后可以保留新的抽取结果。"""
        return f"{self.model_name}|prompt:{self.prompt_version}|schema:{self.schema_version}"


def compact_json_schema(schema: object) -> object:
    """递归移除不影响输出约束的JSON Schema说明字段，减少每次LLM请求的输入长度。"""
    if isinstance(schema, dict):
        return {
            key: compact_json_schema(value)
            for key, value in schema.items()
            if key not in {"title", "description"}
        }
    if isinstance(schema, list):
        return [compact_json_schema(value) for value in schema]
    return schema


def build_user_prompt(job: JobDescription, correction: str | None = None) -> str:
    """把JD原文、输出JSON Schema和上次校验错误组合成一次结构化抽取提示。"""
    # JSON Schema保留类型、枚举、必填项和跨字段结构，删除重复标题及说明以降低调用成本。
    schema_json = json.dumps(
        compact_json_schema(JobExtractionResult.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
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
        raise ExtractionError(f"模型输出不符合抽取数据合同：{exc}") from exc


def normalize_evidence(text: str) -> str:
    """统一证据文本的Unicode和空白格式，使排版差异不影响原文包含检查。"""
    normalized = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", normalized).strip()


def validate_evidence(result: JobExtractionResult, raw_text: str) -> None:
    """确认每条要求的证据都连续存在于原始JD中，阻止无依据结果入库。"""
    normalized_source = normalize_evidence(raw_text)
    missing = []

    for index, item in enumerate(result.requirements):
        if normalize_evidence(item.evidence) not in normalized_source:
            missing.append(f"requirement[{index}]证据不在JD原文中：{item.evidence}")

    if missing:
        raise ExtractionError("；".join(missing))


def extract_job(
    job: JobDescription, client: ExtractionClient, max_attempts: int = 2
) -> tuple[JobExtractionResult, dict[str, object]]:
    """使用当前正式抽取流程（两段式 v0.10 + Schema V3）抽取单份JD，并在校验失败时有限重试。

    延迟导入两段式实现以避免与 app/extraction_two_stage.py 的模块级循环依赖。
    """
    from app.extraction_two_stage import extract_job_two_stage

    return extract_job_two_stage(job, client, max_attempts)


def extraction_result_fingerprint(result: JobExtractionResult) -> str:
    """规范化抽取结果的确定性指纹（用于审核与定稿绑定）。"""
    import hashlib

    payload = json.dumps(
        result.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def rebuild_extraction_result(
    extraction: JobExtraction,
) -> JobExtractionResult:
    """从正式数据库回读重建抽取结果（用于定稿后逐项对比）。"""
    from app.schemas import (
        ProficiencyLevel,
        RequirementCategory,
        RequirementGroupLogic,
        RequirementImportance,
        RequirementItem,
        RoleFamily,
        Seniority,
    )

    requirements = [
        RequirementItem(
            raw_name=item.raw_name,
            category=RequirementCategory(item.category),
            importance=RequirementImportance(item.importance),
            proficiency=ProficiencyLevel(item.proficiency),
            group_id=item.group_id,
            group_logic=RequirementGroupLogic(item.group_logic),
            min_years=item.min_years,
            max_years=item.max_years,
            years_text=item.years_text,
            evidence=item.evidence,
            confidence=item.confidence,
        )
        for item in sorted(extraction.requirements, key=lambda r: r.id)
    ]
    return JobExtractionResult(
        role_family=RoleFamily(extraction.role_family),
        seniority=Seniority(extraction.seniority),
        requirements=requirements,
    )


def list_extractions(session_factory: sessionmaker[Session]) -> list[JobExtraction]:
    """返回包含要求和原始JD关系的全部抽取结果。"""
    with session_factory() as session:
        statement = (
            select(JobExtraction)
            .options(
                selectinload(JobExtraction.job),
                selectinload(JobExtraction.requirements),
            )
            .order_by(JobExtraction.id)
        )
        return list(session.scalars(statement))
