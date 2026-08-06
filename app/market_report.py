"""市场统计 Markdown 报告（离线、确定、可追溯）。

职责边界：

- `validate_report_inputs`：生成报告前的完整数据一致性门禁，复用
  归并持久化验证（精确 ID 覆盖、mapping 与来源分区归属一致、
  occurrence_count 一致），并追加占位 canonical name 检测与
  requirement → extraction → JD 回查完整性；
- `build_market_report`：把 `MarketStatistics` 渲染为可读 Markdown
  的纯函数（无 IO、无模型、无时间戳，重复调用输出一致）。

报告是归并批次的派生产物：任何完整性失败都拒绝生成，不跳过缺失
数据生成残缺报告。
"""
from __future__ import annotations

from typing import Any

from app.consolidation_validation import (
    is_placeholder_canonical_name,
    load_persisted_consolidation_result,
    validate_persisted_consistency,
)
from app.market_analysis import MarketStatistics
from app.models import JobConsolidation, JobExtraction, JobRequirement
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

# 报告顶部样本限制声明：三份 JD 只演示流程，不代表市场结论。
SAMPLE_LIMITATION = (
    "> **样本限制**：本报告基于当前已定稿归并批次（3 份 JD、83 条 "
    "requirement instances、72 个 canonical requirements）生成，是"
    "**流程与证据追溯能力演示**，不代表完整岗位市场结论。所有频率"
    "与排名仅在当前样本范围内有效，不得称为行业排名。"
)

# Markdown 正文转义：防止证据文本破坏列表/引用结构。
_MARKDOWN_ESCAPES = {
    "\\": "\\\\",
    "*": "\\*",
    "_": "\\_",
    "[": "\\[",
    "]": "\\]",
    "#": "\\#",
    "`": "\\`",
}


def escape_markdown(text: str) -> str:
    """正文转义：保留换行语义但转义 Markdown 结构字符。"""
    out = []
    for char in text:
        out.append(_MARKDOWN_ESCAPES.get(char, char))
    return "".join(out)


def escape_table_cell(text: str) -> str:
    """表格单元格转义：竖线与换行不破坏表格结构。"""
    return (
        text.replace("\\", "\\\\")
        .replace("|", "\\|")
        .replace("\r", "")
        .replace("\n", " ")
        .strip()
    )


def _importance_label(counts: dict[str, int]) -> str:
    """JD 级 importance 分布的可读摘要，如 must 2 / preferred 1。"""
    parts = []
    for level in ("must", "preferred", "mentioned", "unknown"):
        if counts.get(level):
            parts.append(f"{level} {counts[level]}")
    return " / ".join(parts) if parts else "-"


def _evidence_lines(source_requirements: tuple[dict[str, Any], ...]) -> list[str]:
    """证据追溯条目（确定性：requirement_id 升序）。"""
    lines: list[str] = []
    for requirement in source_requirements:
        job_id = requirement.get("job_id")
        job_label = f"JD {job_id}" if job_id is not None else "JD 未知"
        lines.append(
            f"- {job_label}｜实例 {requirement['requirement_id']}："
            f"**{escape_markdown(str(requirement['raw_name']))}**"
        )
        detail_parts = [
            f"importance={requirement.get('importance', '-')}",
            f"category={requirement.get('category', '-')}",
            f"proficiency={requirement.get('proficiency', '-')}",
        ]
        lines.append(f"  - {escape_markdown(' / '.join(detail_parts))}")
        evidence = str(requirement.get("evidence", "")).strip()
        lines.append(f"  - 证据：{escape_markdown(evidence)}")
    return lines


def build_market_report(stats: MarketStatistics) -> str:
    """把市场统计渲染为可读 Markdown（纯函数，确定性输出）。"""
    sections: list[str] = []

    # 1. 标题与样本限制声明（醒目，置于最前）。
    sections.append("# 岗位要求市场分析报告（流程演示）\n")
    sections.append(SAMPLE_LIMITATION + "\n")

    # 2. 报告身份。
    sections.append("## 报告身份\n")
    identity = [
        ("归并批次", f"#{stats.consolidation_id}（{stats.scope_key}）"),
        ("JD 数量", str(stats.total_job_count)),
        ("requirement instance 数", str(stats.occurrence_count)),
        ("canonical requirement 数", str(stats.canonical_count)),
        ("抽取器版本", stats.extractor_version),
        ("归并器版本", stats.consolidator_version),
        ("输入身份", f"{stats.input_fingerprint[:12]}…"),
        ("来源 JD", "、".join(str(job_id) for job_id in stats.selected_job_ids)),
    ]
    sections.append(
        "\n".join(f"- {label}：{value}" for label, value in identity) + "\n"
    )
    if stats.job_summaries:
        sections.append("### 来源 JD 摘要\n")
        for job in stats.job_summaries:
            city = job.get("city") or "-"
            sections.append(
                f"- JD {job['job_id']}：{job['company']}｜{job['title']}｜{city}\n"
            )
    sections.append("\n")

    # 3. 总览：全部数字由统计对象确定性计算。
    common = [item for item in stats.canonical_items if item.distinct_job_count > 1]
    single = [item for item in stats.canonical_items if item.distinct_job_count == 1]
    sections.append("## 总览\n")
    overview = [
        ("覆盖 JD 数", str(stats.total_job_count)),
        ("抽取原子要求数", str(stats.occurrence_count)),
        ("归并标准要求数", str(stats.canonical_count)),
        ("出现在多份 JD 的要求数", str(len(common))),
        ("仅出现在单份 JD 的要求数（长尾）", str(len(single))),
    ]
    sections.append(
        "\n".join(f"- {label}：{value}" for label, value in overview) + "\n"
    )
    if common:
        top = common[0]
        sections.append(
            f"- 覆盖 JD 最多的要求：**{escape_markdown(top.canonical_name)}**"
            f"（{top.distinct_job_count}/{stats.total_job_count} 份 JD）\n"
        )
    sections.append("\n")

    # 4. 共同要求（多 JD）：表格展示。
    sections.append("## 跨 JD 共同要求\n")
    if common:
        sections.append(
            "| 要求 | JD 覆盖数 | JD 覆盖率 | 实例数 | JD 级 importance |\n"
            "| --- | --- | --- | --- | --- |\n"
        )
        for item in common:
            sections.append(
                f"| {escape_table_cell(item.canonical_name)} | "
                f"{item.distinct_job_count} | "
                f"{item.distinct_job_count / stats.total_job_count:.0%} | "
                f"{item.instance_count} | "
                f"{escape_table_cell(_importance_label(item.importance_job_counts))} |\n"
            )
    else:
        sections.append("（无跨 JD 共同要求）\n")
    sections.append("\n")

    # 5. 长尾要求（单 JD）：表格展示，按实例数降序、名称升序。
    sections.append("## 单 JD 特有要求（长尾）\n")
    single_sorted = sorted(
        single, key=lambda item: (-item.instance_count, item.canonical_name)
    )
    if single_sorted:
        sections.append(
            "| 要求 | JD 覆盖数 | JD 覆盖率 | 实例数 | JD 级 importance |\n"
            "| --- | --- | --- | --- | --- |\n"
        )
        for item in single_sorted:
            sections.append(
                f"| {escape_table_cell(item.canonical_name)} | "
                f"{item.distinct_job_count} | "
                f"{item.distinct_job_count / stats.total_job_count:.0%} | "
                f"{item.instance_count} | "
                f"{escape_table_cell(_importance_label(item.importance_job_counts))} |\n"
            )
    else:
        sections.append("（无）\n")
    sections.append("\n")

    # 6. 证据追溯：每个 canonical 一节，列表展示来源实例。
    sections.append("## 证据追溯\n")
    for item in stats.canonical_items:
        sections.append(
            f"### {escape_markdown(item.canonical_name)}\n"
        )
        sections.append(
            f"来源：{item.distinct_job_count} 份 JD"
            f"（{'、'.join(f'JD {j}' for j in item.source_job_ids)}），"
            f"{item.instance_count} 个实例；JD 级 importance："
            f"{escape_markdown(_importance_label(item.importance_job_counts))}\n"
        )
        sections.extend(_evidence_lines(item.source_requirements))
        sections.append("\n")

    # 7. 方法与限制。
    sections.append("## 方法与限制\n")
    sections.append(
        "- 市场频率以**独立 JD 数**为主口径（同一 JD 中同一 canonical 的"
        "多个实例只贡献一次 JD 覆盖），实例数作为抽取粒度补充指标。\n"
        "- JD 级 importance 按 `must > preferred > mentioned > unknown` "
        "归并；实例级 importance 仅作诊断参考。\n"
        "- 排序：独立 JD 数降序 → 实例数降序 → 名称升序。\n"
        "- 每个 canonical 均可在「证据追溯」中回查来源 JD、原始要求与"
        "原文 evidence。\n"
        "- 本报告为归并批次的**可再生派生产物**：重新生成会覆盖旧文件，"
        "内容由同一批次确定性决定。\n"
        "- **样本限制**：当前仅 3 份 JD，统计结论不得外推为市场结论。\n"
    )
    return "".join(sections)


def validate_report_inputs(
    session_factory: sessionmaker,
    consolidation_id: int,
) -> list[str]:
    """报告生成前的完整数据一致性门禁，返回违规列表（空 = 通过）。

    复用生产归并验证（精确 requirement ID 覆盖、mapping 与 canonical
    来源分区一致、occurrence_count 一致），并追加：

    - canonical name 不含审核占位标记；
    - 全部 mapping requirement 都能回查到 requirement → extraction → JD。
    """
    failures: list[str] = []
    with session_factory() as session:
        record = session.scalar(
            select(JobConsolidation).where(JobConsolidation.id == consolidation_id)
        )
        if record is None:
            return [f"归并批次不存在：{consolidation_id}"]

        persisted = load_persisted_consolidation_result(
            session_factory, consolidation_id
        )
        failures.extend(validate_persisted_consistency(persisted))

        for item in persisted.result.canonical_requirements:
            if is_placeholder_canonical_name(item.canonical_name):
                failures.append(
                    f"canonical 名称包含审核占位标记：{item.canonical_name}"
                )

        # requirement → extraction → JD 回查完整性。
        requirement_ids = [
            mapping.requirement_id for mapping in persisted.result.mappings
        ]
        if requirement_ids:
            requirements = session.scalars(
                select(JobRequirement).where(
                    JobRequirement.id.in_(requirement_ids)
                )
            ).all()
            found = {row.id for row in requirements}
            missing = sorted(set(requirement_ids) - found)
            if missing:
                failures.append(
                    f"mapping 引用的 requirement 不存在：{missing}"
                )
            extractions = session.scalars(
                select(JobExtraction).where(
                    JobExtraction.id.in_({row.extraction_id for row in requirements})
                )
            ).all()
            extraction_job = {row.id: row.job_id for row in extractions}
            missing_job = sorted(
                row.id
                for row in requirements
                if row.extraction_id not in extraction_job
            )
            if missing_job:
                failures.append(
                    f"requirement 无法回查到 JD（extraction 缺失）：{missing_job}"
                )
    return failures
