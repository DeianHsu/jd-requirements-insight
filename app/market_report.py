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
from app.finalization import validate_consolidation_finalization
from app.market_analysis import MarketStatistics
from app.models import (
    JobConsolidation,
    JobDescription,
    JobExtraction,
    JobRequirement,
)
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


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


IMPORTANCE_LABELS = {
    "must": "明确必需", "preferred": "加分", "mentioned": "普通提及", "unknown": "未明确",
}


def escape_markdown(text: str) -> str:
    """转义正文与 HTML，避免原文破坏折叠区结构。"""
    from html import escape

    return "".join(_MARKDOWN_ESCAPES.get(char, char) for char in escape(text))


def escape_table_cell(text: str) -> str:
    """转义表格内容中的 Markdown、竖线和换行。"""
    return escape_markdown(text).replace("|", "\\|").replace("\r", "").replace("\n", " ").strip()


def _evidence_block(source_requirements: tuple[dict[str, Any], ...]) -> str:
    """用中文说明和原文引用呈现证据，隐藏数据库实例与内部属性。"""
    blocks = []
    for requirement in source_requirements:
        label = IMPORTANCE_LABELS.get(requirement.get("importance"), "未明确")
        if requirement.get("group_logic") == "any_of":
            label += "；任选其一，不要求全部掌握"
        lines = [
            f"- JD {requirement.get('job_id', '未知')}："
            f"**{escape_markdown(str(requirement['raw_name']))}**",
            f"  - {label}",
            "  - 证据：",
        ]
        lines.extend(
            f"    > {escape_markdown(line)}"
            for line in str(requirement.get("evidence", "")).strip().splitlines()
        )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def _ranking_table(items, total: int) -> str:
    lines = [
        "| 排名 | 要求 | 出现 JD 数 | 占比 | 明确必需 | 加分 | 普通提及 | 未明确 | 来源 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rank, item in enumerate(items, 1):
        optional = any(
            source.get("group_logic") == "any_of" for source in item.source_requirements
        )
        name = escape_table_cell(item.canonical_name)
        if optional:
            name += "（含任选条件）"
        counts = item.importance_job_counts
        sources = "、".join(f"JD {job_id}" for job_id in item.source_job_ids)
        ratio = item.distinct_job_count / total if total else 0
        lines.append(
            f"| {rank} | {name} | {item.distinct_job_count}/{total} | {ratio:.0%} | "
            f"{counts.get('must', 0)} | {counts.get('preferred', 0)} | "
            f"{counts.get('mentioned', 0)} | {counts.get('unknown', 0)} | {sources} |"
        )
    return "\n".join(lines) if items else "暂无可统计的要求。"


def build_market_report(
    stats: MarketStatistics, provenance_note: str | None = None
) -> str:
    """输出频率榜、准备优先级榜及折叠证据；内部身份留在数据库。"""
    frequency = sorted(
        stats.canonical_items,
        key=lambda item: (-item.distinct_job_count, item.canonical_name),
    )
    priority = sorted(
        stats.canonical_items,
        key=lambda item: (
            -item.importance_job_counts.get("must", 0),
            -item.importance_job_counts.get("preferred", 0),
            -item.distinct_job_count,
            item.canonical_name,
        ),
    )
    sections = [
        "# 岗位要求市场分析报告",
        f"> 样本范围：{stats.total_job_count} 份 JD，共 {stats.canonical_count} 项要求；"
        "以下排序仅供本批岗位的学习与求职准备参考，不代表整个行业。",
        "各列数字均为独立 JD 篇数，同一 JD 的同一要求只计一次。"
        "明确必需＝明确要求具备；加分＝优先或加分条件；普通提及＝提到但未明确要求；"
        "未明确＝无法判断。重复出现时按明确必需、加分、普通提及、未明确的顺序归类。",
        "标有“含任选条件”的要求可能是若干选项之一；必需篇数也可能指必须满足该任选组，"
        "不代表必须掌握每个选项。具体组合见末尾原文。",
        "## 出现频率榜",
        "按出现 JD 数从多到少排列；篇数相同时按名称排列。",
        _ranking_table(frequency, stats.total_job_count),
        "## 准备优先级榜",
        "先按明确必需的 JD 数降序，再按加分 JD 数降序，最后按总出现 JD 数降序；"
        "全部相同时按名称排列。频繁被提及但很少被要求的项目会排在必需条件之后。"
        "这是需求侧参考，不计个人基础或学习成本。",
        _ranking_table(priority, stats.total_job_count),
        "## 来源与原文证据",
        "<details>\n<summary>展开来源 JD 清单</summary>",
    ]
    sections.extend(
        f"- JD {job['job_id']}：{escape_markdown(str(job['company']))}｜"
        f"{escape_markdown(str(job['title']))}｜"
        f"{escape_markdown(str(job.get('city') or '城市未注明'))}"
        for job in stats.job_summaries
    )
    sections.append("</details>")
    from html import escape

    for item in frequency:
        sections.extend([
            f"<details>\n<summary>{escape(item.canonical_name)}"
            f"（{item.distinct_job_count} 份 JD）</summary>",
            _evidence_block(item.source_requirements),
            "</details>",
        ])
    if provenance_note:
        sections.extend([
            "## 数据来源说明",
            "部分历史来源缺少完整可验证记录，已按限定范围允许使用；相关结果的可追溯性存在限制。",
            "<details>\n<summary>展开来源绑定说明</summary>",
            f"**上游来源绑定**：{escape_markdown(provenance_note)}",
            "</details>",
        ])
    return "\n\n".join(sections) + "\n"


def validate_report_inputs(
    session_factory: sessionmaker,
    consolidation_id: int,
) -> list[str]:
    """报告生成前的完整数据一致性门禁，返回违规列表（空 = 通过）。

    复用生产归并验证（精确 requirement ID 覆盖、mapping 与 canonical
    来源分区一致、occurrence_count 一致、mapping 无重复、canonical
    无空分区、无未知引用），并追加：

    - canonical name 不含审核占位标记；
    - 全部 mapping requirement 都能回查到 requirement → extraction → JD；
    - canonical 记录数与有效统计项数一致（无孤儿 canonical）；
    - 批次 selected_job_ids 全部真实存在；
    - requirement 回查到的来源 JD 全部属于批次范围。
    """
    failures: list[str] = []
    with session_factory() as session:
        record = session.scalar(
            select(JobConsolidation).where(JobConsolidation.id == consolidation_id)
        )
        if record is None:
            return [f"归并批次不存在：{consolidation_id}"]

        try:
            persisted = load_persisted_consolidation_result(
                session_factory, consolidation_id
            )
        except ValueError as exc:
            # 覆盖空 canonical、重复 mapping、未知 canonical 引用等
            # 结构合同违规（数据库损坏时干净拒绝，不输出 traceback）。
            return [f"归并结构合同校验失败：{exc}"]

        failures.extend(validate_persisted_consistency(persisted))
        failures.extend(validate_consolidation_finalization(record, persisted))

        for item in persisted.result.canonical_requirements:
            if is_placeholder_canonical_name(item.canonical_name):
                failures.append(
                    f"canonical 名称包含审核占位标记：{item.canonical_name}"
                )

        # canonical 记录数与有效统计项数一致（统计按 mapping 聚合，
        # 无 mapping 的孤儿/空 canonical 会被统计阶段静默忽略）。
        mapped_canonical_ids = {
            mapping.canonical_requirement_id
            for mapping in persisted.result.mappings
        }
        if len(persisted.result.canonical_requirements) != len(
            mapped_canonical_ids
        ):
            failures.append(
                f"canonical 记录数（{len(persisted.result.canonical_requirements)}）"
                f"与有效统计项数（{len(mapped_canonical_ids)}）不一致，"
                "存在无 mapping 的 canonical"
            )

        # 批次选定 JD 必须全部真实存在（覆盖率分母可信）。
        selected_job_ids = sorted(record.selected_job_ids)
        if selected_job_ids:
            job_rows = session.scalars(
                select(JobDescription.id).where(
                    JobDescription.id.in_(selected_job_ids)
                )
            ).all()
            missing_jobs = sorted(set(selected_job_ids) - set(job_rows))
            if missing_jobs:
                failures.append(
                    f"批次选定 JD 不存在：{missing_jobs}"
                )
        else:
            failures.append("批次 selected_job_ids 为空，覆盖率分母不可信")

        # requirement → extraction → JD 回查完整性，且来源 JD 必须
        # 属于批次范围。
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
            out_of_scope = sorted(
                {
                    extraction_job[row.extraction_id]
                    for row in requirements
                    if row.extraction_id in extraction_job
                }
                - set(selected_job_ids)
            )
            if out_of_scope:
                failures.append(
                    f"requirement 来源 JD 超出批次范围：{out_of_scope}"
                )
    return failures
