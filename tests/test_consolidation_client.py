"""验证P0-4归并LLM客户端、Prompt v4.1、单次聚类解析与有限重试闭环。"""

import json

import pytest

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_READ_TIMEOUT_SECONDS,
    CONSOLIDATION_SCHEMA_VERSION,
    CONSOLIDATION_SYSTEM_PROMPT,
    ConsolidationError,
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    build_canonical_requirements_prompt,
    consolidate_with_correction,
    parse_canonical_requirements_response,
)
from app.config import LLMSettings
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementOccurrence,
)
from app.schemas import RequirementItem


class FakeConsolidationClient:
    """按预设顺序返回 JSON 文本，只模拟单次 canonical 聚类响应。"""

    def __init__(
        self, responses: list[dict[str, object] | ConsolidationError]
    ) -> None:
        """保存待返回响应并初始化调用记录。"""
        self.responses = responses
        self.calls = 0
        self.prompts: list[str] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回当前预设响应，并断言Prompt携带必要约束和实例数据。"""
        assert "要求项" in system_prompt
        assert "canonical_requirements" in user_prompt
        self.prompts.append(user_prompt)
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, ConsolidationError):
            raise response
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
                extraction_id=1001,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                source_hash="a" * 64,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                extraction_id=1002,
                extractor_version="test-model|prompt:0.8|schema:3.0",
                source_hash="b" * 64,
                source_file="job-b.md",
                requirement=requirement(
                    "具备能力甲的使用经验", "具备能力甲的使用经验"
                ),
            ),
        ]
    )


def valid_result_payload() -> dict[str, object]:
    """生成两个实例归并到同一标准要求项的合法单次聚类响应。"""
    return {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "source_requirement_ids": [1, 2],
                "rationale": "两条要求在各自证据中指向同一招聘条件",
                "confidence": 0.95,
            }
        ]
    }


def test_prompt_v41_is_domain_agnostic() -> None:
    """验证Prompt v4.1不绑定任何具体领域技能，只描述单次聚类任务。"""
    assert CONSOLIDATION_PROMPT_VERSION == "4.1"
    assert CONSOLIDATION_SCHEMA_VERSION == "3.0"
    for domain_word in ("Python", "RAG", "LangChain", "Agent", "大模型", "AI"):
        assert domain_word not in CONSOLIDATION_SYSTEM_PROMPT
    assert "证据上下文" in CONSOLIDATION_SYSTEM_PROMPT
    assert "singleton" in CONSOLIDATION_SYSTEM_PROMPT
    assert "不得修改、覆盖或删除" in CONSOLIDATION_SYSTEM_PROMPT
    assert "canonical_name都必须全局唯一" in CONSOLIDATION_SYSTEM_PROMPT
    assert "source_requirement_ids" in CONSOLIDATION_SYSTEM_PROMPT
    # 单次聚类合同：不输出 mappings、不输出关系或层级结构。
    assert "mappings" not in CONSOLIDATION_SYSTEM_PROMPT
    assert "relations" not in CONSOLIDATION_SYSTEM_PROMPT


def test_metadata_combines_version_components() -> None:
    """验证归并器版本由模型、Prompt和合同版本组成（新合同新版本）。"""
    metadata = ConsolidatorMetadata(model_name="test-model")

    assert metadata.consolidator_version == (
        "test-model|prompt:4.1|schema:3.0"
    )


def test_real_client_uses_explicit_full_batch_timeout_and_retry_policy() -> None:
    """验证归并使用显式读取超时，并由项目层而非SDK隐式控制重试。"""
    client = OpenAICompatibleConsolidationClient(
        LLMSettings(api_key="test-key", model="test-model")
    )

    assert client._client.timeout.connect == 5.0
    assert client._client.timeout.read == CONSOLIDATION_READ_TIMEOUT_SECONDS
    assert client._client.max_retries == 0
    client._client.close()


def test_user_prompt_contains_instances_and_canonical_schema() -> None:
    """验证提示携带全部实例字段，输出 schema 只有 canonical_requirements。"""
    payload = json.loads(
        build_canonical_requirements_prompt(consolidation_input())
    )

    assert len(payload["requirements"]) == 2
    first = payload["requirements"][0]
    assert first["id"] == 1
    assert first["raw_name"] == "能力甲使用经验"
    assert first["evidence"] == "具备能力甲使用经验"
    assert first["importance"] == "must"
    assert "output_schema" in payload
    assert set(payload["output_schema"]) == {"canonical_requirements"}
    canonical_schema = payload["output_schema"]["canonical_requirements"][0]
    assert "source_requirement_ids" in canonical_schema
    assert "mappings" not in payload["output_schema"]


def test_valid_response_parses_and_generates_mappings() -> None:
    """验证单次聚类响应解析后确定性生成 mappings 并通过覆盖检查。"""
    client = FakeConsolidationClient([valid_result_payload()])

    result, raw = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 1
    assert len(result.canonical_requirements) == 1
    assert result.canonical_requirements[0].canonical_name == "能力甲使用经验"
    # mappings 由来源分区确定性生成：每个来源实例一条。
    assert len(result.mappings) == 2
    assert {mapping.requirement_id for mapping in result.mappings} == {1, 2}
    assert all(
        mapping.canonical_requirement_id == "requirement-a"
        for mapping in result.mappings
    )
    assert raw["model_response"]["canonical_requirements"][0]["canonical_requirement_id"] == "requirement-a"


def test_invalid_json_raises_consolidation_error() -> None:
    """验证非JSON响应被包装为统一归并错误。"""
    with pytest.raises(ConsolidationError, match="不是合法JSON"):
        parse_canonical_requirements_response("这不是JSON")


def test_partition_gap_raises_consolidation_error() -> None:
    """验证来源分区遗漏要求实例时被拒绝（可反馈模型修正）。"""
    payload = valid_result_payload()
    payload["canonical_requirements"][0]["source_requirement_ids"] = [1]

    with pytest.raises(ConsolidationError, match="遗漏 requirement_id"):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient([payload]),
            max_attempts=1,
        )


def test_unknown_source_id_raises_consolidation_error() -> None:
    """验证来源分区引用未知实例ID时被拒绝。"""
    payload = valid_result_payload()
    payload["canonical_requirements"][0]["source_requirement_ids"] = [1, 99]

    with pytest.raises(ConsolidationError, match="未知 requirement_id"):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient([payload]),
            max_attempts=1,
        )


def test_retry_feeds_correction_and_succeeds() -> None:
    """验证首次分区违规后，第二次带修正提示重试并成功。"""
    payload = valid_result_payload()
    bad_payload = {
        "canonical_requirements": [
            {
                "canonical_requirement_id": "requirement-a",
                "canonical_name": "能力甲使用经验",
                "source_requirement_ids": [1],
                "rationale": "遗漏实例2",
                "confidence": 0.95,
            }
        ]
    }
    client = FakeConsolidationClient([bad_payload, payload])

    result, _ = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 2
    assert len(result.mappings) == 2
    assert "上次校验错误" in client.prompts[1]
    assert "遗漏 requirement_id" in client.prompts[1]


def test_retry_repeats_original_prompt_after_llm_call_error() -> None:
    """验证调用层失败会重试，且不把网络错误写入业务修正提示。"""
    client = FakeConsolidationClient(
        [ConsolidationError("LLM调用失败：临时超时"), valid_result_payload()]
    )

    result, _ = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 2
    assert len(result.mappings) == 2
    assert client.prompts[1] == client.prompts[0]


def test_retry_exhausted_raises_final_error() -> None:
    """验证连续失败超过尝试次数后抛出最终归并错误。"""
    client = FakeConsolidationClient([{"bad": True}, {"bad": True}])

    with pytest.raises(ConsolidationError, match="仍未通过归并校验"):
        consolidate_with_correction(consolidation_input(), client)
