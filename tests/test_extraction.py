"""验证 LLM 结构化抽取、证据约束与有限重试。"""

import json
from datetime import date
import pytest

from app.extraction import (
    ExtractionError,
    build_user_prompt,
    compact_json_schema,
    extract_job,
    validate_evidence,
)
from app.models import JobDescription
from app.schemas import JobExtractionResult


class FakeExtractionClient:
    """按预设顺序返回JSON文本，用于在测试中替代真实且有费用的LLM调用。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存待返回响应并初始化调用次数。"""
        self.responses = responses
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应，并记录提示中包含必要约束和JD原文。"""
        assert "只能依据" in system_prompt
        assert "每个requirement只能表达一个" in system_prompt
        assert "group_logic=any_of" in system_prompt
        assert "不能自动成为独立要求" in system_prompt
        assert "JD原文" in user_prompt
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


class RecordingExperimentClient:
    """记录实验Prompt并返回预设JSON，避免静态阶段调用真实LLM。"""

    def __init__(self, response: dict[str, object]) -> None:
        """保存预设响应并初始化调用记录。"""
        self.response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """记录系统及用户Prompt并返回合法JSON。"""
        self.calls.append((system_prompt, user_prompt))
        return json.dumps(self.response, ensure_ascii=False)


class FakeTwoStageExtractionClient:
    """按预设顺序返回发现段与判断段JSON，用于正式两段式抽取测试。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存按调用顺序排列的发现/判断段响应并初始化调用次数。"""
        self.responses = responses
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """按调用轮次返回发现段或判断段响应，并校验两段式提示结构。"""
        if "全局发现" in system_prompt:
            assert "sentences" in user_prompt
        else:
            assert "精细判断" in system_prompt
            assert "blocks" in user_prompt
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


def valid_payload(evidence: str = "熟悉 Python 和 RAG。") -> dict[str, object]:
    """生成一份符合抽取数据合同且可按需替换证据的测试响应。"""
    return {
        "role_family": "rag_application",
        "seniority": "junior",
        "requirements": [
            {
                "raw_name": "Python",
                "category": "programming_language",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": evidence,
                "confidence": 0.95,
            },
            {
                "raw_name": "RAG",
                "category": "rag",
                "importance": "must",
                "proficiency": "basic",
                "group_id": None,
                "group_logic": "standalone",
                "min_years": None,
                "max_years": None,
                "years_text": None,
                "evidence": evidence,
                "confidence": 0.95,
            }
        ],
    }


def discovery_payload() -> dict[str, object]:
    """生成与make_job三句原文对应的合法发现段响应。"""
    return {
        "role_family": "rag_application",
        "seniority": "junior",
        "blocks": [
            {
                "block_id": "b0",
                "sentence_indexes": [0],
                "kind": "excluded",
                "source_span": "# RAG工程师",
                "note": "标题，非条件内容",
            },
            {
                "block_id": "b1",
                "sentence_indexes": [1],
                "kind": "responsibility",
                "source_span": "负责知识库问答系统开发",
                "note": "工作内容",
            },
            {
                "block_id": "b2",
                "sentence_indexes": [2],
                "kind": "requirement",
                "source_span": "熟悉 Python 和 RAG",
                "note": "候选人条件",
            },
        ],
    }


def make_job() -> JobDescription:
    """创建一份包含连续证据文本的内存JD对象供抽取测试使用。"""
    return JobDescription(
        id=1,
        source_hash="a" * 64,
        source_file="sample.md",
        source_type="test",
        collected_at=date(2026, 7, 21),
        company="示例公司",
        title="RAG工程师",
        company_type="medium_company",
        tags=[],
        extra_metadata={},
        raw_text="# RAG工程师\n\n负责知识库问答系统开发。\n\n熟悉 Python 和 RAG。",
    )


def test_build_user_prompt_contains_schema_v3_and_retry_feedback() -> None:
    """验证用户Prompt携带V2字段、JD原文和上一轮错误以支持定向修正。"""
    prompt = build_user_prompt(make_job(), "any_of组至少需要两个成员")

    assert '"group_id"' in prompt
    assert '"group_logic"' in prompt
    assert '"min_years"' in prompt
    assert '"max_years"' in prompt
    assert '"years_text"' in prompt
    assert "熟悉 Python 和 RAG。" in prompt
    assert "any_of组至少需要两个成员" in prompt
    assert '"title"' not in prompt
    assert '"description"' not in prompt


def test_compact_json_schema_keeps_constraints_without_explanatory_fields() -> None:
    """验证模型JSON Schema删除重复说明时仍保留字段、必填项和枚举约束。"""
    schema = compact_json_schema(JobExtractionResult.model_json_schema())
    serialized = json.dumps(schema, ensure_ascii=False)

    assert "properties" in serialized
    assert "required" in serialized
    assert "enum" in serialized
    assert '"title"' not in serialized
    assert '"description"' not in serialized


def test_extract_job_returns_validated_result() -> None:
    """验证正式两段式流程（发现段+判断段）通过合同和原文证据检查。"""
    client = FakeTwoStageExtractionClient([discovery_payload(), valid_payload()])

    result, raw_response = extract_job(make_job(), client)

    assert result.role_family.value == "rag_application"
    assert [item.raw_name for item in result.requirements] == ["Python", "RAG"]
    assert raw_response["seniority"] == "junior"
    assert client.calls == 2


def test_extract_job_retries_after_invalid_evidence() -> None:
    """验证判断段证据不存在时会把错误反馈给下一次判断段请求。"""
    client = FakeTwoStageExtractionClient(
        [discovery_payload(), valid_payload("JD中不存在的证据"), valid_payload()]
    )

    result, _ = extract_job(make_job(), client, max_attempts=2)

    assert result.requirements[0].evidence == "熟悉 Python 和 RAG。"
    assert client.calls == 3


def test_validate_evidence_rejects_hallucinated_quote() -> None:
    """验证原文中不存在的职责或要求证据会被明确拒绝。"""
    result = JobExtractionResult.model_validate(valid_payload("不存在的技能要求"))

    with pytest.raises(ExtractionError, match="证据不在JD原文中"):
        validate_evidence(result, make_job().raw_text)
