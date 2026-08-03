"""该模块提供本地JD导入和列表查看的命令行界面。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_llm_settings
from app.consolidation import (
    ConsolidatorMetadata,
    OpenAICompatibleConsolidationClient,
    consolidate_requirements,
    list_consolidations,
)
from app.consolidation_validation import (
    load_persisted_consolidation_result,
    validate_contract,
)
from app.database import create_database_engine, create_session_factory, initialize_database
from app.extraction import (
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
    extract_jobs,
    list_extractions,
)
from app.ingestion import import_directory, list_jobs

cli = typer.Typer(no_args_is_help=True, help="JD Skill Insight 本地数据工具")
console = Console()


def database_resources():
    """为一次CLI调用创建数据库Engine、数据表和Session工厂。"""
    engine = create_database_engine()
    initialize_database(engine)
    return engine, create_session_factory(engine)


@cli.command("import-jds")
def import_jds(directory: Path = typer.Argument(..., help="包含Markdown JD的目录")) -> None:
    """把指定目录中的Markdown JD批量导入SQLite并显示汇总结果。"""
    engine, session_factory = database_resources()
    # 无论导入成功还是路径校验失败，都释放Engine持有的数据库连接资源。
    try:
        summary = import_directory(directory, session_factory)
    except (FileNotFoundError, NotADirectoryError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    # 统一展示批处理计数，让用户能够判断是否需要修复数据或重复执行。
    console.print(f"发现 [bold]{summary.discovered}[/bold] 个JD文件")
    console.print(f"成功导入 [green]{summary.imported}[/green]")
    console.print(f"重复跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")

    # 将错误与具体文件关联，避免用户在整个目录中人工排查。
    for error in summary.errors:
        console.print(f"  [red]- {error.source_file}: {error.message}[/red]")

    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("list-jds")
def show_jds() -> None:
    """以表格形式列出已导入JD的摘要信息而不输出完整正文。"""
    engine, session_factory = database_resources()
    # 查询结束后立即释放连接，避免Windows下SQLite文件长期被占用。
    try:
        jobs = list_jobs(session_factory)
    finally:
        engine.dispose()

    if not jobs:
        console.print("数据库中还没有JD。")
        return

    # 表格只展示人工浏览所需摘要，完整原文仍保留在数据库中供后续分析。
    table = Table(title=f"已导入JD（{len(jobs)}）")
    table.add_column("ID", justify="right")
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("城市")
    table.add_column("薪资")
    table.add_column("来源文件")

    for job in jobs:
        table.add_row(
            str(job.id),
            job.company,
            job.title,
            job.city or "-",
            job.salary or "-",
            job.source_file,
        )
    console.print(table)


@cli.command("extract-jds")
def extract_jds(
    max_attempts: int = typer.Option(2, min=1, max=5),
    limit: int = typer.Option(3, min=1, help="开发模式最多抽取的JD数量"),
    all_jobs: bool = typer.Option(False, "--all", help="显式抽取全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只抽取指定JD，可重复传入"
    ),
) -> None:
    """默认小批量抽取JD，也可显式选择全部或指定ID。"""
    if all_jobs and job_ids:
        console.print("[red]--all不能与--job-id同时使用。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    engine, session_factory = database_resources()
    try:
        # 客户端和版本元数据在批次开始时固定，保证整批结果可以复现和比较。
        client = OpenAICompatibleExtractionClient(settings)
        metadata = ExtractorMetadata(model_name=settings.model)
        summary = extract_jobs(
            session_factory,
            client,
            metadata,
            max_attempts=max_attempts,
            limit=None if all_jobs or job_ids else limit,
            job_ids=set(job_ids) if job_ids else None,
        )
    finally:
        engine.dispose()

    console.print(f"本次选择 [bold]{summary.discovered}[/bold] 份JD")
    console.print(f"成功抽取 [green]{summary.extracted}[/green]")
    console.print(f"同版本跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- JD {error.job_id} / {error.source_file}: {error.message}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("consolidate-requirements")
def consolidate_requirements_cmd(
    max_attempts: int = typer.Option(2, min=1, max=5),
    all_jobs: bool = typer.Option(False, "--all", help="显式归并全部JD"),
    job_ids: list[int] | None = typer.Option(
        None, "--job-id", min=1, help="只归并指定JD，可重复传入"
    ),
    extractor_version: str | None = typer.Option(
        None,
        "--extractor-version",
        help="选择覆盖全部目标JD的抽取器版本；存在多个共同版本时必须指定",
    ),
) -> None:
    """对选定JD范围内的要求实例执行跨JD原子要求归并并幂等保存。

    只完成 canonical requirements 与 instance mappings：每个实例必须
    且只能映射到一个标准要求项，不确定时创建 singleton。
    """
    if all_jobs == bool(job_ids):
        console.print("[red]必须且只能选择--all或--job-id之一。[/red]")
        raise typer.Exit(code=2)

    settings = load_llm_settings()
    missing = settings.missing_fields()
    if missing:
        console.print(f"[red]缺少LLM配置：{', '.join(missing)}[/red]")
        console.print("请复制 .env.example 为 .env，并填写真实配置。")
        raise typer.Exit(code=1)

    engine, session_factory = database_resources()
    try:
        # 客户端和版本元数据在批次开始时固定，保证整批结果可以复现和比较。
        client = OpenAICompatibleConsolidationClient(settings)
        metadata = ConsolidatorMetadata(model_name=settings.model)
        summary = consolidate_requirements(
            session_factory,
            client,
            metadata,
            max_attempts=max_attempts,
            job_ids=set(job_ids) if job_ids else None,
            extractor_version=extractor_version,
        )
    finally:
        engine.dispose()

    console.print(f"语料池 [bold]{summary.discovered}[/bold] 条要求实例")
    console.print(f"归并成功 [green]{summary.consolidated}[/green] 条")
    console.print(f"标准要求项 [bold]{summary.canonical_count}[/bold] 个")
    console.print(f"同版本跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    if summary.consolidation_id is not None:
        console.print(f"归并批次ID [bold]{summary.consolidation_id}[/bold]")
    if summary.input_fingerprint is not None:
        console.print(f"输入指纹 [cyan]{summary.input_fingerprint[:12]}[/cyan]")
    for error in summary.errors:
        console.print(f"  [red]- {error.scope}: {error.message}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("list-consolidations")
def show_consolidations() -> None:
    """以表格形式列出已持久化的归并批次摘要而不输出完整映射。"""
    engine, session_factory = database_resources()
    try:
        consolidations = list_consolidations(session_factory)
    finally:
        engine.dispose()

    if not consolidations:
        console.print("数据库中还没有归并批次。")
        return

    table = Table(title=f"已持久化归并批次（{len(consolidations)}）")
    table.add_column("ID", justify="right")
    table.add_column("范围")
    table.add_column("归并器版本")
    table.add_column("抽取器版本")
    table.add_column("输入指纹")
    table.add_column("实例数", justify="right")
    table.add_column("标准项", justify="right")
    table.add_column("映射", justify="right")

    for record in consolidations:
        table.add_row(
            str(record.id),
            record.scope_key,
            record.consolidator_version,
            record.extractor_version,
            record.input_fingerprint[:12],
            str(record.occurrence_count),
            str(len(record.canonical_requirements)),
            str(len(record.mappings)),
        )
    console.print(table)


@cli.command("validate-consolidation")
def validate_consolidation_cmd(
    consolidation_id: int = typer.Option(
        ...,
        "--consolidation-id",
        min=1,
        help="显式指定要验证的持久化归并批次ID",
    ),
) -> None:
    """离线验证一个已持久化归并批次：合同与稳定性检查。

    不调用LLM、不隐式选择最新批次；输出 P0-4 合同违规计数。
    """
    engine, session_factory = database_resources()
    try:
        persisted = load_persisted_consolidation_result(
            session_factory, consolidation_id
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    contract = validate_contract(
        persisted.result,
        expected_requirement_count=len(persisted.result.mappings),
    )

    console.print(f"归并批次ID [bold]{persisted.consolidation_id}[/bold]")
    console.print(f"范围 [bold]{persisted.scope_key}[/bold]")
    console.print(f"归并器版本 [bold]{persisted.consolidator_version}[/bold]")
    console.print(
        "P0-4 完整覆盖 "
        + (f"[green]{contract.coverage:.2%}[/green]"
           if contract.coverage == 1.0 else
           f"[red]{contract.coverage:.2%}[/red]")
    )
    console.print(
        "P0-4 结构违规 "
        + (f"[green]{contract.structural_violation_count}[/green]"
           if contract.structural_violation_count == 0 else
           f"[red]{contract.structural_violation_count}[/red]")
    )


@cli.command("list-extractions")
def show_extractions() -> None:
    """列出已持久化的结构化抽取版本及职责和要求数量。"""
    engine, session_factory = database_resources()
    try:
        extractions = list_extractions(session_factory)
    finally:
        engine.dispose()

    if not extractions:
        console.print("数据库中还没有结构化抽取结果。")
        return

    table = Table(title=f"JD结构化抽取（{len(extractions)}）")
    table.add_column("ID", justify="right")
    table.add_column("公司")
    table.add_column("岗位")
    table.add_column("方向")
    table.add_column("级别")
    table.add_column("要求", justify="right")
    table.add_column("模型")
    for extraction in extractions:
        table.add_row(
            str(extraction.id),
            extraction.job.company,
            extraction.job.title,
            extraction.role_family,
            extraction.seniority,
            str(len(extraction.requirements)),
            extraction.model_name,
        )
    console.print(table)


def main() -> None:
    """启动Typer命令行应用并分派用户输入的子命令。"""
    cli()


if __name__ == "__main__":
    main()
