"""验证P0-4归并LLM客户端、Prompt v1、解析与有限重试闭环。"""

import json

import pytest

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_SCHEMA_VERSION,
    CONSOLIDATION_SYSTEM_PROMPT,
    ConsolidationError,
    ConsolidatorMetadata,
    build_consolidation_user_prompt,
    consolidate_with_correction,
    parse_consolidation_response,
)
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementOccurrence,
)
from app.schemas import RequirementItem


class FakeConsolidationClient:
    """按预设顺序返回JSON文本，并记录用户提示，替代真实且有费用的LLM调用。"""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        """保存待返回响应并初始化调用记录。"""
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应，并断言Prompt携带必要约束和实例数据。"""
        assert "归并" in system_prompt
        assert "requirements" in user_prompt
        self.prompts.append(user_prompt)
        response = self.responses[self.calls]
        self.calls += 1
        return json.dumps(response, ensure_ascii=False)


def requirement(raw_name: str, evidence: str) -> RequirementItem:
    """构造保留完整抽取数据合同字段的中性虚构原子要求。"""
    return RequirementItem.model_validate(
        {
            "raw_name": raw_name,
            "category": "other",
            "importance": "must",
            "proficiency": "unknown",
            "group_id": None,
            "group_logic": "standalone",
            "min_years": None,
            "max_years": None,
            "years_text": None,
            "evidence": evidence,
            "confidence": 0.9,
        }
    )


def consolidation_input() -> RequirementConsolidationInput:
    """构造来自两份JD并共同进入归并语料池的要求实例。"""
    return RequirementConsolidationInput(
        occurrences=[
            RequirementOccurrence(
                requirement_id=1,
                job_id=101,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                source_file="job-b.md",
                requirement=requirement(
                    "具备能力甲的使用经验", "具备能力甲的使用经验"
                ),
            ),
        ]
    )


def valid_result_payload() -> dict[str, object]:
    """生成两个实例归并到同一标准要求项的合法响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "rationale": "两条要求在各自证据中指向同一招聘条件",
                "confidence": 0.95,
            }
        ],
        "mappings": [
            {
                "requirement_id": requirement_id,
                "status": "mapped",
                "canonical_requirement_id": "requirement-a",
                "candidate_requirement_ids": [],
                "rationale": "表述不同但招聘条件相同",
                "confidence": 0.95,
            }
            for requirement_id in (1, 2)
        ],
        "relations": [],
    }


def test_prompt_v1_is_domain_agnostic() -> None:
    """验证Prompt v1不绑定任何具体领域技能，只描述通用归并任务。"""
    assert CONSOLIDATION_PROMPT_VERSION == "1.3"
    assert CONSOLIDATION_SCHEMA_VERSION == "1.0"
    for domain_word in ("Python", "RAG", "LangChain", "Agent", "大模型", "AI"):
        assert domain_word not in CONSOLIDATION_SYSTEM_PROMPT
    assert "证据上下文" in CONSOLIDATION_SYSTEM_PROMPT
    assert "review_required" in CONSOLIDATION_SYSTEM_PROMPT
    assert "不得修改、覆盖或删除" in CONSOLIDATION_SYSTEM_PROMPT


def test_metadata_combines_version_components() -> None:
    """验证归并器版本由模型、Prompt和合同版本组成。"""
    metadata = ConsolidatorMetadata(model_name="test-model")

    assert metadata.consolidator_version == (
        "test-model|prompt:1.3|schema:1.0"
    )


def test_user_prompt_contains_instances_and_output_schema() -> None:
    """验证用户提示携带全部实例字段并说明期望输出结构。"""
    payload = json.loads(build_consolidation_user_prompt(consolidation_input()))

    assert len(payload["requirements"]) == 2
    first = payload["requirements"][0]
    assert first["id"] == 1
    assert first["raw_name"] == "能力甲使用经验"
    assert first["evidence"] == "具备能力甲使用经验"
    assert first["importance"] == "must"
    assert "output_schema" in payload
    assert "canonical_requirements" in payload["output_schema"]
    assert "mappings" in payload["output_schema"]
    assert "relations" in payload["output_schema"]


def test_valid_response_parses_and_passes_coverage() -> None:
    """验证合法响应解析成功并通过覆盖检查，返回结果与原始JSON。"""
    client = FakeConsolidationClient([valid_result_payload()])

    result, raw = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 1
    assert len(result.canonical_requirements) == 1
    assert result.canonical_requirements[0].canonical_name == "能力甲使用经验"
    assert len(result.mappings) == 2
    assert raw["canonical_requirements"][0]["canonical_requirement_id"] == "requirement-a"


def test_invalid_json_raises_consolidation_error() -> None:
    """验证非JSON响应被包装为统一归并错误。"""
    with pytest.raises(ConsolidationError, match="不是合法JSON"):
        parse_consolidation_response("这不是JSON")


def test_contract_violation_raises_consolidation_error() -> None:
    """验证映射引用未知标准要求项时被合同校验拒绝。"""
    payload = valid_result_payload()
    payload["mappings"][0]["canonical_requirement_id"] = "missing"

    with pytest.raises(ConsolidationError, match="映射引用未知标准要求项"):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient([payload]),
            max_attempts=1,
        )


def test_coverage_gap_raises_consolidation_error() -> None:
    """验证遗漏要求实例时即使合同合法也被覆盖检查拒绝。"""
    payload = valid_result_payload()
    payload["mappings"].pop()

    with pytest.raises(ConsolidationError, match="遗漏要求实例"):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient([payload]),
            max_attempts=1,
        )


def test_retry_feeds_correction_and_succeeds() -> None:
    """验证首次输出非法JSON后，第二次带修正提示重试并成功。"""
    client = FakeConsolidationClient([{"bad": True}, valid_result_payload()])

    result, _ = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 2
    assert len(result.mappings) == 2
    assert "上次校验错误" in client.prompts[1]


def test_retry_exhausted_raises_final_error() -> None:
    """验证连续失败超过尝试次数后抛出最终归并错误。"""
    client = FakeConsolidationClient([{"bad": True}, {"bad": True}])

    with pytest.raises(ConsolidationError, match="仍未通过归并校验"):
        consolidate_with_correction(consolidation_input(), client)
