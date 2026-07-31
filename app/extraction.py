"""该模块调用OpenAI兼容LLM抽取JD结构，并校验证据、重试和幂等持久化。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Protocol

from openai import OpenAI
from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker, selectinload

from app.config import LLMSettings
from app.models import JobDescription, JobExtraction, JobRequirement, JobResponsibility
from app.schemas import JobExtractionResult, ResponsibilityItem

PROMPT_VERSION = "2.3.1"
SCHEMA_VERSION = "2.0"

# Prompt V2.3.1补充要求示例边界，并保留V2.3的两阶段职责判断。
SYSTEM_PROMPT = """你是招聘JD结构化抽取器，只能依据用户提供的JD原文输出JSON。

【任务边界】
1. responsibilities记录入职后需要完成的工作；每项只表达一个可独立描述的任务。
2. requirements记录候选人的技能、经验、学历、专业和软能力等条件。
3. 职责中明确出现的技术可以同时作为mentioned要求，但不得把工作任务推断成候选人必须已经具备的能力。
4. 不得补充行业常识、推测隐含要求或生成原文没有出现的技能；无法判断时使用unknown或null。

【职责原子化：先覆盖、后分组】
1. 在组织最终JSON前，先识别候选动作、对象和结果，再判断职责边界；这个分析过程不要输出。
2. 每个原文分句中的实质工作必须映射到一项职责，或者明确判定为实施方式、能力属性或示例，不能因为合并规则而静默漏掉工作内容。
3. 不同对象、不同交付物或可独立验收的业务结果必须拆分；不能仅因共享同一技术对象就合并，也不能只按连接词或动词数量机械拆分。
4. 只有多个动作共同完成一个不可分割的交付，并且拆开后只剩缺少业务含义的通用动作时才合并。每个responsibility使用“动作 + 对象或结果”表达完整业务含义，不能把整句或整段直接复制成一个name。
5. “设计、开发与落地AI Agent管理平台”是同一平台的端到端交付，整体保留；围绕Agent定义、编排、集成、训练、发布和治理开展研发是另一项覆盖生命周期的职责，概括为“开展Agent全生命周期研发”，但不能把每个环节再拆成低价值职责。
6. 协作对象、使用技术和执行手段通常是实施方式，不单独形成职责，但其承载的交付不能丢失；“与产品及业务团队协作，构建智能系统基础设施”抽取为“与产品及业务团队协作构建智能系统基础设施”。
7. “如”“例如”“等”和括号中的内容如果只是上位业务对象的示例，不能展开成独立职责；“开发企业内部AI应用（Agent、智能助手等）”只抽取“开发企业内部AI应用”，Agent和智能助手只是企业内部AI应用示例。
8. 同一句包含不同业务结果时必须拆分。例如“基于大模型技术构建智能体工作流，完成文献检索、实验数据分析及合规报告生成的全流程自动化”拆为“构建智能体工作流”“实现文献检索自动化”“实现实验数据分析自动化”“实现合规报告生成自动化”四项。
9. “设计AI Agent架构、开发核心代码，实现药物研发数据自动化处理”拆为架构设计、核心代码开发和数据自动化处理三项，因为三者具有不同交付结果。
10. “负责AI模型的调研、选型、微调与部署落地，持续优化模型效果与推理性能”拆为“调研AI模型”“选型AI模型”“微调AI模型”“部署落地AI模型”“优化模型效果与推理性能”，不能把这些可独立验收的工作合成长职责。

【原子化】
1. 每个requirement只能表达一个可独立学习、评价、匹配和统计的要求。
2. “熟悉Python和RAG”必须拆成Python与RAG两个要求，两项均为standalone。
3. 技术技能、学历、专业、经验和软能力跨类别出现在同一句时必须拆开。
4. “和”“与”“及”“、”“/”连接的内容若能被分别学习、评价或匹配，就必须拆开；修饰语和共同证据可以复用。
5. 例如“具备良好的代码风格与工程素养，能够驾驭复杂系统实现”应拆为“代码风格”“工程素养”“复杂系统实现能力”三项，不能保留“代码风格与工程素养”这样的复合name。
6. 行业稳定概念或拆开后改变原意的表达整体保留，例如“数据结构与算法”“Prompt Engineering”“大模型应用开发”。
7. raw_name保留原始要求的业务含义：“LangChain使用经验”不能缩减成“LangChain”；不要在抽取阶段做同义词归一。

【任选关系】
1. 原文明确出现“至少一种”“任一”“或”等任选含义时，候选项分别拆成原子要求，使用相同group_id和group_logic=any_of。
2. 同一any_of组至少包含两个成员；group_id在当前JD中使用group_1、group_2等简短唯一编号。
3. 普通独立要求使用group_id=null、group_logic=standalone。
4. “熟悉Python、Java中至少一种”是两个any_of成员；“熟悉Python和Java”是两个standalone要求。
5. 完整上位要求后由“如”“例如”或括号引出的非穷举内容只作为示例，不能自动成为独立要求或any_of成员；“等”字本身不能决定是否为示例。
6. 多个候选项共同受“优先”“加分”或“相关项目经验者优先”修饰，并且具备其中任一项即可形成同类加分时，各候选项使用preferred并共享同一个any_of组。
7. “Python / Node.js 优先”应拆为Python与Node.js两个preferred成员并共享同一个any_of组。
8. “大模型微调、RAG架构搭建、Prompt Engineering等实际项目经验者优先”应拆为三个preferred成员并共享同一个any_of组；不能因为句中使用顿号而把它们设为standalone。

【示例与完整概念】
1. “至少精通一门主流后端开发语言（如Go、Java、C++、Python等）”中的括号列表是非穷举示例，只抽取“主流后端开发语言”，使用proficiency=expert、group_logic=standalone，不能把四种语言建成封闭any_of组。
2. 具体技术名被“熟悉”“掌握”“使用经验”等候选条件直接修饰时必须逐项保留；后面的“等”只表示名单未穷尽，不能把已点名技术改写成上位概念。
3. “熟悉LangChain、AutoGen等主流Agent开发框架”分别抽取LangChain和AutoGen，均为standalone；不能只抽取“主流Agent开发框架”。
4. “有LangChain等Agent框架使用经验”抽取“LangChain框架使用经验”；raw_name保留使用经验，不能退化成泛化的“Agent框架使用经验”。
5. “有Llama、ChatGLM等大模型微调经验”抽取“大模型微调经验”，Llama和ChatGLM只是模型示例。
6. “有大语言模型（如GPT、GLM等）微调、RAG架构搭建经验”拆成“大语言模型微调”和“RAG架构搭建”。
7. 只有当JD明确要求掌握某个具体模型、工具或框架时，才把它单独标成要求。

【重要程度与熟练度】
1. 任职要求中的普通条件为must；明确出现“优先”“加分”时为preferred。
2. 只在职责或业务场景中提及、没有要求候选人掌握时为mentioned；仍无法判断时为unknown。
3. “了解”对应understand，“熟悉”对应familiar，“熟练/扎实”对应proficient，“精通”对应expert。
4. “使用经验”“项目经验”不能自行推断成熟练度，proficiency使用unknown。

【经验年限】
1. 只提取原文明示的数字，不估算年限。
2. “3年以上”填写min_years=3、max_years=null；“3-5年”填写min_years=3、max_years=5。
3. years_text保留完整年限表达；max_years只记录原文上限，不推断为淘汰条件。

【证据与输出】
1. 每条evidence必须是JD原文中连续出现的最小充分文本，不得改写、拼接或翻译。
2. 同一句证据可以支持拆分后的多个原子项。
3. requirements的所有字段都必须输出；不适用的group_id、年限字段使用null，不能省略。
4. 输出前检查：每个实质工作分句都已覆盖；职责没有把独立交付错误合并，也没有把端到端动作、实施方式或示例机械拆开；要求没有可继续拆分的并列概念；any_of组成员不少于两个；年限上下限未颠倒；每条证据都能在原文中直接找到。
5. 严格按照用户提供的JSON Schema输出一个JSON对象，不要输出Markdown代码块或额外说明。"""

# 该指令只用于架构实验，通过先冻结职责再处理要求来测试单次调用中的跨任务干扰。
REORDERED_EXPERIMENT_INSTRUCTION = """【实验执行顺序】
1. 先扫描全部职责原文，建立职责候选清单并完成覆盖、拆分和证据检查。
2. 冻结responsibilities后再处理requirements，不得因要求侧规则删改已确认职责。
3. 最后一次性输出完整JSON，不要输出中间分析。"""

# 该Prompt只抽取职责，用于判断移除要求任务后能否恢复遗漏职责，不作为正式版本。
RESPONSIBILITY_ONLY_SYSTEM_PROMPT = """你是招聘JD职责抽取器，只能依据JD原文输出JSON。
1. responsibilities只记录入职后需要完成的工作，忽略候选人资格、技能和经验要求。
2. 先扫描每个实质工作分句，确保所有工作都有对应职责或被明确判定为实施方式、能力属性或示例。
3. 不同对象、交付物或可独立验收结果必须拆分；只有共同完成不可分割交付时才合并。
4. 每项使用“动作+对象或结果”表达；协作方式和技术手段不单独成项，但其承载的交付不能遗漏。
5. “如”“例如”“等”及括号中的上位业务示例不展开为独立职责。
6. evidence必须是原文连续出现的最小充分文本，不得改写、拼接或翻译。
7. 严格按照JSON Schema输出一个JSON对象，不要输出Markdown或额外说明。"""


class ResponsibilityExperimentResult(BaseModel):
    """定义职责隔离实验返回的最小结构。"""

    model_config = ConfigDict(extra="forbid")

    responsibilities: list[ResponsibilityItem]


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
    """记录模型、Prompt和抽取数据合同版本，并生成用于幂等判断的抽取器版本。"""

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


def build_responsibility_experiment_prompt(
    job: JobDescription, correction: str | None = None
) -> str:
    """为职责隔离实验组合压缩JSON Schema、JD原文和可选校验反馈。"""
    schema_json = json.dumps(
        compact_json_schema(ResponsibilityExperimentResult.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    correction_text = ""
    if correction:
        correction_text = f"\n\n上一次输出未通过校验，请修正：\n{correction}"
    return f"""请只抽取以下招聘JD中的岗位职责。

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


def extract_job_with_system_prompt(
    job: JobDescription,
    client: ExtractionClient,
    system_prompt: str,
    max_attempts: int = 2,
) -> tuple[JobExtractionResult, dict[str, object]]:
    """使用指定系统Prompt抽取完整JD，并在校验失败时有限重试。"""
    correction = None
    last_error: ExtractionError | None = None

    for _ in range(max_attempts):
        prompt = build_user_prompt(job, correction)
        response_text = client.complete(system_prompt, prompt)
        try:
            result = parse_model_response(response_text)
            validate_evidence(result, job.raw_text)
            return result, result.model_dump(mode="json")
        except ExtractionError as exc:
            # 将校验错误反馈给下一次请求，让模型只修正具体结构或证据问题。
            last_error = exc
            correction = str(exc)

    raise ExtractionError(f"经过{max_attempts}次尝试仍未通过校验：{last_error}")


def extract_job(
    job: JobDescription, client: ExtractionClient, max_attempts: int = 2
) -> tuple[JobExtractionResult, dict[str, object]]:
    """使用当前正式Prompt抽取单份JD，并在校验失败时有限重试。"""
    return extract_job_with_system_prompt(job, client, SYSTEM_PROMPT, max_attempts)


def extract_responsibilities_for_experiment(
    job: JobDescription, client: ExtractionClient, max_attempts: int = 1
) -> tuple[ResponsibilityExperimentResult, dict[str, object]]:
    """仅抽取职责供架构实验比较，不写入正式抽取版本。"""
    correction = None
    last_error: ExtractionError | None = None
    normalized_source = normalize_evidence(job.raw_text)

    for _ in range(max_attempts):
        prompt = build_responsibility_experiment_prompt(job, correction)
        response_text = client.complete(RESPONSIBILITY_ONLY_SYSTEM_PROMPT, prompt)
        try:
            payload = json.loads(response_text)
            result = ResponsibilityExperimentResult.model_validate(payload)
            missing = [
                item.evidence
                for item in result.responsibilities
                if normalize_evidence(item.evidence) not in normalized_source
            ]
            if missing:
                raise ExtractionError(f"职责证据不在JD原文中：{'；'.join(missing)}")
            return result, result.model_dump(mode="json")
        except (json.JSONDecodeError, ValidationError, ExtractionError) as exc:
            # 实验默认不重试；显式增加次数时只反馈当前结构或证据错误。
            last_error = ExtractionError(str(exc))
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
            group_id=item.group_id,
            group_logic=item.group_logic.value,
            min_years=item.min_years,
            max_years=item.max_years,
            years_text=item.years_text,
            evidence=item.evidence,
            confidence=item.confidence,
        )
        for item in result.requirements
    )
    session.commit()
    return extraction, True


def extract_jobs(
    session_factory: sessionmaker[Session],
    client: ExtractionClient,
    metadata: ExtractorMetadata,
    max_attempts: int = 2,
    limit: int | None = None,
    job_ids: set[int] | None = None,
) -> ExtractionSummary:
    """按数量或ID选择JD进行抽取，跳过同版本结果并按JD隔离失败。"""
    if limit is not None and limit < 1:
        raise ValueError("limit必须大于等于1")

    with session_factory() as session:
        jobs = list(session.scalars(select(JobDescription).order_by(JobDescription.id)))

    # 指定ID用于针对性样例调试，limit用于控制普通Prompt迭代的调用规模。
    if job_ids is not None:
        jobs = [job for job in jobs if job.id in job_ids]
    if limit is not None:
        jobs = jobs[:limit]

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
