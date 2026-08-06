"""离线补齐旧定稿归并批次的审核元数据（无模型，重放式校验）。

0a32ae9 之前定稿的批次 `raw_response` 只含 review_decisions_fingerprint
与 source_run_identifier，缺 reviewed_by / reviewed_at /
approved_run_index / approved_result_fingerprint / final_result_fingerprint，
无法通过加强后的归并定稿门禁。

本脚本以**重放式校验**补齐（不改变结果内容）：用历史验收报告
（manual_cluster_review）、验收原始结果（raw）与历史最终结果
（apply_review_decisions 输出）重建"批准运行 → 审核决定 → 最终结果"
的完整证据链，并验证链条每一环与批次现有身份一致：

1. 批次必须已具备两个核心锚点：review_decisions_fingerprint 与
   source_run_identifier（不允许凭空签发）；
2. 验收报告 manual_cluster_review 完整，且 approved_run_index 与
   批次 source_run_identifier（run-N）一致；
3. raw 中批准运行的结果指纹（缺失时按结果重算）等于验收报告
   approved_result_fingerprint，等于历史最终结果的
   source_result_fingerprint；
4. 历史最终结果的 review_decisions_fingerprint / source_run_identifier
   / 批次身份（input_fingerprint/extractor_version/selected_job_ids/
   model/prompt_version/schema_version）与批次一致；
5. 历史最终结果指纹等于当前数据库持久化结果指纹（复算）；
6. 批次已有任一目标字段与待补值不同 → 拒绝，不覆盖。

已有字段一致则幂等跳过。输出补齐记录
`reports/P0-4/consolidation-backfill-<id>.json`。
"""
from __future__ import annotations

import argparse
import datetime
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
from app.requirement_consolidation import RequirementConsolidationResult


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
        required=True,
        help="验收私有原始结果（runs 数组；批准运行指纹校验必需）",
    )
    parser.add_argument(
        "--final-result",
        type=Path,
        required=True,
        help="历史最终结果（apply_review_decisions 输出，含证据链字段）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def _run_fingerprint(run: dict) -> str:
    """返回运行结果指纹；缺失时按结果重算（兼容旧格式 raw）。"""
    recorded = run.get("result_fingerprint")
    if recorded:
        return recorded
    return result_fingerprint(
        RequirementConsolidationResult.model_validate(run["result"])
    )


def main() -> int:
    args = parse_args()
    for path in (
        args.acceptance_report,
        args.review_decisions,
        args.raw_output,
        args.final_result,
    ):
        if not path.exists():
            print(f"文件不存在：{path}")
            return 1

    report = json.loads(args.acceptance_report.read_text(encoding="utf-8"))
    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    final = json.loads(args.final_result.read_text(encoding="utf-8"))
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
            pending = {
                "review_decisions_fingerprint": hashlib.sha256(
                    args.review_decisions.read_bytes()
                ).hexdigest(),
                "source_run_identifier": f"run-{review.get('approved_run_index')}",
                "reviewed_by": review.get("reviewed_by"),
                "reviewed_at": review.get("reviewed_at"),
                "approved_run_index": review.get("approved_run_index"),
                "approved_result_fingerprint": review.get(
                    "approved_result_fingerprint"
                ),
                "final_result_fingerprint": None,  # 由持久化结果复算
            }

            # 1. 锚点必须已存在（不允许凭空签发核心身份）。
            for anchor in ("review_decisions_fingerprint", "source_run_identifier"):
                if existing_raw.get(anchor) in (None, ""):
                    findings.append(f"批次缺少核心锚点 {anchor}，拒绝凭空补齐")

            # 2. 验收报告身份与批次一致。
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

            # 3. 历史最终结果与验收报告/批次证据链一致。
            approved_index = review.get("approved_run_index")
            runs = raw.get("runs") or []
            if not 0 <= approved_index < len(runs):
                findings.append(
                    f"批准运行索引（{approved_index}）超出 raw 运行范围"
                )
            else:
                approved_fp = _run_fingerprint(runs[approved_index])
                if approved_fp != review.get("approved_result_fingerprint"):
                    findings.append(
                        "raw 批准运行指纹与验收报告 approved_result_fingerprint 不一致"
                    )
            for field in (
                "source_run_identifier",
                "source_result_fingerprint",
                "review_decisions_fingerprint",
                "result_fingerprint",
            ):
                if final.get(field) in (None, ""):
                    findings.append(f"历史最终结果缺少 {field}")
            if final.get("source_run_identifier") != pending["source_run_identifier"]:
                findings.append(
                    f"历史最终结果来源运行（{final.get('source_run_identifier')}）"
                    f"与验收报告批准运行（{pending['source_run_identifier']}）不一致"
                )
            if final.get("source_result_fingerprint") != review.get(
                "approved_result_fingerprint"
            ):
                findings.append(
                    "历史最终结果来源指纹与验收报告批准结果指纹不一致"
                )
            if final.get("review_decisions_fingerprint") != pending[
                "review_decisions_fingerprint"
            ]:
                findings.append(
                    "历史最终结果审核决定指纹与 --review-decisions 文件不一致"
                )

            # 4. 当前持久化结果 == 历史最终结果（复算）。
            try:
                persisted = load_persisted_consolidation_result(
                    session_factory, args.consolidation_id
                )
                current_fingerprint = result_fingerprint(persisted.result)
            except ValueError as exc:
                findings.append(f"持久化归并结果不可读：{exc}")
                current_fingerprint = None
            if final.get("result_fingerprint") != current_fingerprint:
                findings.append(
                    "历史最终结果指纹与当前持久化归并结果不一致"
                )
            pending["final_result_fingerprint"] = current_fingerprint

            # 5. 已有字段冲突拒绝（不覆盖）。
            for field, value in pending.items():
                if value is None:
                    continue
                if existing_raw.get(field) not in (None, value):
                    findings.append(
                        f"批次已有 {field}（{existing_raw.get(field)}）"
                        f"与待补值（{value}）冲突"
                    )

            if findings:
                print("补齐校验未通过，拒绝写入：")
                for finding in findings:
                    print(f"  - {finding}")
                return 1

            updated = dict(existing_raw)
            changed: list[str] = []
            for field, value in pending.items():
                if value is not None and updated.get(field) != value:
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
        "raw_output": str(args.raw_output),
        "final_result": str(args.final_result),
        "review_decisions_fingerprint": pending["review_decisions_fingerprint"],
        "reviewed_by": review.get("reviewed_by"),
        "reviewed_at": review.get("reviewed_at"),
        "approved_run_index": review.get("approved_run_index"),
        "approved_result_fingerprint": review.get(
            "approved_result_fingerprint"
        ),
        "final_result_fingerprint": pending["final_result_fingerprint"],
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
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
