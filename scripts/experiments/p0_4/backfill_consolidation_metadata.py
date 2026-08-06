"""离线补齐旧定稿归并批次的审核元数据（无模型，重放式校验）。

0a32ae9 之前定稿的批次 `raw_response` 只含 review_decisions_fingerprint
与 source_run_identifier，缺 reviewed_by / reviewed_at /
approved_run_index / approved_result_fingerprint / final_result_fingerprint，
无法通过加强后的归并定稿门禁。

本脚本以**重放式校验**补齐（不改变结果内容）：从验收原始结果中取出
被批准运行的聚类结果，重新应用审核决定（与 apply_review_decisions
相同的 `_apply_decisions` 逻辑）生成最终结果，并验证重放结果与当前
持久化结果一致；同时核对历史最终结果（apply_review_decisions 输出）
的声明字段链与批次身份。验证链条：

1. 批次必须已具备两个核心锚点：review_decisions_fingerprint 与
   source_run_identifier（不允许凭空签发）；
2. 验收报告 manual_cluster_review 完整，approved_run_index 类型合法、
   reviewed_at 可解析，且与批次 source_run_identifier（run-N）一致；
3. raw 中批准运行的结果指纹（缺失时按结果重算）等于验收报告
   approved_result_fingerprint，等于历史最终结果的
   source_result_fingerprint；
4. 历史最终结果的批次身份（input_fingerprint/extractor_version/
   selected_job_ids/model/prompt_version/schema_version）与批次一致，
   其 review_decisions_fingerprint / source_run_identifier 与批次一致，
   **其 result 内容指纹与其声明 result_fingerprint 一致**；
5. 历史最终结果指纹等于当前数据库持久化结果指纹（复算），且
   **重放（批准运行 + 审核决定）结果指纹也等于当前持久化结果**；
6. raw 的整轮身份（存在字段）与批次一致（缺失字段由验收报告身份兜底）；
   批准运行的记录指纹（存在时）与其 result 内容一致；
7. 批次已有任一目标字段与待补值不同 → 拒绝，不覆盖。

**证明强度说明**：本脚本对"当前结果可由来源运行 + 审核决定确定性重建"
给出机器可验证证明（重放）；对审核人/审核时间（reviewed_by /
reviewed_at 等）只能给出验收报告文件的声明记录——旧批次未保存报告
文件指纹锚点，人工审核行为本身无法被机器复核，这是人工审核体系的
固有边界，不代表审核未发生。

已有字段一致则幂等跳过。输出补齐记录
`reports/P0-4/consolidation-backfill-<id>.json`。
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path

from app.consolidation import load_consolidation_selection
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
from scripts.experiments.p0_4.apply_review_decisions import _apply_decisions


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
    # 非法类型/格式必须干净拒绝（不崩溃、不写库）。
    if not isinstance(review.get("approved_run_index"), int) or isinstance(
        review.get("approved_run_index"), bool
    ):
        findings.append(
            f"approved_run_index 类型非法：{review.get('approved_run_index')!r}"
        )
    reviewed_at = review.get("reviewed_at")
    if reviewed_at:
        try:
            datetime.datetime.fromisoformat(
                str(reviewed_at).replace("Z", "+00:00")
            )
        except ValueError:
            findings.append(f"reviewed_at 格式无效：{reviewed_at}")

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
            # raw 整轮身份必须与批次一致（防止传错批次的 raw 文件）。
            # 旧格式 raw（如 acceptance-final.json）顶层缺少
            # prompt_version/schema_version 等字段：存在的字段必须与
            # 批次一致，缺失字段由验收报告身份（已校验 == 批次）兜底。
            raw_identity = {
                field: raw.get(field)
                for field in (
                    "input_fingerprint",
                    "extractor_version",
                    "selected_job_ids",
                    "model",
                    "prompt_version",
                    "schema_version",
                )
            }
            for field, expected in batch_identity.items():
                raw_value = raw_identity.get(field)
                if raw_value is not None and raw_value != expected:
                    findings.append(
                        f"raw 整轮 {field}（{raw_value}）与批次"
                        f"（{expected}）不一致"
                    )
            if (
                not isinstance(approved_index, int)
                or isinstance(approved_index, bool)
                or not 0 <= approved_index < len(runs)
            ):
                findings.append(
                    f"批准运行索引（{approved_index!r}）非法或超出 raw 运行范围"
                )
            else:
                # 批准运行的记录指纹必须与其 result 内容一致（存在时），
                # 防止记录指纹与内容被分别篡改。
                run = runs[approved_index]
                try:
                    run_content_fp = result_fingerprint(
                        RequirementConsolidationResult.model_validate(run["result"])
                    )
                except (KeyError, ValueError) as exc:
                    findings.append(f"批准运行 result 不合法：{exc}")
                    run_content_fp = None
                recorded_run_fp = run.get("result_fingerprint")
                if run_content_fp is not None and recorded_run_fp not in (
                    None,
                    run_content_fp,
                ):
                    findings.append(
                        "批准运行记录指纹与其 result 内容指纹不一致"
                    )
                run_identifier = run.get("run_identifier")
                if run_identifier not in (None, f"run-{approved_index}"):
                    findings.append(
                        f"批准运行标识（{run_identifier}）与 run-"
                        f"{approved_index} 不一致"
                    )
                approved_fp = _run_fingerprint(run)
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
            # 历史最终结果的批次身份必须与批次一致（docstring 声明补实现）。
            for field, expected in batch_identity.items():
                if final.get(field) != expected:
                    findings.append(
                        f"历史最终结果 {field}（{final.get(field)}）与批次"
                        f"（{expected}）不一致"
                    )
            # 历史最终结果的 result 内容指纹必须与其声明指纹一致
            # （防止 result 内容被改而声明指纹未同步）。
            try:
                final_content_fp = result_fingerprint(
                    RequirementConsolidationResult.model_validate(final["result"])
                )
            except (KeyError, ValueError) as exc:
                findings.append(f"历史最终结果 result 不合法：{exc}")
                final_content_fp = None
            if final_content_fp is not None and final_content_fp != final.get(
                "result_fingerprint"
            ):
                findings.append(
                    "历史最终结果内容指纹与其声明 result_fingerprint 不一致"
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

            # 4b. 真正重放：批准运行 + 审核决定 → 重算最终结果，必须与
            # 当前持久化结果一致（证明当前结果确由批准运行与审核决定
            # 确定性产生，而不只是声明字段自洽）。
            if (
                isinstance(approved_index, int)
                and not isinstance(approved_index, bool)
                and 0 <= approved_index < len(runs)
                and current_fingerprint is not None
            ):
                try:
                    decisions_payload = json.loads(
                        args.review_decisions.read_text(encoding="utf-8")
                    )
                    source_result = RequirementConsolidationResult.model_validate(
                        runs[approved_index]["result"]
                    )
                    selection = load_consolidation_selection(
                        session,
                        job_ids=set(record.selected_job_ids) or None,
                        extractor_version=record.extractor_version,
                    )
                    raw_name_by_id = {
                        occ.requirement_id: occ.requirement.raw_name
                        for occ in selection.consolidation_input.occurrences
                    }
                    replayed = _apply_decisions(
                        source_result,
                        decisions_payload.get("decisions") or [],
                        raw_name_by_id,
                    )
                    replayed_fp = result_fingerprint(replayed)
                except (ValueError, KeyError) as exc:
                    findings.append(f"重放（批准运行 + 审核决定）失败：{exc}")
                    replayed_fp = None
                if replayed_fp is not None and replayed_fp != current_fingerprint:
                    findings.append(
                        "重放结果与当前持久化归并结果不一致（当前结果非"
                        "批准运行 + 审核决定的确定性产物）"
                    )

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
