"""该模块提供抽取结果比较的确定性名称相似度工具（仅被 extraction_validation 引用）。

旧人工标准答案评测（Gold/F1）与旧评测方法不再维护，历史由 Git 保存。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from app.schemas import RequirementItem, ResponsibilityItem


def normalize_item_label(text: str) -> str:
    """移除名称中的格式符号但保留技术标识，用于可解释的相似度计算。"""
    normalized = unicodedata.normalize("NFKC", text).lower()
    return re.sub(r"[^0-9a-z+#.\u4e00-\u9fff]", "", normalized)


def item_label(item: RequirementItem | ResponsibilityItem) -> str:
    """统一取得要求的raw_name或职责的name，供同一匹配算法处理。"""
    return item.raw_name if isinstance(item, RequirementItem) else item.name


def item_name_similarity(expected: str, predicted: str) -> float:
    """计算保留专有技术词约束的名称相似度，避免把泛化概念误判为命中。"""
    expected_name = normalize_item_label(expected)
    predicted_name = normalize_item_label(predicted)
    if not expected_name or not predicted_name:
        return 0.0
    if expected_name == predicted_name:
        return 1.0

    # Python、LangChain等专有英文词必须仍出现在预测名称中，通用AI术语不作硬约束。
    generic_tokens = {"ai", "agent", "llm"}
    expected_tokens = {
        token
        for token in re.findall(r"[a-z][a-z0-9.+#-]*", expected_name)
        if token not in generic_tokens
    }
    predicted_tokens = set(re.findall(r"[a-z][a-z0-9.+#-]*", predicted_name))
    if not expected_tokens.issubset(predicted_tokens):
        return 0.0

    similarity = SequenceMatcher(None, expected_name, predicted_name).ratio()
    if expected_name in predicted_name or predicted_name in expected_name:
        containment = min(len(expected_name), len(predicted_name)) / max(
            len(expected_name), len(predicted_name)
        )
        similarity = max(similarity, min(1.0, containment + 0.2))
    return similarity
