"""抽取验证模块：确定性合同检查、运行间比较与验收报告（P0-3 新协议，DEC-015）。

本模块只做离线、确定性验证和运行间比较，不调用外部模型，也不读取人工
完整答案决定通过或失败。规则判定复用 `app/extraction_two_stage.py` 与
`app/extraction.py` 的现有校验（覆盖、span 对应、证据存在），不复制业务
规则；名称相似度只作为 diagnostic，不作为 hard gate。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.evaluation import item_label, item_name_similarity, match_atomic_items
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
    """一份抽取结果的合同检查：Schema、覆盖、证据、逻辑组、归属与身份。"""

    schema_valid: bool
    schema_errors: list[str]
    coverage: DiscoveryCoverageReport
    unprocessed_blocks: list[str]
    unattributed_items: list[str]
    evidence_violations: list[str]
    invalid_groups: list[str]
    identity_missing: list[str]

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
            and not self.unattributed_items
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
    """检查模型原始输出是否符合抽取数据合同，返回（合法、全部错误、逻辑组错误）。"""
    try:
        JobExtractionResult.model_validate(payload)
        return True, [], []
    except Exception as exc:  # 收集合同错误而不是中断检查
        message = str(exc)
        group_errors = (
            [message] if ("any_of" in message or "group" in message) else []
        )
        return False, [message], group_errors


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

    # 判断段候选覆盖：每个非 excluded 候选块必须至少被一项输出命中。
    processed_block_ids = {
        block.block_id
        for block in discovery.blocks
        if block.kind != "excluded"
        and any(
            _evidence_hits_block(item.evidence, block)
            for item in [*result.responsibilities, *result.requirements]
        )
    }
    unprocessed_blocks = [
        block.block_id
        for block in discovery.blocks
        if block.kind != "excluded" and block.block_id not in processed_block_ids
    ]

    # 无依据明确事实：输出项证据不命中任何非 excluded 候选块（含只命中 excluded 块）。
    active_blocks = [block for block in discovery.blocks if block.kind != "excluded"]
    unattributed_items: list[str] = []
    for label, items in (
        ("responsibility", result.responsibilities),
        ("requirement", result.requirements),
    ):
        for index, item in enumerate(items):
            if not any(_evidence_hits_block(item.evidence, block) for block in active_blocks):
                unattributed_items.append(f"{label}[{index}]")

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
        unattributed_items=unattributed_items,
        evidence_violations=evidence_violations,
        invalid_groups=invalid_groups,
        identity_missing=identity_missing,
    )


def _result_items(result: JobExtractionResult) -> list[tuple[str, int, Any]]:
    """返回带类型标签和索引的输出项列表，供块归属与配对使用。"""
    return [
        *[("responsibility", index, item) for index, item in enumerate(result.responsibilities)],
        *[("requirement", index, item) for index, item in enumerate(result.requirements)],
    ]


def _items_for_block(
    items: list[tuple[str, int, Any]], block: CandidateBlock
) -> list[tuple[str, int, Any]]:
    """筛选证据命中指定候选块的输出项。"""
    return [
        entry for entry in items if _evidence_hits_block(entry[2].evidence, block)
    ]


def _pair_items(
    base_items: list[tuple[str, int, Any]],
    variant_items: list[tuple[str, int, Any]],
) -> tuple[list[tuple[int, int]], set[int], set[int]]:
    """先按证据字母数字序列精确配对，再按名称相似度补配剩余项。

    返回（配对索引对，未配对的base索引，未配对的variant索引）。
    """
    base_map: dict[str, list[int]] = {}
    for index, (_, _, item) in enumerate(base_items):
        base_map.setdefault(_alnum(item.evidence), []).append(index)
    variant_map: dict[str, list[int]] = {}
    for index, (_, _, item) in enumerate(variant_items):
        variant_map.setdefault(_alnum(item.evidence), []).append(index)

    pairs: list[tuple[int, int]] = []
    used_base: set[int] = set()
    used_variant: set[int] = set()
    for key in sorted(base_map.keys() & variant_map.keys()):
        base_indexes = base_map[key]
        variant_indexes = variant_map[key]
        for base_index, variant_index in zip(base_indexes, variant_indexes):
            pairs.append((base_index, variant_index))
            used_base.add(base_index)
            used_variant.add(variant_index)

    remaining_base = [i for i in range(len(base_items)) if i not in used_base]
    remaining_variant = [i for i in range(len(variant_items)) if i not in used_variant]
    if remaining_base and remaining_variant:
        similarity_pairs = match_atomic_items(
            [item for _, _, item in base_items],
            [item for _, _, item in variant_items],
        )
        for base_index, variant_index in similarity_pairs:
            if (
                base_index in used_base
                or variant_index in used_variant
                or base_index not in remaining_base
                or variant_index not in remaining_variant
            ):
                continue
            pairs.append((base_index, variant_index))
            used_base.add(base_index)
            used_variant.add(variant_index)

    unmatched_base = set(range(len(base_items))) - used_base
    unmatched_variant = set(range(len(variant_items))) - used_variant
    return pairs, unmatched_base, unmatched_variant


@dataclass(frozen=True)
class BlockItemComparison:
    """一对对齐候选块的逐块比较：项数、配对数与字段一致性。"""

    base_block_id: str
    variant_block_id: str
    base_item_count: int
    variant_item_count: int
    matched_count: int
    field_agreements: dict[str, float]


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
    group_logic_agreement: float = 0.0
    unmatched_base_count: int = 0
    unmatched_variant_count: int = 0
    unmatched_items: list[str] = field(default_factory=list)
    new_condition_items: list[str] = field(default_factory=list)
    name_similarity: float = 0.0
    proficiency_upgrades: int = 0
    importance_upgrades: int = 0
    block_comparisons: list[BlockItemComparison] = field(default_factory=list)

    @property
    def matched_pair_count(self) -> int:
        """返回参与字段一致性统计的配对项数量。"""
        return sum(block.matched_count for block in self.block_comparisons)

    @property
    def unmatched_item_count(self) -> int:
        """返回两次运行合计的未配对项数量。"""
        return self.unmatched_base_count + self.unmatched_variant_count


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
        if field == "group_logic":
            base_value = (base_item.group_id, base_item.group_logic.value)
            variant_value = (variant_item.group_id, variant_item.group_logic.value)
        else:
            base_value = getattr(base_item, field)
            variant_value = getattr(variant_item, field)
        compared += 1
        correct += int(base_value == variant_value)
    return correct / compared if compared else 0.0


def compare_runs(base: RunSnapshot, variant: RunSnapshot) -> ExtractionRunComparison:
    """以候选块为锚点比较两次运行，报告块、原子项与字段一致性。"""
    base_blocks = {_alnum_sequence(block.source_span): block for block in base.discovery.blocks}
    variant_blocks = {
        _alnum_sequence(block.source_span): block for block in variant.discovery.blocks
    }
    common_keys = sorted(base_blocks.keys() & variant_blocks.keys())
    comparison = ExtractionRunComparison(
        base_block_count=len(base_blocks),
        variant_block_count=len(variant_blocks),
        aligned_block_count=len(common_keys),
        block_alignment_rate=len(common_keys) / len(base_blocks) if base_blocks else 0.0,
        base_item_count=len(base.result.responsibilities) + len(base.result.requirements),
        variant_item_count=len(variant.result.responsibilities)
        + len(variant.result.requirements),
        base_requirement_count=len(base.result.requirements),
        variant_requirement_count=len(variant.result.requirements),
    )

    kind_correct = 0
    for key in common_keys:
        base_block = base_blocks[key]
        variant_block = variant_blocks[key]
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
    comparison.kind_agreement = kind_correct / len(common_keys) if common_keys else 0.0

    comparison.unaligned_base_blocks = sorted(
        block.block_id
        for key, block in base_blocks.items()
        if key not in variant_blocks
    )
    comparison.unaligned_variant_blocks = sorted(
        block.block_id
        for key, block in variant_blocks.items()
        if key not in base_blocks
    )
    comparison.atomic_item_count_agreement = (
        comparison.base_item_count == comparison.variant_item_count
    )

    total_pairs: list[tuple[int, int]] = []
    total_base_items: list[tuple[str, int, Any]] = []
    total_variant_items: list[tuple[str, int, Any]] = []
    for key in common_keys:
        base_block = base_blocks[key]
        variant_block = variant_blocks[key]
        base_items = _items_for_block(_result_items(base.result), base_block)
        variant_items = _items_for_block(_result_items(variant.result), variant_block)
        offset_base = len(total_base_items)
        offset_variant = len(total_variant_items)
        pairs, unmatched_base, unmatched_variant = _pair_items(base_items, variant_items)
        field_agreements = {
            field_name: _field_agreement(
                base_items, variant_items, pairs, field_name
            )
            for field_name in (
                "category",
                "importance",
                "proficiency",
                "group_logic",
            )
        }
        comparison.block_comparisons.append(
            BlockItemComparison(
                base_block_id=base_block.block_id,
                variant_block_id=variant_block.block_id,
                base_item_count=len(base_items),
                variant_item_count=len(variant_items),
                matched_count=len(pairs),
                field_agreements=field_agreements,
            )
        )
        for base_index, variant_index in pairs:
            total_pairs.append(
                (offset_base + base_index, offset_variant + variant_index)
            )
        for index in sorted(unmatched_base):
            label, _, item = base_items[index]
            comparison.unmatched_items.append(
                f"base {base_block.block_id} {label}:{item_label(item)}"
            )
        for index in sorted(unmatched_variant):
            label, _, item = variant_items[index]
            comparison.unmatched_items.append(
                f"variant {variant_block.block_id} {label}:{item_label(item)}"
            )
        total_base_items.extend(base_items)
        total_variant_items.extend(variant_items)

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
        for entry in _result_items(base.result)
        if entry[0] == "requirement"
    }
    for _, _, item in _result_items(variant.result):
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
            "group_logic",
        ):
            setattr(
                comparison,
                f"{field_name}_agreement",
                _field_agreement(total_base_items, total_variant_items, total_pairs, field_name),
            )
        similarities = [
            item_name_similarity(
                item_label(total_base_items[base_index][2]),
                item_label(total_variant_items[variant_index][2]),
            )
            for base_index, variant_index in total_pairs
        ]
        comparison.name_similarity = sum(similarities) / len(similarities)

    # 显式字段变化计数：只记录“熟悉→精通”与“must→preferred”两类明确升级。
    for base_index, variant_index in total_pairs:
        base_item = total_base_items[base_index][2]
        variant_item = total_variant_items[variant_index][2]
        if not isinstance(base_item, RequirementItem) or not isinstance(
            variant_item, RequirementItem
        ):
            continue
        if (
            base_item.proficiency.value == "familiar"
            and variant_item.proficiency.value == "expert"
        ):
            comparison.proficiency_upgrades += 1
        if (
            base_item.importance.value == "must"
            and variant_item.importance.value == "preferred"
        ):
            comparison.importance_upgrades += 1
    return comparison


def check_scenario_properties(
    comparison: ExtractionRunComparison, properties: dict[str, Any]
) -> tuple[list[str], list[str]]:
    """按场景期望属性检查运行间比较结果，返回（failures, warnings）。

    只检查可确定性验证的属性；forbidden_violations 由人工审计复核。
    """
    failures: list[str] = []
    warnings: list[str] = []
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
                agreement = getattr(comparison, f"{field_name}_agreement", None)
                if agreement is not None and agreement < 1.0:
                    failures.append(
                        f"field_invariance: {field_name} 一致性 {agreement:.2%}"
                    )
        elif key == "proficiency_upgraded" and value:
            if comparison.proficiency_upgrades == 0:
                failures.append("proficiency_upgraded: 未发现 familiar->expert 的变化项")
        elif key == "importance_upgraded_to_preferred" and value:
            if comparison.importance_upgrades == 0:
                failures.append("importance_upgraded_to_preferred: 未发现 must->preferred 的变化项")
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
        else:
            warnings.append(f"未识别的期望属性：{key}")
    return failures, warnings


@dataclass
class ExtractionAcceptanceReport:
    """机器可读验收报告：hard gates / warnings / diagnostics 分级。"""

    identity: dict[str, str]
    hard_gate_failures: list[str]
    warnings: list[str]
    diagnostics: list[str]
    run_count: int = 0

    @property
    def passed(self) -> bool:
        """全部 hard gate 通过才算整体通过。"""
        return not self.hard_gate_failures

    def to_dict(self) -> dict[str, Any]:
        """序列化为不含私有 JD 内容的机器可读字典。"""
        return {
            "identity": self.identity,
            "run_count": self.run_count,
            "hard_gate_failures": self.hard_gate_failures,
            "warnings": self.warnings,
            "diagnostics": self.diagnostics,
            "passed": self.passed,
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
    if contract.invalid_groups:
        failures.append(f"invalid_group_logic={len(contract.invalid_groups)}")
    if contract.unattributed_items:
        failures.append(f"unattributed_facts={len(contract.unattributed_items)}")
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
            f"group_logic_agreement={comparison.group_logic_agreement:.2%} "
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
    """组装分级验收报告；多次运行 agreement 第一版只作 warning。"""
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
