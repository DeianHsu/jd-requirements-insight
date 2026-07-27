"""该模块提供本地JD导入和列表查看的命令行界面。"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from app.config import load_llm_settings
from app.database import create_database_engine, create_session_factory, initialize_database
from app.evaluation import (
    combine_metrics,
    evaluate_extraction,
    load_golden_file,
    validate_golden_directory,
)
from app.extraction import (
    ExtractorMetadata,
    OpenAICompatibleExtractionClient,
    extract_all_jobs,
    list_extractions,
)
from app.ingestion import import_directory, list_jobs
from app.schemas import JobExtractionResult

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


@cli.command("validate-golden")
def validate_golden(
    golden_directory: Path = typer.Argument(..., help="人工黄金JSON所在目录"),
    raw_jd_directory: Path = typer.Argument(..., help="原始Markdown JD所在目录"),
) -> None:
    """验证黄金数据的Schema、来源文件和原文证据是否全部有效。"""
    summary = validate_golden_directory(golden_directory, raw_jd_directory)
    console.print(f"发现 [bold]{summary.discovered}[/bold] 个黄金文件")
    console.print(f"校验通过 [green]{summary.valid}[/green]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- {error}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


@cli.command("extract-jds")
def extract_jds(max_attempts: int = typer.Option(2, min=1, max=5)) -> None:
    """使用已配置的OpenAI兼容LLM批量抽取数据库中的JD结构。"""
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
        summary = extract_all_jobs(
            session_factory, client, metadata, max_attempts=max_attempts
        )
    finally:
        engine.dispose()

    console.print(f"发现 [bold]{summary.discovered}[/bold] 份JD")
    console.print(f"成功抽取 [green]{summary.extracted}[/green]")
    console.print(f"同版本跳过 [yellow]{summary.skipped}[/yellow]")
    console.print(f"失败 [red]{summary.failed}[/red]")
    for error in summary.errors:
        console.print(f"  [red]- JD {error.job_id} / {error.source_file}: {error.message}[/red]")
    if summary.failed:
        raise typer.Exit(code=1)


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
    table.add_column("职责", justify="right")
    table.add_column("要求", justify="right")
    table.add_column("模型")
    for extraction in extractions:
        table.add_row(
            str(extraction.id),
            extraction.job.company,
            extraction.job.title,
            extraction.role_family,
            extraction.seniority,
            str(len(extraction.responsibilities)),
            str(len(extraction.requirements)),
            extraction.model_name,
        )
    console.print(table)


@cli.command("evaluate-extractions")
def evaluate_extractions(
    golden_directory: Path = typer.Argument(..., help="人工黄金JSON所在目录"),
) -> None:
    """把数据库中每份JD的最新抽取结果与人工黄金答案进行对比评测。"""
    engine, session_factory = database_resources()
    try:
        persisted = list_extractions(session_factory)
    finally:
        engine.dispose()

    # 同一JD存在多个版本时选取ID最大的最新记录，避免重复计入总体指标。
    latest_by_source = {}
    for extraction in persisted:
        latest_by_source[extraction.job.source_file] = extraction

    metrics = []
    missing_sources = []
    for path in sorted(golden_directory.glob("*.json")):
        golden = load_golden_file(path)
        extraction = latest_by_source.get(golden.source_file)
        if extraction is None:
            missing_sources.append(golden.source_file)
            continue
        predicted = JobExtractionResult.model_validate(extraction.raw_response)
        metrics.append(evaluate_extraction(predicted, golden.extraction))

    if not metrics:
        console.print("[yellow]没有可与黄金答案对比的抽取结果。[/yellow]")
        raise typer.Exit(code=1)

    combined = combine_metrics(metrics)
    console.print(f"参与评测JD：[bold]{len(metrics)}[/bold]")
    console.print(f"Precision：[cyan]{combined.precision:.2%}[/cyan]")
    console.print(f"Recall：[cyan]{combined.recall:.2%}[/cyan]")
    console.print(f"F1：[cyan]{combined.f1:.2%}[/cyan]")
    console.print(f"重要程度准确率：[cyan]{combined.importance_accuracy:.2%}[/cyan]")
    if missing_sources:
        console.print(f"[yellow]缺少抽取结果：{', '.join(missing_sources)}[/yellow]")


def main() -> None:
    """启动Typer命令行应用并分派用户输入的子命令。"""
    cli()


if __name__ == "__main__":
    main()
