"""从已完成的真实 JD 验收产物定稿正式抽取（不调用模型）。"""

from __future__ import annotations

import argparse
from pathlib import Path

import app.extraction_finalization as finalization_core
from app.extraction import rebuild_extraction_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--job-id", type=int, required=True)
    parser.add_argument("--run-index", type=int, default=0)
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    # 保留测试/诊断注入点；正式实现位于 app。
    finalization_core.rebuild_extraction_result = rebuild_extraction_result
    return finalization_core.finalize_extraction(
        report_path=args.report,
        raw_output_path=args.raw_output,
        job_id=args.job_id,
        run_index=args.run_index,
        database_url=args.database_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
