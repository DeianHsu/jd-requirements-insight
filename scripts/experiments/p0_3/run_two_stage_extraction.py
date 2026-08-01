"""该模块显式运行P0-3两段式真实抽取，并把原始结果保存到私有实验目录。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlalchemy import select

from app.config import load_llm_settings
from app.database import (
    create_database_engine,
    create_session_factory,
    initialize_database,
)
from app.extraction import OpenAICompatibleExtractionClient
from app.extraction_two_stage import extract_job_two_stage
from app.models import JobDescription

DEFAULT_OUTPUT_PATH = Path(
    "data/private/experiments/p0_3/two_stage_results.json"
)


def parse_args() -> argparse.Namespace:
    """解析实验范围、数据库目标、输出位置和付费调用确认参数。"""
    parser = argparse.ArgumentParser(description="运行P0-3两段式真实抽取实验")
    database_group = parser.add_mutually_exclusive_group(required=True)
    database_group.add_argument(
        "--use-project-database",
        action="store_true",
        help="显式使用项目默认数据库",
    )
    database_group.add_argument(
        "--database-url",
        help="显式指定实验数据库URL，建议使用临时SQLite副本",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"原始结果路径，默认{DEFAULT_OUTPUT_PATH.as_posix()}",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="确认执行会产生费用的真实LLM调用",
    )
    return parser.parse_args()


def main() -> None:
    """按显式参数读取JD、逐份执行两段式抽取并保存成功或失败结果。"""
    args = parse_args()
    if not args.execute:
        raise SystemExit("真实LLM实验必须显式传入--execute")

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        raise SystemExit(f"缺少LLM配置：{', '.join(missing)}")

    database_url = None if args.use_project_database else args.database_url
    engine = create_database_engine(database_url)
    try:
        initialize_database(engine)
        session_factory = create_session_factory(engine)
        with session_factory() as session:
            jobs = list(
                session.scalars(
                    select(JobDescription).order_by(JobDescription.id)
                )
            )

        target = "项目默认数据库" if args.use_project_database else "自定义数据库"
        print(
            f"实验确认: database={target} jobs={len(jobs)} model={settings.model}",
            flush=True,
        )
        client = OpenAICompatibleExtractionClient(settings)
        results: dict[str, object] = {}
        for job in jobs:
            print(f"开始 {job.source_file} (job={job.id})", flush=True)
            try:
                result, raw = extract_job_two_stage(job, client)
                results[job.source_file] = raw
                print(
                    f"  完成: 职责{len(result.responsibilities)}项 "
                    f"要求{len(result.requirements)}项 "
                    f"方向={result.role_family.value} "
                    f"级别={result.seniority.value}",
                    flush=True,
                )
            except Exception as exc:  # 实验批处理保留单份错误并继续其他JD
                print(f"  失败: {exc}", flush=True)
                results[job.source_file] = {"error": str(exc)}

        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已写入 {args.output}", flush=True)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
