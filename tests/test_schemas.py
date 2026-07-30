"""该模块验证Schema V2的逻辑组、年限范围和旧字段兼容规则。"""

import pytest
from pydantic import ValidationError

from app.schemas import JobExtractionResult, RequirementItem


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
    """验证旧Golden字段仍能读取，但新输出只保留Schema V2字段名。"""
    payload = requirement_payload()
    payload.pop("min_years")
    payload["years_required"] = 3

    requirement = RequirementItem.model_validate(payload)
    serialized = requirement.model_dump(mode="json")

    assert requirement.min_years == 3
    assert serialized["min_years"] == 3
    assert "years_required" not in serialized
