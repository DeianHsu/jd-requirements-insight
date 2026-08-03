"""抽取验证模块：确定性合同检查、运行间比较与验收报告（P0-3 新协议，DEC-015）。

本模块只做离线、确定性验证和运行间比较，不调用外部模型，也不读取人工
完整答案决定通过或失败。规则判定复用 `app/extraction_two_stage.py` 与
`app/extraction.py` 的现有校验（覆盖、span 对应、证据存在），不复制业务
规则；名称相似度只作为 diagnostic，不作为 hard gate。

证据三个层次必须分开表述：
- evidence existence：证据文本存在于原文（确定性校验）；
- evidence attribution：输出项能归属到候选块（确定性校验）；
- evidence semantic support：证据确实支持名称与字段判断（人工审计完成）。
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import combinations
from typing import Any

from app.evaluation import item_label, item_name_similarity
from app.extraction import ExtractionError, normalize_evidence, validate_evidence
from app.extraction_two_stage import (
    CandidateBlock,
    DiscoveryResult,
    _alnum_sequence,
    split_sentences,
    validate_discovery_coverage,
)
from app.ingestion import content_hash
from app.schemas import JobExtractionResult, RequirementItem


def compute_input_fingerprint(raw_text: str) -> str:
    """对规范化后的输入文本计算 SHA-256，作为运行身份的一部分。"""
    return content_hash(raw_text)


def sentence_anchor(sentence: str) -> str:
    """计算句子锚点：字母数字序列；重复句由调用方追加 occurrence。"""
    return _alnum_sequence(sentence)


def anchor_ids(raw_text: str) -> list[str]:
    """按分句顺序返回带 occurrence 的稳定锚点 ID 列表（重复句不互相覆盖）。"""
    counts: Counter[str] = Counter()
    anchors: list[str] = []
    for sentence in split_sentences(raw_text):
        base = sentence_anchor(sentence)
        occurrence = counts[base]
        counts[base] += 1
        anchors.append(f"{base}#{occurrence}" if occurrence else base)
    return anchors


def resolve_anchor(raw_text: str, anchor_text: str) -> str | None:
    """在原文中解析场景文件声明的锚点文本，返回稳定锚点 ID；找不到返回 None。

    支持省略编号/修饰前缀的子串匹配（如 "熟悉技术甲和框架乙" 匹配分句
    "1. 熟悉技术甲和框架乙"），但以完整句子优先。
    """
    target = sentence_anchor(anchor_text)
    if not target:
        return None
    anchors = anchor_ids(raw_text)
    for anchor in anchors:
        if anchor == target or anchor.startswith(f"{target}#"):
            return anchor
    for anchor in anchors:
        if target in anchor:
            return anchor
    return None


@dataclass(frozen=True)
class TransformationResult:
    """确定性变换的结果：新文本 + base→transformed 锚点映射 + 预期变化区域。

    anchor_map 支持一对多（一句拆两句等句界变化场景）；
    changed_regions 是字段/结构允许发生预期变化的 base 锚点集合。
    """

    text: str
    transformation_type: str
    anchor_map: dict[str, list[str]]
    changed_regions: frozenset[str]


@dataclass(frozen=True)
class DiscoveryCoverageReport:
    """发现段覆盖检查结果：分句覆盖、唯一覆盖与 span 对应。"""

    sentence_count: int
    block_count: int
    covered_sentence_count: int
    duplicate_covered_sentences: list[int]
    missing_sentences: list[int]
    unexpected_sentences: list[int]
    invalid_span_blocks: list[str]

    @property
    def coverage(self) -> float:
        """返回分句覆盖比例，0 分句时按 0 处理（由调用方另行判定）。"""
        return self.covered_sentence_count / self.sentence_count if self.sentence_count else 0.0

    @property
    def passed(self) -> bool:
        """覆盖 100%、无重复、无未知分句且所有 span 与分句索引对应才通过。"""
        return (
            self.coverage == 1.0
            and not self.duplicate_covered_sentences
            and not self.missing_sentences
            and not self.unexpected_sentences
            and not self.invalid_span_blocks
        )


def _alnum(text: str) -> str:
    """提取规范化文本的字母数字序列，用于容忍标点差异的包含校验。"""
    return "".join(ch for ch in normalize_evidence(text) if ch.isalnum())


def _evidence_hits_block(evidence: str, block: CandidateBlock) -> bool:
    """判断输出项证据是否命中候选块：任一方向包含即视为命中。"""
    evidence_alnum = _alnum(evidence)
    span_alnum = _alnum(block.source_span)
    return bool(evidence_alnum) and (evidence_alnum in span_alnum or span_alnum in evidence_alnum)


def check_discovery_coverage(
    discovery: DiscoveryResult, raw_text: str
) -> DiscoveryCoverageReport:
    """收集式覆盖检查：规则判定复用 validate_discovery_coverage，另产出细粒度统计。"""
    sentences = split_sentences(raw_text)
    all_indexes = [
        index for block in discovery.blocks for index in block.sentence_indexes
    ]
    counts: dict[int, int] = {}
    for index in all_indexes:
        counts[index] = counts.get(index, 0) + 1
    duplicate = sorted(index for index, count in counts.items() if count > 1)

    expected = set(range(len(sentences)))
    covered = set(counts)
    missing = sorted(expected - covered)
    unexpected = sorted(covered - expected)

    invalid_span_blocks: list[str] = []
    for block in discovery.blocks:
        claimed_sequence = "".join(
            _alnum_sequence(sentences[index]) for index in block.sentence_indexes
        )
        if _alnum_sequence(block.source_span) != claimed_sequence:
            invalid_span_blocks.append(block.block_id)

    # 规则判定仍走正式校验入口，保证与运行时行为一致（只用于判定，不抛错）。
    try:
        validate_discovery_coverage(discovery, raw_text)
    except ExtractionError:
        pass

    return DiscoveryCoverageReport(
        sentence_count=len(sentences),
        block_count=len(discovery.blocks),
        covered_sentence_count=len(covered),
        duplicate_covered_sentences=duplicate,
        missing_sentences=missing,
        unexpected_sentences=unexpected,
        invalid_span_blocks=invalid_span_blocks,
    )


@dataclass(frozen=True)
class ExtractionContractReport:
    """一份抽取结果的合同检查：Schema、覆盖、证据、类型归属、逻辑组与身份。"""

    schema_valid: bool
    schema_errors: list[str]
    coverage: DiscoveryCoverageReport
    unprocessed_blocks: list[str]
    type_violations: list[str]
    excluded_violations: list[str]
    ambiguous_evidence: list[str]
    evidence_unattributed_items: list[str]
    evidence_violations: list[str]
    invalid_groups: list[str]
    identity_missing: list[str]
    produced_kinds: dict[str, list[str]]

    @property
    def identity_complete(self) -> bool:
        """结果版本和输入身份字段齐全才算身份完整。"""
        return not self.identity_missing

    @property
    def passed(self) -> bool:
        """全部 hard gate 级合同违规为零才算通过。"""
        return (
            self.schema_valid
            and not self.evidence_violations
            and not self.invalid_groups
            and self.coverage.passed
            and not self.unprocessed_blocks
            and not self.type_violations
            and not self.excluded_violations
            and not self.evidence_unattributed_items
            and self.identity_complete
        )


@dataclass
class RunSnapshot:
    """一次抽取运行的可比较快照：发现段结果、最终结果与原文。"""

    discovery: DiscoveryResult
    result: JobExtractionResult
    raw_text: str
    raw_payload: dict[str, object] | None = None


def check_payload_schema(
    payload: dict[str, object],
) -> tuple[bool, list[str], list[str]]:
    """检查模型原始输出是否符合抽取数据合同，返回（合法、全部错误、逻辑组错误）。

    旧 Schema V2 五级 proficiency 值经 RequirementItem 校验器确定性映射，
    不会因为旧值导致合同误判。
    """
    try:
        JobExtractionResult.model_validate(payload)
        return True, [], []
    except Exception as exc:  # 收集合同错误而不是中断检查
        message = str(exc)
        group_errors = (
            [message] if ("any_of" in message or "group" in message) else []
        )
        return False, [message], group_errors


def _result_items(result: JobExtractionResult) -> list[tuple[str, int, Any]]:
    """返回带类型标签和索引的输出项列表，供块归属与配对使用。"""
    return [
        *[("responsibility", index, item) for index, item in enumerate(result.responsibilities)],
        *[("requirement", index, item) for index, item in enumerate(result.requirements)],
    ]


def _block_anchor_ids(block: CandidateBlock, raw_text: str) -> tuple[str, ...]:
    """返回候选块覆盖分句的稳定锚点 ID 列表（含 occurrence，重复句不覆盖）。"""
    anchors = anchor_ids(raw_text)
    return tuple(
        anchors[index]
        for index in block.sentence_indexes
        if 0 <= index < len(anchors)
    )


def _items_for_blocks(
    items: list[tuple[str, int, Any]], blocks: list[CandidateBlock]
) -> list[tuple[str, int, Any]]:
    """筛选证据命中任一候选块的输出项（同一项不重复计数）。"""
    matched: list[tuple[str, int, Any]] = []
    seen: set[tuple[str, int]] = set()
    for entry in items:
        if any(_evidence_hits_block(entry[2].evidence, block) for block in blocks):
            seen.add((entry[0], entry[1]))
    for entry in items:
        if (entry[0], entry[1]) in seen:
            matched.append(entry)
    return matched


def check_contract(
    discovery: DiscoveryResult,
    result: JobExtractionResult,
    raw_text: str,
    identity: dict[str, str] | None = None,
    raw_payload: dict[str, object] | None = None,
) -> ExtractionContractReport:
    """对一次运行执行全部确定性合同检查，并复用现有校验判定规则。"""
    schema_valid = True
    schema_errors: list[str] = []
    invalid_groups: list[str] = []
    if raw_payload is not None:
        schema_valid, schema_errors, invalid_groups = check_payload_schema(raw_payload)

    # 证据存在性与连续性复用正式校验（收集错误而不是抛错）。
    evidence_violations: list[str] = []
    try:
        validate_evidence(result, raw_text)
    except ExtractionError as exc:
        evidence_violations.append(str(exc))

    coverage = check_discovery_coverage(discovery, raw_text)

    # 类型化候选块覆盖：responsibility 块必须产出 responsibility；
    # requirement 块必须产出 requirement；mixed 块允许两者并记录实际产出；
    # excluded 块不得产出 must/preferred requirement。
    items = _result_items(result)
    active_blocks = [block for block in discovery.blocks if block.kind != "excluded"]
    produced_kinds: dict[str, list[str]] = {}
    unprocessed_blocks: list[str] = []
    type_violations: list[str] = []
    excluded_violations: list[str] = []
    for block in discovery.blocks:
        block_items = [entry for entry in items if _evidence_hits_block(entry[2].evidence, block)]
        produced_kinds[block.block_id] = [entry[0] for entry in block_items]
        if block.kind == "excluded":
            for label, _, item in block_items:
                if (
                    label == "requirement"
                    and isinstance(item, RequirementItem)
                    and item.importance.value in ("must", "preferred")
                ):
                    excluded_violations.append(
                        f"{block.block_id} 从排除内容产出 {item.importance.value} 要求"
                    )
            continue
        if not block_items:
            unprocessed_blocks.append(block.block_id)
            continue
        if block.kind == "responsibility" and not any(
            entry[0] == "responsibility" for entry in block_items
        ):
            type_violations.append(
                f"{block.block_id} 是 responsibility 块但未产出 responsibility"
            )
        if block.kind == "requirement" and not any(
            entry[0] == "requirement" for entry in block_items
        ):
            type_violations.append(
                f"{block.block_id} 是 requirement 块但未产出 requirement"
            )

    # 无依据明确事实：输出项证据不命中任何非 excluded 候选块（含只命中 excluded 块）。
    evidence_unattributed_items: list[str] = []
    for label, index, item in items:
        if not any(_evidence_hits_block(item.evidence, block) for block in active_blocks):
            evidence_unattributed_items.append(f"{label}[{index}]")

    # evidence 歧义：输出项证据命中多个候选块时报告（归属不确定）。
    ambiguous_evidence: list[str] = []
    for label, index, item in items:
        hit_blocks = [
            block.block_id
            for block in discovery.blocks
            if _evidence_hits_block(item.evidence, block)
        ]
        if len(hit_blocks) > 1:
            ambiguous_evidence.append(f"{label}[{index}] 命中 {hit_blocks}")

    identity_missing: list[str] = []
    if identity is not None:
        for key in ("model", "prompt_version", "schema_version", "input_fingerprint"):
            if not identity.get(key):
                identity_missing.append(key)

    return ExtractionContractReport(
        schema_valid=schema_valid,
        schema_errors=schema_errors,
        coverage=coverage,
        unprocessed_blocks=unprocessed_blocks,
        type_violations=type_violations,
        excluded_violations=excluded_violations,
        ambiguous_evidence=ambiguous_evidence,
        evidence_unattributed_items=evidence_unattributed_items,
        evidence_violations=evidence_violations,
        invalid_groups=invalid_groups,
        identity_missing=identity_missing,
        produced_kinds=produced_kinds,
    )


# ---------------------------------------------------------------------------
# 候选块对齐
# ---------------------------------------------------------------------------


def _span_occurrence_keys(blocks: list[CandidateBlock]) -> list[tuple[str, int]]:
    """按出现顺序给相同 span 分配 occurrence，避免字典覆盖丢失重复块。"""
    seen: Counter[str] = Counter()
    keys: list[tuple[str, int]] = []
    for block in blocks:
        span = _alnum_sequence(block.source_span)
        occurrence = seen[span]
        seen[span] += 1
        keys.append((span, occurrence))
    return keys


def _index_keys(blocks: list[CandidateBlock]) -> dict[tuple[int, ...], CandidateBlock]:
    """按 sentence_indexes 建立唯一映射（相同输入时句界不变）。"""
    mapping: dict[tuple[int, ...], CandidateBlock] = {}
    for block in blocks:
        mapping.setdefault(tuple(block.sentence_indexes), block)
    return mapping


def _coverage_keys(
    blocks: list[CandidateBlock], raw_text: str
) -> dict[tuple[str, frozenset[str]], list[CandidateBlock]]:
    """按（kind, 覆盖分句锚点集合）建立宽松匹配键，用于句界漂移后的兜底对齐。"""
    anchors = anchor_ids(raw_text)
    mapping: dict[tuple[str, frozenset[str]], list[CandidateBlock]] = {}
    for block in blocks:
        covered = frozenset(
            anchors[index] for index in block.sentence_indexes if 0 <= index < len(anchors)
        )
        mapping.setdefault((block.kind, covered), []).append(block)
    return mapping


def align_blocks(
    base_blocks: list[CandidateBlock],
    variant_blocks: list[CandidateBlock],
    base_raw_text: str,
    variant_raw_text: str,
    transformation: TransformationResult | None = None,
) -> tuple[
    list[tuple[CandidateBlock, list[CandidateBlock]]],
    list[str],
    list[str],
]:
    """对齐两次运行的候选块。

    相同输入（transformation=None）优先级：sentence_indexes → 规范化
    source_span（occurrence）→（kind, 分句覆盖集合）；变形输入使用
    TransformationResult.anchor_map 定位（一个 base 块可对应多个 variant 块）。

    返回（pairs, unaligned_base_ids, unaligned_variant_ids）。
    """
    used_variant: set[str] = set()
    pairs: list[tuple[CandidateBlock, list[CandidateBlock]]] = []

    if transformation is not None:
        # variant 块 -> 覆盖锚点集合
        variant_by_anchor: dict[str, list[CandidateBlock]] = {}
        for block in variant_blocks:
            for anchor in _block_anchor_ids(block, variant_raw_text):
                variant_by_anchor.setdefault(anchor, []).append(block)
        for base_block in base_blocks:
            matched: list[CandidateBlock] = []
            seen: set[str] = set()
            for anchor in _block_anchor_ids(base_block, base_raw_text):
                for transformed_anchor in transformation.anchor_map.get(anchor, []):
                    for variant_block in variant_by_anchor.get(transformed_anchor, []):
                        if variant_block.block_id not in seen and (
                            variant_block.block_id not in used_variant
                            or variant_block.block_id in seen
                        ):
                            matched.append(variant_block)
                            seen.add(variant_block.block_id)
            if matched:
                pairs.append((base_block, matched))
                used_variant.update(block.block_id for block in matched)
            else:
                # 兜底：按覆盖锚点集合匹配（如 append_text 中未变化块）。
                for variant_block in variant_blocks:
                    if variant_block.block_id in used_variant:
                        continue
                    if set(_block_anchor_ids(base_block, base_raw_text)) & set(
                        _block_anchor_ids(variant_block, variant_raw_text)
                    ):
                        pairs.append((base_block, [variant_block]))
                        used_variant.add(variant_block.block_id)
                        break
    else:
        base_index_keys = _index_keys(base_blocks)
        variant_index_keys = _index_keys(variant_blocks)
        for indexes, base_block in base_index_keys.items():
            variant_block = variant_index_keys.get(indexes)
            if variant_block is not None and variant_block.block_id not in used_variant:
                pairs.append((base_block, [variant_block]))
                used_variant.add(variant_block.block_id)
        remaining_base = [
            block for block in base_blocks if not any(block is pair[0] for pair in pairs)
        ]
        remaining_variant = [
            block for block in variant_blocks if block.block_id not in used_variant
        ]
        base_span_keys = dict(zip([id(block) for block in remaining_base], _span_occurrence_keys(remaining_base)))
        variant_span_keys = dict(
            zip([id(block) for block in remaining_variant], _span_occurrence_keys(remaining_variant))
        )
        used_variant_spans: set[tuple[str, int]] = set()
        for base_block in remaining_base:
            span_key = base_span_keys[id(base_block)]
            for variant_block in remaining_variant:
                if variant_block.block_id in used_variant:
                    continue
                if variant_span_keys[id(variant_block)] == span_key:
                    pairs.append((base_block, [variant_block]))
                    used_variant.add(variant_block.block_id)
                    used_variant_spans.add(span_key)
                    break
        remaining_base = [
            block for block in remaining_base if not any(block is pair[0] for pair in pairs)
        ]
        remaining_variant = [
            block for block in remaining_variant if block.block_id not in used_variant
        ]
        base_coverage = _coverage_keys(remaining_base, base_raw_text)
        variant_coverage = _coverage_keys(remaining_variant, variant_raw_text)
        for key, base_group in base_coverage.items():
            variant_group = variant_coverage.get(key, [])
            for base_block, variant_block in zip(base_group, variant_group):
                if variant_block.block_id not in used_variant:
                    pairs.append((base_block, [variant_block]))
                    used_variant.add(variant_block.block_id)

    aligned_base_ids = {pair[0].block_id for pair in pairs}
    unaligned_base = sorted(
        block.block_id for block in base_blocks if block.block_id not in aligned_base_ids
    )
    unaligned_variant = sorted(
        block.block_id for block in variant_blocks if block.block_id not in used_variant
    )
    return pairs, unaligned_base, unaligned_variant


# ---------------------------------------------------------------------------
# 原子项配对与逻辑组结构
# ---------------------------------------------------------------------------


def _same_group_membership(
    base_item: RequirementItem,
    variant_item: RequirementItem,
    base_group_ids: dict[str, str],
    variant_group_ids: dict[str, str],
) -> bool:
    """比较两项在各自运行中的逻辑组结构身份（不依赖临时 group_id 字符串）。"""
    base_is_any = base_item.group_logic.value == "any_of" and base_item.group_id is not None
    variant_is_any = (
        variant_item.group_logic.value == "any_of" and variant_item.group_id is not None
    )
    if base_is_any != variant_is_any:
        return False
    if not base_is_any:
        return True  # 双方都是 standalone，结构身份一致
    return base_group_ids.get(base_item.group_id) == variant_group_ids.get(
        variant_item.group_id
    )


def _pair_score(
    base_item: Any,
    variant_item: Any,
    base_group_ids: dict[str, str],
    variant_group_ids: dict[str, str],
) -> float:
    """组内配对得分：字段一致性计数；名称相似度仅作辅助（权重 0.2）。"""
    if type(base_item) is not type(variant_item):
        return -1.0
    score = 0.0
    if isinstance(base_item, RequirementItem) and isinstance(variant_item, RequirementItem):
        score += float(base_item.category == variant_item.category)
        score += float(base_item.importance == variant_item.importance)
        score += float(base_item.proficiency == variant_item.proficiency)
        score += float(
            _same_group_membership(base_item, variant_item, base_group_ids, variant_group_ids)
        )
    score += 0.2 * item_name_similarity(item_label(base_item), item_label(variant_item))
    return score


def _group_ids_of(items: list[tuple[str, int, Any]]) -> dict[str, str]:
    """把任意 any_of 组映射为结构身份（按稳定成员身份签名）。

    返回 {group_id: 结构签名}；签名对 group_id 改名与成员顺序变化不敏感，
    成员身份 =（evidence 锚点, category, raw_name）。
    """
    groups: dict[str, list[str]] = {}
    for _, _, item in items:
        if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
            identity = f"{_alnum(item.evidence)}|{item.category.value}|{item.raw_name}"
            groups.setdefault(item.group_id, []).append(identity)
    signature_map: dict[str, str] = {}
    for group_id, identities in groups.items():
        signature_map[group_id] = json.dumps(sorted(identities), separators=(",", ":"))
    return signature_map


def _pair_items(
    base_items: list[tuple[str, int, Any]],
    variant_items: list[tuple[str, int, Any]],
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """重新设计配对：类型隔离 → evidence 锚点分组 → 组内确定性一对一最佳匹配。

    返回的索引是传入列表的位置索引；不按列表顺序 zip；输出顺序变化不产生
    假漂移；responsibility 与 requirement 永不配对。
    """
    base_by_type: dict[str, list[tuple[int, tuple[str, int, Any]]]] = {}
    variant_by_type: dict[str, list[tuple[int, tuple[str, int, Any]]]] = {}
    for position, entry in enumerate(base_items):
        base_by_type.setdefault(entry[0], []).append((position, entry))
    for position, entry in enumerate(variant_items):
        variant_by_type.setdefault(entry[0], []).append((position, entry))

    base_group_ids = _group_ids_of(base_items)
    variant_group_ids = _group_ids_of(variant_items)

    pairs: list[tuple[int, int]] = []
    used_base: set[int] = set()
    used_variant: set[int] = set()
    for item_type in set(base_by_type) & set(variant_by_type):
        base_typed = base_by_type[item_type]
        variant_typed = variant_by_type[item_type]
        # 按 evidence 锚点分组（组内项可自由交换顺序）。
        base_groups: dict[str, list[int]] = {}
        for local_index, (_, entry) in enumerate(base_typed):
            base_groups.setdefault(_alnum(entry[2].evidence), []).append(local_index)
        variant_groups: dict[str, list[int]] = {}
        for local_index, (_, entry) in enumerate(variant_typed):
            variant_groups.setdefault(_alnum(entry[2].evidence), []).append(local_index)

        for evidence_key in sorted(set(base_groups) & set(variant_groups)):
            base_local = base_groups[evidence_key]
            variant_local = variant_groups[evidence_key]
            # 组内贪心最佳匹配：按得分从高到低一对一。
            candidates = []
            for base_position, base_local_index in enumerate(base_local):
                for variant_position, variant_local_index in enumerate(variant_local):
                    score = _pair_score(
                        base_typed[base_local_index][1][2],
                        variant_typed[variant_local_index][1][2],
                        base_group_ids,
                        variant_group_ids,
                    )
                    if score < 0:
                        continue
                    candidates.append((score, base_position, variant_position))
            for _, base_position, variant_position in sorted(
                candidates, key=lambda item: (-item[0], item[1], item[2])
            ):
                base_global = base_typed[base_local[base_position]][0]
                variant_global = variant_typed[variant_local[variant_position]][0]
                if base_global in used_base or variant_global in used_variant:
                    continue
                pairs.append((base_global, variant_global))
                used_base.add(base_global)
                used_variant.add(variant_global)

        # 变形场景兜底：evidence 已变化的剩余项跨组做贪心字段匹配
        # （如文本替换后 evidence 变化但字段保持一致）。
        remaining_base = [
            (position, entry)
            for position, entry in base_typed
            if position not in used_base
        ]
        remaining_variant = [
            (position, entry)
            for position, entry in variant_typed
            if position not in used_variant
        ]
        fallback_candidates = []
        for base_position, (_, base_entry) in enumerate(remaining_base):
            for variant_position, (_, variant_entry) in enumerate(remaining_variant):
                score = _pair_score(
                    base_entry[2],
                    variant_entry[2],
                    base_group_ids,
                    variant_group_ids,
                )
                if score < 2.2:  # 至少两个字段一致（含 0.2 名称辅助）
                    continue
                fallback_candidates.append((score, base_position, variant_position))
        for _, base_position, variant_position in sorted(
            fallback_candidates, key=lambda item: (-item[0], item[1], item[2])
        ):
            base_global = remaining_base[base_position][0]
            variant_global = remaining_variant[variant_position][0]
            if base_global in used_base or variant_global in used_variant:
                continue
            pairs.append((base_global, variant_global))
            used_base.add(base_global)
            used_variant.add(variant_global)

    unmatched_base = set(range(len(base_items))) - used_base
    unmatched_variant = set(range(len(variant_items))) - used_variant
    return pairs, unmatched_base, unmatched_variant


def _field_agreement(
    base_items: list[tuple[str, int, Any]],
    variant_items: list[tuple[str, int, Any]],
    pairs: list[tuple[int, int]],
    field: str,
) -> float:
    """计算配对项在指定字段上的一致比例（职责项无要求字段，跳过）。"""
    compared = 0
    correct = 0
    for base_index, variant_index in pairs:
        base_item = base_items[base_index][2]
        variant_item = variant_items[variant_index][2]
        if not isinstance(base_item, RequirementItem) or not isinstance(
            variant_item, RequirementItem
        ):
            if field != "evidence":
                continue
        if field == "group_type":
            base_value = base_item.group_logic.value
            variant_value = variant_item.group_logic.value
        else:
            base_value = getattr(base_item, field)
            variant_value = getattr(variant_item, field)
        compared += 1
        correct += int(base_value == variant_value)
    # 无配对项视为无漂移（vacuous true），避免空块误报 invariance 失败。
    return correct / compared if compared else 1.0


def _pairwise_membership_agreement(
    base_items: list[tuple[str, int, Any]],
    variant_items: list[tuple[str, int, Any]],
    pairs: list[tuple[int, int]],
) -> float:
    """对配对项两两比较 co-membership：base 中同组 ⇔ variant 中同组。

    临时 group_id 字符串不参与比较；standalone 项视为各自独立组；
    聚类键使用输出项列表位置，与 pairs 索引空间一致。
    """
    def cluster_map(items: list[tuple[str, int, Any]]) -> dict[int, str]:
        groups: dict[str, list[int]] = {}
        for position, (_, _, item) in enumerate(items):
            if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
                groups.setdefault(item.group_id, []).append(position)
        membership: dict[int, str] = {}
        for group_id, positions in groups.items():
            for position in positions:
                membership[position] = f"any_of:{group_id}"
        for position, (_, _, item) in enumerate(items):
            membership.setdefault(position, f"standalone:{position}")
        return membership

    base_clusters = cluster_map(base_items)
    variant_clusters = cluster_map(variant_items)
    compared = 0
    correct = 0
    for (base_i, variant_i), (base_j, variant_j) in combinations(pairs, 2):
        base_same = base_clusters.get(base_i) == base_clusters.get(base_j)
        variant_same = variant_clusters.get(variant_i) == variant_clusters.get(variant_j)
        compared += 1
        correct += int(base_same == variant_same)
    return correct / compared if compared else 1.0


def _group_diagnostics(
    base_items: list[tuple[str, int, Any]],
    variant_items: list[tuple[str, int, Any]],
    pairs: list[tuple[int, int]],
) -> tuple[dict[str, str], int, int]:
    """逻辑组诊断：group_id 改名映射、单成员 any_of 数、跨组合并数。"""
    pair_map = dict(pairs)
    id_map: dict[str, str] = {}
    for base_index, variant_index in pairs:
        base_item = base_items[base_index][2]
        variant_item = variant_items[variant_index][2]
        if (
            isinstance(base_item, RequirementItem)
            and isinstance(variant_item, RequirementItem)
            and base_item.group_id
            and variant_item.group_id
            and base_item.group_logic.value == "any_of"
            and variant_item.group_logic.value == "any_of"
        ):
            id_map.setdefault(base_item.group_id, variant_item.group_id)

    def single_member_any_of(items: list[tuple[str, int, Any]]) -> int:
        groups: dict[str, int] = {}
        for _, _, item in items:
            if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
                groups[item.group_id] = groups.get(item.group_id, 0) + 1
        return sum(1 for size in groups.values() if size < 2)

    # 跨组合并：base 不同 any_of 组的成员在 variant 中落入同一组。
    # 聚类键使用输出项列表位置，与 pairs 索引空间一致。
    base_groups: dict[str, set[int]] = {}
    for position, (_, _, item) in enumerate(base_items):
        if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
            base_groups.setdefault(item.group_id, set()).add(position)
    variant_groups: dict[str, set[int]] = {}
    for position, (_, _, item) in enumerate(variant_items):
        if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
            variant_groups.setdefault(item.group_id, set()).add(position)
    merges = 0
    for group_id, positions in base_groups.items():
        variant_positions = {pair_map[i] for i in positions if i in pair_map}
        for v_group_id, v_positions in variant_groups.items():
            if variant_positions and variant_positions <= v_positions and len(v_positions) > len(positions):
                merges += 1
    return id_map, single_member_any_of(variant_items), merges


@dataclass(frozen=True)
class BlockItemComparison:
    """一对（base 块, variant 块列表）的逐块比较：项数、配对与字段一致性。"""

    base_block_id: str
    variant_block_ids: tuple[str, ...]
    base_anchor_ids: tuple[str, ...]
    base_item_count: int
    variant_item_count: int
    matched_count: int
    field_agreements: dict[str, float]
    proficiency_transitions: dict[tuple[str, str], int]
    importance_transitions: dict[tuple[str, str], int]
    group_type_transitions: dict[tuple[str, str], int]
    any_of_group_sizes: tuple[int, ...]


@dataclass
class ExtractionRunComparison:
    """以候选块为锚点的两次运行比较，不依赖完整 JSON 字符串相等。"""

    base_block_count: int = 0
    variant_block_count: int = 0
    aligned_block_count: int = 0
    block_alignment_rate: float = 0.0
    kind_agreement: float = 0.0
    kind_drift_blocks: list[tuple[str, str, str, str]] = field(default_factory=list)
    unaligned_base_blocks: list[str] = field(default_factory=list)
    unaligned_variant_blocks: list[str] = field(default_factory=list)
    drifted_block_identifiers: list[tuple[str, str]] = field(default_factory=list)
    base_item_count: int = 0
    variant_item_count: int = 0
    atomic_item_count_agreement: bool = False
    base_requirement_count: int = 0
    variant_requirement_count: int = 0
    evidence_span_agreement: float = 0.0
    category_agreement: float = 0.0
    importance_agreement: float = 0.0
    proficiency_agreement: float = 0.0
    group_type_agreement: float = 0.0
    group_membership_agreement: float = 0.0
    unmatched_base_count: int = 0
    unmatched_variant_count: int = 0
    unmatched_items: list[str] = field(default_factory=list)
    new_condition_items: list[str] = field(default_factory=list)
    name_similarity: float = 0.0
    basic_to_advanced_upgrades: int = 0
    importance_upgrades: int = 0
    block_comparisons: list[BlockItemComparison] = field(default_factory=list)
    group_id_map: dict[str, str] = field(default_factory=dict)
    single_member_any_of: int = 0
    cross_group_merges: int = 0

    @property
    def matched_pair_count(self) -> int:
        """返回参与字段一致性统计的配对项数量。"""
        return sum(block.matched_count for block in self.block_comparisons)

    @property
    def unmatched_item_count(self) -> int:
        """返回两次运行合计的未配对项数量。"""
        return self.unmatched_base_count + self.unmatched_variant_count


def compare_runs(
    base: RunSnapshot,
    variant: RunSnapshot,
    transformation: TransformationResult | None = None,
) -> ExtractionRunComparison:
    """以候选块为锚点比较两次运行；变形比较使用 TransformationResult 锚点。"""
    base_blocks = list(base.discovery.blocks)
    variant_blocks = list(variant.discovery.blocks)
    pairs, unaligned_base, unaligned_variant = align_blocks(
        base_blocks,
        variant_blocks,
        base.raw_text,
        variant.raw_text,
        transformation=transformation,
    )
    comparison = ExtractionRunComparison(
        base_block_count=len(base_blocks),
        variant_block_count=len(variant_blocks),
        aligned_block_count=len(pairs),
        block_alignment_rate=len(pairs) / len(base_blocks) if base_blocks else 0.0,
        base_item_count=len(base.result.responsibilities) + len(base.result.requirements),
        variant_item_count=len(variant.result.responsibilities)
        + len(variant.result.requirements),
        base_requirement_count=len(base.result.requirements),
        variant_requirement_count=len(variant.result.requirements),
    )

    kind_correct = 0
    for base_block, variant_blocks_matched in pairs:
        for variant_block in variant_blocks_matched:
            if base_block.kind != variant_block.kind:
                comparison.kind_drift_blocks.append(
                    (base_block.block_id, base_block.kind, variant_block.block_id, variant_block.kind)
                )
            else:
                kind_correct += 1
            if base_block.block_id != variant_block.block_id:
                comparison.drifted_block_identifiers.append(
                    (base_block.block_id, variant_block.block_id)
                )
    total_aligned_variants = sum(len(matched) for _, matched in pairs)
    comparison.kind_agreement = (
        kind_correct / total_aligned_variants if total_aligned_variants else 0.0
    )
    comparison.unaligned_base_blocks = unaligned_base
    comparison.unaligned_variant_blocks = unaligned_variant
    comparison.atomic_item_count_agreement = (
        comparison.base_item_count == comparison.variant_item_count
    )

    base_items = _result_items(base.result)
    variant_items = _result_items(variant.result)
    total_pairs: list[tuple[int, int]] = []
    total_base_items: list[tuple[str, int, Any]] = []
    total_variant_items: list[tuple[str, int, Any]] = []
    base_anchors = anchor_ids(base.raw_text)

    for base_block, variant_blocks_matched in pairs:
        variant_block_ids = tuple(block.block_id for block in variant_blocks_matched)
        base_block_items = _items_for_blocks(base_items, [base_block])
        variant_block_items = _items_for_blocks(variant_items, variant_blocks_matched)
        offset_base = len(total_base_items)
        offset_variant = len(total_variant_items)
        block_pairs, unmatched_base, unmatched_variant = _pair_items(
            base_block_items, variant_block_items
        )
        field_agreements = {
            field_name: _field_agreement(
                base_block_items, variant_block_items, block_pairs, field_name
            )
            for field_name in (
                "category",
                "importance",
                "proficiency",
                "group_type",
                "evidence",
            )
        }
        transitions: dict[tuple[str, str], int] = {}
        importance_transitions: dict[tuple[str, str], int] = {}
        group_transitions: dict[tuple[str, str], int] = {}
        for base_index, variant_index in block_pairs:
            base_item = base_block_items[base_index][2]
            variant_item = variant_block_items[variant_index][2]
            if isinstance(base_item, RequirementItem) and isinstance(
                variant_item, RequirementItem
            ):
                transition = (
                    base_item.proficiency.value,
                    variant_item.proficiency.value,
                )
                transitions[transition] = transitions.get(transition, 0) + 1
                importance_transition = (
                    base_item.importance.value,
                    variant_item.importance.value,
                )
                importance_transitions[importance_transition] = (
                    importance_transitions.get(importance_transition, 0) + 1
                )
                group_transition = (
                    base_item.group_logic.value,
                    variant_item.group_logic.value,
                )
                group_transitions[group_transition] = (
                    group_transitions.get(group_transition, 0) + 1
                )
        any_of_sizes: list[int] = []
        variant_group_counts: Counter[str] = Counter()
        for _, _, item in variant_block_items:
            if isinstance(item, RequirementItem) and item.group_logic.value == "any_of" and item.group_id:
                variant_group_counts[item.group_id] += 1
        any_of_sizes = sorted(size for size in variant_group_counts.values())
        comparison.block_comparisons.append(
            BlockItemComparison(
                base_block_id=base_block.block_id,
                variant_block_ids=variant_block_ids,
                base_anchor_ids=tuple(
                    anchor
                    for anchor in _block_anchor_ids(base_block, base.raw_text)
                    if anchor in base_anchors
                ),
                base_item_count=len(base_block_items),
                variant_item_count=len(variant_block_items),
                matched_count=len(block_pairs),
                field_agreements=field_agreements,
                proficiency_transitions=transitions,
                importance_transitions=importance_transitions,
                group_type_transitions=group_transitions,
                any_of_group_sizes=tuple(any_of_sizes),
            )
        )
        for base_index, variant_index in block_pairs:
            total_pairs.append((offset_base + base_index, offset_variant + variant_index))
        for index in sorted(unmatched_base):
            label, _, item = base_block_items[index]
            comparison.unmatched_items.append(
                f"base {base_block.block_id} {label}:{item_label(item)}"
            )
        for index in sorted(unmatched_variant):
            label, _, item = variant_block_items[index]
            comparison.unmatched_items.append(
                f"variant {base_block.block_id} {label}:{item_label(item)}"
            )
        total_base_items.extend(base_block_items)
        total_variant_items.extend(variant_block_items)

    comparison.unmatched_base_count = sum(
        1 for message in comparison.unmatched_items if message.startswith("base ")
    )
    comparison.unmatched_variant_count = len(comparison.unmatched_items) - (
        comparison.unmatched_base_count
    )

    # 新增“条件”：variant 未配对项中 importance 为 must/preferred 的要求，
    # 且其证据在 base 的 requirements 中不存在（职责证据不视为已有条件）。
    base_requirement_evidence = {
        _alnum(entry[2].evidence)
        for entry in base_items
        if entry[0] == "requirement"
    }
    for _, _, item in variant_items:
        if isinstance(item, RequirementItem) and item.importance.value in (
            "must",
            "preferred",
        ):
            variant_alnum = _alnum(item.evidence)
            if variant_alnum not in base_requirement_evidence:
                comparison.new_condition_items.append(item_label(item))

    if total_pairs:
        comparison.evidence_span_agreement = _field_agreement(
            total_base_items, total_variant_items, total_pairs, "evidence"
        )
        for field_name in (
            "category",
            "importance",
            "proficiency",
            "group_type",
        ):
            setattr(
                comparison,
                f"{field_name}_agreement",
                _field_agreement(total_base_items, total_variant_items, total_pairs, field_name),
            )
        comparison.group_membership_agreement = _pairwise_membership_agreement(
            total_base_items, total_variant_items, total_pairs
        )
        comparison.group_id_map, comparison.single_member_any_of, comparison.cross_group_merges = (
            _group_diagnostics(total_base_items, total_variant_items, total_pairs)
        )
        similarities = [
            item_name_similarity(
                item_label(total_base_items[base_index][2]),
                item_label(total_variant_items[variant_index][2]),
            )
            for base_index, variant_index in total_pairs
        ]
        comparison.name_similarity = sum(similarities) / len(similarities)

    # 显式字段变化计数：只记录“basic→advanced”与“must→preferred”两类明确升级。
    for base_index, variant_index in total_pairs:
        base_item = total_base_items[base_index][2]
        variant_item = total_variant_items[variant_index][2]
        if not isinstance(base_item, RequirementItem) or not isinstance(
            variant_item, RequirementItem
        ):
            continue
        if (
            base_item.proficiency.value == "basic"
            and variant_item.proficiency.value == "advanced"
        ):
            comparison.basic_to_advanced_upgrades += 1
        if (
            base_item.importance.value == "must"
            and variant_item.importance.value == "preferred"
        ):
            comparison.importance_upgrades += 1
    return comparison


def check_scenario_properties(
    comparison: ExtractionRunComparison,
    properties: dict[str, Any],
    changed_regions: frozenset[str] = frozenset(),
    variant_result: JobExtractionResult | None = None,
) -> tuple[list[str], list[str]]:
    """按场景期望属性检查运行间比较结果，返回（failures, warnings）。

    只检查可确定性验证的属性；forbidden_violations 由人工审计复核。
    """
    failures: list[str] = []
    warnings: list[str] = []

    def target_blocks(anchor: str) -> list[BlockItemComparison]:
        return [
            block
            for block in comparison.block_comparisons
            if anchor in block.base_anchor_ids
        ]

    def declared_target_anchors() -> set[str]:
        anchors: set[str] = set(changed_regions)
        for change in properties.get("proficiency_expected_changes", []):
            if change.get("anchor"):
                anchors.add(change["anchor"])
        experience = properties.get("experience_to_unknown_expected_change") or {}
        if experience.get("anchor"):
            anchors.add(experience["anchor"])
        if properties.get("group_logic_changed_to") and properties.get("group_change_anchor"):
            anchors.add(properties["group_change_anchor"])
        return anchors

    for key, value in properties.items():
        if key == "fact_set_preserved" and value:
            if comparison.unmatched_base_count:
                failures.append(f"fact_set_preserved: base 有 {comparison.unmatched_base_count} 项未在 variant 中找到对应")
        elif key == "fact_set_unchanged" and value:
            if comparison.unmatched_base_count or comparison.unmatched_variant_count:
                failures.append(
                    "fact_set_unchanged: 事实集变化 "
                    f"base_unmatched={comparison.unmatched_base_count} "
                    f"variant_unmatched={comparison.unmatched_variant_count}"
                )
        elif key == "block_set_preserved" and value:
            if comparison.unaligned_base_blocks or comparison.kind_drift_blocks:
                failures.append(
                    f"block_set_preserved: base 块未保留 "
                    f"unaligned={comparison.unaligned_base_blocks} "
                    f"kind_drift={[item[0] for item in comparison.kind_drift_blocks]}"
                )
        elif key == "block_item_count_preserved" and value:
            drifted = [
                f"{block.base_block_id}:{block.base_item_count}->{block.variant_item_count}"
                for block in comparison.block_comparisons
                if block.base_item_count != block.variant_item_count
            ]
            if drifted:
                failures.append(f"block_item_count_preserved: 块内项数变化 {drifted}")
        elif key == "field_invariance" and isinstance(value, list):
            for field_name in value:
                if field_name == "group_logic":
                    field_name = "group_type"
                agreement = getattr(comparison, f"{field_name}_agreement", None)
                if agreement is not None and agreement < 1.0:
                    failures.append(
                        f"field_invariance: {field_name} 一致性 {agreement:.2%}"
                    )
        elif key == "no_new_requirement_facts" and value:
            if comparison.variant_requirement_count > comparison.base_requirement_count:
                failures.append(
                    "no_new_requirement_facts: variant 新增要求 "
                    f"{comparison.base_requirement_count}->{comparison.variant_requirement_count}"
                )
        elif key == "no_new_conditions" and value:
            if comparison.new_condition_items:
                failures.append(
                    f"no_new_conditions: variant 新增条件 {comparison.new_condition_items}"
                )
        elif key == "proficiency_expected_changes" and isinstance(value, list):
            for change in value:
                anchor = change.get("anchor")
                expected_from = change.get("from")
                expected_to = change.get("to")
                blocks = target_blocks(anchor) if anchor else comparison.block_comparisons
                found = any(
                    block.proficiency_transitions.get((expected_from, expected_to), 0) > 0
                    for block in blocks
                )
                if not found:
                    failures.append(
                        f"proficiency_expected_changes: 锚点 {anchor} 未发现 "
                        f"{expected_from}->{expected_to} 的变化项"
                    )
        elif key == "experience_to_unknown_expected_change" and isinstance(value, dict):
            anchor = value.get("anchor")
            blocks = target_blocks(anchor) if anchor else comparison.block_comparisons
            found = any(
                block.proficiency_transitions.get(("basic", "unknown"), 0) > 0
                for block in blocks
            )
            if not found:
                failures.append(
                    f"experience_to_unknown_expected_change: 锚点 {anchor} 未发现 "
                    "basic->unknown 的变化项"
                )
        elif key == "importance_expected_change" and isinstance(value, dict):
            anchor = value.get("anchor")
            expected_from = value.get("from")
            expected_to = value.get("to")
            blocks = target_blocks(anchor) if anchor else comparison.block_comparisons
            found = any(
                block.importance_transitions.get((expected_from, expected_to), 0) > 0
                for block in blocks
            )
            if not found:
                failures.append(
                    f"importance_expected_change: 锚点 {anchor} 未发现 "
                    f"{expected_from}->{expected_to} 的变化项"
                )
        elif key == "proficiency_invariance" and value:
            target_anchors = declared_target_anchors()
            for block in comparison.block_comparisons:
                if target_anchors & set(block.base_anchor_ids):
                    continue
                if block.field_agreements.get("proficiency", 1.0) < 1.0:
                    failures.append(
                        f"proficiency_invariance: 非目标块 {block.base_block_id} "
                        f"proficiency 一致性 {block.field_agreements['proficiency']:.2%}"
                    )
        elif key == "group_logic_changed_to" and value in ("any_of", "standalone"):
            anchor = properties.get("group_change_anchor")
            blocks = target_blocks(anchor) if anchor else comparison.block_comparisons
            found = any(
                block.group_type_transitions.get(("standalone", value), 0) > 0
                or block.group_type_transitions.get(("any_of", value), 0) > 0
                for block in blocks
            )
            if not found:
                failures.append(
                    f"group_logic_changed_to: 未发现 group_type 变化为 {value} 的块"
                    + (f"（锚点 {anchor}）" if anchor else "")
                )
        elif key == "expected_group_member_count" and isinstance(value, int):
            target_anchors = declared_target_anchors()
            blocks = (
                [block for block in comparison.block_comparisons if target_anchors & set(block.base_anchor_ids)]
                if target_anchors
                else comparison.block_comparisons
            )
            for block in blocks:
                if block.any_of_group_sizes and value not in block.any_of_group_sizes:
                    failures.append(
                        f"expected_group_member_count: 块 {block.base_block_id} "
                        f"any_of 组成员数 {block.any_of_group_sizes} 不包含期望 {value}"
                    )
        elif key == "group_members_preserved" and value:
            anchor = properties.get("group_change_anchor")
            blocks = target_blocks(anchor) if anchor else comparison.block_comparisons
            for block in blocks:
                if (
                    block.matched_count != block.base_item_count
                    or block.base_item_count != block.variant_item_count
                ):
                    failures.append(
                        f"group_members_preserved: 块 {block.base_block_id} "
                        f"成员未完全保持（base={block.base_item_count} "
                        f"variant={block.variant_item_count} "
                        f"matched={block.matched_count}）"
                    )
                if block.any_of_group_sizes and sum(block.any_of_group_sizes) != (
                    block.variant_item_count
                ):
                    failures.append(
                        f"group_members_preserved: 块 {block.base_block_id} "
                        f"any_of 组未包含全部成员 "
                        f"{block.any_of_group_sizes}"
                    )
        elif key == "raw_name_follows_replacements" and isinstance(value, list):
            old_names = [replacement["find"] for replacement in value]
            new_names = [replacement["replace"] for replacement in value]
            if variant_result is None:
                warnings.append("raw_name_follows_replacements: 缺少 variant_result，跳过检查")
                continue
            labels = [item_label(item) for item in variant_result.requirements]
            old_hits = sorted({name for name in old_names if any(name in label for label in labels)})
            new_hits = sorted({name for name in new_names if any(name in label for label in labels)})
            if old_hits:
                failures.append(f"raw_name_follows_replacements: variant 仍输出旧名 {old_hits}")
            if not new_hits:
                failures.append(
                    f"raw_name_follows_replacements: variant 未输出任何新名 {new_names}"
                )
        else:
            warnings.append(f"未识别的期望属性：{key}")
    return failures, warnings


@dataclass
class ExtractionAcceptanceReport:
    """机器可读验收报告：hard gates / warnings / diagnostics 分级。

    phase：pilot（检查流程、收集指标，不产生批准结论）或 acceptance
    （使用已冻结的规则、范围与阈值，可用于批准当前版本）。
    """

    identity: dict[str, str]
    hard_gate_failures: list[str]
    warnings: list[str]
    diagnostics: list[str]
    run_count: int = 0
    phase: str = "pilot"

    @property
    def passed(self) -> bool:
        """全部 hard gate 通过才算整体通过。"""
        return not self.hard_gate_failures

    @property
    def decision_eligible(self) -> bool:
        """仅 acceptance 阶段且无 hard gate 失败时才可用于批准当前版本。

        阈值冻结（多次运行稳定性等）由用户在 acceptance 前完成；本字段
        只表达"流程与 hard gates 是否允许作出批准结论"。
        """
        return self.phase == "acceptance" and self.passed

    def to_dict(self) -> dict[str, Any]:
        """序列化为不含私有 JD 内容的机器可读字典。"""
        return {
            "identity": self.identity,
            "phase": self.phase,
            "run_count": self.run_count,
            "hard_gate_failures": self.hard_gate_failures,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "passed": self.passed,
            "decision_eligible": self.decision_eligible,
        }

    def to_json(self) -> str:
        """序列化为 JSON 文本。"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def contract_hard_gate_failures(contract: ExtractionContractReport) -> list[str]:
    """把合同检查映射为第一版 hard gate 失败列表。"""
    failures: list[str] = []
    if not contract.schema_valid:
        failures.append("schema_violations")
    if contract.evidence_violations:
        failures.append(f"evidence_violations={len(contract.evidence_violations)}")
    if contract.coverage.coverage != 1.0:
        failures.append(f"discovery_coverage={contract.coverage.coverage:.2%}")
    if contract.coverage.duplicate_covered_sentences:
        failures.append(
            f"duplicate_sentence_coverage={contract.coverage.duplicate_covered_sentences}"
        )
    if contract.coverage.missing_sentences or contract.coverage.unexpected_sentences:
        failures.append(
            "sentence_coverage_mismatch "
            f"missing={contract.coverage.missing_sentences} "
            f"unexpected={contract.coverage.unexpected_sentences}"
        )
    if contract.coverage.invalid_span_blocks:
        failures.append(f"invalid_source_spans={contract.coverage.invalid_span_blocks}")
    if contract.unprocessed_blocks:
        failures.append(f"judge_candidate_coverage: unprocessed_blocks={contract.unprocessed_blocks}")
    if contract.type_violations:
        failures.append(f"candidate_type_violations={contract.type_violations}")
    if contract.excluded_violations:
        failures.append(f"excluded_block_violations={contract.excluded_violations}")
    if contract.invalid_groups:
        failures.append(f"invalid_group_logic={len(contract.invalid_groups)}")
    if contract.evidence_unattributed_items:
        failures.append(
            f"evidence_unattributed_items={len(contract.evidence_unattributed_items)}"
        )
    if not contract.identity_complete:
        failures.append(f"identity_incomplete={contract.identity_missing}")
    return failures


def stability_warnings(
    comparisons: list[ExtractionRunComparison],
) -> tuple[list[str], list[str]]:
    """第一版多次运行 agreement 只产生 warnings 与 diagnostics，不预设阈值。"""
    warnings: list[str] = []
    diagnostics: list[str] = []
    if not comparisons:
        return warnings, diagnostics
    for index, comparison in enumerate(comparisons):
        if comparison.unmatched_item_count:
            warnings.append(
                f"run{index}: unmatched_item_count={comparison.unmatched_item_count}"
            )
        if comparison.kind_drift_blocks:
            warnings.append(
                f"run{index}: kind_drift_blocks="
                f"{[item[0] for item in comparison.kind_drift_blocks]}"
            )
        if comparison.unaligned_base_blocks or comparison.unaligned_variant_blocks:
            warnings.append(
                f"run{index}: unaligned_blocks base={comparison.unaligned_base_blocks} "
                f"variant={comparison.unaligned_variant_blocks}"
            )
        diagnostics.append(
            f"run{index}: block_alignment_rate={comparison.block_alignment_rate:.2%} "
            f"kind_agreement={comparison.kind_agreement:.2%} "
            f"atomic_count_agreement={comparison.atomic_item_count_agreement} "
            f"evidence_span_agreement={comparison.evidence_span_agreement:.2%} "
            f"category_agreement={comparison.category_agreement:.2%} "
            f"importance_agreement={comparison.importance_agreement:.2%} "
            f"proficiency_agreement={comparison.proficiency_agreement:.2%} "
            f"group_type_agreement={comparison.group_type_agreement:.2%} "
            f"group_membership_agreement={comparison.group_membership_agreement:.2%} "
            f"name_similarity={comparison.name_similarity:.2%}"
        )
    return warnings, diagnostics


def build_acceptance_report(
    identity: dict[str, str],
    contract: ExtractionContractReport | None,
    comparisons: list[ExtractionRunComparison] | None = None,
    scenario_failures: list[str] | None = None,
    run_count: int = 0,
) -> ExtractionAcceptanceReport:
    """组装分级验收报告；多次运行 agreement 第一版只作 warning。

    注意：验收脚本必须显式汇总各场景与合同报告，不得先以 contract=None
    构造再覆盖失败结果；本函数供独立工具与测试使用。
    """
    hard_gate_failures = (
        contract_hard_gate_failures(contract) if contract is not None else []
    )
    if contract is None:
        hard_gate_failures.append("contract_check_unavailable")
    if scenario_failures:
        hard_gate_failures.extend(scenario_failures)
    warnings: list[str] = []
    diagnostics: list[str] = []
    if comparisons is not None:
        comparison_warnings, comparison_diagnostics = stability_warnings(comparisons)
        warnings.extend(comparison_warnings)
        diagnostics.extend(comparison_diagnostics)
    report_identity = {
        "model": identity.get("model", ""),
        "prompt_version": identity.get("prompt_version", ""),
        "schema_version": identity.get("schema_version", ""),
        "input_fingerprint": identity.get("input_fingerprint", ""),
        "run_identifier": identity.get("run_identifier", ""),
        "timestamp": identity.get(
            "timestamp", datetime.now(timezone.utc).isoformat(timespec="seconds")
        ),
    }
    return ExtractionAcceptanceReport(
        identity=report_identity,
        hard_gate_failures=hard_gate_failures,
        warnings=warnings,
        diagnostics=diagnostics,
        run_count=run_count,
    )
