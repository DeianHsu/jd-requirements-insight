"""正式抽取离线来源复核（无模型）。

对已定稿的正式抽取记录，核对它是否确为某次完整通过验收的实验中被
人工批准的那一个运行：

- 验收报告顶层 passed=true 且顶层 hard gate 为空；
- expected_runs == successful_runs、failed_runs == 0；
- raw 中该 JD 的运行记录数与 expected_runs 完全一致；
- 报告条目输入指纹与 JD 原文重算一致；
- 正式抽取结果指纹 == raw 中批准运行的指纹；
- 审核元数据完整（approved_run_index / approved_result_fingerprint /
  reviewed_by / reviewed_at）；
- 来源 run_identifier 与批准运行一致。

旧格式验收产物（增强前生成）的 raw 顶层缺少整轮 identity：复核时以
报告 identity 为整轮身份来源（run_identifier 等），并在正式抽取记录
中补齐来源证明字段（acceptance_run_identifier / report_fingerprint /
raw_fingerprint），使正式抽取自洽且可复算。补齐不改变抽取结果内容，
结果指纹不变，不影响引用它的归并批次。

输出：私有核对记录（reports/P0-3/finalized-extraction-source-check.json）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.database import (
    assert_current_database_schema,
    create_database_engine,
    create_session_factory,
)
from app.extraction import extraction_result_fingerprint, rebuild_extraction_result
from app.extraction_validation import compute_input_fingerprint
from app.models import JobDescription, JobExtraction


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-id", type=int, required=True, help="正式抽取所属 JD")
    parser.add_argument(
        "--report",
        type=Path,
        required=True,
        help="验收报告路径（reports/P0-3/module4-jd45-acceptance-report.json）",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        required=True,
        help="验收原始结果路径",
    )
    parser.add_argument(
        "--run-index",
        type=int,
        default=0,
        help="被人工批准的运行索引（默认 0）",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default="sqlite:///data/jd_skill_insight.db",
        help="数据库URL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="私有核对记录输出路径（默认 reports/P0-3/"
        "finalized-extraction-source-check-<job-id>.json）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    raw = json.loads(args.raw_output.read_text(encoding="utf-8"))
    report_fingerprint = hashlib.sha256(
        args.report.read_bytes()
    ).hexdigest()
    raw_fingerprint = hashlib.sha256(
        args.raw_output.read_bytes()
    ).hexdigest()

    findings: list[str] = []
    if report.get("passed") is not True:
        findings.append("报告顶层未通过（passed != true）")
    if report.get("hard_gate_failures"):
        findings.append(f"报告顶层存在 hard gate：{report['hard_gate_failures']}")
    entry = next(
        (job for job in report.get("jobs") or [] if job.get("job_id") == args.job_id),
        None,
    )
    if entry is None:
        findings.append(f"报告缺少 JD {args.job_id} 条目")
        print("复核失败：报告缺少对应 JD 条目")
        return 1
    expected = entry.get("expected_runs")
    successful = entry.get("successful_runs")
    failed = entry.get("failed_runs")
    if expected is None or successful != expected or failed != 0:
        findings.append(
            f"运行不一致：expected={expected} successful={successful} "
            f"failed={failed}"
        )
    if entry.get("hard_gate_failures"):
        findings.append(f"该 JD hard gate 非空：{entry['hard_gate_failures']}")

    run_key = f"job{args.job_id}_run{args.run_index}"
    run_payload = raw.get(run_key)
    raw_run_keys = [k for k in raw if k.startswith(f"job{args.job_id}_run")]
    if run_payload is None:
        findings.append(f"raw 缺少 {run_key}")
    if len(raw_run_keys) != (expected or 0):
        findings.append(
            f"raw 运行记录数（{len(raw_run_keys)}）与 expected（{expected}）不一致"
        )

    report_identity = report.get("identity") or {}
    if not report_identity.get("run_identifier"):
        findings.append("报告缺少整轮 run_identifier")

    engine = create_database_engine(args.database_url)
    try:
        assert_current_database_schema(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            job = session.query(JobDescription).filter(
                JobDescription.id == args.job_id
            ).one_or_none()
            if job is None:
                findings.append(f"JD {args.job_id} 不存在")
            else:
                entry_fingerprint = compute_input_fingerprint(job.raw_text)
                if entry.get("input_fingerprint") != entry_fingerprint:
                    findings.append("报告条目输入指纹与 JD 原文不一致")
            extraction = session.query(JobExtraction).filter(
                JobExtraction.job_id == args.job_id
            ).order_by(JobExtraction.id.desc()).first()
            if extraction is None:
                findings.append(f"JD {args.job_id} 无正式抽取记录")
                print("复核失败：无正式抽取记录")
                return 1

            run_fingerprint = (run_payload or {}).get("result_fingerprint")
            persisted_fingerprint = extraction_result_fingerprint(
                rebuild_extraction_result(extraction)
            )
            if run_fingerprint != persisted_fingerprint:
                findings.append(
                    f"正式抽取结果指纹（{persisted_fingerprint[:16]}…）"
                    f"与批准运行（{run_fingerprint[:16] if run_fingerprint else None}…）不一致"
                )
            existing_raw = extraction.raw_response or {}
            if existing_raw.get("approved_run_index") is None:
                findings.append("正式抽取缺少审核元数据")
            else:
                if existing_raw.get("approved_run_index") != args.run_index:
                    findings.append("正式抽取批准运行索引与本次不一致")
                if existing_raw.get(
                    "approved_result_fingerprint"
                ) != run_fingerprint:
                    findings.append("正式抽取批准结果指纹与批准运行不一致")
                if existing_raw.get("source_run_identifier") != run_key:
                    findings.append("正式抽取来源运行标识与批准运行不一致")
                if not existing_raw.get("reviewed_by"):
                    findings.append("正式抽取缺少 reviewed_by")
                if not existing_raw.get("reviewed_at"):
                    findings.append("正式抽取缺少 reviewed_at")

            if findings:
                print("离线来源复核未通过：")
                for finding in findings:
                    print(f"  - {finding}")
                return 1

            # 补齐来源证明字段（不改变抽取结果内容）。先拷贝再修改，
            # 避免原地修改属性对象导致 SQLAlchemy 检测不到变化。
            updated_raw = dict(existing_raw)
            changed = []
            if updated_raw.get("acceptance_run_identifier") != report_identity.get(
                "run_identifier"
            ):
                updated_raw["acceptance_run_identifier"] = report_identity.get(
                    "run_identifier"
                )
                changed.append("acceptance_run_identifier")
            if updated_raw.get("report_fingerprint") != report_fingerprint:
                updated_raw["report_fingerprint"] = report_fingerprint
                changed.append("report_fingerprint")
            if updated_raw.get("raw_fingerprint") != raw_fingerprint:
                updated_raw["raw_fingerprint"] = raw_fingerprint
                changed.append("raw_fingerprint")
            if changed:
                extraction.raw_response = updated_raw
                session.commit()
            print(
                f"JD {args.job_id} 离线来源复核通过"
                + (f"（补齐：{', '.join(changed)}）" if changed else "（已一致）")
            )
    finally:
        engine.dispose()

    record = {
        "job_id": args.job_id,
        "run_identifier": report_identity.get("run_identifier"),
        "approved_run_index": args.run_index,
        "approved_result_fingerprint": (run_payload or {}).get(
            "result_fingerprint"
        ),
        "expected_runs": expected,
        "successful_runs": successful,
        "failed_runs": failed,
        "report_fingerprint": report_fingerprint,
        "raw_fingerprint": raw_fingerprint,
        "checked_at": "2026-08-06T00:00:00+00:00",
        "passed": True,
    }
    if args.output is None:
        args.output = Path(
            f"reports/P0-3/finalized-extraction-source-check-{args.job_id}.json"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"核对记录：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())