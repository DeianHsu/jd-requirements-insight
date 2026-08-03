"""验证规则场景数据文件与确定性变换（P0-3 新协议，DEC-015）。

场景文件不保存完整 expected extraction；base_input 必须领域中性，不得
包含真实 JD 内容或具体领域技术词（CASE-006 防泄漏规则）。变换返回
TransformationResult（锚点映射 + 预期变化区域），支持一句拆两句的一对多
映射与重复句 occurrence。
"""

import json

import pytest

from app.extraction_two_stage import split_sentences
from app.extraction_validation import anchor_ids
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
    """场景文件包含全部场景与元数据（protocol 1.1，13 个场景）。"""
    assert scenarios["protocol_version"] == "1.1"
    assert scenarios["description"]
    assert len(scenarios["scenarios"]) == 13


def test_every_scenario_has_required_fields(scenarios: dict) -> None:
    """每个场景至少包含必备字段。"""
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
    expected_ids = [f"SCN-{index:03d}" for index in range(1, 14)]
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


def test_scenario_proficiency_values_are_three_level(scenarios: dict) -> None:
    """场景期望属性只使用三级熟练度（unknown/basic/advanced）。"""
    for scenario in scenarios["scenarios"]:
        properties = json.dumps(scenario["expected_properties"], ensure_ascii=False)
        for old_value in ("understand", "familiar", "proficient", "expert"):
            assert old_value not in properties, (
                f"{scenario['scenario_id']} 仍引用旧五级熟练度 {old_value}"
            )


def test_scenarios_cover_required_categories(scenarios: dict) -> None:
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
    """同一输入同一变换两次执行结果一致（含锚点映射与变化区域）。"""
    for scenario in scenarios["scenarios"]:
        base = scenario["base_input"]
        first = apply_transformation(base, scenario["transformation"])
        second = apply_transformation(base, scenario["transformation"])
        assert first.text == second.text
        assert first.anchor_map == second.anchor_map
        assert first.changed_regions == second.changed_regions


def test_format_bullets_keeps_alnum_sequence() -> None:
    """格式变换不得改变字母数字序列（覆盖/证据锚点稳定）。"""
    text = "# 示例岗位\n\n1. 熟悉技术甲和框架乙。\n- 具备能力丙使用经验者优先。"
    result = apply_transformation(text, {"type": "format_bullets"})

    def alnum(value: str) -> str:
        return "".join(ch for ch in value if ch.isalnum())

    assert alnum(text) == alnum(result.text)


def test_reorder_paragraphs_is_reproducible_with_seed() -> None:
    """段落重排按固定种子可复现。"""
    text = "# 示例岗位\n\n负责能力甲体系建设。\n\n熟悉技术甲。\n\n本科及以上学历。"
    transformation = {"type": "reorder_paragraphs", "seed": 20260803}
    first = apply_transformation(text, transformation)
    second = apply_transformation(text, transformation)
    assert first.text == second.text
    assert "# 示例岗位" in first.text.split("\n\n")[0]
    assert set(first.text.split("\n\n")) == set(text.split("\n\n"))


def test_text_replace_anchors_one_to_one() -> None:
    """text_replace 变换产生一对一锚点映射，变化区域指向目标句。"""
    text = "# 示例岗位\n\n1. 熟悉技术甲和框架乙。\n2. 具备能力丙使用经验者优先。"
    result = apply_transformation(
        text,
        {"type": "text_replace", "replacements": [{"find": "熟悉", "replace": "精通"}]},
    )
    base_anchors = anchor_ids(text)
    assert base_anchors[1] in result.anchor_map
    assert len(result.anchor_map[base_anchors[1]]) == 1
    assert base_anchors[1] in result.changed_regions
    # 未变化句子也在锚点映射中（恒等匹配）。
    assert base_anchors[0] in result.anchor_map


def test_split_sentence_anchors_one_to_many() -> None:
    """一句拆两句产生一对多锚点映射（测试 16 的变换面）。"""
    text = "# 示例岗位\n\n1. 熟悉技术甲和框架乙。"
    result = apply_transformation(
        text,
        {
            "type": "text_replace",
            "replacements": [
                {"find": "1. 熟悉技术甲和框架乙。", "replace": "1. 熟悉技术甲。2. 掌握框架乙。"}
            ],
        },
    )
    base_anchor = anchor_ids(text)[1]
    assert base_anchor in result.anchor_map
    assert len(result.anchor_map[base_anchor]) == 2
    transformed_anchors = anchor_ids(result.text)
    assert set(result.anchor_map[base_anchor]) == {
        transformed_anchors[1],
        transformed_anchors[2],
    }
    assert base_anchor in result.changed_regions


def test_duplicate_sentence_anchors_with_occurrence() -> None:
    """重复句通过 occurrence 锚点区分，不互相覆盖（测试 13 的变换面）。"""
    text = "具备能力丙使用经验者优先。"
    result = apply_transformation(
        text, {"type": "duplicate_sentence", "target": "具备能力丙使用经验者优先。"}
    )
    anchors = anchor_ids(result.text)
    assert len(anchors) == 2
    assert anchors[0] != anchors[1]  # 第二个带 occurrence 后缀
    assert anchors[0] in result.changed_regions


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


def test_anchor_map_covers_all_unchanged_sentences() -> None:
    """追加无关内容时原有句子全部保持恒等锚点映射。"""
    text = "# 示例岗位\n\n熟悉技术甲。"
    result = apply_transformation(
        text,
        {"type": "append_text", "text": "\n\n## 工作地点\n北京市海淀区。"},
    )
    for anchor in anchor_ids(text):
        assert anchor in result.anchor_map
    assert not result.changed_regions
    assert len(split_sentences(result.text)) == len(split_sentences(text)) + 2
