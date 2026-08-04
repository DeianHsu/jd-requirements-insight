"""从已完成的 P0-4 验收运行定稿正式归并批次（不调用模型）。

保证“被人工审核和验收的结果 = 最终持久化的结果”：

1. 读取脱敏验收报告：hard_gate_failures 必须为空，
   manual_cluster_review.reviewed_by 必须已完成；
2. 读取私有原始结果，选择 --run-index 指定的独立运行；
3. 重新加载数据库选择，核对 input_fingerprint / extractor_version /
   selected_job_ids 与验收原始结果一致；
4. 重新执行确定性合同检查（coverage=100%、结构违规=0）；
5. 使用现有持久化逻辑保存（重复定稿幂等，返回已有批次）。

本脚本不初始化 LLM 客户端、不接受 --execute、不调用任何模型。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.consolidation import (
    ConsolidatorMetadata,
    load_consolidation_selection,
    persist_consolidation,
    scope_key_for,
)
from app.consolidation_validation import validate_contract
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.requirement_consolidation import RequirementConsolidationResult


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="脱敏验收报告路径（P0-4 acceptance report）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        required=True,
        help="私有原始结果路径（含证据，不提交）",
    )
    parser.add_argument(
        "--run-index",
        type=int,
        default=0,
        help="选择定稿的独立运行索引（默认 0）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.report.exists():
        print(f"验收报告不存在：{args.report}")
        return 1
    if not args.raw_output.exists():
        print(f"私有原始结果不存在：{args.raw_output}")
        return 1

    report = json.loads(args.report.read_text(encoding="utf-8"))
    hard_gate_failures = report.get("hard_gate_failures") or []
    if hard_gate_failures:
        print(f"验收未通过，拒绝定稿：{hard_gate_failures}")
        return 1
    review = report.get("manual_cluster_review") or {}
    if not review.get("reviewed_by"):
        print("人工 cluster 审核未完成（reviewed_by 为空），拒绝定稿。")
        return 1

    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    runs = raw.get("runs") or []
    if not 0 <= args.run_index < len(runs):
        print(f"run-index {args.run_index} 超出独立运行范围 0..{len(runs) - 1}")
        return 1
    selected_run = runs[args.run_index]

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job_ids = set(raw.get("selected_job_ids") or [])
            selection = load_consolidation_selection(
                session, job_ids=job_ids or None
            )
            # 输入指纹核对：验收输入必须与数据库当前输入一致。
            if selection.input_fingerprint != raw.get("input_fingerprint"):
                print("输入指纹不一致，拒绝定稿（输入已变化，请重新验收）。")
                print(f"  验收指纹：{raw.get('input_fingerprint')}")
                print(f"  当前指纹：{selection.input_fingerprint}")
                return 1
            # 抽取器版本核对。
            if selection.extractor_version != raw.get("extractor_version"):
                print("抽取器版本不一致，拒绝定稿。")
                return 1

            metadata_dict = selected_run.get("metadata") or {}
            metadata = ConsolidatorMetadata(
                model_name=metadata_dict.get("model", ""),
                prompt_version=metadata_dict.get("prompt_version"),
                schema_version=metadata_dict.get("schema_version"),
            )
            try:
                result = RequirementConsolidationResult.model_validate(
                    selected_run["result"]
                )
            except ValueError as exc:
                print(f"规范化结果不合法，拒绝定稿：{exc}")
                return 1

            # 重新执行全部确定性合同检查。
            violations = validate_contract(
                result,
                expected_requirement_count=len(
                    selection.consolidation_input.occurrences
                ),
            )
            if violations.coverage != 1.0:
                print(f"重新检查 coverage={violations.coverage}，拒绝定稿。")
                return 1
            if violations.structural_violation_count != 0:
                print(
                    "重新检查结构违规 "
                    f"{violations.structural_violation_count}，拒绝定稿。"
                )
                return 1

            scope_key = scope_key_for(job_ids or None)
            batch, created = persist_consolidation(
                session,
                selection,
                result,
                selected_run.get("raw_response") or {},
                metadata,
                scope_key,
            )
            print(f"归并批次 ID：{batch.id}（{'新建' if created else '已存在，幂等跳过'}）")
            print(f"范围：{scope_key}")
            print(f"输入指纹：{selection.input_fingerprint}")
            print(f"抽取器版本：{selection.extractor_version}")
            print(f"归并器版本：{metadata.consolidator_version}")
            print(f"来源运行：run{args.run_index}（{review.get('reviewed_by')} 审核）")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
