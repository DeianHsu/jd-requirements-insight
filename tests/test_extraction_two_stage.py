"""验证两段式抽取：中间合同、覆盖检查、发现/判断分离与有限重试。"""

import json
from datetime import date

import pytest

from app.extraction import PROMPT_VERSION, SCHEMA_VERSION, ExtractionError
from app.extraction_two_stage import (
    DISCOVERY_SYSTEM_PROMPT,
    JUDGE_SYSTEM_PROMPT,
    TWO_STAGE_PROMPT_VERSION,
    TWO_STAGE_SCHEMA_VERSION,
    extract_job_two_stage,
    parse_discovery_response,
    split_sentences,
    validate_discovery_coverage,
)
from app.models import JobDescription


class FakeTwoStageClient:
    """按预设顺序返回发现段与判断段响应，并记录提示。"""

    def __init__(
        self, discovery: dict, judge: dict, fail_discovery_times: int = 0
    ) -> None:
        """保存预设响应与失败次数。"""
        self.discovery = discovery
        self.judge = judge
        self.fail_discovery_times = fail_discovery_times
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """按调用轮次返回发现段或判断段响应。"""
        self.prompts.append(user_prompt)
        if self.calls < self.fail_discovery_times:
            self.calls += 1
            return '{"bad": true}'
        if "全局发现" in system_prompt:
            self.calls += 1
            return json.dumps(self.discovery, ensure_ascii=False)
        self.calls += 1
        return json.dumps(self.judge, ensure_ascii=False)


def make_job() -> JobDescription:
    """创建使用中性虚构内容的两句JD，供两段式抽取测试。"""
    return JobDescription(
        id=1,
        source_hash="a" * 64,
        source_file="sample.md",
        source_type="test",
        collected_at=date(2026, 7, 21),
        company="示例公司",
        title="示例岗位",
        company_type="medium_company",
        tags=[],
        extra_metadata={},
        raw_text="# 示例岗位\n\n负责能力甲体系建设。\n\n具备能力甲使用经验。",
    )


def discovery_payload() -> dict:
    """返回覆盖三句JD（标题+工作+条件）的合法发现段响应。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0],
                "kind": "excluded",
                "source_span": "# 示例岗位",
                "note": "标题，非条件内容",
            },
            {
                "block_id": "b1",
                "sentence_indexes": [1],
                "kind": "responsibility",
                "source_span": "负责能力甲体系建设",
                "note": "工作内容",
            },
            {
                "block_id": "b2",
                "sentence_indexes": [2],
                "kind": "requirement",
                "source_span": "具备能力甲使用经验",
                "note": "候选人条件",
            },
        ],
    }


def judge_payload() -> dict:
    """返回与候选块一致的合法抽取数据合同响应。"""
    return {
        "role_family": "other",
        "seniority": "unknown",
        "requirements": [
            {
                "raw_name": "能力甲使用经验",
                "category": "other",
                "importance": "must",
                "proficiency": "unknown",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": "具备能力甲使用经验",
                "confidence": 0.95,
            }
        ],
    }


def test_split_sentences_is_deterministic() -> None:
    """验证分句按换行与中文标点拆分且忽略空白分句。"""
    sentences = split_sentences("# 示例岗位\n\n负责能力甲体系建设。\n\n具备能力甲使用经验。")

    assert sentences == ["# 示例岗位", "负责能力甲体系建设", "具备能力甲使用经验"]


def test_two_stage_prompts_are_domain_agnostic() -> None:
    """验证两段Prompt不绑定任何具体领域技能，当前版本号与抽取器一致。"""
    assert TWO_STAGE_PROMPT_VERSION == "0.10"
    assert PROMPT_VERSION == TWO_STAGE_PROMPT_VERSION
    assert SCHEMA_VERSION == TWO_STAGE_SCHEMA_VERSION == "3.0"
    for domain_word in ("Python", "RAG", "LangChain", "Agent", "大模型", "AI"):
        assert domain_word not in DISCOVERY_SYSTEM_PROMPT
        assert domain_word not in JUDGE_SYSTEM_PROMPT
    assert "不得遗漏" in DISCOVERY_SYSTEM_PROMPT
    assert "固定复合要求" in JUDGE_SYSTEM_PROMPT
    assert "proficiency" in JUDGE_SYSTEM_PROMPT


def test_judge_prompt_uses_three_level_proficiency() -> None:
    """Judge Prompt 使用三级熟练度且不输出旧五级枚举。"""
    for value in ("unknown", "basic", "advanced"):
        assert value in JUDGE_SYSTEM_PROMPT
    for old_value in ("understand", "familiar", "proficient", "expert"):
        assert old_value not in JUDGE_SYSTEM_PROMPT
    assert "项目经验" in JUDGE_SYSTEM_PROMPT


def test_judge_prompt_references_stable_rule_ids() -> None:
    """判断段 Prompt 引用 P0-1 稳定规则 ID，便于测试与审计追溯。"""
    for rule_id in (
        "RESP-01",
        "RESP-02",
        "REQ-01",
        "REQ-02",
        "REQ-06",
        "GROUP-01",
        "GROUP-02",
        "FIELD-01",
        "FIELD-02",
        "FIELD-03",
        "COVER-04",
        "EVID-01",
        "EVID-02",
    ):
        assert rule_id in JUDGE_SYSTEM_PROMPT


def test_discovery_prompt_references_coverage_rule_ids() -> None:
    """发现段 Prompt 引用覆盖规则 ID（COVER-01～COVER-03）。"""
    for rule_id in ("COVER-01", "COVER-02", "COVER-03"):
        assert rule_id in DISCOVERY_SYSTEM_PROMPT


def test_judge_prompt_enforces_preferred_and_any_of_separation() -> None:
    """"优先"只决定 preferred；明确"或/至少一种"才建 any_of，"和/与/并且"保持 standalone。"""
    assert 'GROUP-03："优先""加分"只决定importance=preferred，不产生any_of' in JUDGE_SYSTEM_PROMPT
    assert '"和""与""并且"等并列连接默认保持standalone' in JUDGE_SYSTEM_PROMPT
    assert '"有语言甲或语言乙经验者优先"' in JUDGE_SYSTEM_PROMPT
    assert '"有语言甲和语言乙经验者优先"' in JUDGE_SYSTEM_PROMPT
    assert "至少一种" in JUDGE_SYSTEM_PROMPT


def test_judge_prompt_removed_historical_case_patches() -> None:
    """当前 Prompt 不包含针对单个历史 case 的补丁式示例。"""
    for case_patch_marker in (
        "新一代架构",
        "能力模型的调研",
        "信息检索",
    ):
        assert case_patch_marker not in JUDGE_SYSTEM_PROMPT


def test_discovery_coverage_passes_when_all_sentences_covered() -> None:
    """验证覆盖检查通过合法发现结果。"""
    job = make_job()
    discovery = parse_discovery_response(
        json.dumps(discovery_payload(), ensure_ascii=False)
    )

    validate_discovery_coverage(discovery, job.raw_text)


def test_discovery_coverage_rejects_missing_sentence() -> None:
    """验证发现段遗漏分句时被拒绝。"""
    job = make_job()
    payload = discovery_payload()
    payload["blocks"].pop()
    discovery = parse_discovery_response(
        json.dumps(payload, ensure_ascii=False)
    )

    with pytest.raises(ExtractionError, match="覆盖不完整"):
        validate_discovery_coverage(discovery, job.raw_text)


def test_discovery_coverage_rejects_span_not_in_source() -> None:
    """验证候选块原文片段不在JD原文中时被拒绝（防幻觉）。"""
    job = make_job()
    payload = discovery_payload()
    payload["blocks"][0]["source_span"] = "不存在的原文"
    discovery = parse_discovery_response(
        json.dumps(payload, ensure_ascii=False)
    )

    with pytest.raises(ExtractionError, match="与分句索引不对应"):
        validate_discovery_coverage(discovery, job.raw_text)


def test_discovery_coverage_rejects_duplicate_sentence_assignment() -> None:
    """验证同一分句不能同时归属两个候选块。"""
    job = make_job()
    payload = discovery_payload()
    payload["blocks"][2]["sentence_indexes"] = [1]
    payload["blocks"][2]["source_span"] = "负责能力甲体系建设"
    discovery = parse_discovery_response(json.dumps(payload, ensure_ascii=False))

    with pytest.raises(ExtractionError, match="重复覆盖"):
        validate_discovery_coverage(discovery, job.raw_text)


def test_discovery_contract_rejects_duplicate_block_ids() -> None:
    """验证候选块ID必须唯一。"""
    payload = discovery_payload()
    payload["blocks"][2]["block_id"] = "b1"

    with pytest.raises(ExtractionError, match="候选块ID不能重复"):
        parse_discovery_response(json.dumps(payload, ensure_ascii=False))


def test_discovery_contract_rejects_negative_sentence_index() -> None:
    """验证候选块不能引用负分句索引。"""
    payload = discovery_payload()
    payload["blocks"][0]["sentence_indexes"] = [-1]

    with pytest.raises(ExtractionError, match="分句索引不能为负数"):
        parse_discovery_response(json.dumps(payload, ensure_ascii=False))


def test_discovery_contract_rejects_noncontiguous_merge() -> None:
    """验证单个候选块不能跨过中间分句进行拼接。"""
    payload = discovery_payload()
    payload["blocks"][0]["sentence_indexes"] = [0, 2]

    with pytest.raises(ExtractionError, match="只能合并相邻连续分句"):
        parse_discovery_response(json.dumps(payload, ensure_ascii=False))


def test_discovery_allows_merged_adjacent_sentences() -> None:
    """验证相邻分句可合并为一个候选块（sentence_indexes列表）。"""
    raw_text = "甲负责能力建设；乙负责能力评测"
    payload = {
        "role_family": "other",
        "seniority": "unknown",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0, 1],
                "kind": "responsibility",
                "source_span": raw_text,
                "note": "相邻工作内容",
            }
        ],
    }
    discovery = parse_discovery_response(
        json.dumps(payload, ensure_ascii=False)
    )

    validate_discovery_coverage(discovery, raw_text)


def test_two_stage_extraction_succeeds_with_fake_client() -> None:
    """验证两段式抽取成功路径：发现段+判断段各一次调用。"""
    job = make_job()
    client = FakeTwoStageClient(discovery_payload(), judge_payload())

    result, raw = extract_job_two_stage(job, client)

    assert client.calls == 2
    assert len(result.requirements) == 1
    assert result.requirements[0].raw_name == "能力甲使用经验"


def test_discovery_retry_fixes_invalid_response() -> None:
    """验证发现段首次非法输出后带修正提示重试成功。"""
    job = make_job()
    client = FakeTwoStageClient(
        discovery_payload(), judge_payload(), fail_discovery_times=1
    )

    result, _ = extract_job_two_stage(job, client)

    assert client.calls == 3
    assert len(result.requirements) == 1
    assert "上次校验错误" in client.prompts[1]
