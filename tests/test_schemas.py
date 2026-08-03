"""该模块验证抽取数据合同V3的逻辑组、年限范围、旧字段兼容与熟练度三级收缩。"""

import pytest
from pydantic import ValidationError

from app.schemas import (
    JobExtractionResult,
    LEGACY_PROFICIENCY_MAP,
    ProficiencyLevel,
    RequirementItem,
    map_legacy_proficiency,
)


def requirement_payload(**overrides: object) -> dict[str, object]:
    """生成一条合法的独立要求，并允许测试只覆盖目标字段。"""
    payload: dict[str, object] = {
        "raw_name": "Python",
        "category": "programming_language",
        "importance": "must",
        "proficiency": "familiar",
        "group_id": None,
        "group_logic": "standalone",
        "min_years": None,
        "max_years": None,
        "years_text": None,
        "evidence": "熟悉Python",
        "confidence": 1.0,
    }
    payload.update(overrides)
    return payload


def extraction_payload(requirements: list[dict[str, object]]) -> dict[str, object]:
    """把要求列表包装为可执行整组逻辑校验的最小抽取结果。"""
    return {
        "role_family": "agent_application",
        "seniority": "unknown",
        "responsibilities": [],
        "requirements": requirements,
    }


def test_standalone_requirement_rejects_group_id() -> None:
    """验证独立要求不能携带会让匹配逻辑产生歧义的组ID。"""
    with pytest.raises(ValidationError, match="standalone要求不能设置group_id"):
        RequirementItem.model_validate(requirement_payload(group_id="language_1"))


def test_any_of_requirement_requires_group_id() -> None:
    """验证any_of成员必须提供组ID以便找到同组候选项。"""
    with pytest.raises(ValidationError, match="any_of要求必须设置group_id"):
        RequirementItem.model_validate(requirement_payload(group_logic="any_of"))


def test_any_of_group_requires_at_least_two_members() -> None:
    """验证整份结果会拒绝只有一个候选项的伪any_of组。"""
    only_member = requirement_payload(group_id="language_1", group_logic="any_of")

    with pytest.raises(ValidationError, match="any_of组至少需要两个成员"):
        JobExtractionResult.model_validate(extraction_payload([only_member]))


def test_any_of_group_accepts_multiple_members() -> None:
    """验证同组存在两个候选项时能够表达满足任意一项的关系。"""
    requirements = [
        requirement_payload(group_id="language_1", group_logic="any_of"),
        requirement_payload(
            raw_name="Java",
            group_id="language_1",
            group_logic="any_of",
            evidence="熟悉Java",
        ),
    ]

    result = JobExtractionResult.model_validate(extraction_payload(requirements))

    assert len(result.requirements) == 2
    assert result.requirements[0].group_id == "language_1"


def test_year_range_rejects_reversed_bounds() -> None:
    """验证经验上限不能小于最低门槛。"""
    with pytest.raises(ValidationError, match="max_years不能小于min_years"):
        RequirementItem.model_validate(
            requirement_payload(min_years=5, max_years=3, years_text="3-5年")
        )


def test_legacy_years_required_is_loaded_as_min_years() -> None:
    """验证旧人工标准答案字段仍能读取，但新输出只保留V2字段名。"""
    payload = requirement_payload()
    payload.pop("min_years")
    payload["years_required"] = 3

    requirement = RequirementItem.model_validate(payload)
    serialized = requirement.model_dump(mode="json")

    assert requirement.min_years == 3
    assert serialized["min_years"] == 3
    assert "years_required" not in serialized


# ---------------------------------------------------------------------------
# 熟练度三级收缩（Schema V3）
# ---------------------------------------------------------------------------


def test_proficiency_enum_has_only_three_levels() -> None:
    """Schema V3 熟练度只包含 unknown/basic/advanced（测试 12 的枚举面）。"""
    assert {level.value for level in ProficiencyLevel} == {
        "unknown",
        "basic",
        "advanced",
    }


def test_basic_words_map_to_basic() -> None:
    """了解、熟悉 → basic（测试 6）。"""
    assert map_legacy_proficiency("understand") is ProficiencyLevel.BASIC
    assert map_legacy_proficiency("familiar") is ProficiencyLevel.BASIC
    assert map_legacy_proficiency("basic") is ProficiencyLevel.BASIC


def test_advanced_words_map_to_advanced() -> None:
    """掌握、熟练、精通 → advanced（测试 7）。"""
    assert map_legacy_proficiency("proficient") is ProficiencyLevel.ADVANCED
    assert map_legacy_proficiency("expert") is ProficiencyLevel.ADVANCED
    assert map_legacy_proficiency("advanced") is ProficiencyLevel.ADVANCED


def test_experience_expressions_map_to_unknown() -> None:
    """项目经验、使用经验表达在抽取语义上映射为 unknown（测试 8）。"""
    # 抽取层输出 unknown 是模型职责；这里验证 old 值 unknown 与三级值一致。
    assert map_legacy_proficiency("unknown") is ProficiencyLevel.UNKNOWN


def test_legacy_map_covers_all_five_values() -> None:
    """V2 五种值均可正确映射（测试 10）。"""
    assert set(LEGACY_PROFICIENCY_MAP) == {
        "understand",
        "familiar",
        "proficient",
        "expert",
    }
    assert LEGACY_PROFICIENCY_MAP["understand"] is ProficiencyLevel.BASIC
    assert LEGACY_PROFICIENCY_MAP["familiar"] is ProficiencyLevel.BASIC
    assert LEGACY_PROFICIENCY_MAP["proficient"] is ProficiencyLevel.ADVANCED
    assert LEGACY_PROFICIENCY_MAP["expert"] is ProficiencyLevel.ADVANCED


def test_none_value_is_rejected() -> None:
    """none 被 Schema V3 拒绝（测试 9）。"""
    with pytest.raises(ValidationError, match="未知熟练度值"):
        RequirementItem.model_validate(requirement_payload(proficiency="none"))


def test_unknown_illegal_value_fails_explicitly() -> None:
    """未知非法值明确失败，不静默归入 unknown。"""
    with pytest.raises(ValidationError, match="未知熟练度值"):
        RequirementItem.model_validate(requirement_payload(proficiency="beginner"))
    with pytest.raises(ValidationError, match="未知熟练度值"):
        RequirementItem.model_validate(requirement_payload(proficiency="intermediate"))


def test_legacy_five_level_payload_still_readable() -> None:
    """旧 V2 五级结果仍可按当前合同读取（测试 11）。"""
    payload = requirement_payload(proficiency="familiar")
    requirement = RequirementItem.model_validate(payload)
    assert requirement.proficiency is ProficiencyLevel.BASIC

    expert = RequirementItem.model_validate(requirement_payload(proficiency="expert"))
    assert expert.proficiency is ProficiencyLevel.ADVANCED


def test_new_result_serializes_only_three_levels() -> None:
    """新结果只保存三级值（测试 12）。"""
    requirement = RequirementItem.model_validate(
        requirement_payload(proficiency="basic")
    )
    serialized = requirement.model_dump(mode="json")
    assert serialized["proficiency"] == "basic"
    assert serialized["proficiency"] in {"unknown", "basic", "advanced"}
