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
from app.consolidation_validation import (
    validate_contract,
    validate_exact_identity,
    result_fingerprint,
)
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
        "--final-result",
        type=Path,
        help="审核应用后的最终结果 JSON（apply_review_decisions 输出）；"
        "提供时用其结果持久化，并核对来源运行与审核决定指纹",
    )
    parser.add_argument(
        "--review-decisions",
        type=Path,
        help="人工审核决定文件（与 --final-result 配套核对指纹）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL",
    )
    return parser.parse_args()


def _raw_identity(raw: dict) -> dict[str, object]:
    """raw 顶层身份字段；旧格式缺省时回退到首个运行的 metadata。"""
    runs = raw.get("runs") or []
    meta = runs[0].get("metadata") or {} if runs else {}
    return {
        "input_fingerprint": raw.get("input_fingerprint"),
        "extractor_version": raw.get("extractor_version"),
        "selected_job_ids": sorted(raw.get("selected_job_ids") or []),
        "model": raw.get("model") or meta.get("model"),
        "prompt_version": raw.get("prompt_version")
        or meta.get("prompt_version"),
        "schema_version": raw.get("schema_version")
        or meta.get("schema_version"),
        "run_count": raw.get("run_count")
        if raw.get("run_count") is not None
        else len(raw.get("runs") or []),
    }


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
    if review.get("approved_run_index") is None:
        print("人工审核未指定 approved_run_index，拒绝定稿。")
        return 1
    if review.get("approved_result_fingerprint") is None:
        print("人工审核未记录 approved_result_fingerprint，拒绝定稿。")
        return 1

    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    runs = raw.get("runs") or []
    if not 0 <= args.run_index < len(runs):
        print(f"run-index {args.run_index} 超出独立运行范围 0..{len(runs) - 1}")
        return 1
    selected_run = runs[args.run_index]
    raw_identity = _raw_identity(raw)

    # 审核应用后的最终结果（可选）：核对来源运行、审核决定与身份指纹。
    final_payload: dict | None = None
    if args.final_result is not None:
        if args.review_decisions is None:
            print("提供 --final-result 时必须同时提供 --review-decisions。")
            return 1
        if not args.final_result.exists() or not args.review_decisions.exists():
            print("--final-result 或 --review-decisions 不存在。")
            return 1
        import hashlib

        final_payload = json.loads(
            args.final_result.read_text(encoding="utf-8")
        )
        decisions_fingerprint = hashlib.sha256(
            args.review_decisions.read_bytes()
        ).hexdigest()
        source_identifier = selected_run.get(
            "run_identifier", f"run-{args.run_index}"
        )
        source_fingerprint = selected_run.get("result_fingerprint")
        if source_fingerprint is None:
            # 旧格式 raw 未记录指纹：由规范化结果确定性重算。
            source_fingerprint = result_fingerprint(
                RequirementConsolidationResult.model_validate(
                    selected_run["result"]
                )
            )
        if final_payload.get("source_run_identifier") != source_identifier:
            print("最终结果的来源运行标识与 raw 被选运行不一致，拒绝定稿。")
            return 1
        if final_payload.get("source_result_fingerprint") != source_fingerprint:
            print("最终结果的来源运行指纹与 raw 被选运行不一致，拒绝定稿。")
            return 1
        if final_payload.get("review_decisions_fingerprint") != (
            decisions_fingerprint
        ):
            print("最终结果与审核决定文件指纹不一致，拒绝定稿。")
            return 1
        for field in ("input_fingerprint", "extractor_version",
                      "model", "prompt_version", "schema_version"):
            if final_payload.get(field) != raw_identity[field]:
                print(
                    f"最终结果与 raw 身份不一致（{field}），拒绝定稿。"
                )
                return 1
        if final_payload.get("selected_job_ids") != raw_identity[
            "selected_job_ids"
        ]:
            print("最终结果与 raw 的 selected_job_ids 不一致，拒绝定稿。")
            return 1

    # 报告与私有原始结果必须共享并核对全部身份字段；不一致时拒绝定稿。
    report_identity = report.get("input_identity") or {}
    identity_checks = [
        ("input_fingerprint", report_identity.get("input_fingerprint"),
         raw_identity["input_fingerprint"]),
        ("extractor_version", report_identity.get("extractor_version"),
         raw_identity["extractor_version"]),
        ("selected_job_ids", report_identity.get("selected_job_ids"),
         raw_identity["selected_job_ids"]),
        ("model", report_identity.get("model"), raw_identity["model"]),
        ("prompt_version", report_identity.get("prompt_version"),
         raw_identity["prompt_version"]),
        ("schema_version", report_identity.get("schema_version"),
         raw_identity["schema_version"]),
        ("run_count", report.get("p0_4_stability", {}).get("run_count"),
         raw_identity["run_count"]),
    ]
    for field, report_value, raw_value in identity_checks:
        if report_value != raw_value:
            print(
                f"报告与私有原始结果身份不一致（{field}），拒绝定稿：\n"
                f"  报告={report_value}\n  raw={raw_value}"
            )
            return 1

    # 审核与选定运行绑定：批准运行必须与 --run-index 及结果指纹一致，
    # 不得只检查 reviewed_by 后放行任意运行。
    if review.get("approved_run_index") != args.run_index:
        print(
            f"审核批准的运行 run{review.get('approved_run_index')} 与 "
            f"--run-index {args.run_index} 不一致，拒绝定稿。"
        )
        return 1
    selected_fingerprint = selected_run.get("result_fingerprint")
    if selected_fingerprint is None:
        # 旧格式 raw 未记录指纹：由规范化结果确定性重算。
        selected_fingerprint = result_fingerprint(
            RequirementConsolidationResult.model_validate(
                selected_run["result"]
            )
        )
    if selected_fingerprint != review.get("approved_result_fingerprint"):
        print("被选运行的结果指纹与审核记录不一致，拒绝定稿。")
        return 1

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
            if final_payload is not None:
                try:
                    result = RequirementConsolidationResult.model_validate(
                        final_payload["result"]
                    )
                except ValueError as exc:
                    print(f"最终规范化结果不合法，拒绝定稿：{exc}")
                    return 1
                if result_fingerprint(result) != final_payload.get(
                    "result_fingerprint"
                ):
                    print("最终结果指纹与记录不一致，拒绝定稿。")
                    return 1
            else:
                try:
                    result = RequirementConsolidationResult.model_validate(
                        selected_run["result"]
                    )
                except ValueError as exc:
                    print(f"规范化结果不合法，拒绝定稿：{exc}")
                    return 1

            # 重新执行全部确定性合同检查：coverage 与结构违规；
            # 再用真实输入 ID 集合做精确覆盖校验（数量相同但 ID 被
            # 替换、来源遗漏、归属冲突都会被拒绝）。
            expected_ids = {
                occurrence.requirement_id
                for occurrence in selection.consolidation_input.occurrences
            }
            violations = validate_contract(
                result,
                expected_ids=expected_ids,
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
            identity_failures = validate_exact_identity(result, expected_ids)
            if identity_failures:
                print("精确 ID 覆盖校验未通过，拒绝定稿：")
                for failure in identity_failures:
                    print(f"  - {failure}")
                return 1
            if final_payload is None and result_fingerprint(result) != (
                selected_fingerprint
            ):
                print("规范化结果指纹与 raw 记录不一致，拒绝定稿。")
                return 1

            scope_key = scope_key_for(job_ids or None)
            raw_response = dict(selected_run.get("raw_response") or {})
            if final_payload is not None:
                raw_response["review_decisions_fingerprint"] = (
                    final_payload["review_decisions_fingerprint"]
                )
                raw_response["source_run_identifier"] = (
                    final_payload["source_run_identifier"]
                )
            batch, created = persist_consolidation(
                session,
                selection,
                result,
                raw_response,
                metadata,
                scope_key,
            )
            print(f"归并批次 ID：{batch.id}（{'新建' if created else '已存在，幂等跳过'}）")
            print(f"范围：{scope_key}")
            print(f"输入指纹：{selection.input_fingerprint}")
            print(f"抽取器版本：{selection.extractor_version}")
            print(f"归并器版本：{metadata.consolidator_version}")
            if final_payload is not None:
                print(
                    f"来源运行：{final_payload['source_run_identifier']}"
                    f"（{review.get('reviewed_by')} 审核）"
                )
                print(
                    "审核决定指纹："
                    f"{final_payload['review_decisions_fingerprint'][:16]}…"
                )
            else:
                print(f"来源运行：run{args.run_index}（{review.get('reviewed_by')} 审核）")
            return 0
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
