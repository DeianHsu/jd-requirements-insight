"""该模块验证确定性名称相似度工具（evaluation 模块当前唯一保留职责）。"""

from app.evaluation import item_label, item_name_similarity
from app.schemas import (
    RequirementCategory,
    RequirementImportance,
    RequirementItem,
)


def make_requirement(name: str) -> RequirementItem:
    """构造最小合法要求项。"""
    return RequirementItem(
        raw_name=name,
        category=RequirementCategory.PROGRAMMING_LANGUAGE,
        importance=RequirementImportance.MUST,
        evidence="熟悉Python",
        confidence=1.0,
    )


def test_item_name_similarity_keeps_specific_technology_boundary() -> None:
    """专有技术词必须保留，泛化概念不算命中。"""
    assert item_name_similarity("Python", "Python") == 1.0
    assert item_name_similarity("Python", "精通Python") >= 0.55
    assert item_name_similarity("LangChain框架使用经验", "Agent框架使用经验") == 0.0
    assert item_name_similarity("Python", "编程语言") == 0.0


def test_item_name_similarity_tolerates_whitespace_and_case() -> None:
    """名称相似度容忍空白与大小写差异。"""
    assert item_name_similarity("  Python ", "python") == 1.0


def test_item_label_extracts_raw_name() -> None:
    """要求项取 raw_name。"""
    requirement = make_requirement("技术甲")

    assert item_label(requirement) == "技术甲"


def test_item_name_similarity_returns_zero_for_empty() -> None:
    """空名称相似度为 0，不产生除零。"""
    assert item_name_similarity("", "Python") == 0.0
    assert item_name_similarity("Python", "") == 0.0
