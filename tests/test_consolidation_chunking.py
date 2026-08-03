"""验证P0-4分阶段/受控分块输出边界：模拟大响应截断并驱动分批调用。

本文件先于实现编写：旧实现把全部实例放入单次完整请求，模拟截断的
假客户端会让测试失败；分阶段/分块实现后，任何单次映射请求的实例数
都不会超过分块上限，测试转为通过。
"""

import json

import pytest

from app.consolidation import (
    ConsolidationError,
    consolidate_with_correction,
)
from app.requirement_consolidation import (
    RequirementConsolidationInput,
    RequirementOccurrence,
)
from app.schemas import RequirementItem


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


def make_input(instance_count: int) -> RequirementConsolidationInput:
    """构造指定数量、来自多份JD的要求实例，模拟完整归并语料池规模。"""
    names = ["能力甲", "能力乙", "能力丙"]
    return RequirementConsolidationInput(
        occurrences=[
            RequirementOccurrence(
                requirement_id=requirement_id,
                job_id=100 + (requirement_id % 3),
                extraction_id=1000 + requirement_id,
                extractor_version="test-model|prompt:1.0|schema:2.0",
                source_hash=f"{requirement_id:064x}",
                source_file=f"job-{requirement_id % 3 + 1}.md",
                requirement=requirement(
                    f"{names[requirement_id % 3]}使用经验",
                    f"具备{names[requirement_id % 3]}使用经验。",
                ),
            )
            for requirement_id in range(1, instance_count + 1)
        ]
    )


class TruncationSimulatingClient:
    """模拟真实模型的输出截断：映射输出规模随请求实例数增长。

    旧实现的单次完整请求要求同时输出canonical_requirements、mappings和
    relations，实例数超过上限时返回截断的非法JSON；分阶段实现只在小块的
    映射请求中输出mappings，标准项与关系轮输出规模小，不受该限制。
    """

    def __init__(self, max_instances_per_request: int = 50) -> None:
        """保存截断阈值并初始化每次请求的实例数记录。"""
        self.max_instances_per_request = max_instances_per_request
        self.requested_instance_counts: list[int] = []

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        """返回合法JSON；映射请求超过阈值时模拟约50KB截断。"""
        payload = json.loads(user_prompt)
        task = payload["task"]
        instance_count = len(payload.get("requirements", []))
        self.requested_instance_counts.append(instance_count)
        if (
            ("只输出mappings" in task or "执行跨JD原子要求归并" in task)
            and instance_count > self.max_instances_per_request
        ):
            # 模拟截断：返回中途截断的非法JSON。
            return '{"mappings": [{"requirement_id": 1, "canonical_requirement_id": "cr-1", '
        return self._valid_response(payload)

    def _valid_response(self, payload: dict[str, object]) -> str:
        """按请求阶段生成合法响应。"""
        task = payload["task"]
        if "只输出canonical_requirements" in task:
            return json.dumps(
                {
                    "canonical_requirements": [
                        {
                            "canonical_requirement_id": "cr-1",
                            "canonical_name": "统一测试条件",
                            "source_requirement_ids": [
                                item["id"] for item in payload["requirements"]
                            ],
                            "rationale": "测试归并",
                            "confidence": 0.9,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        if "只输出mappings" in task:
            return json.dumps(
                {
                    "mappings": [
                        {
                            "requirement_id": item["id"],
                            "canonical_requirement_id": "cr-1",
                            "rationale": "测试映射",
                            "confidence": 0.9,
                        }
                        for item in payload["requirements"]
                    ]
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"未知任务：{task}")


class UnknownCanonicalClient(TruncationSimulatingClient):
    """模拟第二块映射请求引用不存在的标准要求项ID的模型错误。"""

    def __init__(self) -> None:
        """初始化截断模拟并记录映射请求序号。"""
        super().__init__()
        self.mapping_call = 0

    def _valid_response(self, payload: dict[str, object]) -> str:
        """返回合法响应，但第二块映射引用未知标准要求项。"""
        task = payload["task"]
        if "只输出mappings" in task:
            self.mapping_call += 1
            canonical_requirement_id = (
                "missing-cr" if self.mapping_call == 2 else "cr-1"
            )
            return json.dumps(
                {
                    "mappings": [
                        {
                            "requirement_id": item["id"],
                            "canonical_requirement_id": canonical_requirement_id,
                            "rationale": "测试映射",
                            "confidence": 0.9,
                        }
                        for item in payload["requirements"]
                    ]
                },
                ensure_ascii=False,
            )
        return super()._valid_response(payload)


def test_large_input_avoids_truncation_via_chunked_mapping_requests() -> None:
    """验证149实例规模通过分块映射避免单次大响应截断，且覆盖完整。"""
    chunk_size = 50  # 与实现的映射分块上限常量保持一致
    client = TruncationSimulatingClient(max_instances_per_request=chunk_size)
    consolidation_input = make_input(149)

    result, raw = consolidate_with_correction(consolidation_input, client)

    assert len(result.mappings) == 149
    assert len(raw["mappings"]) == 149
    mapping_request_counts = [
        count
        for count in client.requested_instance_counts
        if count <= chunk_size
    ]
    assert sum(mapping_request_counts) == 149
    assert len(mapping_request_counts) == 3
    assert max(mapping_request_counts) <= chunk_size


def test_chunk_boundary_crosses_into_multiple_mapping_requests() -> None:
    """验证实例数跨过块边界时映射请求被拆成多块且合成结果完整。"""
    chunk_size = 50  # 与实现的映射分块上限常量保持一致
    client = TruncationSimulatingClient()
    consolidation_input = make_input(chunk_size * 2 + 1)

    result, _ = consolidate_with_correction(consolidation_input, client)

    assert len(result.mappings) == chunk_size * 2 + 1
    mapping_request_counts = [
        count for count in client.requested_instance_counts if count <= chunk_size
    ]
    assert len(mapping_request_counts) == 3


def test_cross_chunk_unknown_canonical_reference_fails_global_validation() -> None:
    """验证某块映射引用未知标准项ID时，块级校验拒绝并反馈修正。"""
    client = UnknownCanonicalClient()
    consolidation_input = make_input(101)

    with pytest.raises(
        ConsolidationError,
        match="映射引用了标准要求项清单中不存在的ID",
    ):
        consolidate_with_correction(consolidation_input, client, max_attempts=1)
