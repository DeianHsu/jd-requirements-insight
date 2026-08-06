"""从已审核 P0-4 验收产物定稿正式归并批次（不调用模型）。"""

from __future__ import annotations

import argparse
from pathlib import Path

from app.consolidation_finalization import finalize_consolidation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True)
    parser.add_argument("--run-index", type=int, default=0)
    parser.add_argument("--final-result", type=Path)
    parser.add_argument("--review-decisions", type=Path)
    parser.add_argument(
        "--database-url",
        type=str,
        required=True,
        help="显式选择的数据库 URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return finalize_consolidation(
        report_path=args.report,
        raw_output_path=args.raw_output,
        run_index=args.run_index,
        final_result_path=args.final_result,
        review_decisions_path=args.review_decisions,
        database_url=args.database_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
