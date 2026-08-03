"""验证P0-4归并LLM客户端、Prompt v1、解析与有限重试闭环。"""

import json

import pytest

from app.consolidation import (
    CONSOLIDATION_PROMPT_VERSION,
    CONSOLIDATION_READ_TIMEOUT_SECONDS,
    CONSOLIDATION_SCHEMA_VERSION,
    CONSOLIDATION_SYSTEM_PROMPT,
    RELATION_SYSTEM_PROMPT,
    ConsolidationError,
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    build_consolidation_user_prompt,
    consolidate_with_correction,
    parse_consolidation_response,
    parse_mappings_response,
    parse_relations_response,
)
from app.config import LLMSettings
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementOccurrence,
    RequirementRelationType,
)
from app.schemas import RequirementItem


class FakeConsolidationClient:
    """按预设顺序返回JSON文本，并记录用户提示，替代真实且有费用的LLM调用。"""

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
        assert "requirements" in user_prompt
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
                extractor_version="test-model|prompt:1.0|schema:2.0",
                source_hash="a" * 64,
                source_file="job-a.md",
                requirement=requirement("能力甲使用经验", "具备能力甲使用经验"),
            ),
            RequirementOccurrence(
                requirement_id=2,
                job_id=102,
                extraction_id=1002,
                extractor_version="test-model|prompt:1.0|schema:2.0",
                source_hash="b" * 64,
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


def test_prompt_v4_is_domain_agnostic() -> None:
    """验证Prompt v4.0不绑定任何具体领域技能，只描述通用归并任务。"""
    assert CONSOLIDATION_PROMPT_VERSION == "4.0"
    assert CONSOLIDATION_SCHEMA_VERSION == "2.0"
    for domain_word in ("Python", "RAG", "LangChain", "Agent", "大模型", "AI"):
        assert domain_word not in CONSOLIDATION_SYSTEM_PROMPT
        assert domain_word not in RELATION_SYSTEM_PROMPT
    assert "证据上下文" in CONSOLIDATION_SYSTEM_PROMPT
    assert "不输出unmapped或review_required" in CONSOLIDATION_SYSTEM_PROMPT
    assert "singleton" in CONSOLIDATION_SYSTEM_PROMPT
    assert "不得修改、覆盖或删除" in CONSOLIDATION_SYSTEM_PROMPT
    assert "canonical_name都必须全局唯一" in CONSOLIDATION_SYSTEM_PROMPT
    assert "等价性已由映射表达" in RELATION_SYSTEM_PROMPT
    assert "更宽泛 -> 更具体" in RELATION_SYSTEM_PROMPT
    assert "相似\"不等于\"等价" in RELATION_SYSTEM_PROMPT
    assert "相关\"不等于\"包含" in RELATION_SYSTEM_PROMPT
    assert "uncertain" in RELATION_SYSTEM_PROMPT
    assert "强行生成边" in RELATION_SYSTEM_PROMPT
    assert "只输出用户提示指定的relations数组" in RELATION_SYSTEM_PROMPT


def test_metadata_combines_version_components() -> None:
    """验证归并器版本由模型、Prompt和合同版本组成。"""
    metadata = ConsolidatorMetadata(model_name="test-model")

    assert metadata.consolidator_version == (
        "test-model|prompt:4.0|schema:2.0"
    )


def test_real_client_uses_explicit_full_batch_timeout_and_retry_policy() -> None:
    """验证全量归并使用显式读取超时，并由项目层而非SDK隐式控制重试。"""
    client = OpenAICompatibleConsolidationClient(
        LLMSettings(api_key="test-key", model="test-model")
    )

    assert client._client.timeout.connect == 5.0
    assert client._client.timeout.read == CONSOLIDATION_READ_TIMEOUT_SECONDS
    assert client._client.max_retries == 0
    client._client.close()


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
    assert "全局唯一" in payload["output_schema"]["canonical_requirements"][0][
        "canonical_name"
    ]
    assert "broader_than" in payload["output_schema"]["relations"][0][
        "relation_type"
    ]
    assert "uncertain" in payload["output_schema"]["relations"][0][
        "relation_type"
    ]


def stage_payloads(payload: dict[str, object]) -> list[dict[str, object]]:
    """把完整归并结果拆成标准项、映射和关系三个阶段的独立响应。"""
    return [
        {"canonical_requirements": payload["canonical_requirements"]},
        {"mappings": payload["mappings"]},
        {"relations": payload["relations"]},
    ]


def test_valid_response_parses_and_passes_coverage() -> None:
    """验证三阶段响应合成后解析成功并通过覆盖检查，返回完整结果。"""
    client = FakeConsolidationClient(stage_payloads(valid_result_payload()))

    result, raw = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 3
    assert len(result.canonical_requirements) == 1
    assert result.canonical_requirements[0].canonical_name == "能力甲使用经验"
    assert len(result.mappings) == 2
    assert raw["canonical_requirements"][0]["canonical_requirement_id"] == "requirement-a"


def test_invalid_json_raises_consolidation_error() -> None:
    """验证非JSON响应被包装为统一归并错误。"""
    with pytest.raises(ConsolidationError, match="不是合法JSON"):
        parse_consolidation_response("这不是JSON")


def test_mappings_response_normalizes_null_candidate_ids() -> None:
    """验证模型用null表达无候选时被规范化为空列表，语义不变。"""
    payload = {
        "mappings": [
            {
                "requirement_id": 1,
                "status": "mapped",
                "canonical_requirement_id": "requirement-a",
                "candidate_requirement_ids": None,
                "rationale": "表述不同但招聘条件相同",
                "confidence": 0.95,
            }
        ]
    }

    mappings = parse_mappings_response(json.dumps(payload, ensure_ascii=False))

    assert len(mappings) == 1
    assert mappings[0].candidate_requirement_ids == []


def test_contract_violation_raises_consolidation_error() -> None:
    """验证映射引用未知标准项ID时被块级校验拒绝（可反馈模型修正）。"""
    payload = valid_result_payload()
    payload["mappings"][0]["canonical_requirement_id"] = "missing"

    with pytest.raises(
        ConsolidationError,
        match="映射引用了标准要求项清单中不存在的ID",
    ):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient(stage_payloads(payload)),
            max_attempts=1,
        )


def test_coverage_gap_raises_consolidation_error() -> None:
    """验证映射块遗漏要求实例时被块级覆盖检查拒绝。"""
    payload = valid_result_payload()
    payload["mappings"].pop()

    with pytest.raises(ConsolidationError, match="遗漏要求实例"):
        consolidate_with_correction(
            consolidation_input(),
            FakeConsolidationClient(stage_payloads(payload)),
            max_attempts=1,
        )


def test_retry_feeds_correction_and_succeeds() -> None:
    """验证标准项阶段首次输出非法JSON后，第二次带修正提示重试并成功。"""
    client = FakeConsolidationClient(
        [{"bad": True}] + stage_payloads(valid_result_payload())
    )

    result, _ = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 4
    assert len(result.mappings) == 2
    assert "上次校验错误" in client.prompts[1]


def test_retry_repeats_original_prompt_after_llm_call_error() -> None:
    """验证标准项阶段调用层失败会重试，且不把网络错误写入业务修正提示。"""
    client = FakeConsolidationClient(
        [ConsolidationError("LLM调用失败：临时超时")]
        + stage_payloads(valid_result_payload())
    )

    result, _ = consolidate_with_correction(consolidation_input(), client)

    assert client.calls == 4
    assert len(result.mappings) == 2
    assert client.prompts[1] == client.prompts[0]


def test_retry_exhausted_raises_final_error() -> None:
    """验证连续失败超过尝试次数后抛出最终归并错误。"""
    client = FakeConsolidationClient([{"bad": True}, {"bad": True}])

    with pytest.raises(ConsolidationError, match="仍未通过归并校验"):
        consolidate_with_correction(consolidation_input(), client)


def test_part_of_normalizes_to_broader_than_with_direction_swap() -> None:
    """验证旧part_of语义规范化为broader_than并交换方向（整体->组成部分）。"""
    payload = {
        "relations": [
            {
                "source_requirement_id": "requirement-b",
                "target_requirement_id": "requirement-a",
                "relation_type": "part_of",
                "rationale": "乙是甲的组成部分",
                "confidence": 0.8,
            }
        ]
    }

    relations, uncertain = parse_relations_response(
        json.dumps(payload, ensure_ascii=False)
    )

    assert uncertain == []
    assert len(relations) == 1
    assert relations[0].relation_type is RequirementRelationType.BROADER_THAN
    assert relations[0].source_requirement_id == "requirement-a"
    assert relations[0].target_requirement_id == "requirement-b"


def test_is_a_normalizes_to_broader_than_with_direction_swap() -> None:
    """验证旧is_a语义规范化为broader_than并交换方向（上位->下位）。"""
    payload = {
        "relations": [
            {
                "source_requirement_id": "requirement-b",
                "target_requirement_id": "requirement-a",
                "relation_type": "is_a",
                "rationale": "乙是甲的一种具体类型",
                "confidence": 0.8,
            }
        ]
    }

    relations, uncertain = parse_relations_response(
        json.dumps(payload, ensure_ascii=False)
    )

    assert uncertain == []
    assert len(relations) == 1
    assert relations[0].relation_type is RequirementRelationType.BROADER_THAN
    assert relations[0].source_requirement_id == "requirement-a"
    assert relations[0].target_requirement_id == "requirement-b"


def test_related_to_is_rejected_as_deprecated() -> None:
    """验证废弃的related_to被拒绝，普通相关不再建立任何关系。"""
    payload = {
        "relations": [
            {
                "source_requirement_id": "requirement-a",
                "target_requirement_id": "requirement-b",
                "relation_type": "related_to",
                "rationale": "统计上相关",
                "confidence": 0.7,
            }
        ]
    }

    with pytest.raises(ConsolidationError, match="related_to 已废弃"):
        parse_relations_response(json.dumps(payload, ensure_ascii=False))


def test_uncertain_relations_split_out_and_create_no_edges() -> None:
    """验证uncertain判断与正式关系分离，不创建任何正式关系边。"""
    payload = {
        "relations": [
            {
                "source_requirement_id": "requirement-a",
                "target_requirement_id": "requirement-b",
                "relation_type": "uncertain",
                "rationale": "名称抽象，无法判断包含方向",
                "confidence": 0.5,
            }
        ]
    }

    relations, uncertain = parse_relations_response(
        json.dumps(payload, ensure_ascii=False)
    )

    assert relations == []
    assert len(uncertain) == 1
    assert uncertain[0].relation_type is RequirementRelationType.UNCERTAIN


def test_none_relations_accept_empty_array() -> None:
    """验证无包含关系的标准项之间不输出边（none），空数组合法。"""
    relations, uncertain = parse_relations_response(
        json.dumps({"relations": []}, ensure_ascii=False)
    )

    assert relations == []
    assert uncertain == []
