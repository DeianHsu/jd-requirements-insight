"""验证规则场景数据文件与确定性变换（P0-3 新协议，DEC-015）。

场景文件不保存完整 expected extraction；base_input 必须领域中性，不得
包含真实 JD 内容或具体领域技术词（CASE-006 防泄漏规则）。
"""

import json

import pytest

from scripts.experiments.p0_3.run_acceptance import apply_transformation

SCENARIOS_PATH = "data/rule_scenarios/extraction_metamorphic_cases.json"

# 与 test_extraction_two_stage 相同的领域中性约束词。
DOMAIN_WORDS = (
    "Python",
    "RAG",
    "LangChain",
    "LangGraph",
    "Agent",
    "大模型",
    "AI",
    "FastAPI",
    "Go",
    "Java",
    "C++",
    "Llama",
    "ChatGLM",
    "GPT",
    "Docker",
    "K8s",
)

REQUIRED_SCENARIO_KEYS = {
    "scenario_id",
    "rule_ids",
    "base_input",
    "transformation",
    "expected_properties",
    "forbidden_violations",
    "severity",
}


@pytest.fixture(scope="module")
def scenarios() -> dict:
    """加载规则场景文件。"""
    with open(SCENARIOS_PATH, encoding="utf-8") as file:
        return json.load(file)


def test_scenario_file_has_all_required_sections(scenarios: dict) -> None:
    """场景文件包含全部 10 个场景与元数据。"""
    assert scenarios["protocol_version"]
    assert scenarios["description"]
    assert len(scenarios["scenarios"]) == 10


def test_every_scenario_has_required_fields(scenarios: dict) -> None:
    """每个场景至少包含 scenario_id/rule_ids/base_input/transformation/expected_properties/forbidden_violations/severity。"""
    scenario_ids = set()
    for scenario in scenarios["scenarios"]:
        assert REQUIRED_SCENARIO_KEYS <= set(scenario)
        assert scenario["severity"] in {"high", "medium", "low"}
        assert isinstance(scenario["rule_ids"], list) and scenario["rule_ids"]
        assert isinstance(scenario["expected_properties"], dict)
        assert isinstance(scenario["forbidden_violations"], list)
        assert scenario["transformation"]["type"]
        scenario_ids.add(scenario["scenario_id"])
    assert len(scenario_ids) == len(scenarios["scenarios"])


def test_scenario_ids_are_stable_and_unique(scenarios: dict) -> None:
    """场景 ID 稳定唯一（SCN-001 起连续编号）。"""
    expected_ids = [f"SCN-{index:03d}" for index in range(1, 11)]
    assert [scenario["scenario_id"] for scenario in scenarios["scenarios"]] == expected_ids


def test_scenario_base_inputs_are_domain_neutral(scenarios: dict) -> None:
    """base_input 与变换参数不得包含具体领域技术词或真实 JD 内容。"""
    for scenario in scenarios["scenarios"]:
        combined = scenario["base_input"] + json.dumps(
            scenario["transformation"], ensure_ascii=False
        )
        for word in DOMAIN_WORDS:
            assert word not in combined, (
                f"{scenario['scenario_id']} 包含领域词 {word}"
            )


def test_scenarios_cover_all_required_categories(scenarios: dict) -> None:
    """变换类型覆盖格式、顺序、无关内容、重复、措辞、改名、拆分等类别。"""
    transform_types = {
        scenario["transformation"]["type"] for scenario in scenarios["scenarios"]
    }
    assert "format_bullets" in transform_types
    assert "reorder_paragraphs" in transform_types
    assert "append_text" in transform_types
    assert "duplicate_sentence" in transform_types
    assert "text_replace" in transform_types


def test_transformations_are_deterministic(scenarios: dict) -> None:
    """同一输入同一变换两次执行结果一致。"""
    for scenario in scenarios["scenarios"]:
        base = scenario["base_input"]
        first = apply_transformation(base, scenario["transformation"])
        second = apply_transformation(base, scenario["transformation"])
        assert first == second


def test_format_bullets_keeps_alnum_sequence() -> None:
    """格式变换不得改变字母数字序列（覆盖/证据锚点稳定）。"""
    text = "# 示例岗位\n\n1. 熟悉技术甲和框架乙。\n- 具备能力丙使用经验者优先。"
    transformed = apply_transformation(text, {"type": "format_bullets"})

    def alnum(value: str) -> str:
        return "".join(ch for ch in value if ch.isalnum())

    assert alnum(text) == alnum(transformed)


def test_reorder_paragraphs_is_reproducible_with_seed() -> None:
    """段落重排按固定种子可复现。"""
    text = "# 示例岗位\n\n负责能力甲体系建设。\n\n熟悉技术甲。\n\n本科及以上学历。"
    transformation = {"type": "reorder_paragraphs", "seed": 20260803}
    first = apply_transformation(text, transformation)
    second = apply_transformation(text, transformation)
    assert first == second
    assert "# 示例岗位" in first.split("\n\n")[0]
    assert set(first.split("\n\n")) == set(text.split("\n\n"))


def test_text_replace_requires_existing_target() -> None:
    """text_replace 找不到目标时拒绝变换，防止静默无效实验。"""
    with pytest.raises(ValueError, match="未找到目标"):
        apply_transformation(
            "熟悉技术甲。",
            {"type": "text_replace", "replacements": [{"find": "不存在的词", "replace": "X"}]},
        )


def test_duplicate_sentence_requires_existing_target() -> None:
    """duplicate_sentence 找不到目标时拒绝变换。"""
    with pytest.raises(ValueError, match="未找到目标"):
        apply_transformation(
            "熟悉技术甲。", {"type": "duplicate_sentence", "target": "不存在的句"}
        )


def test_unknown_transformation_is_rejected() -> None:
    """未知变换类型直接拒绝。"""
    with pytest.raises(ValueError, match="未知变换类型"):
        apply_transformation("text", {"type": "not_a_real_transform"})
