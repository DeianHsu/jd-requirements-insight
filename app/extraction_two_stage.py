"""两段式抽取实验：发现段与判断段分离，验证跨任务干扰假设（P0-3恢复）。

第一段（发现）只做全局扫描：分句归属（职责/要求/混合/排除）与岗位信息，
不输出任何原子项；第二段（判断）只做局部语义判断：对候选块执行职责拆分、
要求原子化、字段与逻辑组判定。两段之间用确定性覆盖检查衔接。
"""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.extraction import (
    ExtractionClient,
    ExtractionError,
    normalize_evidence,
    parse_model_response,
    validate_evidence,
)
from app.models import JobDescription
from app.schemas import JobExtractionResult, RoleFamily, Seniority

# 当前唯一抽取配置：v0.8 + Schema V3（两段式，三级熟练度）。
# 旧 Prompt（V2.3.1、v0.6、v0.7）不再维护，历史由 Git 与已有报告保存。
# 必须与 app/extraction.py 的 PROMPT_VERSION / SCHEMA_VERSION 保持同步。
TWO_STAGE_PROMPT_VERSION = "0.8"
TWO_STAGE_SCHEMA_VERSION = "3.0"

# 发现段Prompt：只做全局扫描与分句归属，不做拆分与字段判断。
DISCOVERY_SYSTEM_PROMPT = """你是招聘JD结构化分析的第一阶段：全局发现。通读用户提供的完整JD原文，只完成三件事，不做任何拆分、原子化或字段判断。

【任务】
1. 判断岗位方向（role_family）与岗位级别（seniority）。
2. 把原文按给定的分句编号拆成候选块：每个分句（或相邻连续语义单元）必须归属且只归属一个候选块，标注kind：
   - responsibility：候选人入职后需要完成的工作；
   - requirement：公司对候选人的条件（技能、经验、学历、专业、软能力）；
   - mixed：同一句同时包含工作内容与候选人条件；
   - excluded：宣传语、福利待遇、办公地点、投递提示、公司介绍等非条件内容。
3. 每个候选块保留原文连续片段作为source_span，列出其覆盖的全部分句编号（sentence_indexes），并给出简短的归属理由note。

【要求】
1. COVER-01：每个分句都必须出现在某个候选块的sentence_indexes中，不得遗漏任何实质内容；相邻同类的连续分句可以合并为一个候选块，但不得跳句。
2. COVER-02：同一分句不得重复归属到多个候选块。
3. COVER-03：source_span必须是原文中连续出现的文本，不得改写、拼接或翻译，且与sentence_indexes对应的分句完全一致。
4. 不要输出responsibilities或requirements明细，本阶段只发现候选块。
5. 严格按照用户提供的JSON结构输出一个JSON对象，不要输出Markdown代码块或额外说明。"""

# 判断段Prompt：只做局部语义判断，直接输出完整抽取数据合同。
# v0.8：基于 v0.7 规则化 Prompt（规则 ID 引用），FIELD-03 熟练度收缩为
# 三级枚举（unknown/basic/advanced），删除旧五级输出说明。
JUDGE_SYSTEM_PROMPT = """你是招聘JD结构化分析的第二阶段：精细判断。输入是第一阶段的候选块列表（每个块含原文连续证据与归属），请对每个候选块做以下判断并输出完整抽取数据合同JSON。规则编号与 P0-1 语义决策规则对应（docs/annotation/）。

【职责边界（RESP-01～RESP-02）】
1. RESP-01：responsibility 块中的工作内容不是岗位要求，不得从 responsibility 块抽取 requirement；职责中出现的技术只在JD明确要求候选人掌握时才成为要求（否则至多mentioned）。
2. RESP-02：mixed 块先分离工作部分与条件部分，只把候选人条件部分抽取为 requirement，工作内容不进入 requirement。

【要求原子化（REQ-01～REQ-08）】
1. REQ-01：只抽取候选人的技能、经验、学历、专业或软能力条件；工资福利、办公地点、招聘者信息、投递提示、公司宣传口号和由常识推断的隐含技能不标注。
2. REQ-02：每个requirement只能表达一个可独立学习、评价、匹配和统计的条件。
3. REQ-03：技术技能、学历、专业、经验和软能力跨类别出现在同一句时必须拆开。
4. REQ-04：行业稳定复合表达（固定复合要求，如"数据结构与算法"）或拆分后改变原意的表达整体保留。
5. REQ-05：raw_name保留原始业务含义，不做同义归一；"XX使用经验"不能缩减成"XX"。
6. REQ-06：只依据JD明示内容，不补充行业常识或隐含技能；相关但未被条件修饰的技术不得补充成条件。

【任选关系与示例边界（GROUP-01～GROUP-03）】
1. GROUP-01：只有原文明确出现"至少一种""任一""或"等有限任选含义时才建立any_of组；当"至少一种""任一""或"后直接列举具体技术名时，该列举是有限候选项，必须逐项拆成any_of成员，成员raw_name直接用具体技术名；只有"完整上位概念+如/例如/括号引出+明显是非穷举示例"时，才保留上位概念为standalone，不建立any_of；"等"字本身不能决定是否为示例，关键是括号/如引出的内容是对上位概念的举例还是被候选条件直接修饰的具体技术名。
2. GROUP-02：any_of组内各成员输出相同group_id（字符串，如"group_1"）和group_logic="any_of"，且同一组至少包含两个成员；普通独立要求必须输出group_logic="standalone"、group_id=null。group_logic不允许为null，group_id不允许输出数字。
3. GROUP-03：多个候选项共同受"优先""加分"或"相关项目经验者优先"修饰，并且具备任一项即可形成同类加分时，各候选项使用preferred并共享同一个any_of组。
4. 正反例（领域中性，按同类结构判断）：
   - 正："至少精通一门主流后端开发语言（如 甲、乙 等）"：括号内容是非穷举示例，只保留"主流后端开发语言"standalone，proficiency=advanced（GROUP-01）；
   - 正："熟悉至少一种开发框架（甲 / 乙 / 丙 等）"：甲、乙、丙被"至少一种"直接修饰，拆成3个any_of成员（GROUP-01）；
   - 反："熟悉 甲、乙 等主流框架"：甲、乙被候选条件直接修饰，必须逐项保留为standalone，不得改写成上位概念（REQ-07）。
5. REQ-08：具体模型名只用于修饰上位经验类型时，不单独标注模型（保留在证据中）。

【字段判断（FIELD-01～FIELD-05）】
1. FIELD-01：category必须输出枚举值（programming_language、backend_engineering、agent_framework、agent_capability、rag、llm_application、model_training、ml_framework、retrieval、deployment、software_engineering、domain_knowledge、education、experience、soft_skill、other），不要输出中文类别名；无法归类时用other。
2. FIELD-02：importance：任职要求普通条件为must；明确"优先""加分"为preferred；只提及未要求为mentioned；无法判断为unknown。必须输出枚举值，不要输出中文。
3. FIELD-03：proficiency 使用三级枚举并输出枚举值（unknown/basic/advanced）：
   - unknown：没有明确程度词，或仅出现使用经验、项目经验、有经验、参与过等表达（不得推断程度）；
   - basic：了解、理解、熟悉、能够使用、具备基础使用能力；
   - advanced：掌握、熟练、扎实、精通、专家级。
   不要输出中文程度词，也不要输出旧五级枚举值；
   "使用经验""项目经验"不得推断熟练度，使用unknown；原始程度词保留在evidence中。
4. FIELD-04：年限只提取原文明示的数字，不估算年限；min_years为下限，max_years只保存原文上限，years_text保留完整年限表达；无法判断使用null；年限上下限不得颠倒。
5. FIELD-05：不确定的字段使用unknown或null，不得猜测。

【覆盖与证据（COVER-04、EVID-01～EVID-04）】
1. COVER-04：必须处理所有非excluded候选块；每个实质工作内容都已覆盖为要求或明确排除，不得静默丢弃。
2. EVID-01：每条evidence必须是JD原文中连续出现的最小充分文本，不得改写、拼接或翻译；不得拼接不连续片段。
3. EVID-02：evidence必须足以支持职责或要求名称及字段判断（重要程度、熟练度、年限）。
4. EVID-03：多个原子项可以共享同一句证据。

【岗位信息】role_family与seniority必须直接使用输入中"job_info"字段给出的值，不能省略或置为null，也不要重新判断。

【输出前检查】
每个实质工作内容都已覆盖为要求或明确排除（COVER-04）；要求没有可继续拆分的并列概念；any_of组成员不少于两个（GROUP-02）；年限上下限未颠倒；每条evidence都能在原文中找到（EVID-01）。

【输出要求】
1. 严格按照抽取数据合同的JSON结构输出：requirements、role_family、seniority。
2. requirements中的每一项必须包含以下全部字段：raw_name、category、importance、proficiency、group_id、group_logic、min_years、max_years、years_text、evidence、confidence。不要用name替代raw_name，不要省略字段（不适用时用null）。
3. 不要输出Markdown代码块或额外说明。"""



class CandidateBlock(BaseModel):
    """发现段输出的候选块：一段连续原文及其归属判定。"""

    model_config = ConfigDict(extra="forbid")

    block_id: str = Field(min_length=1, max_length=50)
    sentence_indexes: list[int] = Field(min_length=1)
    kind: Literal["responsibility", "requirement", "mixed", "excluded"]
    source_span: str = Field(min_length=1)
    note: str = Field(min_length=1)

    @field_validator("source_span", "note")
    @classmethod
    def block_text_must_not_be_blank(cls, value: str) -> str:
        """清理候选块必填文本并拒绝空白。"""
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("不能为空")
        return cleaned

    @field_validator("sentence_indexes")
    @classmethod
    def sentence_indexes_must_be_ordered_contiguous_and_unique(
        cls, values: list[int]
    ) -> list[int]:
        """拒绝负数、重复、乱序或跳跃索引，确保候选块对应连续原文。"""
        if any(index < 0 for index in values):
            raise ValueError("分句索引不能为负数")
        if len(values) != len(set(values)):
            raise ValueError("候选块内分句索引不能重复")
        if values != sorted(values):
            raise ValueError("候选块内分句索引必须升序")
        if any(right - left != 1 for left, right in zip(values, values[1:])):
            raise ValueError("候选块只能合并相邻连续分句")
        return values


class DiscoveryResult(BaseModel):
    """发现段输出：全文候选块列表与岗位信息。"""

    model_config = ConfigDict(extra="forbid")

    role_family: RoleFamily
    seniority: Seniority
    blocks: list[CandidateBlock] = Field(min_length=1)

    @model_validator(mode="after")
    def block_ids_must_be_unique(self) -> "DiscoveryResult":
        """拒绝重复候选块ID，保证发现段结果可以稳定定位。"""
        block_ids = [block.block_id for block in self.blocks]
        if len(block_ids) != len(set(block_ids)):
            raise ValueError("候选块ID不能重复")
        return self


def split_sentences(raw_text: str) -> list[str]:
    """按换行和中文标点把JD原文拆成分句，供覆盖检查与发现段输入。"""
    pieces = re.split(r"[。；;\n]+", raw_text)
    return [piece.strip() for piece in pieces if piece.strip()]


def _alnum_sequence(text: str) -> str:
    """提取规范化文本的字母数字字符序列，用于容忍标点差异的包含校验。"""
    return "".join(ch for ch in normalize_evidence(text) if ch.isalnum())


def validate_discovery_coverage(
    discovery: DiscoveryResult, raw_text: str
) -> None:
    """确认发现段覆盖全部原文分句，阻止静默漏句进入判断段。"""
    sentences = split_sentences(raw_text)
    all_indexes = [
        index for block in discovery.blocks for index in block.sentence_indexes
    ]
    covered_indexes = set(all_indexes)
    if len(all_indexes) != len(covered_indexes):
        raise ExtractionError("发现段存在重复覆盖的分句")
    expected_indexes = set(range(len(sentences)))
    missing = sorted(expected_indexes - covered_indexes)
    unexpected = sorted(covered_indexes - expected_indexes)
    if missing or unexpected:
        raise ExtractionError(
            f"发现段分句覆盖不完整：遗漏{missing}，未知{unexpected}"
        )
    for block in discovery.blocks:
        claimed_sequence = "".join(
            _alnum_sequence(sentences[index]) for index in block.sentence_indexes
        )
        if _alnum_sequence(block.source_span) != claimed_sequence:
            raise ExtractionError(
                f"候选块{block.block_id}的原文片段与分句索引不对应"
            )


def build_discovery_user_prompt(job: JobDescription) -> str:
    """构造发现段用户提示：分句编号列表与期望输出结构。"""
    sentences = split_sentences(job.raw_text)
    payload = {
        "task": "请对以下JD原文分句执行全局发现，输出role_family、seniority和blocks。",
        "output_schema": {
            "role_family": "agent_application|llm_application|rag_application|ai_algorithm|ai_platform|other|unknown",
            "seniority": "junior|transition|mid|senior|unknown",
            "blocks": [
                {
                    "block_id": "string，唯一",
                    "sentence_indexes": ["int，覆盖的全部分句编号"],
                    "kind": "responsibility|requirement|mixed|excluded",
                    "source_span": "string，原文连续片段",
                    "note": "string，归属理由",
                }
            ],
        },
        "sentences": [
            {"index": index, "text": text}
            for index, text in enumerate(sentences)
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def parse_discovery_response(response_text: str) -> DiscoveryResult:
    """把发现段模型输出解析并校验为DiscoveryResult。"""
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"发现段返回内容不是合法JSON：{exc}") from exc
    try:
        return DiscoveryResult.model_validate(payload)
    except Exception as exc:
        raise ExtractionError(f"发现段输出不符合中间合同：{exc}") from exc


def build_judge_user_prompt(
    job: JobDescription, discovery: DiscoveryResult
) -> str:
    """构造判断段用户提示：候选块列表、原文分句与岗位信息。"""
    payload = {
        "task": "请对以下候选块执行精细判断，输出完整抽取数据合同JSON。",
        "job_info": {
            "role_family": discovery.role_family.value,
            "seniority": discovery.seniority.value,
        },
        "blocks": [block.model_dump() for block in discovery.blocks],
        "sentences": [
            {"index": index, "text": text}
            for index, text in enumerate(split_sentences(job.raw_text))
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def extract_job_two_stage(
    job: JobDescription,
    client: ExtractionClient,
    max_attempts: int = 2,
) -> tuple[JobExtractionResult, dict[str, object]]:
    """对一份JD执行发现段与判断段两次调用（当前唯一配置 v0.8 + Schema V3）。"""
    _, result, raw = extract_job_two_stage_with_discovery(job, client, max_attempts)
    return result, raw


def extract_job_two_stage_with_discovery(
    job: JobDescription,
    client: ExtractionClient,
    max_attempts: int = 2,
) -> tuple[DiscoveryResult, JobExtractionResult, dict[str, object]]:
    """两段式抽取并返回发现段结果，供验证与审计记录使用。"""
    # 发现段：全局扫描，失败时把校验错误反馈重试。
    correction = None
    discovery: DiscoveryResult | None = None
    for _ in range(max_attempts):
        prompt = build_discovery_user_prompt(job)
        if correction is not None:
            prompt = f"{prompt}\n\n【上次校验错误，请修正后重新输出】\n{correction}"
        response_text = client.complete(DISCOVERY_SYSTEM_PROMPT, prompt)
        try:
            candidate = parse_discovery_response(response_text)
            validate_discovery_coverage(candidate, job.raw_text)
            discovery = candidate
            break
        except ExtractionError as exc:
            correction = str(exc)
    if discovery is None:
        raise ExtractionError(
            f"发现段经过{max_attempts}次尝试仍未通过覆盖校验：{correction}"
        )

    # 判断段：局部语义判断，输出完整抽取数据合同并校验证据存在。
    correction = None
    for _ in range(max_attempts):
        prompt = build_judge_user_prompt(job, discovery)
        if correction is not None:
            prompt = f"{prompt}\n\n【上次校验错误，请修正后重新输出】\n{correction}"
        response_text = client.complete(JUDGE_SYSTEM_PROMPT, prompt)
        try:
            result = parse_model_response(response_text)
            validate_evidence(result, job.raw_text)
            return discovery, result, result.model_dump(mode="json")
        except ExtractionError as exc:
            correction = str(exc)

    raise ExtractionError(
        f"判断段经过{max_attempts}次尝试仍未通过合同或证据校验：{correction}"
    )
