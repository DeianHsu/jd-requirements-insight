"""离线补齐旧定稿归并批次的审核元数据（无模型）。

0a32ae9 之前定稿的批次 `raw_response` 只含 review_decisions_fingerprint
与 source_run_identifier，缺 reviewed_by / reviewed_at /
approved_run_index / approved_result_fingerprint / final_result_fingerprint，
无法通过加强后的归并定稿门禁。本脚本从 P0-4 验收报告
（manual_cluster_review）与审核决定文件复算补齐，**不改变结果内容**：

- 校验验收报告与批次身份一致（input_fingerprint / extractor_version /
  selected_job_ids / model / prompt_version / schema_version）；
- 校验 manual_cluster_review 完整且 approved_run_index 与批次
  source_run_identifier（run-N）一致；
- 校验 review-decisions 文件指纹与批次已有 review_decisions_fingerprint
  一致（缺失时补齐）；
- final_result_fingerprint 由当前持久化结果复算（不依赖旧产物）；
- 可选 --raw-output 时校验 approved_result_fingerprint 与 raw 中
  批准运行的结果指纹一致。

已有字段一致则幂等跳过；任何不一致拒绝写入。输出补齐记录
`reports/P0-4/consolidation-backfill-<id>.json`。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.consolidation_validation import (
    load_persisted_consolidation_result,
    result_fingerprint,
)
from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.models import JobConsolidation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--consolidation-id", type=int, required=True)
    parser.add_argument(
        "--acceptance-report",
        type=Path,
        required=True,
        help="P0-4 验收报告（含 manual_cluster_review）",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        required=True,
        help="人工审核决定文件（review-decisions*.json）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        default=None,
        help="可选：验收私有原始结果（校验批准运行指纹）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.acceptance_report.exists() or not args.review_decisions.exists():
        print("验收报告或审核决定文件不存在。")
        return 1

    report = json.loads(args.acceptance_report.read_text(encoding="utf-8"))
    decisions_fingerprint = hashlib.sha256(
        args.review_decisions.read_bytes()
    ).hexdigest()
    review = report.get("manual_cluster_review") or {}
    identity = report.get("input_identity") or {}
    findings: list[str] = []

    if report.get("hard_gate_failures"):
        findings.append(f"验收报告存在 hard gate：{report['hard_gate_failures']}")
    for field in (
        "reviewed_by",
        "reviewed_at",
        "approved_run_index",
        "approved_result_fingerprint",
    ):
        if review.get(field) in (None, ""):
            findings.append(f"验收报告 manual_cluster_review 缺少 {field}")
    for field in (
        "input_fingerprint",
        "extractor_version",
        "selected_job_ids",
        "model",
        "prompt_version",
        "schema_version",
    ):
        if identity.get(field) in (None, ""):
            findings.append(f"验收报告 input_identity 缺少 {field}")

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            record = session.query(JobConsolidation).filter(
                JobConsolidation.id == args.consolidation_id
            ).one_or_none()
            if record is None:
                print(f"归并批次不存在：{args.consolidation_id}")
                return 1
            existing_raw = record.raw_response or {}

            # 身份一致性：验收报告必须描述同一批次的同一输入。
            batch_identity = {
                "input_fingerprint": record.input_fingerprint,
                "extractor_version": record.extractor_version,
                "selected_job_ids": list(record.selected_job_ids),
                "model": record.model_name,
                "prompt_version": record.prompt_version,
                "schema_version": record.schema_version,
            }
            for field, expected in batch_identity.items():
                if identity.get(field) != expected:
                    findings.append(
                        f"验收报告 {field}（{identity.get(field)}）与批次"
                        f"（{expected}）不一致"
                    )

            # 批准运行与批次来源运行一致（source_run_identifier=run-N）。
            approved_index = review.get("approved_run_index")
            expected_source = f"run-{approved_index}"
            if existing_raw.get("source_run_identifier") not in (
                None,
                expected_source,
            ):
                findings.append(
                    f"批次来源运行（{existing_raw.get('source_run_identifier')}）"
                    f"与验收报告批准运行（{expected_source}）不一致"
                )

            # 审核决定指纹：已有必须一致，缺失则补齐。
            if existing_raw.get("review_decisions_fingerprint") not in (
                None,
                decisions_fingerprint,
            ):
                findings.append(
                    "批次审核决定指纹与 --review-decisions 文件不一致"
                )

            # 批准结果指纹：提供 raw 时校验与批准运行一致。
            approved_fingerprint = review.get("approved_result_fingerprint")
            if args.raw_output is not None and args.raw_output.exists():
                raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
                runs = raw.get("runs") or []
                if not 0 <= approved_index < len(runs):
                    findings.append(
                        f"批准运行索引（{approved_index}）超出 raw 运行范围"
                    )
                else:
                    run_fingerprint = runs[approved_index].get(
                        "result_fingerprint"
                    )
                    if run_fingerprint != approved_fingerprint:
                        findings.append(
                            "验收报告批准结果指纹与 raw 批准运行不一致"
                        )

            # 最终结果指纹由当前持久化结果复算。
            try:
                persisted = load_persisted_consolidation_result(
                    session_factory, args.consolidation_id
                )
                final_fingerprint = result_fingerprint(persisted.result)
            except ValueError as exc:
                findings.append(f"持久化归并结果不可读：{exc}")

            if findings:
                print("补齐校验未通过，拒绝写入：")
                for finding in findings:
                    print(f"  - {finding}")
                return 1

            updated = dict(existing_raw)
            changed: list[str] = []
            additions = {
                "review_decisions_fingerprint": decisions_fingerprint,
                "source_run_identifier": expected_source,
                "reviewed_by": review["reviewed_by"],
                "reviewed_at": review["reviewed_at"],
                "approved_run_index": approved_index,
                "approved_result_fingerprint": approved_fingerprint,
                "final_result_fingerprint": final_fingerprint,
            }
            for field, value in additions.items():
                if updated.get(field) != value:
                    updated[field] = value
                    changed.append(field)
            if changed:
                record.raw_response = updated
                session.commit()
            print(
                f"批次 #{args.consolidation_id} 审核元数据"
                + (f"补齐：{', '.join(changed)}" if changed else "已一致，无需修改")
            )
    finally:
        engine.dispose()

    record_out = {
        "consolidation_id": args.consolidation_id,
        "acceptance_report": str(args.acceptance_report),
        "review_decisions_fingerprint": decisions_fingerprint,
        "reviewed_by": review.get("reviewed_by"),
        "reviewed_at": review.get("reviewed_at"),
        "approved_run_index": review.get("approved_run_index"),
        "approved_result_fingerprint": review.get(
            "approved_result_fingerprint"
        ),
        "final_result_fingerprint": final_fingerprint,
        "checked_at": "2026-08-07T00:00:00+00:00",
        "passed": True,
    }
    output = Path(f"reports/P0-4/consolidation-backfill-{args.consolidation_id}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(record_out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"补齐记录：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
